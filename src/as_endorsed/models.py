"""Shared data model.

Everything downstream keys off these records. Retrieval never sees a fixed-size
window of text; it sees a Clause, addressed by form, edition and path.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BBox(BaseModel):
    page: int = Field(description="1-based page number in the source PDF")
    x0: float
    y0: float
    x1: float
    y1: float


class FormSpec(BaseModel):
    """A form in the corpus registry. `form_id` + `edition` is the identity."""

    form_id: str
    edition: str = Field(description="Edition stamp, ISO-ish: 2021-10")
    kind: Literal["base", "endorsement", "bundle"]
    title: str
    source: str
    url: str
    filename: str
    license: str
    parse_supported: bool = True
    base_form_id: str | None = Field(default=None, description="For endorsements: the form they amend")
    bundled: str | None = Field(default=None, description="Repo-relative path of a copy shipped with the source (public-domain forms only)")
    note: str | None = None

    @property
    def key(self) -> str:
        return f"{self.form_id}@{self.edition}"


class Clause(BaseModel):
    clause_id: str = Field(description="`<form_id>@<edition>:<path>`")
    form_id: str
    edition: str
    path: str = Field(description="Dotted label path from the section down, e.g. II.C.6.b")
    parent_path: str | None
    level: int = Field(description="0 = section (Roman numeral), then 1..5 by label type")
    label: str
    heading: str | None = Field(default=None, description="Short title when the clause's own line is a title, e.g. 'Building'")
    term: str | None = Field(default=None, description="Defined term, only for definition clauses")
    text: str = Field(description="The clause's own text, excluding child clauses")
    page_start: int
    bboxes: list[BBox]


class ParsedForm(BaseModel):
    form_id: str
    edition: str
    title: str
    source_pdf: str
    pages: int
    preamble: str
    clauses: list[Clause]
    warnings: list[str] = Field(default_factory=list)

    def by_path(self) -> dict[str, Clause]:
        return {c.path: c for c in self.clauses}
