from __future__ import annotations

from datetime import timedelta

import pymupdf

from as_endorsed.synth.accounts import BUILDING_LIMIT_MAX, CONTENTS_LIMIT_MAX, DEDUCTIBLES, generate_accounts
from as_endorsed.synth.qa import questions_for
from as_endorsed.synth.render import render_declarations


def test_generation_is_deterministic():
    a = generate_accounts(10, seed=1)
    b = generate_accounts(10, seed=1)
    assert [x.model_dump() for x in a] == [x.model_dump() for x in b]
    assert generate_accounts(1, seed=2)[0].policy.policy_number != a[0].policy.policy_number


def test_limits_respect_nfip_maximums():
    for acct in generate_accounts(60, seed=7):
        for c in acct.policy.coverages:
            cap = BUILDING_LIMIT_MAX if c.coverage == "building" else CONTENTS_LIMIT_MAX
            assert 0 < c.limit <= cap
            assert c.deductible in DEDUCTIBLES
        for ch in acct.policy.endorsements:
            assert acct.policy.term_start < ch.effective_date < acct.policy.term_end
            assert ch.old_value != ch.new_value


def test_value_as_of_applies_change_on_effective_date():
    acct = next(a for a in generate_accounts(60, seed=7) if a.policy.endorsements)
    ch = acct.policy.endorsements[0]
    p = acct.policy
    assert p.value_as_of(ch.field, ch.effective_date - timedelta(days=1)) == ch.old_value
    assert p.value_as_of(ch.field, ch.effective_date) == ch.new_value
    assert p.value_as_of(ch.field, p.term_end) == ch.new_value


def test_questions_have_exact_answers():
    acct = next(a for a in generate_accounts(60, seed=7) if a.policy.endorsements)
    rows = questions_for(acct)
    assert all(r["account_id"] == acct.account_id for r in rows)
    assert any(r["difficulty"] == "as-of" for r in rows)
    ch = acct.policy.endorsements[0]
    as_of = [r for r in rows if r["as_of"] == ch.effective_date and r["source_field"] == ch.field]
    assert as_of and as_of[0]["answer"] == ch.new_value


def test_rendered_pdf_contains_declarations_and_endorsement(tmp_path):
    acct = next(a for a in generate_accounts(60, seed=7) if a.policy.endorsements)
    out = render_declarations(acct, tmp_path / "dec.pdf")
    doc = pymupdf.open(out)
    assert len(doc) == 1 + len(acct.policy.endorsements)
    text = "\n".join(p.get_text() for p in doc)
    assert acct.policy.policy_number in text
    assert acct.policy.named_insured in text
    assert f"${acct.policy.coverage('building').limit:,.0f}" in text
    assert "THIS ENDORSEMENT CHANGES THE POLICY" in text
    assert acct.policy.endorsements[0].endorsement_number in text
