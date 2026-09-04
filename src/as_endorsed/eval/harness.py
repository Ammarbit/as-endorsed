"""The ablation ladder.

Each rung adds one thing, so the results table reads as a story:

    1  fixed 512      dense
    2  recursive      dense
    3  clause-aware   dense
    4  clause-aware   hybrid (dense + BM25, RRF)
    5  clause-aware   hybrid + cross-encoder rerank
    6  as-endorsed    hybrid + rerank           <- the headline jump
    7  + contextual header
    7d + definition pull-in

Metrics on the endorsement-resolved category:

    hit@k        a retrieved chunk covers an expected clause path
    mrr          reciprocal rank of the first such chunk
    answer@k     a retrieved chunk carries the *current* answer: for a policy
                 with an endorsement attached, the clause as amended (or the
                 endorsement text naming the clause); for a policy without,
                 the unamended clause. This is the metric that separates a
                 chunk that looks relevant from one that is right.

Declarations questions go through the router and structured lookup; their
exact-match accuracy is reported once, since it does not depend on the rung.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np

from as_endorsed.config import settings
from as_endorsed.endorse.models import ResolvedPolicy
from as_endorsed.endorse.pipeline import load_accounts, load_extraction, load_parsed, parse_endorsement, resolve_account
from as_endorsed.models import ParsedForm
from as_endorsed.retrieval.chunking import VARIANTS, Chunk, build_chunks
from as_endorsed.retrieval.embed import Embedder, make_embedder
from as_endorsed.retrieval.index import MemoryIndex, SearchConfig, search
from as_endorsed.retrieval.rerank import Reranker, make_reranker
from as_endorsed.retrieval.router import answer_declarations, retrieval_query, route
from as_endorsed.synth.accounts import Account
from as_endorsed.synth.endorsements import EDITION as SYN_EDITION, LIBRARY


@dataclass(frozen=True)
class Rung:
    id: str
    variant: str
    mode: str
    rerank: bool
    pull_definitions: bool = False

    @property
    def label(self) -> str:
        parts = [self.variant, self.mode]
        if self.rerank:
            parts.append("rerank")
        if self.pull_definitions:
            parts.append("defs")
        return " + ".join(parts)


RUNGS: list[Rung] = [
    Rung("1", "fixed", "dense", False),
    Rung("2", "recursive", "dense", False),
    Rung("3", "clause", "dense", False),
    Rung("4", "clause", "hybrid", False),
    Rung("5", "clause", "hybrid", True),
    Rung("6", "resolved", "hybrid", True),
    Rung("7", "header", "hybrid", True),
    Rung("7d", "header", "hybrid", True, True),
]


@dataclass
class Corpus:
    base: ParsedForm
    accounts: list[Account]
    resolved: dict[str, ResolvedPolicy]
    endorsement_forms: dict[str, ParsedForm]
    qa: list[dict]

    @property
    def by_id(self) -> dict[str, Account]:
        return {a.account_id: a for a in self.accounts}


def load_corpus(limit_accounts: int | None = None, *, parse_endorsements: bool = True) -> Corpus:
    """Load everything an index or an eval run needs.

    `parse_endorsements=False` skips re-parsing the endorsement PDFs, which only the
    window and clause chunk variants need; the as-endorsed variants read the already
    resolved policies instead.
    """
    base = load_parsed("NFIP-DWELLING@2021-10")
    accounts = load_accounts()
    if limit_accounts:
        accounts = accounts[:limit_accounts]
    ids = {a.account_id for a in accounts}
    resolved = {}
    for a in accounts:
        p = settings.resolved_dir / f"{a.account_id}.json"
        resolved[a.account_id] = ResolvedPolicy.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else resolve_account(a, base)
    end_forms = {}
    for spec in LIBRARY if parse_endorsements else []:
        pdf = settings.synthetic_dir / "endorsements" / f"{spec.form_id}.pdf"
        if pdf.exists():
            end_forms[spec.key] = parse_endorsement(pdf, form_id=spec.form_id, edition=SYN_EDITION, title=spec.title)
    qa = [json.loads(l) for l in (settings.synthetic_dir / "qa.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    qa = [r for r in qa if r["account_id"] in ids]
    return Corpus(base, accounts, resolved, end_forms, qa)


def build_index(corpus: Corpus, variant: str, embedder: Embedder) -> MemoryIndex:
    chunks: list[Chunk] = []
    for a in corpus.accounts:
        chunks.extend(build_chunks(a, variant, corpus.base, resolved=corpus.resolved[a.account_id], endorsement_forms=corpus.endorsement_forms))
    emb = embedder.embed_passages([c.embed_text for c in chunks])
    return MemoryIndex(variant, chunks, emb)


def _as_of_index(corpus: Corpus, acct: Account, variant: str, as_of: date, embedder: Embedder) -> MemoryIndex:
    """Resolve the account as of a date and index just those chunks (on-demand as-of view)."""
    extractions = {e.key: load_extraction(e.key) for e in acct.policy.endorsement_forms}
    rp = resolve_account(acct, corpus.base, as_of=as_of, extractions=extractions)
    chunks = build_chunks(acct, variant, corpus.base, resolved=rp, endorsement_forms=corpus.endorsement_forms)
    return MemoryIndex(variant, chunks, embedder.embed_passages([c.embed_text for c in chunks]))


# ----------------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------------

def _norm(v) -> str:
    s = str(v).strip().casefold()
    s = s.replace("$", "").replace(",", "")
    return s.rstrip(".")


def declarations_exact(row: dict, value) -> bool:
    return _norm(value) == _norm(row["answer"])


def path_hit(row: dict, hits) -> tuple[bool, float]:
    want = set(row.get("expected_paths") or [])
    if not want:
        return False, 0.0
    for h in hits:
        if want & set(h.chunk.paths):
            return True, 1.0 / h.rank
    return False, 0.0


def answer_hit(row: dict, hits) -> bool | None:
    want_paths = set(row.get("expected_paths") or [])
    want_ends = set(row.get("expected_endorsements") or [])
    if want_ends:
        for h in hits:
            c = h.chunk
            if want_paths & set(c.paths) and want_ends <= set(c.lineage):
                return True  # the clause as endorsed
            if c.source in want_ends and (not want_paths or any(p in c.text for p in want_paths)):
                return True  # the endorsement text, naming the clause it changes
            if c.kind == "unresolved" and c.source in want_ends:
                return True
        return False
    if want_paths:
        for h in hits:
            c = h.chunk
            if want_paths & set(c.paths) and not c.lineage and c.active:
                return True
        return False
    return None


@dataclass
class RungResult:
    rung: str
    label: str
    n: int = 0
    hit_at_k: float = 0.0
    mrr: float = 0.0
    answer_at_k: float = 0.0
    by_difficulty: dict[str, dict[str, float]] = field(default_factory=dict)
    latency_ms_p50: float = 0.0
    latency_ms_p95: float = 0.0
    chunks: int = 0


@dataclass
class EvalReport:
    embedder: str
    reranker: str
    k: int
    accounts: int
    declarations: dict[str, float]
    rungs: list[RungResult]

    def to_markdown(self) -> str:
        lines = [f"Embedder: `{self.embedder}` · Reranker: `{self.reranker}` · k={self.k} · accounts={self.accounts}", "",
                 f"Declarations (router + structured lookup): exact match **{self.declarations['exact']:.1%}** on {int(self.declarations['n'])} questions; "
                 f"routed to lookup {self.declarations['routed']:.1%}.", "",
                 "| Rung | Configuration | Chunks | hit@k | MRR | answer@k | resolved | negative | as-of | p50 ms | p95 ms |",
                 "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for r in self.rungs:
            d = r.by_difficulty
            cell = lambda name: f"{d[name]['answer_at_k']:.0%}" if name in d else "–"
            lines.append(f"| {r.rung} | {r.label} | {r.chunks:,} | {r.hit_at_k:.1%} | {r.mrr:.2f} | **{r.answer_at_k:.1%}** | "
                         f"{cell('resolved')} | {cell('negative')} | {cell('as-of')} | {r.latency_ms_p50:.0f} | {r.latency_ms_p95:.0f} |")
        return "\n".join(lines)


def run(rungs: list[Rung] | None = None, *, embedder_name: str = "bge", reranker_name: str = "minilm", k: int = 5,
        limit_accounts: int | None = None, out_dir: Path | None = None, log=print) -> EvalReport:
    rungs = rungs or RUNGS
    corpus = load_corpus(limit_accounts)
    embedder = make_embedder(embedder_name)
    reranker: Reranker | None = make_reranker(reranker_name) if any(r.rerank for r in rungs) else None
    by_id = corpus.by_id

    # Declarations via router, once.
    dec_rows = [r for r in corpus.qa if r["category"] == "declarations"]
    routed = exact = 0
    for row in dec_rows:
        rt = route(row["question"])
        if rt.kind in ("declarations", "mixed") and rt.field:
            routed += 1
            ans = answer_declarations(by_id[row["account_id"]], rt.field, rt.as_of)
            if ans is not None and declarations_exact(row, ans.value):
                exact += 1
    declarations = {"n": float(len(dec_rows)), "routed": routed / len(dec_rows) if dec_rows else 0.0, "exact": exact / len(dec_rows) if dec_rows else 0.0}
    log(f"declarations: {exact}/{len(dec_rows)} exact ({declarations['exact']:.1%}), routed {routed}")

    clause_rows = [r for r in corpus.qa if r["category"] == "endorsement-resolved"]
    indexes: dict[str, MemoryIndex] = {}
    results: list[RungResult] = []
    for rung in rungs:
        if rung.variant not in indexes:
            t = time.perf_counter()
            indexes[rung.variant] = build_index(corpus, rung.variant, embedder)
            if hasattr(embedder, "flush"):
                embedder.flush()
            log(f"index {rung.variant}: {len(indexes[rung.variant].chunks):,} chunks in {time.perf_counter() - t:.1f}s")
        index = indexes[rung.variant]
        cfg = SearchConfig(mode=rung.mode, rerank=rung.rerank, k=k, pull_definitions=rung.pull_definitions)
        res = RungResult(rung.id, rung.label, chunks=len(index.chunks))
        lat: list[float] = []
        agg: dict[str, list[tuple[bool, float, bool | None]]] = {}
        as_of_cache: dict[tuple[str, str], MemoryIndex] = {}
        for row in clause_rows:
            acct = by_id[row["account_id"]]
            rt = route(row["question"])
            idx = index
            if row.get("as_of") and rung.variant in ("resolved", "header"):
                key = (acct.account_id, row["as_of"])
                if key not in as_of_cache:
                    as_of_cache[key] = _as_of_index(corpus, acct, rung.variant, date.fromisoformat(row["as_of"]), embedder)
                idx = as_of_cache[key]
            t = time.perf_counter()
            hits = search(idx, embedder, retrieval_query(row["question"]), acct.account_id, cfg, reranker, corpus.base)
            lat.append((time.perf_counter() - t) * 1000)
            h, rr = path_hit(row, hits)
            a = answer_hit(row, hits)
            agg.setdefault(row["difficulty"], []).append((h, rr, a))
        if hasattr(embedder, "flush"):
            embedder.flush()
        allrows = [x for v in agg.values() for x in v]
        scored = [x for x in allrows if x[2] is not None]
        res.n = len(allrows)
        res.hit_at_k = sum(1 for x in allrows if x[0]) / len(allrows) if allrows else 0.0
        res.mrr = sum(x[1] for x in allrows) / len(allrows) if allrows else 0.0
        res.answer_at_k = sum(1 for x in scored if x[2]) / len(scored) if scored else 0.0
        for diff, xs in agg.items():
            sc = [x for x in xs if x[2] is not None]
            res.by_difficulty[diff] = {"n": len(xs), "hit_at_k": sum(1 for x in xs if x[0]) / len(xs),
                                       "answer_at_k": sum(1 for x in sc if x[2]) / len(sc) if sc else 0.0}
        res.latency_ms_p50 = statistics.median(lat) if lat else 0.0
        res.latency_ms_p95 = float(np.percentile(lat, 95)) if lat else 0.0
        results.append(res)
        log(f"rung {rung.id:>2} {rung.label:34} hit@{k}={res.hit_at_k:.1%} mrr={res.mrr:.2f} answer@{k}={res.answer_at_k:.1%} p50={res.latency_ms_p50:.0f}ms")

    report = EvalReport(embedder.name, reranker.name if reranker else "none", k, len(corpus.accounts), declarations, results)
    out_dir = out_dir or (settings.data_dir / "eval")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps({
        "embedder": report.embedder, "reranker": report.reranker, "k": k, "accounts": report.accounts,
        "declarations": declarations, "rungs": [r.__dict__ for r in results]}, indent=2), encoding="utf-8")
    (out_dir / "results.md").write_text(report.to_markdown(), encoding="utf-8")
    return report
