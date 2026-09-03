"""Optional LLM extraction for endorsement text the rules could not place.

Used only when credentials are present (`ANTHROPIC_API_KEY` or an SDK-resolvable
profile) and the `anthropic` package is installed. Output is constrained to a
schema; every op still goes through the same deterministic reference resolver,
so the model proposes and the clause tree disposes.
"""

from __future__ import annotations

import importlib.util
import os

from pydantic import BaseModel, Field

from as_endorsed.config import settings
from as_endorsed.endorse.extract import _finalise
from as_endorsed.endorse.models import EndorsementOp, NewClause, OpKind
from as_endorsed.models import ParsedForm


class LLMOp(BaseModel):
    op: OpKind
    target_ref: str | None = Field(default=None, description="The base-form clause reference exactly as the endorsement words it")
    target_term: str | None = Field(default=None, description="Defined term, for AMEND_DEF")
    new_label: str | None = Field(default=None, description="Label of an added clause, e.g. 'c' or '17'")
    new_text: str | None = Field(default=None, description="Replacement or added text, verbatim")
    confidence: float
    rationale: str


class LLMOps(BaseModel):
    ops: list[LLMOp]


SYSTEM = (
    "You extract amendment operations from insurance endorsements. An endorsement is a diff against a base "
    "policy form. Return one operation per change the endorsement makes: REPLACE (a clause is replaced), "
    "DELETE (a clause is deleted or does not apply), ADD (new text is added to a section or after a clause), "
    "AMEND_DEF (a defined term's definition is changed), SCHEDULE (text with blanks to be filled from a schedule). "
    "Quote target references exactly as written; do not invent clause numbers. Quote new text verbatim. "
    "If the endorsement only grants coverage without naming a clause, use ADD with the most specific section "
    "name the text implies and say so in the rationale."
)


def llm_available() -> bool:
    has_creds = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    return has_creds and importlib.util.find_spec("anthropic") is not None


def _outline(base: ParsedForm, max_level: int = 2) -> str:
    rows = []
    for c in base.clauses:
        if c.level <= max_level:
            head = c.heading or c.term or c.text[:60]
            rows.append(f"{c.path}: {head}")
    return "\n".join(rows)


def extract_ops_llm(endorsement: ParsedForm, base: ParsedForm, *, model: str | None = None) -> list[EndorsementOp]:
    import anthropic

    client = anthropic.Anthropic()
    body = "\n".join(c.text for c in endorsement.clauses) or endorsement.preamble
    response = client.messages.parse(
        model=model or settings.llm_model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Base form: {base.title} ({base.form_id}@{base.edition}). Outline of its clause paths:\n"
                f"{_outline(base)}\n\n"
                f"Endorsement {endorsement.form_id}@{endorsement.edition} text:\n{body}"
            ),
        }],
        output_format=LLMOps,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined the extraction request")
    parsed: LLMOps = response.parsed_output
    ops: list[EndorsementOp] = []
    for i, o in enumerate(parsed.ops, start=1):
        op = EndorsementOp(
            op_id=f"{endorsement.form_id}@{endorsement.edition}#llm{i}",
            endorsement_form_id=endorsement.form_id, endorsement_edition=endorsement.edition,
            op=o.op, target_ref=o.target_ref, target_term=o.target_term, new_label=o.new_label,
            new_text=o.new_text, new_clauses=[NewClause(labels=[o.new_label] if o.new_label else [], text=o.new_text)] if o.new_text else [],
            method="llm", confidence=min(max(o.confidence, 0.0), 0.85), status="unresolved",
            notes=[f"llm: {o.rationale}"],
        )
        _finalise(op, base)
        ops.append(op)
    return ops
