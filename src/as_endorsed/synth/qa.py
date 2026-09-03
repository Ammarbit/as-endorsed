"""Templated ground-truth questions for the declarations-lookup eval category.

Each row carries the exact answer, where it came from, and an `as_of` date when
the answer depends on a mid-term change. These are auto-labeled, so they are
cheap and exact; the hand-written categories live elsewhere.
"""

from __future__ import annotations

from datetime import timedelta

from as_endorsed.synth.accounts import Account
from as_endorsed.synth.endorsements import LIBRARY

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


def endorsement_questions(acct: Account) -> list[dict]:
    """Endorsement-resolved category: the answer depends on which synthetic
    endorsements are attached and, for mid-term attachments, on the as-of date."""
    p = acct.policy
    attached = {e.form_id: e for e in p.endorsement_forms}
    # The two basement amendments target the same clause; the later effective one controls.
    basement = [attached[f] for f in ("SYN-END-01", "SYN-END-06") if f in attached]
    basement_winner = max(basement, key=lambda e: (e.effective_date, e.form_id)).form_id if basement else None
    rows: list[dict] = []
    negatives_budget = 2
    for i, spec in enumerate(LIBRARY):
        e = attached.get(spec.form_id)
        if spec.form_id in ("SYN-END-01", "SYN-END-06") and e is not None and spec.form_id != basement_winner:
            continue
        for t in spec.qa:
            q = t.question.format(pn=p.policy_number, addr=p.property_location.one_line())
            base_row = {
                "account_id": acct.account_id, "policy_number": p.policy_number,
                "category": "endorsement-resolved", "answer_type": t.answer_type,
                "expected_paths": t.paths, "source_field": spec.form_id,
            }
            if e is not None:
                answer = t.attached.format(schedule=next(iter(e.schedule_values.values()), ""))
                rows.append({**base_row, "difficulty": "resolved", "question": q, "answer": answer,
                             "expected_endorsements": [e.key], "as_of": None})
                if e.effective_date > p.term_start:
                    before = e.effective_date - timedelta(days=1)
                    # Another attached endorsement on the same clause, in force on that date, supplies the answer.
                    in_force = [
                        (o, attached[o.form_id]) for o in LIBRARY
                        if o.form_id != spec.form_id and o.form_id in attached
                        and attached[o.form_id].effective_date <= before
                        and any(set(ot.paths) & set(t.paths) for ot in o.qa)
                    ]
                    if in_force:
                        o, oe = max(in_force, key=lambda x: x[1].effective_date)
                        ot = next((x for x in o.qa if set(x.paths) & set(t.paths) and x.question == t.question), None) or o.qa[0]
                        answer, ends = ot.attached.format(schedule=next(iter(oe.schedule_values.values()), "")), [oe.key]
                    else:
                        answer, ends = t.not_attached, []
                    rows.append({**base_row, "difficulty": "as-of", "as_of": before,
                                 "question": q[:-1] + f" as of {before.isoformat()}?",
                                 "answer": answer, "expected_endorsements": ends})
            elif negatives_budget > 0 and (i + acct.seed) % 3 == 0:
                negatives_budget -= 1
                rows.append({**base_row, "difficulty": "negative", "question": q, "answer": t.not_attached,
                             "expected_endorsements": [], "as_of": None})
    return rows
