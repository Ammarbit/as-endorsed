"""Apply endorsement operations to a base form for one account.

Precedence, in order:
1. An endorsement controls over the base form wherever they conflict.
2. Between endorsements, the later effective date controls.
3. Same date: the position in the account's endorsement schedule controls, and
   the pair is recorded as a conflict with both texts so a reviewer can see it.

Rule 4 in the scoping document (contra proferentem and the like) is legal
interpretation and is deliberately not implemented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from as_endorsed.endorse.models import (
    Conflict, EndorsementOp, ExtractionResult, Lineage, NewClause, ResolvedClause, ResolvedPolicy,
)
from as_endorsed.models import Clause, ParsedForm

BLANK_RE = re.compile(r"_{4,}")


@dataclass
class ScheduledEndorsement:
    """An endorsement attached to an account."""

    extraction: ExtractionResult
    effective_date: date | None
    order: int
    schedule_values: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.extraction.endorsement_form_id}@{self.extraction.endorsement_edition}"


def resolve_policy(
    *, account_id: str, base: ParsedForm, attached: list[ScheduledEndorsement], as_of: date
) -> ResolvedPolicy:
    by_path = base.by_path()
    state: dict[str, ResolvedClause] = {}
    conflicts: list[Conflict] = []
    unresolved: list[EndorsementOp] = []
    held: list[EndorsementOp] = []
    applied: list[str] = []
    added_count = 0

    def current(path: str) -> ResolvedClause | None:
        if path in state:
            return state[path]
        c = by_path.get(path)
        if c is None:
            return None
        rc = ResolvedClause(path=c.path, parent_path=c.parent_path, base_clause_id=c.clause_id,
                            text_as_endorsed=c.text, original_text=c.text)
        state[path] = rc
        return rc

    def touch(rc: ResolvedClause, op: EndorsementOp, att: ScheduledEndorsement) -> None:
        # Same-date, same-target changes from different endorsements are a conflict.
        for lin in rc.lineage:
            if lin.effective_date == att.effective_date and lin.endorsement_key != att.key and lin.op in ("REPLACE", "DELETE", "AMEND_DEF"):
                conflicts.append(Conflict(
                    path=rc.path, ops=[lin.op_id, op.op_id],
                    texts=[rc.text_as_endorsed, op.new_text or ""],
                    reason=f"{lin.endorsement_key} and {att.key} both change {rc.path} effective {att.effective_date}; "
                           f"schedule order applied ({att.key} controls)",
                ))
                rc.flags.append(f"conflict: see {op.op_id}")
        rc.lineage.append(Lineage(op_id=op.op_id, endorsement_key=att.key, op=op.op, effective_date=att.effective_date))

    ordered = sorted(attached, key=lambda a: (a.effective_date or date.min, a.order))
    for att in ordered:
        if att.effective_date is not None and att.effective_date > as_of:
            continue
        for op in att.extraction.ops:
            op = op.model_copy(deep=True)
            if op.schedule_key and att.schedule_values:
                _fill_schedule(op, att.schedule_values)
                if op.target_path is not None:
                    op.status = "resolved"
            if op.status == "held":
                held.append(op)
                continue
            if op.status == "unresolved" or op.target_path is None:
                added_count += 1
                rc = ResolvedClause(
                    path=f"UNRESOLVED.{att.key}.{added_count}", parent_path=None, base_clause_id=None,
                    text_as_endorsed=op.new_text or op.directive, added_by=att.key,
                    flags=[f"unresolved target: {op.target_ref or 'none given'}"] + op.notes,
                )
                rc.lineage.append(Lineage(op_id=op.op_id, endorsement_key=att.key, op=op.op, effective_date=att.effective_date))
                state[rc.path] = rc
                unresolved.append(op)
                applied.append(op.op_id)
                continue

            target = current(op.target_path)
            if target is None:
                unresolved.append(op)
                continue
            if op.op == "REPLACE":
                touch(target, op, att)
                target.text_as_endorsed = _replacement_text(op, target) or target.text_as_endorsed
                target.active = True
            elif op.op == "DELETE":
                touch(target, op, att)
                target.active = False
            elif op.op == "AMEND_DEF":
                touch(target, op, att)
                target.text_as_endorsed = (target.text_as_endorsed.rstrip() + " " + (op.new_text or "")).strip()
            elif op.op in ("ADD", "SCHEDULE"):
                _apply_add(op, att, target, state, by_path)
            applied.append(op.op_id)

    changed = [rc for rc in state.values() if rc.base_clause_id is None or rc.lineage]
    return ResolvedPolicy(
        account_id=account_id, base_form_key=f"{base.form_id}@{base.edition}", as_of=as_of,
        changed=changed, conflicts=conflicts, unresolved=unresolved, held=held, applied_op_ids=applied,
    )


def _fill_schedule(op: EndorsementOp, values: dict[str, str]) -> None:
    fill = values.get(op.schedule_key or "") or next(iter(values.values()), None)
    if not fill:
        return
    if op.new_text:
        op.new_text = BLANK_RE.sub(fill, op.new_text, count=1)
        op.new_text = BLANK_RE.sub("", op.new_text).strip()
    op.new_clauses = [NewClause(labels=f.labels, text=BLANK_RE.sub(fill, f.text, count=1)) for f in op.new_clauses]
    op.new_clauses = [NewClause(labels=f.labels, text=BLANK_RE.sub("", f.text).strip()) for f in op.new_clauses]
    op.notes.append(f"schedule filled: {fill!r}")
    op.schedule_key = None


def _replacement_text(op: EndorsementOp, target: ResolvedClause) -> str | None:
    """The restated clause matching the target's own label, with its descendants;
    otherwise everything captured after the directive."""
    last = target.path.split(".")[-1]
    match = next((f for f in op.new_clauses if f.labels and f.labels[-1] == last), None)
    if match is None:
        return op.new_text
    prefix = match.labels
    parts = [match.text]
    for f in op.new_clauses:
        if f is not match and len(f.labels) > len(prefix) and f.labels[: len(prefix)] == prefix:
            parts.append(f"{f.labels[-1]} {f.text}".strip())
    return " ".join(parts).strip()


def _apply_add(op: EndorsementOp, att: ScheduledEndorsement, target: ResolvedClause,
               state: dict[str, ResolvedClause], by_path: dict[str, Clause]) -> None:
    lin = Lineage(op_id=op.op_id, endorsement_key=att.key, op=op.op, effective_date=att.effective_date)
    fragments = op.new_clauses or ([NewClause(labels=[op.new_label] if op.new_label else [], text=op.new_text or "")])
    created: list[ResolvedClause] = []
    base_label = op.new_label
    for f in fragments:
        labels = f.labels
        if not labels:
            if created:
                created[-1].text_as_endorsed = (created[-1].text_as_endorsed + " " + f.text).strip()
                continue
            labels = [base_label] if base_label else ["+"]
        # Fragments that are breadcrumbs of the target (restating its own label chain) are skipped.
        if base_label and labels[0] != base_label and len(labels) == 1 and labels[0] in target.path.split("."):
            continue
        rel = labels if not base_label or labels[0] == base_label else [base_label] + labels
        path = f"{target.path}.{'.'.join(rel)}"
        if path in by_path or (path in state and state[path].added_by != att.key):
            path = f"{path}+{att.extraction.endorsement_form_id}"
        parent = path.rsplit(".", 1)[0]
        rc = ResolvedClause(path=path, parent_path=parent, base_clause_id=None, text_as_endorsed=f.text,
                            added_by=att.key, lineage=[lin])
        state[path] = rc
        created.append(rc)
    if not created:
        rc = ResolvedClause(path=f"{target.path}.+{att.extraction.endorsement_form_id}", parent_path=target.path,
                            base_clause_id=None, text_as_endorsed=op.new_text or "", added_by=att.key, lineage=[lin])
        state[rc.path] = rc


def materialize(base: ParsedForm, resolved: ResolvedPolicy) -> list[ResolvedClause]:
    """Full as-endorsed view: every base clause (with its current text and active
    flag) plus added clauses, in document order with additions after their parent."""
    changed = resolved.changed_by_path()
    out: list[ResolvedClause] = []
    added_by_parent: dict[str | None, list[ResolvedClause]] = {}
    for rc in resolved.changed:
        if rc.base_clause_id is None:
            added_by_parent.setdefault(rc.parent_path, []).append(rc)
    for c in base.clauses:
        rc = changed.get(c.path) or ResolvedClause(
            path=c.path, parent_path=c.parent_path, base_clause_id=c.clause_id, text_as_endorsed=c.text, original_text=c.text
        )
        out.append(rc)
    # Insert additions after the last descendant of their parent.
    for parent, adds in added_by_parent.items():
        if parent is None:
            out.extend(adds)
            continue
        idx = max((i for i, rc in enumerate(out) if rc.path == parent or rc.path.startswith(parent + ".")), default=len(out) - 1)
        for k, rc in enumerate(adds):
            out.insert(idx + 1 + k, rc)
    return out
