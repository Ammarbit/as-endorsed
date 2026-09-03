"""Endorsement engine tests: reference resolution, extraction on the synthetic
library (through real PDFs), extraction on real TWIA endorsements, and the
resolver's precedence rules."""

from __future__ import annotations

from datetime import date

import pytest

from as_endorsed.config import settings
from as_endorsed.corpus import registry
from as_endorsed.endorse.extract import extract_ops, split_sentences
from as_endorsed.endorse.models import ExtractionResult
from as_endorsed.endorse.refs import resolve_ref, resolve_term
from as_endorsed.endorse.resolve import ScheduledEndorsement, materialize, resolve_policy
from as_endorsed.ingest.clauses import parse_form
from as_endorsed.synth.endorsements import BY_ID, EDITION, LIBRARY, compare_ops, render_library

NFIP = settings.raw_dir / registry.get("NFIP-DWELLING@2021-10").filename
TWIA = settings.raw_dir / registry.get("TWIA-DWELLING@2023-08").filename

pytestmark = pytest.mark.skipif(not NFIP.exists(), reason="corpus not downloaded")


@pytest.fixture(scope="module")
def nfip():
    spec = registry.get("NFIP-DWELLING@2021-10")
    return parse_form(NFIP, form_id=spec.form_id, edition=spec.edition, title=spec.title)


@pytest.fixture(scope="module")
def twia():
    if not TWIA.exists():
        pytest.skip("TWIA policy not downloaded")
    spec = registry.get("TWIA-DWELLING@2023-08")
    return parse_form(TWIA, form_id=spec.form_id, edition=spec.edition, title=spec.title)


@pytest.fixture(scope="module")
def synthetic(nfip, tmp_path_factory):
    pdfs = render_library(tmp_path_factory.mktemp("endorsements"))
    out = {}
    for spec in LIBRARY:
        e = parse_form(pdfs[spec.form_id], form_id=spec.form_id, edition=EDITION, title=spec.title, strict_sequence=False, root_paragraphs=True)
        out[spec.form_id] = extract_ops(e, nfip)
    return out


def test_split_sentences_keeps_labels_together():
    s = split_sentences("Not a FEMA document. Paragraph II.C.5 of Section II. DEFINITIONS is replaced by the following: Condition 4.b.(2). is replaced by the following: All other terms apply.")
    assert s[1].startswith("Paragraph II.C.5 of Section II. DEFINITIONS")
    assert s[2].startswith("Condition 4.b.(2). is replaced")


def test_refs_nfip(nfip):
    assert resolve_ref("Paragraph II.C.5 (Basement) of Section II. DEFINITIONS", nfip).path == "II.C.5"
    assert resolve_ref("Section IV. PROPERTY NOT INSURED", nfip).path == "IV"
    assert resolve_ref("Paragraph III.C.2.a.(1)", nfip).path == "III.C.2.a.(1)"
    assert resolve_term("Elevated Building", nfip).path == "II.C.16"
    assert resolve_ref("Paragraph II.C.99", nfip).path is None


def test_refs_twia(twia):
    assert resolve_ref("Your Duties After Loss Condition 4.a.(5)", twia).path == "CONDITIONS.4.a.(5)"
    assert resolve_ref("Our Duties After Loss Settlement Condition 4.b.(2).", twia).path == "CONDITIONS.4.b.(2)"
    assert resolve_ref("Loss Settlement Condition 6.", twia).path == "CONDITIONS.6"
    assert resolve_ref("the DEDUCTIBLE clause", twia).path == "DEDUCTIBLE"
    assert resolve_ref("Coverage A (Dwelling)", twia).path == "COVERAGE-A-DWELLING"


def test_synthetic_library_extracts_exactly(synthetic):
    misses = []
    for spec in LIBRARY:
        for row in compare_ops(spec, synthetic[spec.form_id].ops):
            if not row["matched"]:
                misses.append((spec.form_id, row["expected"], [(o.op, o.target_path, o.status) for o in synthetic[spec.form_id].ops]))
    assert misses == []


def test_synthetic_statuses(synthetic):
    assert synthetic["SYN-END-01"].ops[0].status == "resolved"
    assert synthetic["SYN-END-07"].ops[0].status == "held"  # schedule blanks unfilled
    assert synthetic["SYN-END-08"].ops[0].status == "unresolved"  # no target named


