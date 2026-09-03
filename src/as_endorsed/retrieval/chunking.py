"""Chunk variants for the ablation ladder.

Every rung of the ladder indexes the same account material; what differs is
how it is cut and whether endorsements have been resolved into the clauses:

    fixed        512-token windows over the flat text (baseline)
    recursive    paragraph / sentence / word recursive splitter, same budget
    clause       one chunk per clause of the base form; endorsements are
                 separate documents in the pile, as a naive pipeline would see them
    resolved     one chunk per clause *as endorsed*: replaced text in place,
                 deleted clauses marked, added clauses present, unresolved
                 endorsement text attached as flagged siblings
    header       resolved, plus a one-line contextual header on every chunk
                 (form, section path, heading, modified-by)

Chunks from the naive variants still record which clause paths they cover, so
hit@k is scored fairly for every rung.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from as_endorsed.endorse.models import ResolvedClause, ResolvedPolicy
from as_endorsed.endorse.resolve import materialize
from as_endorsed.models import Clause, ParsedForm
from as_endorsed.synth.accounts import Account

VARIANTS = ["fixed", "recursive", "clause", "resolved", "header"]
CHARS_PER_TOKEN = 4
FIXED_TOKENS = 512
FIXED_OVERLAP_TOKENS = 64


@dataclass
class Chunk:
    chunk_id: str
    account_id: str
    variant: str
    text: str
    kind: str  # clause | declarations | endorsement | unresolved
    source: str  # form key, "declarations", or endorsement key
    paths: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)  # endorsement keys that changed this clause
    header: str | None = None
    active: bool = True

    @property
    def embed_text(self) -> str:
        return f"{self.header}\n{self.text}" if self.header else self.text


def _cid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------------------
# Source documents for an account
# ----------------------------------------------------------------------------

def declarations_text(acct: Account) -> list[tuple[str, str]]:
    """(label, text) blocks for the declarations page, as a flat-text pipeline would see them."""
    p = acct.policy
    loc = p.property_location
    blocks = [
        ("policy", f"Flood Insurance Policy Declarations. Policy Number {p.policy_number}. Policy Term {p.term_start.isoformat()} to {p.term_end.isoformat()}. "
                   f"Named Insured {p.named_insured}{' and ' + p.co_insured if p.co_insured else ''}. Mailing Address {p.mailing_address}. Producer {p.agency}."),
        ("property", f"Insured Property. Property Location {loc.one_line()}. Community Number {loc.community_number}. Flood Zone {loc.flood_zone}. "
                     f"Occupancy {loc.occupancy}. Building Description {loc.building_description}. Primary Residence {'Yes' if loc.primary_residence else 'No'}. Rating Method {p.rating_method}."),
    ]
    cov = "Coverage and Deductibles. " + " ".join(
        f"{'Building Property (Coverage A)' if c.coverage == 'building' else 'Personal Property (Coverage B)'}: limit of liability ${c.limit:,}, deductible ${c.deductible:,}."
        for c in p.coverages)
    if not any(c.coverage == "contents" for c in p.coverages):
        cov += " Personal Property (Coverage B): not purchased."
    cov += f" Total Annual Premium ${p.annual_premium:,}."
    blocks.append(("coverage", cov))
    forms = "Forms and Endorsements. " + " ".join(f"{f.form_id} {f.edition} {f.title}." for f in p.forms_schedule)
    for e in p.endorsement_forms:
        sched = (" Schedule: " + "; ".join(f"{k}: {v}" for k, v in e.schedule_values.items())) if e.schedule_values else ""
        forms += f" {e.form_id} {e.edition} {e.title}, effective {e.effective_date.isoformat()}.{sched}"
    for ch in p.endorsements:
        forms += (f" {ch.endorsement_number} General Change Endorsement effective {ch.effective_date.isoformat()}: "
                  f"the {ch.field.replace('_', ' ')} is changed from ${ch.old_value:,} to ${ch.new_value:,}.")
    blocks.append(("forms", forms))
    return blocks


def clause_line(c: Clause) -> str:
    """A clause as it reads in the form: label + own text."""
    label = c.label if c.label.startswith("(") or c.level == 0 else f"{c.label}."
    if c.level == 0:
        return f"{c.label}. {c.heading or c.text}" if not c.label[0].isalpha() or c.label.isupper() and len(c.label) <= 5 else (c.heading or c.text)
    return f"{label} {c.text}".strip()


def flat_form_text(form: ParsedForm) -> tuple[str, list[tuple[int, int, str]]]:
    """Concatenate clauses with their labels; return text and (start, end, path) spans."""
    parts: list[str] = []
    spans: list[tuple[int, int, str]] = []
    pos = 0
    for c in form.clauses:
        line = clause_line(c)
        parts.append(line)
        spans.append((pos, pos + len(line), c.path))
        pos += len(line) + 2  # "\n\n"
    return "\n\n".join(parts), spans


def endorsement_documents(acct: Account, endorsement_forms: dict[str, ParsedForm]) -> list[tuple[str, str]]:
    """(endorsement key, flat text) for each attached endorsement, as a naive pipeline sees them."""
    docs = []
    for e in acct.policy.endorsement_forms:
        form = endorsement_forms.get(e.key)
        if form is None:
            continue
        text = "\n\n".join(clause_line(c) for c in form.clauses) or form.preamble
        docs.append((e.key, text))
    return docs


# ----------------------------------------------------------------------------
# Splitters
# ----------------------------------------------------------------------------

def fixed_windows(text: str, size_chars: int, overlap_chars: int) -> list[tuple[int, int]]:
    out = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size_chars)
        out.append((start, end))
        if end == len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return out


_SEPARATORS = ["\n\n", "\n", ". ", " "]


def recursive_split(text: str, size_chars: int) -> list[tuple[int, int]]:
    """LangChain-style recursive splitter over character offsets."""

    def split(start: int, end: int, level: int) -> list[tuple[int, int]]:
        if end - start <= size_chars:
            return [(start, end)]
        if level >= len(_SEPARATORS):
            return fixed_windows(text[start:end], size_chars, 0) and [(start + a, start + b) for a, b in fixed_windows(text[start:end], size_chars, 0)]
        sep = _SEPARATORS[level]
        pieces: list[tuple[int, int]] = []
        cursor = start
        for m in re.finditer(re.escape(sep), text[start:end]):
            cut = start + m.end()
            pieces.append((cursor, cut))
            cursor = cut
        if cursor < end:
            pieces.append((cursor, end))
        if len(pieces) <= 1:
            return split(start, end, level + 1)
        # Merge small pieces up to the budget, recursing into oversized ones.
        out: list[tuple[int, int]] = []
        acc_start, acc_end = None, None
        for a, b in pieces:
            if b - a > size_chars:
                if acc_start is not None:
                    out.append((acc_start, acc_end))
                    acc_start = acc_end = None
                out.extend(split(a, b, level + 1))
                continue
            if acc_start is None:
                acc_start, acc_end = a, b
            elif b - acc_start <= size_chars:
                acc_end = b
            else:
                out.append((acc_start, acc_end))
                acc_start, acc_end = a, b
        if acc_start is not None:
            out.append((acc_start, acc_end))
        return out

    return [(a, b) for a, b in split(0, len(text), 0) if text[a:b].strip()]


def _paths_in_span(spans: list[tuple[int, int, str]], a: int, b: int) -> list[str]:
    return [p for s, e, p in spans if s < b and e > a]


# ----------------------------------------------------------------------------
# Variant builders
# ----------------------------------------------------------------------------

def _window_chunks(acct: Account, variant: str, base: ParsedForm, endorsement_forms: dict[str, ParsedForm], splitter) -> list[Chunk]:
    chunks: list[Chunk] = []
    base_key = f"{base.form_id}@{base.edition}"
    text, spans = flat_form_text(base)
    for i, (a, b) in enumerate(splitter(text)):
        chunks.append(Chunk(_cid(acct.account_id, variant, base_key, str(i)), acct.account_id, variant, text[a:b], "clause", base_key, paths=_paths_in_span(spans, a, b)))
    dec = "\n\n".join(t for _, t in declarations_text(acct))
    for i, (a, b) in enumerate(splitter(dec)):
        chunks.append(Chunk(_cid(acct.account_id, variant, "dec", str(i)), acct.account_id, variant, dec[a:b], "declarations", "declarations"))
    for key, doc in endorsement_documents(acct, endorsement_forms):
        for i, (a, b) in enumerate(splitter(doc)):
            chunks.append(Chunk(_cid(acct.account_id, variant, key, str(i)), acct.account_id, variant, doc[a:b], "endorsement", key))
    return chunks


def _clause_chunks(acct: Account, base: ParsedForm, endorsement_forms: dict[str, ParsedForm]) -> list[Chunk]:
    chunks: list[Chunk] = []
    base_key = f"{base.form_id}@{base.edition}"
    for c in base.clauses:
        if c.level == 0 and not c.text.strip():
            continue
        chunks.append(Chunk(_cid(acct.account_id, "clause", c.clause_id), acct.account_id, "clause", clause_line(c), "clause", base_key, paths=[c.path]))
    for label, text in declarations_text(acct):
        chunks.append(Chunk(_cid(acct.account_id, "clause", "dec", label), acct.account_id, "clause", text, "declarations", "declarations"))
    for key, doc in endorsement_documents(acct, endorsement_forms):
        for i, para in enumerate(doc.split("\n\n")):
            if para.strip():
                chunks.append(Chunk(_cid(acct.account_id, "clause", key, str(i)), acct.account_id, "clause", para, "endorsement", key))
    return chunks


def _breadcrumb(base: ParsedForm, path: str, by_path: dict[str, Clause]) -> str:
    parts = []
    comps = path.split(".")
    for i in range(1, len(comps) + 1):
        c = by_path.get(".".join(comps[:i]))
        if c is None:
            continue
        head = c.heading or c.term
        parts.append(f"{c.label} {head}" if head and c.level > 0 else (head or c.label))
    return " › ".join(parts)


def _resolved_chunks(acct: Account, variant: str, base: ParsedForm, resolved: ResolvedPolicy, with_header: bool) -> list[Chunk]:
    chunks: list[Chunk] = []
    base_key = f"{base.form_id}@{base.edition}"
    by_path = base.by_path()
    eff = {e.key: e.effective_date for e in acct.policy.endorsement_forms}
    for rc in materialize(base, resolved):
        if rc.base_clause_id is None and rc.path.startswith("UNRESOLVED."):
            text = f"[Unplaced endorsement text from {rc.added_by}; the endorsement does not name the clause it modifies] {rc.text_as_endorsed}"
            header = f"{base_key} › unplaced endorsement text › {rc.added_by}" if with_header else None
            chunks.append(Chunk(_cid(acct.account_id, variant, rc.path), acct.account_id, variant, text, "unresolved", rc.added_by or base_key,
                                paths=[], lineage=[l.endorsement_key for l in rc.lineage], header=header))
            continue
        c = by_path.get(rc.path)
        label = rc.path.split(".")[-1]
        label = label if label.startswith("(") else f"{label}."
        body = f"{label} {rc.text_as_endorsed}".strip() if (c is None or c.level > 0) else rc.text_as_endorsed
        lineage = [l.endorsement_key for l in rc.lineage]
        if not rc.active:
            who = ", ".join(f"{l.endorsement_key} effective {eff.get(l.endorsement_key, l.effective_date)}" for l in rc.lineage if l.op == "DELETE")
            body = f"[This clause is deleted by endorsement {who}; it no longer applies to this policy.] {body}"
        elif lineage:
            mods = ", ".join(f"{k} effective {eff.get(k)}" for k in dict.fromkeys(lineage))
            body = f"{body} [As amended by endorsement {mods}.]"
        header = None
        if with_header:
            crumb = _breadcrumb(base, rc.path, by_path) if c is not None else f"{_breadcrumb(base, rc.parent_path or '', by_path)} › {label} (added by {rc.added_by})"
            header = f"{base_key} › {crumb}" + (f" | modified by {', '.join(dict.fromkeys(lineage))}" if lineage else "")
        chunks.append(Chunk(_cid(acct.account_id, variant, rc.path), acct.account_id, variant, body, "clause", base_key,
                            paths=[rc.path], lineage=lineage, header=header, active=rc.active))
    for label, text in declarations_text(acct):
        header = f"declarations › {label}" if with_header else None
        chunks.append(Chunk(_cid(acct.account_id, variant, "dec", label), acct.account_id, variant, text, "declarations", "declarations", header=header))
    return chunks


def build_chunks(acct: Account, variant: str, base: ParsedForm, *, resolved: ResolvedPolicy | None = None,
                 endorsement_forms: dict[str, ParsedForm] | None = None) -> list[Chunk]:
    endorsement_forms = endorsement_forms or {}
    size = FIXED_TOKENS * CHARS_PER_TOKEN
    if variant == "fixed":
        return _window_chunks(acct, variant, base, endorsement_forms, lambda t: fixed_windows(t, size, FIXED_OVERLAP_TOKENS * CHARS_PER_TOKEN))
    if variant == "recursive":
        return _window_chunks(acct, variant, base, endorsement_forms, lambda t: recursive_split(t, size))
    if variant == "clause":
        return _clause_chunks(acct, base, endorsement_forms)
    if variant in ("resolved", "header"):
        if resolved is None:
            raise ValueError(f"variant {variant} needs a ResolvedPolicy")
        return _resolved_chunks(acct, variant, base, resolved, with_header=(variant == "header"))
    raise ValueError(f"unknown variant {variant}")
