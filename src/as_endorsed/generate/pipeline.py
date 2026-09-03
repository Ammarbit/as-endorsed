"""Question in, checked and cited answer out.

    route ─► declarations lookup (typed facts, cited to the declarations page)
         └► retrieve (account-scoped, as-of aware) ─► draft ─► checks ─► answer
                        ▲                               │
                        └── one rewrite-and-retry ◄── can't answer

Checks, in order:
    groundedness   every claim cites a retrieved chunk that lexically supports it;
                   unsupported claims are dropped, and an answer with nothing
                   left is withheld
    numeric guard  the number the answer turns on must appear in a cited chunk
                   (or be the declarations value); otherwise the answer is withheld
    abstention     "the policy does not address this" is a valid, cited outcome
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from as_endorsed.generate.context import money_values, numbers, supported
from as_endorsed.generate.schema import Answer, Citation, Claim, Draft
from as_endorsed.models import ParsedForm
from as_endorsed.retrieval.embed import Embedder
from as_endorsed.retrieval.index import Hit, Index, SearchConfig, search
from as_endorsed.retrieval.rerank import Reranker
from as_endorsed.retrieval.router import answer_declarations, retrieval_query, route
from as_endorsed.synth.accounts import Account


class Generator(Protocol):
    name: str
    supports_rewrite: bool

    def draft(self, question: str, hits: list[Hit]) -> Draft: ...

    def rewrite(self, question: str, hits: list[Hit], missing: str) -> str | None: ...


@dataclass
class GenConfig:
    search: SearchConfig = field(default_factory=lambda: SearchConfig(mode="hybrid", rerank=True, k=5, pull_definitions=True))
    loop: bool = True
    min_support: float = 0.5


@dataclass
class Resources:
    """What answering needs for one account: the index to search (already as-of
    resolved when a date is in play), the embedder, the reranker, the base form."""

    index: Index
    embedder: Embedder
    reranker: Reranker | None
    base: ParsedForm


def answer_question(question: str, acct: Account, res: Resources, generator: Generator, cfg: GenConfig,
                    *, as_of: date | None = None) -> Answer:
    t0 = time.perf_counter()
    rt = route(question)
    when = as_of or rt.as_of
    ans = Answer(question=question, account_id=acct.account_id, status="answered", answer="", route=rt.kind, generator=generator.name)

    if rt.kind == "declarations" and rt.field:
        dec = answer_declarations(acct, rt.field, when)
        if dec is not None:
            value = dec.value
            ans.answer = f"${value:,}" if isinstance(value, int) and rt.field.endswith(("limit", "deductible", "premium")) else str(value)
            ans.numeric_value = float(value) if isinstance(value, (int, float)) else None
            ans.claims = [Claim(text=ans.answer, chunk_ids=[f"declarations:{rt.field}"])]
            ans.citations = [Citation(chunk_id=f"declarations:{rt.field}", paths=[], source="declarations", lineage=[], quote=dec.citation)]
            if isinstance(value, str) and value.lower().startswith("no "):
                ans.status = "abstain"
                ans.reason = value
            ans.checks = {"groundedness": True, "numeric": True}
            ans.latency_ms = (time.perf_counter() - t0) * 1000
            return ans

    query = retrieval_query(question)
    hits = search(res.index, res.embedder, query, acct.account_id, cfg.search, res.reranker, res.base)
    draft = generator.draft(question, hits)

    if not draft.can_answer and cfg.loop and generator.supports_rewrite:
        rewritten = generator.rewrite(question, hits, draft.missing)
        if rewritten:
            ans.loop_used, ans.rewritten_query = True, rewritten
            more = search(res.index, res.embedder, rewritten, acct.account_id, cfg.search, res.reranker, res.base)
            seen = {h.chunk.chunk_id for h in hits}
            hits = hits + [h for h in more if h.chunk.chunk_id not in seen]
            draft = generator.draft(question, hits)

    if not draft.can_answer:
        ans.status, ans.reason = "abstain", draft.missing or "the retrieved clauses do not address the question"
        ans.citations = [_cite(h) for h in hits[:3]]
        ans.checks = {"groundedness": True, "numeric": True}
        ans.latency_ms = (time.perf_counter() - t0) * 1000
        return ans

    by_id = {h.chunk.chunk_id: h for h in hits}
    kept: list[Claim] = []
    cited: dict[str, Hit] = {}
    for claim in draft.claims:
        good = [cid for cid in claim.chunk_ids if cid in by_id and supported(claim.text, by_id[cid].chunk.embed_text, cfg.min_support)]
        if good:
            kept.append(Claim(text=claim.text, chunk_ids=good))
            for cid in good:
                cited[cid] = by_id[cid]
    grounded = bool(kept)
    ans.checks["groundedness"] = grounded

    numeric_ok = True
    if draft.numeric_value is not None:
        pool: set[float] = set()
        for h in cited.values():
            pool |= money_values(h.chunk.text) | {float(n) for n in numbers(h.chunk.text) if n.replace(".", "", 1).isdigit()}
        numeric_ok = draft.numeric_value in pool
    ans.checks["numeric"] = numeric_ok

    if not grounded:
        ans.status, ans.reason = "withheld", "no claim in the draft could be grounded in a retrieved clause"
        ans.citations = [_cite(h) for h in hits[:3]]
    elif not numeric_ok:
        ans.status, ans.reason = "withheld", f"the amount {draft.numeric_value:g} does not appear in any cited clause"
        ans.claims, ans.citations = kept, [_cite(h) for h in cited.values()]
    else:
        ans.answer = draft.answer if len(kept) == len(draft.claims) else " ".join(c.text for c in kept)
        ans.claims, ans.numeric_value = kept, draft.numeric_value
        ans.citations = [_cite(h) for h in cited.values()]
    ans.latency_ms = (time.perf_counter() - t0) * 1000
    return ans


def _cite(h: Hit) -> Citation:
    c = h.chunk
    return Citation(chunk_id=c.chunk_id, paths=list(c.paths), source=c.source, lineage=list(c.lineage), quote=c.text[:240])
