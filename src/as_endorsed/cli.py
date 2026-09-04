from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import typer
from rich import print as rprint
from rich.table import Table

from as_endorsed.cli_endorse import endorse_app, resolve_cmd, review_cmd
from as_endorsed.cli_generate import ask_cmd, eval_generate_cmd
from as_endorsed.cli_retrieval import eval_app, search_cmd
from as_endorsed.config import settings
from as_endorsed.corpus import registry

app = typer.Typer(no_args_is_help=True, help="As-Endorsed: policy ingestion, synthetic data, and evaluation.")
corpus_app = typer.Typer(no_args_is_help=True, help="Fetch and list the public form corpus.")
synth_app = typer.Typer(no_args_is_help=True, help="Generate synthetic accounts and declarations.")
app.add_typer(corpus_app, name="corpus")
app.add_typer(synth_app, name="synth")
app.add_typer(endorse_app, name="endorse")
app.command("resolve")(resolve_cmd)
app.command("review")(review_cmd)
app.command("search")(search_cmd)
app.command("ask")(ask_cmd)
eval_app.command("generate")(eval_generate_cmd)
app.add_typer(eval_app, name="eval")


@corpus_app.command("list")
def corpus_list() -> None:
    t = Table("key", "kind", "title", "parse", "local")
    for f in registry.FORMS:
        local = settings.raw_dir / f.filename
        t.add_row(f.key, f.kind, f.title, "yes" if f.parse_supported else "no", "✓" if local.exists() else "–")
    rprint(t)


@corpus_app.command("download")
def corpus_download(force: bool = typer.Option(False, help="Re-download even if present")) -> None:
    for f in registry.FORMS:
        dest = registry.download(f, settings.raw_dir, force=force)
        rprint(f"[green]✓[/] {f.key:32} {dest.relative_to(settings.data_dir)}  sha256={registry.sha256(dest)[:12]}")


@app.command("parse")
def parse(
    form: str = typer.Argument(None, help="Registry key, e.g. NFIP-DWELLING@2021-10"),
    pdf: Path = typer.Option(None, help="Parse an arbitrary PDF instead of a registry form"),
    form_id: str = typer.Option(None, help="Required with --pdf"),
    edition: str = typer.Option(None, help="Required with --pdf"),
    all_forms: bool = typer.Option(False, "--all", help="Parse every registry form that supports parsing"),
    out: Path = typer.Option(None, help="Output directory (default: data/parsed)"),
) -> None:
    """Parse a form PDF into a clause tree (JSON + outline)."""
    from as_endorsed.ingest.clauses import outline, parse_form

    out = out or settings.parsed_dir
    out.mkdir(parents=True, exist_ok=True)

    jobs: list[tuple[Path, str, str, str]] = []
    if pdf:
        if not (form_id and edition):
            raise typer.BadParameter("--pdf needs --form-id and --edition")
        jobs.append((pdf, form_id, edition, pdf.stem))
    elif all_forms:
        for f in registry.FORMS:
            if f.parse_supported:
                jobs.append((settings.raw_dir / f.filename, f.form_id, f.edition, f.title))
    elif form:
        f = registry.get(form)
        if not f.parse_supported:
            raise typer.BadParameter(f"{f.key}: {f.note}")
        jobs.append((settings.raw_dir / f.filename, f.form_id, f.edition, f.title))
    else:
        raise typer.BadParameter("give a registry key, --pdf, or --all")

    for path, fid, ed, title in jobs:
        if not path.exists():
            rprint(f"[red]missing[/] {path}. Run: as-endorsed corpus download")
            raise typer.Exit(1)
        parsed = parse_form(path, form_id=fid, edition=ed, title=title)
        stem = f"{fid}@{ed}"
        (out / f"{stem}.json").write_text(parsed.model_dump_json(indent=2), encoding="utf-8")
        (out / f"{stem}.outline.md").write_text(outline(parsed), encoding="utf-8")
        levels = Counter(c.level for c in parsed.clauses)
        sections = [c.heading or c.text[:40] for c in parsed.clauses if c.level == 0]
        rprint(f"[bold]{stem}[/]  pages={parsed.pages}  clauses={len(parsed.clauses)}  "
               f"by level={dict(sorted(levels.items()))}  warnings={len(parsed.warnings)}")
        for s in sections:
            rprint(f"   {s}")
        for w in parsed.warnings[:8]:
            rprint(f"   [yellow]warn[/] {w}")
        if len(parsed.warnings) > 8:
            rprint(f"   [yellow]… {len(parsed.warnings) - 8} more in {stem}.outline.md[/]")
        rprint(f"   → {out / (stem + '.json')}")


