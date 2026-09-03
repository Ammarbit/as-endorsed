"""Rule-based extraction of endorsement operations.

An endorsement is a diff against a base form, written in prose. The prose is
formulaic, so the common idioms are matched with patterns and the restated
text that follows a directive is captured with the label structure the
endorsement gave it. Anything the rules cannot place is *not* guessed: it
becomes an unresolved or held op that shows up in the review list.

Idioms handled:

    X is replaced by the following:                     REPLACE
    X is deleted.  /  X does not apply.                 DELETE
    The following [section c.] is added to X:           ADD
    The definition of "T" [in X] is amended to ...:     AMEND_DEF
    this policy is extended to provide the following:   ADD to an extensions section
    This policy is amended as follows:                  ADD, no target (unresolved)
    It is agreed that ...                               ADD, no target (held)
    Under X, we do not cover the following: ____        ADD with a schedule blank (held until filled)
"""

from __future__ import annotations

import re

from as_endorsed.endorse.models import EndorsementOp, ExtractionResult, NewClause, OpKind
from as_endorsed.endorse.refs import resolve_ref, resolve_term
from as_endorsed.models import Clause, ParsedForm

_F = re.I | re.S
DIRECTIVES: list[tuple[OpKind, re.Pattern[str], dict]] = [
    ("REPLACE", re.compile(
        r"^(?:in\s+consideration\s+of[^,]{0,100},\s*)?(?P<target>.{3,160}?)\s+(?:is|are)\s+(?:hereby\s+)?(?:deleted(?:\s+in\s+(?:its|their)\s+entirety)?\s+and\s+)?"
        r"replaced\s+(?:by|with)\s+the\s+following\s*:?\s*(?P<inline>.*)$", _F), {}),
    ("DELETE", re.compile(
        r"^(?:in\s+consideration\s+of[^,]{0,100},\s*)?(?P<target>.{3,160}?)\s+(?:is|are)\s+(?:hereby\s+)?deleted(?:\s+in\s+(?:its|their)\s+entirety)?\s*\.?\s*$", _F), {}),
    ("DELETE", re.compile(
        r"^(?P<target>.{3,160}?)\s+(?:does|do|shall)\s+not\s+apply(?:\s+to\s+this\s+policy)?\s*\.?\s*$", _F), {}),
    ("ADD", re.compile(
        r"^the\s+following\s+(?:(?:new\s+)?(?:section|paragraph|item|subsection|clause|exclusion|definition|condition|sentence)s?\s+"
        r"(?P<newlabel>[A-Za-z0-9()]{1,6}?)\.?\s+)?(?:is|are)\s+added\s+(?:to|after|following|at\s+the\s+end\s+of)\s+"
        r"(?P<target>[^:]{3,160}?)\s*(?::\s*(?P<inline>.*))?$", _F), {}),
    ("AMEND_DEF", re.compile(
        r"^the\s+definition\s+of\s+[“\"']?(?P<term>[^”\"']{2,60}?)[”\"']?(?:\s+(?:in|under|at)\s+(?P<target>.{3,80}?))?\s+is\s+"
        r"(?:amended|changed|revised|expanded|modified)(?:\s+to\s+(?:include|read|add)(?:\s+the\s+following)?)?\s*:?\s*(?P<inline>.*)$", _F), {}),
    ("ADD", re.compile(
        r"^(?:in\s+consideration\s+(?:of\s+)?.{0,100}?,\s*)?this\s+policy\s+is\s+(?:extended|modified|amended)\s+to\s+provide\s+"
        r"(?:coverage\s+for\s+)?the\s+following(?:\s+coverage)?\s*:?\s*(?P<inline>.*)$", _F), {"implicit_target": "extensions of coverage"}),
    ("ADD", re.compile(
        r"^(?:this|the)\s+policy\s+is\s+(?:amended|changed|modified)\s+as\s+follows\s*:?\s*(?P<inline>.*)$", _F), {"loose": True}),
    ("ADD", re.compile(r"^it\s+is\s+(?:hereby\s+)?agreed\s+that\s+(?P<inline>.+)$", _F), {"loose": True, "held": True}),
    ("ADD", re.compile(
        r"^under\s+(?P<target>.{3,80}?)(?:\s+of\s+this\s+policy)?,?\s+we\s+do\s+not\s+(?:cover|insure)\s+(?P<inline>.+)$", _F), {}),
    ("ADD", re.compile(
        r"\bchanges\s+the\s+(?P<target>[A-Z][A-Za-z ,]{2,60}?)\s+section\b(?:,\s*(?P<sub>[A-Z][A-Z ]{2,40}))?(?P<inline>.*)$", _F),
        {"notice": True, "held": True}),
]
CLOSER_RE = re.compile(
    r"^(all\s+other\s+(terms|provisions|conditions)|except\s+as\s+(specifically\s+|otherwise\s+)?(modified|provided|amended|stated)"
    r"|the\s+coverage\s+provided\s+by\s+this|this\s+endorsement\s+(applies|does\s+not|will\s+not|is\s+attached)|signed\b"
    r"|nothing\s+(else|herein)|this\s+agreement\s+also\s+applies|named\s+insured\s*:|policy\s+number\s*:|effective\s+date\s*:"
    r"|attached\s+to\s+and\s+forming|dated\b)", re.I)
