"""HTTP surface: ask a question about an account, get a checked, cited answer
whose citations carry page and bounding boxes for highlighting in the PDF.

    GET  /api/health
    GET  /api/accounts
    GET  /api/accounts/{id}              declarations, attached endorsements, as-endorsed changes
    GET  /api/accounts/{id}/pdf          the declarations PDF
    POST /api/ask                        {account_id, question, as_of?, generator?, loop?}
    GET  /api/forms/{key}/pdf            a base form or endorsement PDF
    GET  /api/forms/{key}/clauses/{path} a clause with its bounding boxes (as endorsed when account_id is given)
    GET  /api/review                     held and unresolved endorsement ops
    GET  /api/eval                       the retrieval and generation result tables
    /                                    the reference client

Everything is loaded once at startup; as-of views are resolved and indexed on demand.
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from as_endorsed import __version__
from as_endorsed.config import settings
from as_endorsed.corpus import registry
from as_endorsed.endorse.models import ExtractionResult
from as_endorsed.eval.harness import Corpus, _as_of_index, build_index, load_corpus
from as_endorsed.generate.extractive import ExtractiveGenerator
from as_endorsed.generate.pipeline import GenConfig, Resources, answer_question
from as_endorsed.generate.schema import Answer
from as_endorsed.retrieval.embed import make_embedder
from as_endorsed.retrieval.index import MemoryIndex, SearchConfig
from as_endorsed.retrieval.rerank import make_reranker
from as_endorsed.retrieval.router import route
from as_endorsed.synth.endorsements import EDITION as SYN_EDITION, LIBRARY

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
BASE_KEY = "NFIP-DWELLING@2021-10"

# An as-of view is resolved and embedded on demand, so the cache is bounded: an open
# endpoint that allocates per distinct input is a memory-exhaustion lever otherwise.
MAX_AS_OF_CACHE = 32
ASK_RATE_LIMIT, ASK_RATE_WINDOW = 30, 60.0  # requests per client per minute on the expensive path
MAX_RATE_KEYS = 4096

CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdnjs.cloudflare.com; "
    "worker-src 'self' blob: https://cdnjs.cloudflare.com; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data: blob:; "
    "connect-src 'self' https://cdnjs.cloudflare.com; "
    "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
)

_rate: dict[str, list[float]] = {}
_rate_lock = Lock()


def _rate_limited(key: str) -> bool:
    now = time.monotonic()
    with _rate_lock:
        if len(_rate) > MAX_RATE_KEYS:
            _rate.clear()
        hits = [t for t in _rate.get(key, ()) if now - t < ASK_RATE_WINDOW]
        hits.append(now)
        _rate[key] = hits
        return len(hits) > ASK_RATE_LIMIT

class State:
    corpus: Corpus
    embedder: Any
    reranker: Any
    index: MemoryIndex
    generators: dict[str, Any]
    as_of_cache: dict[tuple[str, str], MemoryIndex]


state = State()


def _load() -> None:
    state.corpus = load_corpus()
    state.embedder = make_embedder(os.environ.get("AS_ENDORSED_EMBEDDER", settings.embedder))
    rr = os.environ.get("AS_ENDORSED_RERANKER", settings.reranker)
    state.reranker = make_reranker(rr) if rr != "none" else None
    state.index = build_index(state.corpus, "header", state.embedder)
    if hasattr(state.embedder, "flush"):
        state.embedder.flush()
    state.generators = {"extractive": ExtractiveGenerator()}
    try:
        from as_endorsed.generate.llm import ClaudeGenerator, claude_available

        if claude_available():
            state.generators["claude"] = ClaudeGenerator()
    except Exception:  # noqa: BLE001
        pass
    state.as_of_cache = OrderedDict()


@asynccontextmanager
async def _lifespan(_: FastAPI):
    _load()
    yield


app = FastAPI(title="As-Endorsed", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=_lifespan)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    # The generated API docs load their own bundle and inline scripts; the app's own
    # pages get the strict policy.
    if not request.url.path.startswith(("/api/docs", "/api/openapi.json")):
        response.headers.setdefault("Content-Security-Policy", CSP)
    return response


# ----------------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------------

class AskRequest(BaseModel):
    account_id: str
    question: str = Field(min_length=3, max_length=500)
    as_of: date | None = None
    generator: str | None = Field(default=None, description="extractive | claude; default: claude when available")
    loop: bool = True


class BBoxOut(BaseModel):
    page: int
    x0: float
    y0: float
    x1: float
    y1: float


class CitationOut(BaseModel):
    chunk_id: str
    paths: list[str]
    source: str
    lineage: list[str]
    quote: str
    form_key: str | None = None
    pdf_url: str | None = None
    page: int | None = None
    bboxes: list[BBoxOut] = Field(default_factory=list)
    original_text: str | None = None
    text_as_endorsed: str | None = None
    active: bool = True


class AskResponse(BaseModel):
    answer: Answer
    citations: list[CitationOut]
    retrieval_query: str


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _acct(account_id: str):
    a = state.corpus.by_id.get(account_id)
    if a is None:
        raise HTTPException(404, f"unknown account {account_id}")
    return a


def _pdf_path(form_key: str) -> Path:
    """Resolve a form key to a file through an allowlist.

    The key is never used to build a path directly: only keys that exist in the
    synthetic library or the corpus registry resolve, so no input can walk the
    filesystem.
    """
    synthetic = {spec.key: spec.form_id for spec in LIBRARY}
    if form_key in synthetic:
        p = settings.synthetic_dir / "endorsements" / f"{synthetic[form_key]}.pdf"
    else:
        try:
            p = settings.raw_dir / registry.get(form_key).filename
        except KeyError as e:
            raise HTTPException(404, f"unknown form {form_key}") from e
    if not p.exists():
        raise HTTPException(404, f"PDF for {form_key} is not present; run `as-endorsed bootstrap`")
    return p


def _enrich(c, acct) -> CitationOut:
    out = CitationOut(chunk_id=c.chunk_id, paths=c.paths, source=c.source, lineage=c.lineage, quote=c.quote)
    base = state.corpus.base
    resolved = state.corpus.resolved.get(acct.account_id)
    if c.source == "declarations" or c.chunk_id.startswith("declarations:"):
        out.form_key, out.pdf_url, out.page = "declarations", f"/api/accounts/{acct.account_id}/pdf", 1
        return out
    if c.paths:
        clause = base.by_path().get(c.paths[0])
        changed = resolved.changed_by_path().get(c.paths[0]) if resolved else None
        if clause is not None:
            out.form_key, out.pdf_url, out.page = BASE_KEY, f"/api/forms/{BASE_KEY}/pdf", clause.page_start
            out.bboxes = [BBoxOut(**b.model_dump()) for b in clause.bboxes]
            out.original_text = clause.text
        if changed is not None:
            out.text_as_endorsed, out.active = changed.text_as_endorsed, changed.active
            if clause is None and changed.added_by:
                out.form_key, out.pdf_url, out.page = changed.added_by, f"/api/forms/{changed.added_by}/pdf", 1
        return out
    if c.source and c.source != BASE_KEY:
        out.form_key, out.pdf_url, out.page = c.source, f"/api/forms/{c.source}/pdf", 1
    return out


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "accounts": len(state.corpus.accounts), "chunks": len(state.index.chunks),
            "embedder": state.embedder.name, "reranker": state.reranker.name if state.reranker else "none",
            "generators": list(state.generators)}


@app.get("/api/accounts")
def accounts() -> list[dict]:
    out = []
    for a in state.corpus.accounts:
        p = a.policy
        rp = state.corpus.resolved.get(a.account_id)
        out.append({
            "account_id": a.account_id, "policy_number": p.policy_number, "named_insured": p.named_insured,
            "location": p.property_location.one_line(), "state": p.property_location.state, "flood_zone": p.property_location.flood_zone,
            "term_start": p.term_start.isoformat(), "term_end": p.term_end.isoformat(),
            "endorsements": [{"form_id": e.form_id, "title": e.title, "effective_date": e.effective_date.isoformat()} for e in p.endorsement_forms],
            "mid_term_changes": len(p.endorsements),
            "clauses_changed": len(rp.changed) if rp else 0,
        })
    return out


@app.get("/api/accounts/{account_id}")
def account(account_id: str) -> dict:
    a = _acct(account_id)
    rp = state.corpus.resolved.get(account_id)
    by_path = state.corpus.base.by_path()
    changed = []
    if rp:
        for rc in rp.changed:
            base = by_path.get(rc.path)
            changed.append({
                "path": rc.path, "parent_path": rc.parent_path, "active": rc.active, "added_by": rc.added_by,
                "heading": (base.heading or base.term) if base else None,
                "original_text": rc.original_text, "text_as_endorsed": rc.text_as_endorsed,
                "lineage": [{"endorsement": l.endorsement_key, "op": l.op, "effective_date": l.effective_date.isoformat() if l.effective_date else None} for l in rc.lineage],
                "flags": rc.flags, "page": base.page_start if base else None,
            })
    return {
        "account": a.model_dump(mode="json"),
        "changed": changed,
        "conflicts": [c.model_dump() for c in rp.conflicts] if rp else [],
        "unresolved": [o.model_dump(mode="json") for o in rp.unresolved] if rp else [],
        "held": [o.model_dump(mode="json") for o in rp.held] if rp else [],
        "examples": _examples(a),
    }


def _examples(a) -> list[str]:
    p = a.policy
    qs = [f"What is the building deductible on policy {p.policy_number}?", "How does the policy define 'basement'?",
          "Does the policy exclude hot tubs, spas and swimming pools?",
          "What is the most the policy will pay for sandbags, supplies and labor to protect the building from flood?"]
    for e in p.endorsement_forms:
        spec = next((s for s in LIBRARY if s.form_id == e.form_id), None)
        if spec and spec.qa:
            qs.append(spec.qa[0].question.format(pn=p.policy_number, addr=p.property_location.one_line()))
            if e.effective_date > p.term_start:
                before = e.effective_date - timedelta(days=1)
                qs.append(spec.qa[0].question.format(pn=p.policy_number, addr="")[:-1] + f" as of {before.isoformat()}?")
    return list(dict.fromkeys(qs))[:8]


@app.get("/api/accounts/{account_id}/pdf")
def account_pdf(account_id: str) -> FileResponse:
    _acct(account_id)
    p = settings.synthetic_dir / "accounts" / f"{account_id}.pdf"
    if not p.exists():
        raise HTTPException(404, "declarations PDF not present")
    return FileResponse(p, media_type="application/pdf")


@app.get("/api/forms/{form_key}/pdf")
def form_pdf(form_key: str) -> FileResponse:
    return FileResponse(_pdf_path(form_key), media_type="application/pdf")


@app.get("/api/forms/{form_key}/clauses/{path}")
def clause(form_key: str, path: str, account_id: str | None = None) -> dict:
    if form_key != BASE_KEY:
        raise HTTPException(404, "only the base form's clauses are addressable")
    c = state.corpus.base.by_path().get(path)
    if c is None:
        raise HTTPException(404, f"no clause {path}")
    out = c.model_dump(mode="json")
    if account_id:
        rp = state.corpus.resolved.get(account_id)
        rc = rp.changed_by_path().get(path) if rp else None
        out["as_endorsed"] = rc.model_dump(mode="json") if rc else None
    return out


@app.post("/api/ask", response_model=AskResponse)
def ask(req: AskRequest, request: Request) -> AskResponse:
    from as_endorsed.retrieval.router import retrieval_query

    client = request.client.host if request.client else "unknown"
    if _rate_limited(client):
        raise HTTPException(429, f"more than {ASK_RATE_LIMIT} questions a minute; try again shortly")
    a = _acct(req.account_id)
    name = req.generator or ("claude" if "claude" in state.generators else "extractive")
    gen = state.generators.get(name)
    if gen is None:
        raise HTTPException(400, f"generator {name!r} is not available (have: {', '.join(state.generators)})")
    when = req.as_of or route(req.question).as_of
    index = state.index
    if when:
        # An as-of date outside the policy term has no meaning, and accepting arbitrary
        # dates would let a caller allocate an index per request.
        p = a.policy
        if not (p.term_start <= when <= p.term_end):
            raise HTTPException(422, f"as_of must fall in the policy term, {p.term_start.isoformat()} to {p.term_end.isoformat()}")
        key = (a.account_id, when.isoformat())
        if key not in state.as_of_cache:
            state.as_of_cache[key] = _as_of_index(state.corpus, a, "header", when, state.embedder)
            while len(state.as_of_cache) > MAX_AS_OF_CACHE:
                state.as_of_cache.popitem(last=False)
        state.as_of_cache.move_to_end(key)
        index = state.as_of_cache[key]
    res = Resources(index=index, embedder=state.embedder, reranker=state.reranker, base=state.corpus.base)
    cfg = GenConfig(search=SearchConfig(mode="hybrid", rerank=state.reranker is not None, k=5, pull_definitions=True), loop=req.loop)
    ans = answer_question(req.question, a, res, gen, cfg, as_of=when)
    return AskResponse(answer=ans, citations=[_enrich(c, a) for c in ans.citations], retrieval_query=retrieval_query(req.question))


@app.get("/api/review")
def review() -> list[dict]:
    out = []
    for p in sorted(settings.endorse_dir.glob("*.json")):
        r = ExtractionResult.model_validate_json(p.read_text(encoding="utf-8"))
        for op in r.ops:
            if op.status != "resolved":
                out.append({**op.model_dump(mode="json"), "endorsement_key": f"{r.endorsement_form_id}@{r.endorsement_edition}", "scanned": r.scanned})
    return out


@app.get("/api/eval")
def eval_tables() -> dict:
    d = settings.data_dir / "eval"
    read = lambda name: (d / name).read_text(encoding="utf-8") if (d / name).exists() else None
    return {"retrieval": read("results.md"), "generation": read("generation-extractive-loop.md"),
            "generation_claude": read("generation-claude-claude-opus-5-loop.md")}


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> str:
        return (WEB_DIR / "index.html").read_text(encoding="utf-8")
