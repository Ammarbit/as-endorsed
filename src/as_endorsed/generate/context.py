"""Context formatting shared by every generator, and the lexical checks."""

from __future__ import annotations

import re

from as_endorsed.retrieval.index import Hit

MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")
NUMBER_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?(?![\w.])")
_WORD_RE = re.compile(r"[a-z0-9]+")
STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "was", "were", "be", "by", "this",
        "that", "it", "its", "as", "at", "with", "any", "we", "you", "your", "our", "not", "no", "yes", "under", "policy",
        "endorsement", "does", "do", "if", "from", "which", "what", "how", "has", "have", "will", "may", "such", "than"}


def render_context(hits: list[Hit]) -> str:
    """Numbered context blocks the generator can cite by chunk id."""
    blocks = []
    for h in hits:
        c = h.chunk
        meta = []
        if c.paths:
            meta.append("clause " + ", ".join(c.paths))
        if c.kind == "declarations":
            meta.append("declarations page")
        if c.kind == "endorsement":
            meta.append(f"endorsement document {c.source}")
        if c.lineage:
            meta.append("as amended by " + ", ".join(c.lineage))
        if not c.active:
            meta.append("DELETED clause, no longer applies")
        if c.kind == "unresolved":
            meta.append("endorsement text that names no clause")
        head = f"[{c.chunk_id}] ({'; '.join(meta)})"
        body = (c.header + "\n" if c.header else "") + c.text
        blocks.append(f"{head}\n{body}")
    return "\n\n".join(blocks)


def content_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in STOP and len(w) > 2}


def money_values(text: str) -> set[float]:
    return {float(m.replace("$", "").replace(",", "").strip()) for m in MONEY_RE.findall(text)}


def numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in NUMBER_RE.findall(text)}


def supported(claim: str, chunk_text: str, min_overlap: float = 0.5) -> bool:
    """A claim is supported when most of its content words, and every dollar amount
    and number in it, appear in the cited chunk."""
    cw = content_words(claim)
    if not cw:
        return True
    overlap = len(cw & content_words(chunk_text)) / len(cw)
    if overlap < min_overlap:
        return False
    return money_values(claim) <= money_values(chunk_text) and numbers(claim) <= numbers(chunk_text) | {n.rstrip("%") for n in numbers(chunk_text)}
