"""Generation eval over the ground-truth set.

Runs the full answer pipeline (router, retrieval at a chosen rung, generator,
checks, optional loop) and scores:

    exact          money / date / short-text answers, normalised equality
    lexical        long-text answers: share of the reference's content words
                   present in the answer (>= 0.6 counts). A proxy, reported as
                   such; the LLM judge replaces it when a model is configured
    judged         LLM-judge correctness, when available
    abstain P/R    precision and recall of abstaining where the reference says
                   the policy has nothing (unanswerable rows)
    cite@1         the first citation is one of the expected clauses
    withheld       answers the checks refused to release
    loop rate      how often the rewrite-and-retry loop fired
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
from as_endorsed.eval.harness import Corpus, _as_of_index, build_index, load_corpus
from as_endorsed.generate.context import content_words
from as_endorsed.generate.extractive import ExtractiveGenerator
from as_endorsed.generate.pipeline import GenConfig, Generator, Resources, answer_question
from as_endorsed.generate.schema import Answer
from as_endorsed.retrieval.embed import make_embedder
from as_endorsed.retrieval.index import SearchConfig
from as_endorsed.retrieval.rerank import make_reranker


def _norm(v) -> str:
    return str(v).strip().casefold().replace("$", "").replace(",", "").rstrip(".")


def score_row(row: dict, ans: Answer) -> dict:
    expected = str(row["answer"])
    out = {"status": ans.status, "loop": ans.loop_used, "exact": None, "lexical": None, "abstain_expected": row["answer_type"] == "abstain"}
    if row["answer_type"] == "abstain":
        out["abstain_correct"] = ans.status == "abstain" or "no contents coverage" in ans.answer.lower()
        return out
    got = ans.answer if ans.status == "answered" else ""
    if row["answer_type"] in ("money", "date") or len(expected) <= 40:
        want = _norm(expected)
        cand = {_norm(got)}
        if ans.numeric_value is not None:
            cand.add(_norm(int(ans.numeric_value) if float(ans.numeric_value).is_integer() else ans.numeric_value))
        out["exact"] = want in cand or (bool(got) and want in _norm(got))
    else:
        ew = content_words(expected)
        out["lexical"] = (len(ew & content_words(got)) / len(ew)) if ew and got else 0.0
    want_paths = set(row.get("expected_paths") or [])
    out["cite_hit"] = bool(ans.citations) and bool(want_paths & set(ans.citations[0].paths)) if want_paths else None
    return out


@dataclass
class GenReport:
    generator: str
    rung: str
    loop: bool
    n: int
    exact_n: int
    exact: float
    lexical_n: int
    lexical: float
    judged_n: int
    judged: float | None
    abstain_precision: float
    abstain_recall: float
    withheld_rate: float
    abstain_rate: float
    loop_rate: float
    cite_at_1: float
    latency_ms_p50: float
    latency_ms_p95: float
    by_category: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_markdown(self) -> str:
        judged = f"{self.judged:.1%} (n={self.judged_n})" if self.judged is not None else "no model configured"
        lines = [
            f"Generator: `{self.generator}` · retrieval rung: {self.rung} · loop: {'on' if self.loop else 'off'} · questions: {self.n}", "",
            "| Metric | Value |", "|---|---:|",
            f"| Exact match (money, dates, short text; n={self.exact_n}) | **{self.exact:.1%}** |",
            f"| Lexical correctness proxy (long text, ≥0.6 overlap; n={self.lexical_n}) | {self.lexical:.1%} |",
            f"| LLM-judged correctness | {judged} |",
            f"| Abstention precision / recall (unanswerable) | {self.abstain_precision:.0%} / {self.abstain_recall:.0%} |",
            f"| Citation@1 hits an expected clause | {self.cite_at_1:.1%} |",
            f"| Withheld by checks | {self.withheld_rate:.1%} |",
            f"| Abstained | {self.abstain_rate:.1%} |",
            f"| Rewrite-and-retry loop fired | {self.loop_rate:.1%} |",
            f"| Latency p50 / p95 (ms) | {self.latency_ms_p50:.0f} / {self.latency_ms_p95:.0f} |",
            "", "| Category | n | exact | lexical ≥0.6 | cite@1 |", "|---|---:|---:|---:|---:|",
        ]
        for cat, m in self.by_category.items():
            def cell(k):
                return f"{m[k]:.0%}" if m.get(k + "_n", 1) else "–"
            lines.append(f"| {cat} | {int(m['n'])} | {cell('exact')} | {cell('lexical')} | {cell('cite')} |")
        return "\n".join(lines)


def run(*, generator: Generator | None = None, rung: str = "7d", embedder_name: str = "bge", reranker_name: str = "minilm",
        loop: bool = True, limit_accounts: int | None = None, judge=None, out_dir: Path | None = None, log=print) -> GenReport:
    from as_endorsed.eval.harness import RUNGS

    r = next(x for x in RUNGS if x.id == rung)
    corpus = load_corpus(limit_accounts)
    embedder = make_embedder(embedder_name)
    reranker = make_reranker(reranker_name) if r.rerank else None
    generator = generator or ExtractiveGenerator()
    index = build_index(corpus, r.variant, embedder)
    if hasattr(embedder, "flush"):
        embedder.flush()
    cfg = GenConfig(search=SearchConfig(mode=r.mode, rerank=r.rerank, k=5, pull_definitions=r.pull_definitions), loop=loop)
    by_id = corpus.by_id
    as_of_cache: dict[tuple[str, str], object] = {}
    rows_out: list[dict] = []
    lat: list[float] = []
    t0 = time.perf_counter()
    for i, row in enumerate(corpus.qa):
        acct = by_id[row["account_id"]]
        idx = index
        if row.get("as_of") and r.variant in ("resolved", "header") and row["category"] != "declarations":
            key = (acct.account_id, row["as_of"])
            if key not in as_of_cache:
                as_of_cache[key] = _as_of_index(corpus, acct, r.variant, date.fromisoformat(row["as_of"]), embedder)
            idx = as_of_cache[key]
        res = Resources(index=idx, embedder=embedder, reranker=reranker, base=corpus.base)
        ans = answer_question(row["question"], acct, res, generator, cfg)
        lat.append(ans.latency_ms)
        sc = score_row(row, ans)
        if judge is not None and sc.get("exact") is None and not sc["abstain_expected"]:
            try:
                sc["judged"] = judge(row["question"], str(row["answer"]), ans.answer if ans.status == "answered" else f"[{ans.status}] {ans.reason}").correct
            except Exception as e:  # noqa: BLE001
                sc["judged"] = None
                log(f"judge failed on row {i}: {e}")
        rows_out.append({"row": row, "answer": ans.model_dump(mode="json"), "score": sc})
        if (i + 1) % 100 == 0:
            log(f"{i + 1}/{len(corpus.qa)} answered in {time.perf_counter() - t0:.0f}s")

    def rate(xs):
        return sum(1 for x in xs if x) / len(xs) if xs else 0.0

    scores = [x["score"] for x in rows_out]
    exact = [s["exact"] for s in scores if s["exact"] is not None]
    lex = [s["lexical"] >= 0.6 for s in scores if s["lexical"] is not None]
    judged = [s["judged"] for s in scores if s.get("judged") is not None]
    abst_exp = [s for s in scores if s["abstain_expected"]]
    abst_pred = [s for s in scores if s["status"] == "abstain"]
    tp = sum(1 for s in abst_exp if s.get("abstain_correct"))
    cites = [s["cite_hit"] for s in scores if s.get("cite_hit") is not None]
    by_cat: dict[str, dict[str, float]] = {}
    for x in rows_out:
        cat = f"{x['row']['category']}/{x['row']['difficulty']}"
        s = x["score"]
        d = by_cat.setdefault(cat, {"n": 0, "_e": [], "_l": [], "_c": []})
        d["n"] += 1
        if s["exact"] is not None:
            d["_e"].append(s["exact"])
        if s["lexical"] is not None:
            d["_l"].append(s["lexical"] >= 0.6)
        if s.get("cite_hit") is not None:
            d["_c"].append(s["cite_hit"])
    for d in by_cat.values():
        e, l, c = d.pop("_e"), d.pop("_l"), d.pop("_c")
        d["exact"], d["lexical"], d["cite"] = rate(e), rate(l), rate(c)
        d["exact_n"], d["lexical_n"], d["cite_n"] = len(e), len(l), len(c)

    report = GenReport(
        generator=generator.name, rung=rung, loop=loop, n=len(rows_out),
        exact_n=len(exact), exact=rate(exact), lexical_n=len(lex), lexical=rate(lex),
        judged_n=len(judged), judged=rate(judged) if judged else None,
        abstain_precision=(tp / len(abst_pred)) if abst_pred else 0.0, abstain_recall=(tp / len(abst_exp)) if abst_exp else 0.0,
        withheld_rate=rate([s["status"] == "withheld" for s in scores]), abstain_rate=rate([s["status"] == "abstain" for s in scores]),
        loop_rate=rate([s["loop"] for s in scores]), cite_at_1=rate(cites),
        latency_ms_p50=statistics.median(lat) if lat else 0.0, latency_ms_p95=float(np.percentile(lat, 95)) if lat else 0.0,
        by_category=dict(sorted(by_cat.items())),
    )
    out_dir = out_dir or (settings.data_dir / "eval")
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"generation-{generator.name.replace(':', '-').replace('/', '-')}-{'loop' if loop else 'noloop'}"
    (out_dir / f"{tag}.json").write_text(json.dumps({"report": report.__dict__, "rows": rows_out}, indent=2, default=str), encoding="utf-8")
    (out_dir / f"{tag}.md").write_text(report.to_markdown(), encoding="utf-8")
    return report
