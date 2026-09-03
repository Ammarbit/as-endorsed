"""`as-endorsed endorse ...`, `resolve`, `review`: the endorsement engine commands."""

from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

import typer
from rich import print as rprint
from rich.table import Table

from as_endorsed.config import settings
from as_endorsed.corpus import registry

endorse_app = typer.Typer(no_args_is_help=True, help="Extract operations from endorsements and check them against ground truth.")


@endorse_app.command("extract")
def endorse_extract(
    key: str = typer.Argument(None, help="Registry key of an endorsement, e.g. TWIA-802@2019-11"),
    all_forms: bool = typer.Option(False, "--all", help="Every registry endorsement whose PDF is present"),
    llm: bool = typer.Option(False, help="Try the LLM extractor when rules find nothing resolvable (needs credentials)"),
) -> None:
    """Parse an endorsement, extract its operations, resolve targets against its base form."""
    from as_endorsed.endorse.pipeline import extract_registry_endorsement

    specs = [f for f in registry.FORMS if f.kind == "endorsement"] if all_forms else [registry.get(key)]
    t = Table("endorsement", "op", "target as written", "resolved path", "status", "conf", "why", expand=True)
    counts: Counter[str] = Counter()
    for spec in specs:
        if not (settings.raw_dir / spec.filename).exists():
            rprint(f"[yellow]skip[/] {spec.key}: not downloaded")
            continue
        result = extract_registry_endorsement(spec, use_llm=llm)
        for op in result.ops:
            counts[op.status] += 1
            why = next((n for n in op.notes if not n.startswith(("section ", "explicit ", "heading ", "defined "))), op.notes[0] if op.notes else "")
            t.add_row(spec.key, op.op, (op.target_ref or "")[:40], op.target_path or "–", _status(op.status), f"{op.confidence:.2f}", why[:60])
    rprint(t)
    total = sum(counts.values())
    if total:
        rprint(f"ops={total}  resolved={counts['resolved']} ({counts['resolved'] / total:.0%})  unresolved={counts['unresolved']}  held={counts['held']}")


@endorse_app.command("synthetic")
def endorse_synthetic() -> None:
    """Render the synthetic endorsement library, extract, and score against ground truth."""
    from as_endorsed.endorse.pipeline import extract_synthetic_library

    rows = extract_synthetic_library()
    t = Table("endorsement", "expected op", "target", "matched", "status")
    for r in rows:
        exp = r["expected"]
        t.add_row(r["endorsement"], exp["op"], str(exp.get("target_path")), "[green]yes[/]" if r["matched"] else "[red]no[/]", r["status"] or "–")
    rprint(t)
    ok = sum(1 for r in rows if r["matched"])
    rprint(f"extraction accuracy on synthetic library: {ok}/{len(rows)} ({ok / len(rows):.0%})")
    if ok < len(rows):
        raise typer.Exit(1)


def resolve_cmd(
    as_of: str = typer.Option(None, help="Resolve as of this date (YYYY-MM-DD); default: each policy's term end"),
) -> None:
    """Apply attached endorsements to every synthetic account and write the resolved policies."""
    from as_endorsed.endorse.pipeline import resolve_all

    when = date.fromisoformat(as_of) if as_of else None
    resolved = resolve_all(as_of=when)
    n_ops = sum(len(r.applied_op_ids) + len(r.held) for r in resolved)
    n_unres = sum(len(r.unresolved) for r in resolved)
    n_held = sum(len(r.held) for r in resolved)
    n_conf = sum(len(r.conflicts) for r in resolved)
    n_changed = sum(len(r.changed) for r in resolved)
    with_ops = sum(1 for r in resolved if r.applied_op_ids)
    rprint(f"[green]✓[/] {len(resolved)} accounts resolved → {settings.resolved_dir}")
    rprint(f"   accounts with endorsements: {with_ops}   clauses changed/added: {n_changed}")
    rprint(f"   ops: {n_ops} total, {n_ops - n_unres - n_held} resolved, {n_unres} unresolved (attached as flagged siblings), {n_held} held")
    rprint(f"   same-date conflicts surfaced: {n_conf}")


def review_cmd() -> None:
    """List every held and unresolved op across extractions, and write review.md."""
    from as_endorsed.endorse.models import ExtractionResult

    lines = ["# Endorsement review queue", ""]
    t = Table("endorsement", "op", "status", "target as written", "why")
    n = 0
    for path in sorted(settings.endorse_dir.glob("*.json")):
        res = ExtractionResult.model_validate_json(path.read_text(encoding="utf-8"))
        for op in res.ops:
            if op.status == "resolved":
                continue
            n += 1
            why = "; ".join(op.notes)
            t.add_row(res.endorsement_form_id, op.op, _status(op.status), (op.target_ref or "–")[:40], why[:80])
            lines += [f"## {op.op_id}", f"- op: {op.op}", f"- status: {op.status}", f"- target as written: {op.target_ref or '–'}",
                      f"- directive: {op.directive}", f"- notes: {why}", f"- text: {(op.new_text or '')[:400]}", ""]
    rprint(t)
    settings.resolved_dir.mkdir(parents=True, exist_ok=True)
    out = settings.resolved_dir / "review.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    rprint(f"{n} ops need review → {out}")


def _status(s: str) -> str:
    return {"resolved": "[green]resolved[/]", "unresolved": "[yellow]unresolved[/]", "held": "[red]held[/]"}[s]
