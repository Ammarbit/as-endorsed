"""Clause tree parser for numbered policy forms.

Insurance forms are consistently numbered, which is the one gift the domain
gives you. The parser is a state machine over layout lines:

* A line that starts with a label (I. / A. / 1. / a. / (1) / (a)) opens a clause,
  provided the label is the *expected next* label at that level. That sequence
  check is what keeps "2. " inside running text from opening a bogus clause.
* A line without a label continues the deepest open clause whose label sits to
  the left of the line. Wrapped text indents one step past its own label, so
  trailing text that belongs to a parent (after the parent's children) lands on
  the parent, not on the last child.

No LLM is involved. Where this parser fails it fails loudly, in `warnings`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from as_endorsed.ingest.pdf import Line, extract_lines
from as_endorsed.models import BBox, Clause, ParsedForm

LEVEL_ROMAN, LEVEL_UPPER, LEVEL_NUM, LEVEL_LOWER, LEVEL_PNUM, LEVEL_PLOWER, LEVEL_PROMAN = range(7)
LEVEL_NAMES = ["section", "upper", "num", "lower", "paren-num", "paren-lower", "paren-roman"]

LABEL_RE = re.compile(
    r"^\s*(?P<label>\((?:\d{1,2}|[a-z]|[ivx]{1,4})\)|[IVX]{1,5}\.|[A-Z]\.|\d{1,2}\.|[a-z]\.)(?=\s|$)\s*(?P<rest>.*)$"
)
ROMANS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV"]
LOWER_ROMANS = [r.lower() for r in ROMANS]
FIRST = {LEVEL_ROMAN: "I", LEVEL_UPPER: "A", LEVEL_NUM: "1", LEVEL_LOWER: "a", LEVEL_PNUM: "1", LEVEL_PLOWER: "a", LEVEL_PROMAN: "i"}
INDENT_SLACK = 3.0  # points


def successor(level: int, label: str) -> str | None:
    if level == LEVEL_ROMAN:
        i = ROMANS.index(label)
        return ROMANS[i + 1] if i + 1 < len(ROMANS) else None
    if level == LEVEL_PROMAN:
        i = LOWER_ROMANS.index(label)
        return LOWER_ROMANS[i + 1] if i + 1 < len(LOWER_ROMANS) else None
    if level in (LEVEL_NUM, LEVEL_PNUM):
        return str(int(label) + 1)
    if label == "z" or label == "Z":
        return None
    return chr(ord(label) + 1)


def _looks_like_heading(rest: str) -> bool:
    letters = [c for c in rest if c.isalpha()]
    return len(letters) >= 3 and all(c.isupper() for c in letters)


def classify(label: str, rest: str) -> list[tuple[int, str]]:
    """Map a raw label token to candidate (level, core label) readings, most likely first.

    `(i)` may be the ninth lettered item or the first lower-roman item; both
    readings are returned and the sequence check decides.
    """
    if label.startswith("("):
        inner = label[1:-1]
        if inner.isdigit():
            return [(LEVEL_PNUM, inner)]
        out: list[tuple[int, str]] = []
        if len(inner) == 1:
            out.append((LEVEL_PLOWER, inner))
        if inner in LOWER_ROMANS:
            out.append((LEVEL_PROMAN, inner))
        return out
    core = label[:-1]
    if core.isdigit():
        return [(LEVEL_NUM, core)]
    if core.islower():
        return [(LEVEL_LOWER, core)]
    if len(core) >= 2 and re.fullmatch(r"[IVX]+", core):
        return [(LEVEL_ROMAN, core)]
    if core in ("I", "V", "X") and _looks_like_heading(rest):
        return [(LEVEL_ROMAN, core)]
    return [(LEVEL_UPPER, core)]


@dataclass
class _Node:
    level: int
    label: str
    label_x: float  # column-relative x of the label
    parent: "_Node | None"
    lines: list[Line] = field(default_factory=list)
    rel_xs: list[float] = field(default_factory=list)
    own_line_count_before_children: int = 0
    has_children: bool = False

    @property
    def path(self) -> str:
        parts = []
        n: _Node | None = self
        while n is not None:
            parts.append(_path_label(n))
            n = n.parent
        return ".".join(reversed(parts))


def _path_label(n: _Node) -> str:
    if n.level in (LEVEL_PNUM, LEVEL_PLOWER, LEVEL_PROMAN):
        return f"({n.label})"
    return n.label


def _clean(text: str) -> str:
    text = text.replace("\xad ", "").replace("\xad", "")  # soft hyphen at a line break
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _column_bases(lines: list[Line]) -> dict[int, float]:
    """Left edge of each column, taken from the leftmost label line in it."""
    bases: dict[int, float] = {}
    for ln in lines:
        if LABEL_RE.match(ln.text):
            bases[ln.col] = min(bases.get(ln.col, ln.x0), ln.x0)
    return bases


def parse_form(
    pdf_path: str | Path,
    *,
    form_id: str,
    edition: str,
    title: str,
) -> ParsedForm:
    lines, npages = extract_lines(pdf_path)
    bases = _column_bases(lines)
    warnings: list[str] = []
    preamble: list[str] = []
    roots: list[_Node] = []
    order: list[_Node] = []
    stack: list[_Node] = []

    for ln in lines:
        rel_x = ln.x0 - bases.get(ln.col, ln.x0)
        m = LABEL_RE.match(ln.text)
        if m:
            candidates = classify(m.group("label"), m.group("rest"))
            local_warnings: list[str] = []
            accepted = next(
                ((lvl, c) for lvl, c in candidates if _accepts_label(stack, lvl, c, rel_x, local_warnings, ln)),
                None,
            )
            if accepted is None:
                warnings.extend(local_warnings)
            else:
                level, core = accepted
                while stack and stack[-1].level >= level:
                    stack.pop()
                parent = stack[-1] if stack else None
                node = _Node(level, core, rel_x, parent)
                if parent is not None:
                    if not parent.has_children:
                        parent.own_line_count_before_children = len(parent.lines)
                    parent.has_children = True
                else:
                    roots.append(node)
                node.lines.append(replace_text(ln, m.group("rest")))
                node.rel_xs.append(rel_x)
                stack.append(node)
                order.append(node)
                continue
        # continuation
        if not stack:
            preamble.append(_clean(ln.text))
            continue
        target = _continuation_target(stack, rel_x)
        while stack[-1] is not target:
            stack.pop()
        target.lines.append(ln)
        target.rel_xs.append(rel_x)

    clauses = [_to_clause(n, form_id, edition, roots) for n in order]
    return ParsedForm(
        form_id=form_id,
        edition=edition,
        title=title,
        source_pdf=str(pdf_path),
        pages=npages,
        preamble=" ".join(p for p in preamble if p),
        clauses=clauses,
        warnings=warnings,
    )


def replace_text(ln: Line, text: str) -> Line:
    from dataclasses import replace

    return replace(ln, text=text)


def _accepts_label(
    stack: list[_Node], level: int, core: str, rel_x: float, warnings: list[str], ln: Line
) -> bool:
    """Only open a clause when this is the label we expect next at its level."""
    open_at_level = next((n for n in reversed(stack) if n.level == level), None)
    if open_at_level is not None:
        expected = successor(level, open_at_level.label)
        # A shallower sibling may have closed this level; then a fresh sequence is fine.
        if core == expected:
            return True
        if core == FIRST[level] and _level_was_closed(stack, level):
            return True
        warnings.append(f"p{ln.page}: rejected label {core!r} at {LEVEL_NAMES[level]} (expected {expected!r}): {ln.text[:60]!r}")
        return False
    # Nothing open at this level: a fresh list must start at its first label. Forms do
    # skip levels (a section that numbers items directly, with no letter tier), so the
    # only structural check is that the label is not indented left of its parent.
    if core != FIRST[level]:
        if level == LEVEL_ROMAN and not stack:
            return True  # first section can start anywhere if the preamble swallowed nothing
        warnings.append(f"p{ln.page}: rejected label {core!r} at {LEVEL_NAMES[level]} (no open sequence): {ln.text[:60]!r}")
        return False
    parent = stack[-1] if stack else None
    if parent is not None and parent.level != LEVEL_ROMAN and rel_x < parent.label_x - INDENT_SLACK:
        warnings.append(f"p{ln.page}: rejected label {core!r} at {LEVEL_NAMES[level]}, indented left of its parent: {ln.text[:60]!r}")
        return False
    return True


def _level_was_closed(stack: list[_Node], level: int) -> bool:
    """True when the deepest open node at `level` is not the current tail of its parent,
    i.e. a shallower label has since opened a new branch."""
    idx = max(i for i, n in enumerate(stack) if n.level == level)
    return any(n.level < level for n in stack[idx + 1 :])


def _continuation_target(stack: list[_Node], rel_x: float) -> _Node:
    for n in reversed(stack):
        if n.label_x < rel_x - INDENT_SLACK:
            return n
    return stack[-1]


def _to_clause(n: _Node, form_id: str, edition: str, roots: list[_Node]) -> Clause:
    own_count = n.own_line_count_before_children if n.has_children else len(n.lines)
    text = _clean(" ".join(l.text for l in n.lines))
    head_text = _clean(" ".join(l.text for l in n.lines[:own_count]))

    # A heading is a short first line without terminal punctuation that is either the
    # clause's only own line, or is followed by body text at the label's own indent
    # (wrapped text would sit one step deeper).
    heading = None
    first = _clean(n.lines[0].text)
    short = 0 < len(first) <= 70 and not first.endswith((".", ";", ",", ":"))
    if n.level == LEVEL_ROMAN:
        heading = first
    elif short and (own_count == 1 or n.rel_xs[1] <= n.label_x + INDENT_SLACK):
        heading = first

    term = None
    section = n
    while section.parent is not None:
        section = section.parent
    in_definitions = "DEFINITION" in _clean(" ".join(l.text for l in section.lines[:1])).upper()
    if in_definitions and n.level == LEVEL_NUM:
        tm = re.match(r"^([A-Z][A-Za-z0-9'() \-/]{1,50}?)(?:\.\s|$)", head_text)
        if tm:
            term = tm.group(1).strip()
        elif heading:
            term = heading

    per_page: dict[int, list[float]] = {}
    for l in n.lines:
        box = per_page.setdefault(l.page, [l.x0, l.y0, l.x1, l.y1])
        box[0], box[1] = min(box[0], l.x0), min(box[1], l.y0)
        box[2], box[3] = max(box[2], l.x1), max(box[3], l.y1)
    bboxes = [BBox(page=p, x0=b[0], y0=b[1], x1=b[2], y1=b[3]) for p, b in sorted(per_page.items())]

    path = n.path
    return Clause(
        clause_id=f"{form_id}@{edition}:{path}",
        form_id=form_id,
        edition=edition,
        path=path,
        parent_path=n.parent.path if n.parent else None,
        level=n.level,
        label=_path_label(n),
        heading=heading,
        term=term,
        text=text,
        page_start=n.lines[0].page,
        bboxes=bboxes,
    )


def outline(form: ParsedForm, *, max_chars: int = 90) -> str:
    """Indented text outline, for eyeballing a parse."""
    out = [f"# {form.title} ({form.form_id}@{form.edition})", ""]
    for c in form.clauses:
        indent = "  " * c.level
        head = c.heading or c.term or c.text[:max_chars]
        if head != c.text and len(c.text) > len(head):
            head = head.rstrip() + " …"
        out.append(f"{indent}{c.label} {head}   [p{c.page_start}]")
    if form.warnings:
        out += ["", "## Warnings"] + [f"- {w}" for w in form.warnings]
    return "\n".join(out)
