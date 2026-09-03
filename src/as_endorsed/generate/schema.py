"""What an answer looks like, regardless of which generator produced it.

Every sentence the system asserts is a Claim tied to the chunk ids that
support it. The pipeline verifies those ties before the answer is released;
the generator proposes, the checks dispose.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AnswerStatus = Literal["answered", "abstain", "withheld"]


class Claim(BaseModel):
    text: str = Field(description="One sentence the answer asserts")
    chunk_ids: list[str] = Field(description="Ids of the context chunks that support this sentence, at least one")


class Draft(BaseModel):
    """What a generator returns before the checks run."""

    can_answer: bool = Field(description="False when the context does not contain what the question asks for")
    answer: str = Field(description="The answer in plain language; empty when can_answer is false")
    claims: list[Claim] = Field(default_factory=list)
    numeric_value: float | None = Field(default=None, description="The single number the answer turns on, if any (a dollar amount, a percentage, a count)")
    missing: str = Field(default="", description="When can_answer is false: what the context would need to contain")


class Sufficiency(BaseModel):
    """Grader output for the retrieve-again loop."""

    sufficient: bool
    missing: str = ""
    rewritten_query: str = Field(default="", description="A better retrieval query naming the missing concept, or empty")


class Judgement(BaseModel):
    """LLM-judge output for the eval."""

    correct: bool
    rationale: str


class Citation(BaseModel):
    chunk_id: str
    paths: list[str]
    source: str
    lineage: list[str]
    quote: str


class Answer(BaseModel):
    question: str
    account_id: str
    status: AnswerStatus
    answer: str
    claims: list[Claim] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    numeric_value: float | None = None
    reason: str = Field(default="", description="Why the answer was withheld or the system abstained")
    route: str = "clause"
    loop_used: bool = False
    rewritten_query: str | None = None
    generator: str = ""
    checks: dict[str, bool] = Field(default_factory=dict)
    latency_ms: float = 0.0
