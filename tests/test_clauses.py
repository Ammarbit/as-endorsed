"""Parser tests against the real FEMA Dwelling Form (F-122, Oct 2021).

Skipped when the corpus has not been downloaded. Run `as-endorsed corpus download` first.
"""

from __future__ import annotations

import pytest

from as_endorsed.config import settings
from as_endorsed.corpus import registry
from as_endorsed.ingest.clauses import parse_form

SPEC = registry.get("NFIP-DWELLING@2021-10")
PDF = settings.raw_dir / SPEC.filename

pytestmark = pytest.mark.skipif(not PDF.exists(), reason="corpus not downloaded")


@pytest.fixture(scope="module")
def form():
    return parse_form(PDF, form_id=SPEC.form_id, edition=SPEC.edition, title=SPEC.title)


@pytest.fixture(scope="module")
def by_path(form):
    return form.by_path()


def test_ten_sections_in_order(form):
    sections = [c for c in form.clauses if c.level == 0]
    assert [c.label for c in sections] == ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
    assert sections[0].heading == "AGREEMENT"
    assert sections[4].heading == "EXCLUSIONS"


def test_no_warnings(form):
    assert form.warnings == []


def test_definitions_are_complete_and_ordered(by_path):
    nums = [int(p.split(".")[-1]) for p in by_path if p.startswith("II.C.") and p.count(".") == 2]
    assert nums == list(range(1, 31))


def test_definition_terms(by_path):
    assert by_path["II.C.5"].term == "Basement"
    assert "below ground level on all sides" in by_path["II.C.5"].text
    assert by_path["II.C.21"].term == "National Flood Insurance Program (NFIP)"
    assert by_path["II.C.28"].term == "Special Flood Hazard Area (SFHA)"


def test_trailing_text_lands_on_parent_not_last_child(by_path):
    building = by_path["II.C.6"]
    assert building.heading == "Building"
    assert "does not mean a gas or liquid storage tank" in building.text
    assert "does not mean" not in by_path["II.C.6.c"].text
    assert by_path["II.C.6.c"].parent_path == "II.C.6"


def test_right_column_clauses_follow_left_column(by_path):
    # I.E..I.G sit in the right column of page 1 of the form.
    assert "insures only one building" in by_path["I.E"].text
    assert "Subject to the exception in I.G below" in by_path["I.F"].text
    assert "no more than $250,000" in by_path["I.G"].text


def test_section_headings_split_columns(by_path):
    # Definitions 26-30 are in the right column above the centred "III." heading.
    assert by_path["II.C.26"].term == "Probation Surcharge"
    assert by_path["III.A"].heading == "Coverage A—Building Property"
    assert by_path["III.A.1"].text.startswith("The dwelling at the described location")


def test_section_without_letter_tier(by_path):
    assert by_path["IV.1"].text.startswith("Personal property not inside a building")
    assert by_path["IV.12"].text.startswith("Fences, retaining walls, seawalls")  # soft hyphen healed
    assert "IV.16" in by_path


def test_lower_roman_level(by_path):
    assert by_path["III.C.2.a.(1).(a).(i)"].text.startswith("Sandbags")
    assert by_path["III.C.2.a.(1).(a).(iv)"].parent_path == "III.C.2.a.(1).(a)"
    assert by_path["III.C.2.a.(1).(b)"].text.startswith("The value of work")


def test_headers_and_footers_stripped(form):
    for c in form.clauses:
        assert "NFIP DWELLING FORM SFIP" not in c.text
        assert "PAGE " not in c.text or "Declarations Page" in c.text


def test_every_clause_has_location(form):
    for c in form.clauses:
        assert c.page_start >= 3
        assert c.bboxes and all(b.x1 > b.x0 and b.y1 > b.y0 for b in c.bboxes)
        assert c.clause_id == f"NFIP-DWELLING@2021-10:{c.path}"
