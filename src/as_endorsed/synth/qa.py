"""Templated ground-truth questions for the declarations-lookup eval category.

Each row carries the exact answer, where it came from, and an `as_of` date when
the answer depends on a mid-term change. These are auto-labeled, so they are
cheap and exact; the hand-written categories live elsewhere.
"""

from __future__ import annotations

from datetime import timedelta

from as_endorsed.synth.accounts import Account

FIELD_LABEL = {
    "building_limit": "building coverage limit",
    "contents_limit": "contents coverage limit",
    "building_deductible": "building deductible",
    "contents_deductible": "contents deductible",
}


def _row(acct: Account, question: str, answer, *, field: str, answer_type: str, as_of=None, difficulty: str = "lookup"):
    return {
        "account_id": acct.account_id,
        "policy_number": acct.policy.policy_number,
        "category": "declarations",
        "difficulty": difficulty,
        "question": question,
        "answer": answer,
        "answer_type": answer_type,
        "source_field": field,
        "as_of": as_of,
    }


def questions_for(acct: Account) -> list[dict]:
    p = acct.policy
    addr = p.property_location.one_line()
    short = p.property_location.street
    rows: list[dict] = []

    rows.append(_row(acct, f"What is the policy number for the flood policy at {addr}?", p.policy_number, field="policy_number", answer_type="text"))
    rows.append(_row(acct, f"Who is the named insured on policy {p.policy_number}?", p.named_insured, field="named_insured", answer_type="text"))
    rows.append(_row(acct, f"When does policy {p.policy_number} expire?", p.term_end.isoformat(), field="term_end", answer_type="date"))
    rows.append(_row(acct, f"What flood zone is {short} rated in?", p.property_location.flood_zone, field="flood_zone", answer_type="text"))
    rows.append(_row(acct, f"What is the annual premium on policy {p.policy_number}?", p.annual_premium, field="annual_premium", answer_type="money"))
    rows.append(_row(acct, f"Which policy form applies to {short}?", p.forms_schedule[0].title, field="forms_schedule", answer_type="text"))

    for cov in p.coverages:
        rows.append(_row(acct, f"What is the {cov.coverage} coverage limit for {short}?", p.value_as_of(f"{cov.coverage}_limit", p.term_end), field=f"{cov.coverage}_limit", answer_type="money"))
        rows.append(_row(acct, f"What is the {cov.coverage} deductible on policy {p.policy_number}?", p.value_as_of(f"{cov.coverage}_deductible", p.term_end), field=f"{cov.coverage}_deductible", answer_type="money"))
    if not any(c.coverage == "contents" for c in p.coverages):
        rows.append(_row(acct, f"What is the contents coverage limit for {short}?", "No contents coverage on this policy", field="contents_limit", answer_type="abstain", difficulty="unanswerable"))

    for ch in p.endorsements:
        label = FIELD_LABEL[ch.field]
        before = ch.effective_date - timedelta(days=1)
        rows.append(_row(acct, f"What was the {label} for {short} on {before.isoformat()}?", p.value_as_of(ch.field, before), field=ch.field, answer_type="money", as_of=before, difficulty="as-of"))
        rows.append(_row(acct, f"What is the {label} for {short} as of {ch.effective_date.isoformat()}?", p.value_as_of(ch.field, ch.effective_date), field=ch.field, answer_type="money", as_of=ch.effective_date, difficulty="as-of"))
        rows.append(_row(acct, f"Which endorsement changed the {label} on policy {p.policy_number}, and when did it take effect?", f"{ch.endorsement_number}, effective {ch.effective_date.isoformat()}", field="endorsements", answer_type="text", difficulty="as-of"))
    return rows
