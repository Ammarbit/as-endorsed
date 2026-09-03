"""Question router and structured declarations answering.

Limits, deductibles, premiums, dates and identifiers live on the declarations
page as typed facts. Answering them from a vector search is both slower and
less reliable than reading the record, so the router sends those questions to
a lookup and everything else to retrieval. Mixed questions run both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from as_endorsed.synth.accounts import Account

DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
POLICY_RE = re.compile(r"\b(NFP-\d{4}-\d{7})\b")

FIELD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("endorsement_change", re.compile(r"\bwhich endorsement changed\b|\bwhen did .* take effect", re.I)),
    ("policy_number", re.compile(r"\bpolicy number\b", re.I)),
    ("named_insured", re.compile(r"\bnamed insured\b|\bwho is (the )?insured\b", re.I)),
    ("term_end", re.compile(r"\bexpir|\bend of (the )?(policy )?term|\bterm end|\brenew", re.I)),
    ("term_start", re.compile(r"\binception|\beffective date of (the )?policy|\bterm start|\bwhen (does|did) .* (begin|start)", re.I)),
    ("flood_zone", re.compile(r"\bflood zone\b|\bzone\b.*\brated\b", re.I)),
    ("annual_premium", re.compile(r"\bpremium\b", re.I)),
    ("forms_schedule", re.compile(r"\b(policy )?form(s)? appl|\bwhich (policy )?form\b|\bforms? schedule", re.I)),
    ("building_deductible", re.compile(r"\bbuilding\b.*\bdeductible|\bdeductible\b.*\bbuilding\b", re.I)),
    ("contents_deductible", re.compile(r"\b(contents|personal property)\b.*\bdeductible|\bdeductible\b.*\b(contents|personal property)\b", re.I)),
    ("building_limit", re.compile(r"\bbuilding\b.*\b(limit|coverage amount|insured for)|\b(limit|coverage)\b.*\bbuilding\b", re.I)),
    ("contents_limit", re.compile(r"\b(contents|personal property)\b.*\b(limit|coverage amount)|\b(limit|coverage)\b.*\b(contents|personal property)\b", re.I)),
]
# Words that mean the question is about policy language even if a declarations word appears.
CLAUSE_HINTS = re.compile(r"\b(define|definition|exclude|exclusion|cover(ed|s)?\b(?! ?age)|sublimit|most .* will pay|pay up to|sandbag|basement|elevated building|hot tub|solar|detached structure|mudflow|amend)", re.I)


_DATE_SCOPE_RE = re.compile(r"\b(?:as of|on|effective|before|after)\s+\d{4}-\d{2}-\d{2}\b|\b\d{4}-\d{2}-\d{2}\b", re.I)
_POLICY_SCOPE_RE = re.compile(r"\b(?:the\s+)?policy\s+NFP-\d{4}-\d{7}\b", re.I)


def retrieval_query(question: str) -> str:
    """The question with account and date scope removed: those are filters, not search terms."""
    q = _POLICY_SCOPE_RE.sub("the policy", question)
    q = POLICY_RE.sub(" ", q)
    q = _DATE_SCOPE_RE.sub(" ", q)
    q = re.sub(r"\s+", " ", q).strip(" ,?") + "?"
    return q


@dataclass
class Route:
    kind: str  # declarations | clause | mixed
    field: str | None
    as_of: date | None
    policy_number: str | None


def route(question: str) -> Route:
    as_of = None
    m = DATE_RE.search(question)
    if m:
        as_of = date.fromisoformat(m.group(1))
    pn = POLICY_RE.search(question)
    field = next((f for f, rx in FIELD_PATTERNS if rx.search(question)), None)
    clause_like = bool(CLAUSE_HINTS.search(question))
    if field and not clause_like:
        return Route("declarations", field, as_of, pn.group(1) if pn else None)
    if field and clause_like:
        return Route("mixed", field, as_of, pn.group(1) if pn else None)
    return Route("clause", None, as_of, pn.group(1) if pn else None)


@dataclass
class DeclarationsAnswer:
    field: str
    value: object
    citation: str


def answer_declarations(acct: Account, field: str, as_of: date | None = None) -> DeclarationsAnswer | None:
    p = acct.policy
    when = as_of or p.term_end
    cite = f"declarations page, policy {p.policy_number}"
    if field == "policy_number":
        return DeclarationsAnswer(field, p.policy_number, cite)
    if field == "named_insured":
        return DeclarationsAnswer(field, p.named_insured, cite)
    if field == "term_end":
        return DeclarationsAnswer(field, p.term_end.isoformat(), cite)
    if field == "term_start":
        return DeclarationsAnswer(field, p.term_start.isoformat(), cite)
    if field == "flood_zone":
        return DeclarationsAnswer(field, p.property_location.flood_zone, cite)
    if field == "annual_premium":
        return DeclarationsAnswer(field, p.annual_premium, cite)
    if field == "forms_schedule":
        return DeclarationsAnswer(field, p.forms_schedule[0].title, cite)
    if field in ("building_limit", "building_deductible", "contents_limit", "contents_deductible"):
        kind = field.split("_")[0]
        if not any(c.coverage == kind for c in p.coverages):
            return DeclarationsAnswer(field, "No contents coverage on this policy", cite)
        return DeclarationsAnswer(field, p.value_as_of(field, when), cite + (f", as of {when.isoformat()}" if as_of else ""))
    if field == "endorsement_change":
        if not p.endorsements:
            return DeclarationsAnswer(field, "No mid-term change endorsements on this policy", cite)
        ch = p.endorsements[-1]
        return DeclarationsAnswer(field, f"{ch.endorsement_number}, effective {ch.effective_date.isoformat()}", cite)
    return None
