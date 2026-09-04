"""Claude-backed generator, grader and judge.

Structured outputs constrain every call to a schema; the pipeline's checks
still run on what comes back. The client is injectable so the code path is
tested without network access. Credentials are resolved by the SDK
(`ANTHROPIC_API_KEY`, an auth token, or a stored profile).
"""

from __future__ import annotations

import time
from typing import Any

from as_endorsed.config import settings
from as_endorsed.generate.context import render_context
from as_endorsed.generate.schema import Draft, Judgement, Sufficiency
from as_endorsed.retrieval.index import Hit

SYSTEM = (
    "You answer questions about one insurance policy using only the context blocks provided. Each block is a clause "
    "of the policy as it currently reads for this account, a declarations page block, or endorsement text. Blocks "
    "marked 'as amended by' already include the endorsement's change; blocks marked DELETED no longer apply. "
    "Report what the policy says and where. Never decide whether a claim will be paid, and never interpret "
    "ambiguity. Every sentence of your answer must be a claim tied to the ids of the blocks that support it, and "
    "each claim must be verifiable against those blocks alone. Quote dollar amounts and numbers exactly as they "
    "appear in the blocks. If the context does not contain what the question asks for, set can_answer to false and "
    "say what is missing; do not guess."
)
GRADER_SYSTEM = (
    "You judge whether a set of retrieved policy blocks is sufficient to answer a question about the policy. If it "
    "is not, name what is missing and write a short retrieval query, in the policy's own vocabulary, that would "
    "find it."
)
JUDGE_SYSTEM = (
    "You compare a system's answer to a reference answer for a question about an insurance policy. The answer is "
    "correct if it conveys the same facts as the reference: the same clause outcome, the same amounts, the same "
    "endorsement if one is named. Wording may differ. Extra correct detail does not make it wrong; a missing or "
    "contradicted fact does."
)


class GeneratorError(RuntimeError):
    pass


class ClaudeGenerator:
    supports_rewrite = True

    def __init__(self, client: Any | None = None, model: str | None = None, effort: str = "medium") -> None:
        self.model = model or settings.llm_model
        self.name = f"claude:{self.model}"
        self.effort = effort
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client

    def _parse(self, system: str, user: str, schema, *, effort: str, max_tokens: int = 8000):
        # The SDK's typed errors drive the retry policy; with an injected client and no SDK
        # installed (tests, minimal CI) nothing is retried and errors surface as-is.
        try:
            import anthropic

            rate_limit, status_error = anthropic.RateLimitError, anthropic.APIStatusError
        except ImportError:  # pragma: no cover
            rate_limit = status_error = ()

        for attempt in range(2):
            try:
                response = self.client.messages.parse(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    output_format=schema,
                    output_config={"effort": effort},
                )
                break
            except rate_limit as e:
                if attempt:
                    raise GeneratorError("rate limited twice") from e
                time.sleep(int(e.response.headers.get("retry-after", "10")))
            except status_error as e:
                if attempt or e.status_code < 500:
                    raise GeneratorError(f"API error {e.status_code}: {e.message}") from e
                time.sleep(2)
        if getattr(response, "stop_reason", None) == "refusal":
            raise GeneratorError("the model declined the request")
        return response.parsed_output

    def draft(self, question: str, hits: list[Hit]) -> Draft:
        user = f"Question: {question}\n\nContext blocks:\n{render_context(hits)}"
        return self._parse(SYSTEM, user, Draft, effort=self.effort)

    def rewrite(self, question: str, hits: list[Hit], missing: str) -> str | None:
        user = (f"Question: {question}\n\nA first attempt could not answer; the generator said it was missing: {missing or 'unspecified'}\n\n"
                f"Retrieved blocks:\n{render_context(hits)}")
        out: Sufficiency = self._parse(GRADER_SYSTEM, user, Sufficiency, effort="low", max_tokens=2000)
        if out.sufficient or not out.rewritten_query.strip():
            return None
        return out.rewritten_query.strip()

    def judge(self, question: str, reference: str, answer: str) -> Judgement:
        user = f"Question: {question}\n\nReference answer: {reference}\n\nSystem answer: {answer}"
        return self._parse(JUDGE_SYSTEM, user, Judgement, effort="low", max_tokens=2000)


def claude_available() -> bool:
    import importlib.util
    import os

    return importlib.util.find_spec("anthropic") is not None and bool(
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_PROFILE")
    )