@synth_app.command("accounts")
def synth_accounts(
    n: int = typer.Option(40, "--count", "-n", help="Number of accounts"),
    seed: int = typer.Option(20260903, help="Master seed; same seed, same accounts"),
    out: Path = typer.Option(None, help="Output directory (default: data/synthetic)"),
    no_pdf: bool = typer.Option(False, help="Skip PDF rendering"),
) -> None:
    """Generate synthetic accounts: declarations JSON, rendered PDFs, and ground-truth Q&A."""
    from as_endorsed.synth.accounts import generate_accounts
    from as_endorsed.synth.endorsements import render_library
    from as_endorsed.synth.qa import endorsement_questions, questions_for
    from as_endorsed.synth.render import render_declarations

    out = out or settings.synthetic_dir
    (out / "accounts").mkdir(parents=True, exist_ok=True)
    accounts = generate_accounts(n, seed=seed)
    qa_path = out / "qa.jsonl"
    n_qa: Counter[str] = Counter()
    with qa_path.open("w", encoding="utf-8") as qa:
        for acct in accounts:
            (out / "accounts" / f"{acct.account_id}.json").write_text(acct.model_dump_json(indent=2), encoding="utf-8")
            if not no_pdf:
                render_declarations(acct, out / "accounts" / f"{acct.account_id}.pdf")
            for q in questions_for(acct) + endorsement_questions(acct):
                qa.write(json.dumps(q, default=str) + "\n")
                n_qa[f"{q['category']}/{q['difficulty']}"] += 1
    (out / "accounts.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in accounts], indent=2), encoding="utf-8"
    )
    if not no_pdf:
        render_library(out / "endorsements")
    with_changes = sum(1 for a in accounts if a.policy.endorsements)
    with_forms = sum(1 for a in accounts if a.policy.endorsement_forms)
    rprint(f"[green]✓[/] {len(accounts)} accounts ({with_changes} with mid-term changes, {with_forms} with endorsement forms) → {out}")
    rprint(f"   Q&A rows: {sum(n_qa.values())}  " + "  ".join(f"{k}={v}" for k, v in sorted(n_qa.items())))


@app.command("bootstrap")
def bootstrap(force: bool = typer.Option(False, help="Regenerate even if outputs exist")) -> None:
    """Everything a fresh checkout needs before `uvicorn as_endorsed.api:app`: corpus, parses,
    synthetic accounts, endorsement extraction, resolution, and warm models."""
    from as_endorsed.endorse.pipeline import extract_registry_endorsement, extract_synthetic_library, resolve_all

    rprint("[bold]1/6[/] corpus"); corpus_download()
    if force or not (settings.parsed_dir / "NFIP-DWELLING@2021-10.json").exists():
        rprint("[bold]2/6[/] parse"); parse(None, None, None, None, True, None)
    if force or not (settings.synthetic_dir / "accounts.json").exists():
        rprint("[bold]3/6[/] synthetic accounts"); synth_accounts(40, 20260903, None, False)
    if force or not (settings.endorse_dir / "SYN-END-01@2026-01.json").exists():
        rprint("[bold]4/6[/] endorsement extraction")
        extract_synthetic_library()
        for spec in registry.FORMS:
            if spec.kind == "endorsement" and (settings.raw_dir / spec.filename).exists():
                extract_registry_endorsement(spec)
    if force or not any(settings.resolved_dir.glob("SYN-*.json")):
        rprint("[bold]5/6[/] resolve"); resolve_all()
    rprint("[bold]6/6[/] build the search index (so a cold start does not have to)")
    from as_endorsed.eval.harness import build_index, load_corpus
    from as_endorsed.retrieval.embed import make_embedder
    from as_endorsed.retrieval.rerank import make_reranker
    from as_endorsed.retrieval.store import load_index, save_index

    embedder = make_embedder(settings.embedder)
    if settings.reranker != "none":
        make_reranker(settings.reranker).score("warm", ["warm"])  # pull the reranker weights too
    if force or load_index("header", embedder.name) is None:
        index = build_index(load_corpus(parse_endorsements=False), "header", embedder)
        if hasattr(embedder, "flush"):
            embedder.flush()
        path = save_index(index, embedder.name)
        rprint(f"   {len(index.chunks):,} chunks → {path}")
    rprint("[green]✓[/] ready: uvicorn as_endorsed.api:app --port 8000")


if __name__ == "__main__":
    app()
