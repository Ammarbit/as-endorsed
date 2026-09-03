"""Resolve a textual clause reference to a path in a parsed base form.

Endorsements refer to the base form in several idioms:

    "Paragraph II.C.5"                       explicit dotted path
    "Section IV. PROPERTY NOT INSURED"       Roman section with its heading
    "Your Duties After Loss Condition 4.a.(5)"  section by name + path under it
    "Loss Settlement Condition 6."           section by name + item, heading words as check
    "the DEDUCTIBLE clause"                  section by name only
    "the definition of 'Basement'"           defined term

Resolution is deterministic and reports why it failed; nothing is guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from as_endorsed.models import Clause, ParsedForm

STOPWORDS = {
    "the", "of", "this", "that", "in", "under", "to", "a", "an", "and", "or", "your", "our",
    "policy", "section", "paragraph", "clause", "subsection", "item", "provision", "form",
    "above", "below", "following", "endorsement", "part",
}
# Section-name words that identify a section but also read as ordinary words.
SECTION_SYNONYMS = {
    "condition": "CONDITIONS", "conditions": "CONDITIONS",
    "exclusion": "EXCLUSIONS", "exclusions": "EXCLUSIONS",
    "definition": "DEFINITIONS", "definitions": "DEFINITIONS",
    "deductible": "DEDUCTIBLE", "deductibles": "DEDUCTIBLES",
}
PATH_TOKEN_RE = re.compile(
    r"^(?:[IVX]{1,5}|[A-Z]|\d{1,2}|[a-z]|\(\d{1,2}\)|\([a-z]{1,4}\))"
    r"(?:\.(?:[A-Z]|\d{1,2}|[a-z]|\(\d{1,2}\)|\([a-z]{1,4}\)))*\.?$"
)


@dataclass
class RefResolution:
    path: str | None
    confidence: float
    reason: str
    alternates: list[str] = field(default_factory=list)


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS]


def _singular(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def _section_words(c: Clause) -> set[str]:
    return {_singular(w) for w in re.findall(r"[a-z0-9]+", (c.heading or c.label).lower())}


def _path_tokens(ref: str) -> list[str]:
    tokens = []
    for tok in re.findall(r"[A-Za-z0-9().]+", ref):
        core = tok.rstrip(".").rstrip(",")
        if not core:
            continue
        has_structure = any(ch.isdigit() for ch in core) or "." in core or re.fullmatch(r"[IVX]{1,5}", core)
        if has_structure and PATH_TOKEN_RE.match(core + ("." if tok.endswith(".") else "")):
            tokens.append(core)
    return tokens


def resolve_ref(ref: str, form: ParsedForm) -> RefResolution:
    by_path = form.by_path()
    sections = [c for c in form.clauses if c.level == 0]
    ref_clean = re.sub(r"\b(of|in) (this|the) policy\b", "", ref, flags=re.I).strip(" .:;,")

    # 1. Explicit path that exists as written.
    for tok in _path_tokens(ref_clean):
        if tok in by_path:
            return RefResolution(tok, 0.95, f"explicit path {tok}")

    # 2. Section by name (synonym or heading words), then a path under it.
    words = _words(ref_clean)
    section_hits: list[Clause] = []
    for w in words:
        syn = SECTION_SYNONYMS.get(w)
        for s in sections:
            sw = _section_words(s)
            if (syn and s.label.startswith(syn.rstrip("S"))) or _singular(w) in sw:
                if s not in section_hits:
                    section_hits.append(s)
    # Prefer sections whose full heading is covered by the reference words.
    section_hits.sort(key=lambda s: -len(_section_words(s) & {_singular(w) for w in words}))

    tokens = _path_tokens(ref_clean)
    for s in section_hits:
        for tok in tokens:
            cand = f"{s.path}.{tok}"
            if cand in by_path:
                return RefResolution(cand, 0.9, f"section {s.path} + path {tok}")
    if tokens:
        # Any section that has this path under it.
        found = [f"{s.path}.{tokens[0]}" for s in sections if f"{s.path}.{tokens[0]}" in by_path]
        if len(found) == 1:
            return RefResolution(found[0], 0.7, f"path {tokens[0]} found under one section only")
        if len(found) > 1:
            return RefResolution(None, 0.3, f"path {tokens[0]} is ambiguous across sections", found)

    # 3. Heading words anywhere in the tree ("Loss Settlement" -> CONDITIONS.6).
    if words:
        want = {_singular(w) for w in words if w not in SECTION_SYNONYMS}
        if want:
            scored: list[tuple[int, Clause]] = []
            for c in form.clauses:
                if not c.heading:
                    continue
                hw = {_singular(w) for w in re.findall(r"[a-z0-9]+", c.heading.lower())}
                if want <= hw:
                    scored.append((len(hw - want), c))
            scored.sort(key=lambda t: (t[0], t[1].level))
            if scored:
                best = scored[0][1]
                alts = [c.path for _, c in scored[1:4]]
                conf = 0.85 if len(scored) == 1 or scored[0][0] < scored[1][0] else 0.5
                return RefResolution(best.path, conf, f"heading words {sorted(want)} matched {best.path}", alts)

    # 4. A section named by synonym alone ("the DEDUCTIBLE clause").
    if section_hits and not tokens:
        s = section_hits[0]
        return RefResolution(s.path, 0.8, f"section by name {s.path}")

    return RefResolution(None, 0.0, f"no clause matches {ref!r}")


def resolve_term(term: str, form: ParsedForm) -> RefResolution:
    norm = re.sub(r"[^a-z0-9 ]", "", term.lower()).strip()
    hits = [c for c in form.clauses if c.term and re.sub(r"[^a-z0-9 ]", "", c.term.lower()).strip() == norm]
    if len(hits) == 1:
        return RefResolution(hits[0].path, 0.95, f"defined term {term!r}")
    if not hits:
        # Loose: term appears at the start of a definition clause.
        loose = [c for c in form.clauses if c.term and norm in re.sub(r"[^a-z0-9 ]", "", c.term.lower())]
        if len(loose) == 1:
            return RefResolution(loose[0].path, 0.7, f"defined term {term!r} (partial match)")
        return RefResolution(None, 0.0, f"no definition for {term!r}")
    return RefResolution(None, 0.3, f"term {term!r} is ambiguous", [c.path for c in hits])
