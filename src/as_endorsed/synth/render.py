"""Render a synthetic account to a declarations PDF, plus one page per mid-term
change, so the same ingestion path that reads real forms also reads these."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from as_endorsed.synth.accounts import Account, MidTermChange
from as_endorsed.synth.qa import FIELD_LABEL

_styles = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=_styles["Title"], fontSize=15, leading=18, spaceAfter=2, alignment=0)
H2 = ParagraphStyle("h2", parent=_styles["Heading2"], fontSize=11, leading=14, spaceBefore=10, spaceAfter=4)
BODY = ParagraphStyle("body", parent=_styles["BodyText"], fontSize=9, leading=12)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7.5, leading=10, textColor=colors.HexColor("#555555"))
NOTICE = ParagraphStyle("notice", parent=BODY, fontSize=8, leading=10, textColor=colors.HexColor("#8A1C1C"))

GRID = TableStyle([
    ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
    ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#444444")),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#BBBBBB")),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ("TOPPADDING", (0, 0), (-1, -1), 4),
])
HEADED = TableStyle([
    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
    ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
    ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
    ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#BBBBBB")),
    ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ("TOPPADDING", (0, 0), (-1, -1), 4),
])


COVERAGE_LABEL = {"building": "Building Property (Coverage A)", "contents": "Personal Property (Coverage B)"}


def money(v: int) -> str:
    return f"${v:,.0f}"


def _kv(rows: list[tuple[str, str]]) -> Table:
    t = Table([[Paragraph(escape(k), BODY), Paragraph(escape(v), BODY)] for k, v in rows], colWidths=[1.9 * inch, 4.9 * inch])
    t.setStyle(GRID)
    return t


def render_declarations(acct: Account, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    p = acct.policy
    loc = p.property_location
    doc = SimpleDocTemplate(
        str(out_path), pagesize=letter,
        leftMargin=0.8 * inch, rightMargin=0.8 * inch, topMargin=0.8 * inch, bottomMargin=0.8 * inch,
        title=f"Flood Insurance Declarations {p.policy_number}", author="As-Endorsed synthetic data",
    )
    story = [
        Paragraph("National Flood Insurance Program", H1),
        Paragraph("Flood Insurance Policy Declarations", H2),
        Paragraph("SYNTHETIC DOCUMENT. Generated for software testing. Not a policy, not an offer of coverage, no real persons.", NOTICE),
        Spacer(1, 8),
        _kv([
            ("Policy Number", p.policy_number),
            ("Policy Term", f"{p.term_start:%B %d, %Y} to {p.term_end:%B %d, %Y}, 12:01 a.m. local time"),
            ("Named Insured", p.named_insured + (f" and {p.co_insured}" if p.co_insured else "")),
            ("Mailing Address", p.mailing_address),
            ("Producer", p.agency),
        ]),
        Paragraph("Insured Property", H2),
        _kv([
            ("Property Location", loc.one_line()),
            ("Community Number", loc.community_number),
            ("Flood Zone", loc.flood_zone),
            ("Occupancy", loc.occupancy),
            ("Building Description", loc.building_description),
            ("Primary Residence", "Yes" if loc.primary_residence else "No"),
            ("Rating Method", p.rating_method),
        ]),
        Paragraph("Coverage and Deductibles", H2),
    ]
    cov_rows = [["Coverage", "Limit of Liability", "Deductible"]]
    for c in p.coverages:
        cov_rows.append([COVERAGE_LABEL[c.coverage], money(c.limit), money(c.deductible)])
    if not any(c.coverage == "contents" for c in p.coverages):
        cov_rows.append(["Personal Property (Coverage B)", "Not purchased", "-"])
    cov = Table(cov_rows, colWidths=[3.3 * inch, 1.8 * inch, 1.7 * inch])
    cov.setStyle(HEADED)
    story += [cov, Spacer(1, 6), _kv([("Total Annual Premium", money(p.annual_premium) + " (includes federal policy fee and reserve fund assessment)")])]

    story.append(Paragraph("Forms and Endorsements", H2))
    fe_rows = [["Form", "Edition", "Title"]]
    for f in p.forms_schedule:
        fe_rows.append([f.form_id, f.edition, f.title])
    for e in p.endorsement_forms:
        extra = ""
        if e.schedule_values:
            extra = " Schedule: " + "; ".join(f"{k}: {v}" for k, v in e.schedule_values.items())
        fe_rows.append([e.form_id, e.edition, Paragraph(escape(f"{e.title}, effective {e.effective_date:%B %d, %Y}.{extra}"), BODY)])
    for ch in p.endorsements:
        fe_rows.append([ch.endorsement_number, ch.effective_date.isoformat(), f"General Change Endorsement, effective {ch.effective_date:%B %d, %Y}"])
    fe = Table(fe_rows, colWidths=[1.6 * inch, 0.9 * inch, 4.3 * inch])
    fe.setStyle(TableStyle(HEADED.getCommands()[:4] + [("ALIGN", (0, 0), (-1, -1), "LEFT"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 4)]))
    story += [fe, Spacer(1, 10), Paragraph(
        "This declarations page, the Standard Flood Insurance Policy form listed above, the application, "
        "and any endorsements listed above together make up the policy. Where an endorsement and the form "
        "conflict, the endorsement controls. Where two endorsements conflict, the later effective date controls.",
        SMALL)]

    for ch in p.endorsements:
        story += [PageBreak()] + _endorsement_page(acct, ch)

    doc.build(story)
    return out_path


def _endorsement_page(acct: Account, ch: MidTermChange) -> list:
    p = acct.policy
    label = FIELD_LABEL[ch.field]
    kind = ch.field.split("_")[0]
    return [
        Paragraph("National Flood Insurance Program", H1),
        Paragraph("General Change Endorsement", H2),
        Paragraph("THIS ENDORSEMENT CHANGES THE POLICY. PLEASE READ IT CAREFULLY.", NOTICE),
        Paragraph("SYNTHETIC DOCUMENT. Generated for software testing.", NOTICE),
        Spacer(1, 8),
        _kv([
            ("Endorsement Number", ch.endorsement_number),
            ("Policy Number", p.policy_number),
            ("Named Insured", p.named_insured),
            ("Property Location", p.property_location.one_line()),
            ("Effective Date of Change", f"{ch.effective_date:%B %d, %Y}, 12:01 a.m. local time"),
            ("Reason for Change", ch.reason),
        ]),
        Paragraph("Change", H2),
        Paragraph(
            escape(f"The {label} shown on the Declarations Page for {kind} property is changed from "
                   f"{money(ch.old_value)} to {money(ch.new_value)}, effective {ch.effective_date:%B %d, %Y}. "
                   f"All other terms, conditions, limits and deductibles remain unchanged."),
            BODY),
        Spacer(1, 6),
        Paragraph("Declarations as amended", H2),
        _amended_table(acct, ch),
    ]


def _amended_table(acct: Account, ch: MidTermChange) -> Table:
    p = acct.policy
    rows = [["Coverage", "Limit of Liability", "Deductible"]]
    for c in p.coverages:
        rows.append([
            COVERAGE_LABEL[c.coverage],
            money(p.value_as_of(f"{c.coverage}_limit", ch.effective_date)),
            money(p.value_as_of(f"{c.coverage}_deductible", ch.effective_date)),
        ])
    t = Table(rows, colWidths=[3.3 * inch, 1.8 * inch, 1.7 * inch])
    t.setStyle(HEADED)
    return t
