"""Synthetic endorsements against the real NFIP Dwelling Form.

Each spec carries the endorsement body in the industry's own idiom, the
operations it is supposed to produce (ground truth for the extractor), and
question templates whose answers differ depending on whether the endorsement
is attached (ground truth for the endorsement-resolved eval category).

The base form is real; only the endorsements are invented. Their form numbers
are prefixed SYN-END so nobody mistakes them for FEMA material.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

BASE_FORM_KEY = "NFIP-DWELLING@2021-10"
EDITION = "2026-01"


@dataclass
class QATemplate:
    question: str  # may use {pn} (policy number) and {addr}
    attached: str
    not_attached: str
    answer_type: str = "text"
    paths: list[str] = field(default_factory=list)


@dataclass
class SynthEndorsement:
    form_id: str
    title: str
    body: list[tuple[int, str]]  # (indent level, text); indent 0 = directive/plain, 1+ = restated clause
    expected: list[dict]  # ground-truth ops: op, target_path, new_label, text_contains, schedule
    qa: list[QATemplate]
    schedule_prompt: str | None = None  # key for schedule_values when the text has blanks

    @property
    def key(self) -> str:
        return f"{self.form_id}@{EDITION}"


BASEMENT_01 = ("Basement. Any area of a building, including any sunken room or sunken portion of a room, having its "
               "floor below ground level on all sides, and any area having a finished floor more than two feet below "
               "the adjoining exterior grade on at least three sides.")
BASEMENT_06 = ("Basement. Any area of a building having its floor below ground level on all sides. A crawlspace, or "
               "an area with its floor at or above the lowest adjacent grade on any side, is not a basement.")
BASEMENT_BASE = ("Basement. Any area of a building, including any sunken room or sunken portion of a room, having its "
                 "floor below ground level on all sides.")

LIBRARY: list[SynthEndorsement] = [
    SynthEndorsement(
        form_id="SYN-END-01", title="Basement Definition Amendment",
        body=[(0, "Paragraph II.C.5 (Basement) of Section II. DEFINITIONS is replaced by the following:"),
              (1, "5. " + BASEMENT_01),
              (0, "All other terms and conditions of this policy remain unchanged.")],
        expected=[{"op": "REPLACE", "target_path": "II.C.5", "text_contains": "three sides"}],
        qa=[QATemplate("How does policy {pn} define 'basement'?", BASEMENT_01, BASEMENT_BASE, paths=["II.C.5"]),
            QATemplate("Which endorsement, if any, amends the definition of 'basement' on policy {pn}?",
                       "SYN-END-01, Basement Definition Amendment", "None; the base form definition at II.C.5 applies", paths=["II.C.5"])],
    ),
    SynthEndorsement(
        form_id="SYN-END-02", title="Hot Tub, Spa and Swimming Pool Coverage",
        body=[(0, "In consideration of an additional premium, Paragraph IV.14 of Section IV. PROPERTY NOT INSURED is deleted."),
              (0, "All other terms and conditions of this policy remain unchanged.")],
        expected=[{"op": "DELETE", "target_path": "IV.14"}],
        qa=[QATemplate("Does policy {pn} exclude hot tubs, spas and swimming pools and their equipment?",
                       "No. The exclusion at IV.14 was deleted by endorsement SYN-END-02.",
                       "Yes. IV.14 excludes hot tubs and spas that are not bathroom fixtures, swimming pools, and their equipment.",
                       paths=["IV.14"])],
    ),
    SynthEndorsement(
        form_id="SYN-END-03", title="Solar Equipment Exclusion",
        body=[(0, "The following is added to Section IV. PROPERTY NOT INSURED:"),
              (1, "17. Solar panels, solar water heaters, and related mounting hardware that are not permanently affixed "
                  "to the building, and any batteries or inverters located outside the building."),
              (0, "All other terms and conditions of this policy remain unchanged.")],
        expected=[{"op": "ADD", "target_path": "IV", "new_label": "17", "text_contains": "Solar panels"}],
        qa=[QATemplate("Are solar panels that are not permanently affixed to the building covered under policy {pn}?",
                       "No. They are excluded by IV.17, added by endorsement SYN-END-03.",
                       "The policy has no solar-specific exclusion; coverage follows the general terms for building and personal property.",
                       paths=["IV.17"])],
    ),
    SynthEndorsement(
        form_id="SYN-END-04", title="Elevated Building Definition Extension",
        body=[(0, "The definition of “Elevated Building” in Paragraph II.C.16 is amended to include the following:"),
              (1, "A building whose lowest floor is raised above ground level on a permanent pier foundation certified by "
                  "a licensed engineer is an elevated building even if the enclosed area below the lowest floor exceeds "
                  "300 square feet, provided that area is used solely for parking, building access, or storage."),
              (0, "All other terms and conditions of this policy remain unchanged.")],
        expected=[{"op": "AMEND_DEF", "target_path": "II.C.16", "text_contains": "300 square feet"}],
        qa=[QATemplate("Under policy {pn}, can a building with an enclosed area of more than 300 square feet below its lowest floor still be an elevated building?",
                       "Yes, if it stands on an engineer-certified permanent pier foundation and the enclosed area is used only for parking, building access or storage (II.C.16 as amended by SYN-END-04).",
                       "The definition at II.C.16 does not address that; it requires the lowest elevated floor to be raised above ground level by foundation walls, shear walls, posts, piers, pilings, or columns.",
                       paths=["II.C.16"])],
    ),
    SynthEndorsement(
        form_id="SYN-END-05", title="Increased Loss Avoidance Limit",
        body=[(0, "Paragraph III.C.2.a.(1) of Section III. PROPERTY INSURED is replaced by the following:"),
              (1, "(1) We will pay up to $2,500 for costs you incur to protect the insured building from a flood or "
                  "imminent danger of flood, for the following:"),
              (0, "All other terms and conditions of this policy remain unchanged.")],
        expected=[{"op": "REPLACE", "target_path": "III.C.2.a.(1)", "text_contains": "$2,500"}],
        qa=[QATemplate("What is the most policy {pn} will pay for sandbags, supplies and labor to protect the building from flood?",
                       "2500", "1000", answer_type="money", paths=["III.C.2.a.(1)"])],
    ),
    SynthEndorsement(
        form_id="SYN-END-06", title="Basement Definition Amendment (Coastal Revision)",
        body=[(0, "Paragraph II.C.5 (Basement) of Section II. DEFINITIONS is deleted and replaced by the following:"),
              (1, "5. " + BASEMENT_06),
              (0, "All other terms and conditions of this policy remain unchanged.")],
        expected=[{"op": "REPLACE", "target_path": "II.C.5", "text_contains": "crawlspace"}],
        qa=[QATemplate("How does policy {pn} define 'basement'?", BASEMENT_06, BASEMENT_BASE, paths=["II.C.5"]),
            QATemplate("Which endorsement, if any, amends the definition of 'basement' on policy {pn}?",
                       "SYN-END-06, Basement Definition Amendment (Coastal Revision)", "None; the base form definition at II.C.5 applies", paths=["II.C.5"])],
    ),
    SynthEndorsement(
        form_id="SYN-END-07", title="Scheduled Detached Structure Exclusion",
        body=[(0, "The following is added to Section IV. PROPERTY NOT INSURED:"),
              (1, "18. The following detached structure(s) at the described location, as shown in the Schedule: ________________________"),
              (0, "All other terms and conditions of this policy remain unchanged.")],
        expected=[{"op": "ADD", "target_path": "IV", "new_label": "18", "schedule": True}],
        qa=[QATemplate("Which detached structures are specifically excluded from coverage on policy {pn}?",
                       "{schedule}", "None; the policy schedules no detached-structure exclusion.", paths=["IV.18"])],
        schedule_prompt="structures",
    ),
    SynthEndorsement(
        form_id="SYN-END-08", title="Mudflow Sublimit",
        body=[(0, "This policy is amended as follows:"),
              (1, "The most we will pay for loss caused by mudflow in any one occurrence is $5,000. This sublimit does "
                  "not increase the limit of liability shown on the Declarations Page."),
              (0, "All other terms and conditions of this policy remain unchanged.")],
        expected=[{"op": "ADD", "target_path": None, "text_contains": "$5,000"}],
        qa=[QATemplate("Is there a sublimit for mudflow losses on policy {pn}?",
                       "Yes. Endorsement SYN-END-08 caps mudflow loss at $5,000 per occurrence; the endorsement does not name the clause it modifies.",
                       "No. Mudflow is covered as flood, subject to the policy limits.", paths=[])],
    ),
]
BY_ID = {e.form_id: e for e in LIBRARY}
SCHEDULE_VALUES = ["Detached workshop at the rear of the lot", "Boat house on the canal frontage", "Pole barn north of the driveway", "Guest cottage"]

_styles = getSampleStyleSheet()
_H1 = ParagraphStyle("h1", parent=_styles["Title"], fontSize=13, leading=16, alignment=0, spaceAfter=2)
_BANNER = ParagraphStyle("banner", parent=_styles["BodyText"], fontSize=8.5, leading=11, textColor=colors.HexColor("#8A1C1C"))
_META = ParagraphStyle("meta", parent=_styles["BodyText"], fontSize=8.5, leading=11, textColor=colors.HexColor("#444444"))
_BODY = ParagraphStyle("body", parent=_styles["BodyText"], fontSize=10, leading=13, spaceAfter=14)


def render_endorsement(spec: SynthEndorsement, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    doc = SimpleDocTemplate(str(out_path), pagesize=letter, leftMargin=inch, rightMargin=inch, topMargin=inch, bottomMargin=inch,
                            title=f"{spec.form_id} {spec.title}", author="As-Endorsed synthetic data")
    story = [
        Paragraph(escape(f"{spec.form_id} ({EDITION})"), _META),
        Paragraph(escape(spec.title), _H1),
        Paragraph("THIS ENDORSEMENT CHANGES THE POLICY. PLEASE READ IT CAREFULLY.", _BANNER),
        Paragraph("SYNTHETIC ENDORSEMENT. Written for software testing against the NFIP Dwelling Form (F-122, Oct 2021). Not a FEMA document.", _BANNER),
        Spacer(1, 10),
    ]
    for indent, text in spec.body:
        style = ParagraphStyle(f"b{indent}", parent=_BODY, leftIndent=18 * (indent + (1 if indent else 0)), firstLineIndent=-18 if indent else 0)
        story.append(Paragraph(escape(text), style))
    doc.build(story)
    return out_path


def render_library(out_dir: str | Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return {spec.form_id: render_endorsement(spec, out_dir / f"{spec.form_id}.pdf") for spec in LIBRARY}


def compare_ops(spec: SynthEndorsement, ops) -> list[dict]:
    """Match each expected op against the extracted ops. Returns one row per expectation."""
    rows = []
    for exp in spec.expected:
        hit = None
        for op in ops:
            if op.op != exp["op"]:
                continue
            if exp.get("target_path") != op.target_path:
                continue
            if exp.get("new_label") and op.new_label != exp["new_label"]:
                continue
            if exp.get("text_contains") and exp["text_contains"] not in (op.new_text or ""):
                continue
            if exp.get("schedule") and not op.schedule_key:
                continue
            hit = op
            break
        rows.append({"endorsement": spec.form_id, "expected": exp, "matched": hit is not None,
                     "op_id": hit.op_id if hit else None, "status": hit.status if hit else None,
                     "extracted_ops": len(ops)})
    return rows
