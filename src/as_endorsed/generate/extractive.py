"""A generator that uses no model at all.

It answers with the retrieved clause whose wording best matches the question,
cites it, and abstains when nothing retrieved shares the question's terms. It
exists so the whole pipeline runs, and is measured, without a hosted model,
and so the checks downstream are exercised by something that can be wrong.
"""

from __future__ import annotations

import re

from as_endorsed.generate.context import MONEY_RE, content_words
from as_endorsed.generate.schema import Claim, Draft
from as_endorsed.retrieval.index import Hit

AMOUNT_Q = re.compile(r"\b(most|maximum|how much|pay up to|limit|sublimit|amount)\b", re.I)
_LABEL_RE = re.compile(r"^(?:\(\w{1,4}\)|[A-Za-z0-9]{1,4}\.)\s+")


class ExtractiveGenerator:
    name = "extractive"
    supports_rewrite = False
    min_overlap = 0.15

    def draft(self, question: str, hits: list[Hit]) -> Draft:
        qw = content_words(question)
        if not hits or not qw:
            return Draft(can_answer=False, answer="", missing="nothing was retrieved")
        scored = []
        for h in hits:
            cw = content_words(h.chunk.text)
            overlap = len(qw & cw) / len(qw)
            # Prefer clause chunks over declarations boilerplate at equal overlap; prefer rank.
            scored.append((overlap, -h.rank, h))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        overlap, _, best = scored[0]
        matched = len(qw & content_words(best.chunk.text))
        enough = matched >= 2 or (len(qw) <= 2 and matched >= 1)
        if overlap < self.min_overlap or not enough:
            return Draft(can_answer=False, answer="", missing=f"no retrieved clause mentions {', '.join(sorted(qw)[:4])}")
        text = _LABEL_RE.sub("", best.chunk.text).strip()
        numeric = None
        if AMOUNT_Q.search(question):
            m = MONEY_RE.search(text)
            if m:
                numeric = float(m.group(0).replace("$", "").replace(",", "").strip())
        return Draft(can_answer=True, answer=text, claims=[Claim(text=text, chunk_ids=[best.chunk.chunk_id])], numeric_value=numeric)

    def rewrite(self, question: str, hits: list[Hit], missing: str) -> str | None:
        return None