def test_twia_802_extracts_all_directives(twia):
    spec = registry.get("TWIA-802@2019-11")
    pdf = settings.raw_dir / spec.filename
    if not pdf.exists():
        pytest.skip("TWIA 802 not downloaded")
    e = parse_form(pdf, form_id=spec.form_id, edition=spec.edition, title=spec.title, strict_sequence=False, root_paragraphs=True)
    ops = extract_ops(e, twia).ops
    got = {(o.op, o.target_path) for o in ops}
    assert ("REPLACE", "CONDITIONS.4.a.(5)") in got
    assert ("REPLACE", "CONDITIONS.4.b.(2)") in got
    assert ("ADD", "CONDITIONS.6") in got
    add = next(o for o in ops if o.op == "ADD" and o.target_path == "CONDITIONS.6")
    assert add.new_label == "c"
    assert any(f.labels[-1:] == ["(1)"] for f in add.new_clauses)


def _attached(synthetic, form_id: str, effective: date, order: int = 0, values=None) -> ScheduledEndorsement:
    return ScheduledEndorsement(extraction=synthetic[form_id], effective_date=effective, order=order, schedule_values=values or {})


def test_resolve_replace_delete_add_amend(nfip, synthetic):
    rp = resolve_policy(account_id="T", base=nfip, as_of=date(2027, 1, 1), attached=[
        _attached(synthetic, "SYN-END-01", date(2026, 1, 1), 0),
        _attached(synthetic, "SYN-END-02", date(2026, 1, 1), 1),
        _attached(synthetic, "SYN-END-03", date(2026, 1, 1), 2),
        _attached(synthetic, "SYN-END-04", date(2026, 1, 1), 3),
    ])
    by = rp.changed_by_path()
    assert "three sides" in by["II.C.5"].text_as_endorsed
    assert by["II.C.5"].original_text.endswith("on all sides.")
    assert by["IV.14"].active is False
    assert by["IV.17"].text_as_endorsed.startswith("Solar panels")
    assert by["IV.17"].parent_path == "IV"
    assert "300 square feet" in by["II.C.16"].text_as_endorsed and by["II.C.16"].text_as_endorsed.startswith("Elevated Building.")
    assert rp.conflicts == [] and rp.unresolved == [] and rp.held == []
    full = materialize(nfip, rp)
    paths = [c.path for c in full]
    assert paths.index("IV.17") == paths.index("IV.16") + 1


def test_resolve_later_date_wins(nfip, synthetic):
    rp = resolve_policy(account_id="T", base=nfip, as_of=date(2027, 1, 1), attached=[
        _attached(synthetic, "SYN-END-06", date(2026, 1, 1), 0),
        _attached(synthetic, "SYN-END-01", date(2026, 3, 1), 1),
    ])
    assert "three sides" in rp.changed_by_path()["II.C.5"].text_as_endorsed
    assert rp.conflicts == []
    assert [l.endorsement_key for l in rp.changed_by_path()["II.C.5"].lineage] == ["SYN-END-06@2026-01", "SYN-END-01@2026-01"]


def test_resolve_same_date_is_a_conflict(nfip, synthetic):
    rp = resolve_policy(account_id="T", base=nfip, as_of=date(2027, 1, 1), attached=[
        _attached(synthetic, "SYN-END-01", date(2026, 1, 1), 0),
        _attached(synthetic, "SYN-END-06", date(2026, 1, 1), 1),
    ])
    assert len(rp.conflicts) == 1 and rp.conflicts[0].path == "II.C.5"
    assert "crawlspace" in rp.changed_by_path()["II.C.5"].text_as_endorsed  # schedule order applied


def test_resolve_respects_as_of(nfip, synthetic):
    rp = resolve_policy(account_id="T", base=nfip, as_of=date(2026, 2, 1), attached=[
        _attached(synthetic, "SYN-END-05", date(2026, 6, 1), 0),
    ])
    assert rp.changed == []


def test_resolve_schedule_fill_and_unresolved(nfip, synthetic):
    rp = resolve_policy(account_id="T", base=nfip, as_of=date(2027, 1, 1), attached=[
        _attached(synthetic, "SYN-END-07", date(2026, 1, 1), 0, {"structures": "Boat house on the canal"}),
        _attached(synthetic, "SYN-END-08", date(2026, 1, 1), 1),
    ])
    by = rp.changed_by_path()
    assert "Boat house on the canal" in by["IV.18"].text_as_endorsed and "____" not in by["IV.18"].text_as_endorsed
    unresolved = [c for c in rp.changed if c.path.startswith("UNRESOLVED.")]
    assert len(unresolved) == 1 and "$5,000" in unresolved[0].text_as_endorsed
    assert rp.held == []
    # Without a schedule value the same op stays held.
    rp2 = resolve_policy(account_id="T", base=nfip, as_of=date(2027, 1, 1), attached=[_attached(synthetic, "SYN-END-07", date(2026, 1, 1), 0)])
    assert len(rp2.held) == 1 and "IV.18" not in rp2.changed_by_path()
