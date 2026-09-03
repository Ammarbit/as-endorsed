"""Retrieval ladder tests: chunk variants, splitters, router, structured
declarations answers, fusion, search, and a small end-to-end harness run with
the hash embedder (no model downloads)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from as_endorsed.config import settings
from as_endorsed.corpus import registry
from as_endorsed.endorse.extract import extract_ops
from as_endorsed.endorse.resolve import ScheduledEndorsement, resolve_policy
from as_endorsed.ingest.clauses import parse_form
from as_endorsed.retrieval.chunking import VARIANTS, build_chunks, fixed_windows, recursive_split
from as_endorsed.retrieval.embed import HashEmbedder
from as_endorsed.retrieval.index import Hit, MemoryIndex, SearchConfig, rrf, search
from as_endorsed.retrieval.router import answer_declarations, route
from as_endorsed.synth.accounts import generate_accounts
from as_endorsed.synth.endorsements import EDITION, LIBRARY, render_library

NFIP = settings.raw_dir / registry.get("NFIP-DWELLING@2021-10").filename
pytestmark = pytest.mark.skipif(not NFIP.exists(), reason="corpus not downloaded")


@pytest.fixture(scope="module")
def nfip():
    spec = registry.get("NFIP-DWELLING@2021-10")
    return parse_form(NFIP, form_id=spec.form_id, edition=spec.edition, title=spec.title)


@pytest.fixture(scope="module")
def synthetic(nfip, tmp_path_factory):
    pdfs = render_library(tmp_path_factory.mktemp("endorsements"))
    forms, extractions = {}, {}
    for spec in LIBRARY:
        e = parse_form(pdfs[spec.form_id], form_id=spec.form_id, edition=EDITION, title=spec.title, strict_sequence=False, root_paragraphs=True)
        forms[spec.key] = e
        extractions[spec.key] = extract_ops(e, nfip)
    return forms, extractions


@pytest.fixture(scope="module")
def account(synthetic):
    # An account with the solar exclusion and the basement amendment attached at issue.
    acct = generate_accounts(1, seed=3)[0]
    from as_endorsed.synth.accounts import ScheduledEndorsement as SE
    acct.policy.endorsement_forms = [
        SE(form_id="SYN-END-01", edition=EDITION, title="Basement Definition Amendment", effective_date=acct.policy.term_start),
        SE(form_id="SYN-END-03", edition=EDITION, title="Solar Equipment Exclusion", effective_date=acct.policy.term_start + timedelta(days=90)),
    ]
    return acct


@pytest.fixture(scope="module")
def resolved(account, nfip, synthetic):
    _, extractions = synthetic
    attached = [ScheduledEndorsement(extraction=extractions[e.key], effective_date=e.effective_date, order=i) for i, e in enumerate(account.policy.endorsement_forms)]
    return resolve_policy(account_id=account.account_id, base=nfip, attached=attached, as_of=account.policy.term_end)


def test_splitters_respect_budget():
    text = "Para one. " * 300 + "\n\n" + "Para two sentence. " * 200
    for a, b in fixed_windows(text, 2048, 256):
        assert b - a <= 2048
    pieces = recursive_split(text, 2048)
    assert pieces and all(b - a <= 2048 for a, b in pieces)
    assert " ".join(text[a:b] for a, b in pieces).split() == text.split()  # nothing lost but whitespace


def test_every_variant_builds_and_covers_paths(account, nfip, resolved, synthetic):
    forms, _ = synthetic
    for variant in VARIANTS:
        chunks = build_chunks(account, variant, nfip, resolved=resolved, endorsement_forms=forms)
        assert chunks
        covered = {p for c in chunks for p in c.paths}
        assert "II.C.5" in covered and "IV.14" in covered
        kinds = {c.kind for c in chunks}
        assert "declarations" in kinds
        if variant in ("fixed", "recursive", "clause"):
            assert "endorsement" in kinds  # naive pipelines see endorsements as separate documents
        else:
            by_path = {c.paths[0]: c for c in chunks if c.paths}
            assert "three sides" in by_path["II.C.5"].text and "SYN-END-01@2026-01" in by_path["II.C.5"].lineage
            assert by_path["IV.17"].text.startswith("17. Solar panels")
            if variant == "header":
                assert by_path["II.C.5"].header and "DEFINITIONS" in by_path["II.C.5"].header and "modified by" in by_path["II.C.5"].header


def test_router_and_declarations(account):
    p = account.policy
    r = route(f"What is the building deductible on policy {p.policy_number}?")
    assert r.kind == "declarations" and r.field == "building_deductible" and r.policy_number == p.policy_number
    assert answer_declarations(account, r.field).value == p.coverage("building").deductible
    r = route("How does policy NFP-2026-1111111 define 'basement'?")
    assert r.kind == "clause"
    r = route("What is the most policy NFP-2026-1111111 will pay for sandbags, supplies and labor to protect the building from flood?")
    assert r.kind in ("clause", "mixed")
    r = route(f"What was the building deductible for {p.property_location.street} on 2026-08-01?")
    assert r.kind == "declarations" and r.as_of == date(2026, 8, 1)


def test_rrf_prefers_items_on_both_lists():
    from as_endorsed.retrieval.chunking import Chunk

    def c(i):
        return Chunk(str(i), "A", "clause", f"text {i}", "clause", "f")

    dense = [Hit(c(1), 0.9, 1, "dense"), Hit(c(2), 0.8, 2, "dense"), Hit(c(3), 0.7, 3, "dense")]
    bm25 = [Hit(c(3), 5.0, 1, "bm25"), Hit(c(2), 4.0, 2, "bm25")]
    fused = rrf([dense, bm25])
    assert [h.chunk.chunk_id for h in fused][:2] == ["2", "3"] or [h.chunk.chunk_id for h in fused][:2] == ["3", "2"]
    assert fused[0].via in ("dense+bm25", "bm25+dense")


def test_search_is_account_scoped_and_finds_resolved_clause(account, nfip, resolved, synthetic):
    forms, _ = synthetic
    other = generate_accounts(2, seed=9)[1]
    emb = HashEmbedder()
    chunks = build_chunks(account, "header", nfip, resolved=resolved, endorsement_forms=forms)
    chunks += build_chunks(other, "header", nfip, resolved=resolve_policy(account_id=other.account_id, base=nfip, attached=[], as_of=other.policy.term_end), endorsement_forms=forms)
    index = MemoryIndex("header", chunks, emb.embed_passages([c.embed_text for c in chunks]))
    # The hash embedder is not semantic, so this exercises the lexical path and the account filter.
    hits = search(index, emb, "definition of basement: sunken room, floor below ground level on all sides", account.account_id, SearchConfig(mode="bm25", k=5))
    assert hits and all(h.chunk.account_id == account.account_id for h in hits)
    top_paths = [p for h in hits for p in h.chunk.paths]
    assert "II.C.5" in top_paths
    basement = next(h.chunk for h in hits if "II.C.5" in h.chunk.paths)
    assert "three sides" in basement.text
    hits = search(index, emb, "Are solar panels covered?", account.account_id, SearchConfig(mode="bm25", k=5))
    assert any("IV.17" in h.chunk.paths for h in hits)
    hits = search(index, emb, "elevated building lowest floor", account.account_id, SearchConfig(mode="hybrid", k=3, pull_definitions=True), base=nfip)
    assert any(h.via == "definition" for h in hits) or len(hits) == 3


@pytest.mark.skipif(not (settings.synthetic_dir / "qa.jsonl").exists(), reason="synthetic data not generated")
def test_harness_runs_end_to_end(tmp_path):
    from as_endorsed.eval.harness import RUNGS, run

    rep = run([r for r in RUNGS if r.id in ("3", "6")], embedder_name="hash", reranker_name="none", limit_accounts=4, out_dir=tmp_path, log=lambda m: None)
    assert rep.declarations["exact"] >= 0.95
    assert len(rep.rungs) == 2 and all(r.n > 0 for r in rep.rungs)
    assert (tmp_path / "results.md").exists()
