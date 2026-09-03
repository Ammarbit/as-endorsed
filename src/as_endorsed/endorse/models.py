from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

OpKind = Literal["REPLACE", "DELETE", "ADD", "AMEND_DEF", "SCHEDULE"]
OpStatus = Literal["resolved", "unresolved", "held"]
OpMethod = Literal["rule", "llm", "synthetic"]


class NewClause(BaseModel):
    """A fragment of restated text inside an endorsement body, with the label
    structure the endorsement gave it (e.g. `4.a.(5)`)."""

    labels: list[str]
    text: str


class EndorsementOp(BaseModel):
    op_id: str
    endorsement_form_id: str
    endorsement_edition: str
    op: OpKind
    target_ref: str | None = Field(default=None, description="The reference text as written in the endorsement")
    target_path: str | None = Field(default=None, description="Resolved path in the base form, if found")
    target_term: str | None = None
    new_label: str | None = None
    new_text: str | None = None
    new_clauses: list[NewClause] = Field(default_factory=list)
    schedule_key: str | None = Field(default=None, description="Set when the text has blanks to fill from a schedule")
    method: OpMethod
    confidence: float
    status: OpStatus
    notes: list[str] = Field(default_factory=list)
    source_page: int = 1
    directive: str = Field(default="", description="The sentence that produced this op")

    @property
    def endorsement_key(self) -> str:
        return f"{self.endorsement_form_id}@{self.endorsement_edition}"


class ExtractionResult(BaseModel):
    endorsement_form_id: str
    endorsement_edition: str
    ops: list[EndorsementOp]
    scanned: bool = False
    notes: list[str] = Field(default_factory=list)

    @property
    def resolution_rate(self) -> float | None:
        if not self.ops:
            return None
        return sum(1 for o in self.ops if o.status == "resolved") / len(self.ops)


class Lineage(BaseModel):
    op_id: str
    endorsement_key: str
    op: OpKind
    effective_date: date | None


class ResolvedClause(BaseModel):
    path: str
    parent_path: str | None
    base_clause_id: str | None = Field(default=None, description="None for clauses added by an endorsement")
    text_as_endorsed: str
    original_text: str | None = None
    active: bool = True
    added_by: str | None = None
    lineage: list[Lineage] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class Conflict(BaseModel):
    path: str
    ops: list[str]
    texts: list[str]
    reason: str


class ResolvedPolicy(BaseModel):
    account_id: str
    base_form_key: str
    as_of: date
    changed: list[ResolvedClause] = Field(description="Modified, added, or deleted clauses only")
    conflicts: list[Conflict] = Field(default_factory=list)
    unresolved: list[EndorsementOp] = Field(default_factory=list)
    held: list[EndorsementOp] = Field(default_factory=list)
    applied_op_ids: list[str] = Field(default_factory=list)

    def changed_by_path(self) -> dict[str, ResolvedClause]:
        return {c.path: c for c in self.changed}