HEADER_RE = re.compile(r"^(endorsement\s+no\.?|edition\s+date|form\s+no\.?|effective\s*:|prescribed\s+by|texas\s+windstorm|windstorm\s+and\s+hail$)", re.I)
BLANK_RE = re.compile(r"_{4,}")
LABEL_COMPONENT_RE = re.compile(r"^(?:[A-Za-z]|\d{1,2}|\([a-z0-9]{1,4}\)|[IVX]{1,5})$")
_SPLIT_RE = re.compile(r"(?<=[.:;!?])\s+(?=[A-Z“\"(])")
_NO_SPLIT_BEFORE = re.compile(r"(?:^|\s)(?:[IVX]{1,5}|\d{1,2}|[A-Za-z]|No|Inc|Corp|St|vs|etc|Sec|Para|Art)\.$")


def split_sentences(text: str) -> list[str]:
    """Sentence split that keeps 'Section IV. PROPERTY' and 'Condition 4.b.(2). is' intact."""
    out: list[str] = []
    start = 0
    for m in _SPLIT_RE.finditer(text):
        before = text[start:m.start()]
        if _NO_SPLIT_BEFORE.search(before):
            continue
        out.append(before.strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return [x for x in out if x]


def _labels_of(c: Clause) -> list[str]:
    return [p for p in c.path.split(".") if LABEL_COMPONENT_RE.match(p) and not re.fullmatch(r"p\d+", p)]


def _fragment(c: Clause) -> NewClause:
    text = c.text
    return NewClause(labels=_labels_of(c), text=text)


def _join(fragments: list[NewClause]) -> str:
    parts = []
    for f in fragments:
        label = f.labels[-1] if f.labels else ""
        label = label if label.startswith("(") else (label + "." if label else "")
        parts.append((label + " " + f.text).strip())
    return " ".join(parts).strip()


def extract_ops(endorsement: ParsedForm, base: ParsedForm) -> ExtractionResult:
    key_id, key_ed = endorsement.form_id, endorsement.edition
    result = ExtractionResult(endorsement_form_id=key_id, endorsement_edition=key_ed, ops=[])
    if not endorsement.clauses and not endorsement.preamble.strip():
        result.scanned = True
        result.ops.append(_held(key_id, key_ed, 1, "no extractable text; the PDF is probably scanned", page=1))
        return result

    ops: list[EndorsementOp] = []
    current: EndorsementOp | None = None
    fragments: list[NewClause] = []
    n = 0

    def close() -> None:
        nonlocal current, fragments
        if current is not None:
            current.new_clauses = fragments
            current.new_text = _join(fragments) if fragments else (current.new_text or None)
            _finalise(current, base)
            ops.append(current)
        current, fragments = None, []

    for c in endorsement.clauses:
        text = c.text.strip()
        if not text:
            continue
        labels = _labels_of(c)
        pre: list[str] = []  # sentences of this clause seen before any directive in it
        for sentence in split_sentences(text):
            if HEADER_RE.match(sentence):
                continue
            hit = None
            for kind, rx, opts in DIRECTIVES:
                m = rx.search(sentence) if opts.get("notice") else rx.match(sentence)
                if m:
                    hit = (kind, m, opts)
                    break
            if hit is None:
                if current is not None and CLOSER_RE.match(sentence):
                    if pre:
                        fragments.append(NewClause(labels=labels, text=" ".join(pre)))
                        pre = []
                    close()
                    continue
                pre.append(sentence)
                continue
            # A directive: whatever preceded it in this clause belongs to the open op.
            if current is not None and pre:
                fragments.append(NewClause(labels=labels, text=" ".join(pre)))
            pre = []
            close()
            kind, m, opts = hit
            n += 1
            groups = m.groupdict()
            target_ref = (groups.get("sub") or groups.get("target") or "").strip(" .:;,") or None
            current = EndorsementOp(
                op_id=f"{key_id}@{key_ed}#{n}",
                endorsement_form_id=key_id, endorsement_edition=key_ed,
                op=kind, target_ref=target_ref, target_term=(groups.get("term") or None),
                new_label=(groups.get("newlabel") or None),
                method="rule", confidence=0.0, status="unresolved",
                source_page=c.page_start, directive=sentence[:200],
            )
            if opts.get("implicit_target"):
                current.target_ref = current.target_ref or opts["implicit_target"]
                current.notes.append("target implied by 'extended to provide' idiom")
            if opts.get("loose"):
                current.notes.append("directive names no target clause")
            if opts.get("held"):
                current.notes.append("low-confidence idiom; review before applying")
                current.confidence = 0.4
            inline = (groups.get("inline") or "").strip()
            if inline:
                fragments.append(NewClause(labels=[], text=inline))
            labels = []  # text after a directive inside the same clause is inline, not the clause's own
        if current is not None and pre:
            fragments.append(NewClause(labels=labels, text=" ".join(pre)))
    close()

    if not ops:
        body = " ".join(c.text for c in endorsement.clauses if not HEADER_RE.match(c.text)).strip()
        ops.append(_held(key_id, key_ed, 1, "no directive recognised in the endorsement text", page=1, text=body[:2000]))
    result.ops = ops
    return result


def _held(fid: str, ed: str, n: int, note: str, *, page: int, text: str | None = None) -> EndorsementOp:
    return EndorsementOp(
        op_id=f"{fid}@{ed}#{n}", endorsement_form_id=fid, endorsement_edition=ed, op="ADD",
        new_text=text, method="rule", confidence=0.0, status="held", notes=[note], source_page=page,
    )


def _finalise(op: EndorsementOp, base: ParsedForm) -> None:
    """Resolve the target reference against the base form and set confidence/status."""
    blank_fragments = [f for f in op.new_clauses if BLANK_RE.search(f.text) and len(f.text) <= 400]
    if blank_fragments:
        op.schedule_key = op.endorsement_form_id
        op.notes.append("text contains blanks to be filled from a schedule")
    elif op.new_text and BLANK_RE.search(op.new_text):
        op.notes.append("blanks present in long text (signature or header lines); not treated as a schedule")
    conf = op.confidence or 0.0
    if op.op == "AMEND_DEF" and op.target_term:
        r = resolve_term(op.target_term, base)
        if r.path is None and op.target_ref:
            r = resolve_ref(op.target_ref, base)
        op.target_path, conf = r.path, max(conf, r.confidence)
        op.notes.append(r.reason)
    elif op.target_ref:
        r = resolve_ref(op.target_ref, base)
        op.target_path = r.path
        conf = r.confidence if not conf else min(conf, r.confidence) if r.path else conf
        op.notes.append(r.reason)
        if r.alternates:
            op.notes.append("alternates: " + ", ".join(r.alternates))
    if op.op == "ADD" and not op.new_label and op.new_clauses:
        first = next((f for f in op.new_clauses if f.labels), None)
        if first:
            op.new_label = first.labels[0]
    if op.op in ("REPLACE", "ADD", "AMEND_DEF") and not op.new_text:
        op.notes.append("no replacement text captured")
        conf = min(conf, 0.3)
    op.confidence = round(conf, 2)
    if op.schedule_key:
        op.status = "held"
    elif op.target_path is None:
        op.status = "unresolved" if op.confidence >= 0.3 or op.target_ref or op.op == "ADD" else "held"
        if any("low-confidence" in n for n in op.notes):
            op.status = "held"
    elif op.confidence < 0.6:
        op.status = "held"
    else:
        op.status = "resolved"
