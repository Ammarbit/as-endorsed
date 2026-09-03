"""`as-endorsed search` and `as-endorsed eval ...`: the retrieval ladder commands."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import typer
from rich import print as rprint
from rich.table import Table

from as_endorsed.config import settings

eval_app = typer.Typer(no_args_is_help=True, help="Run the ablation ladder and print the results table.")


def search_cmd(
    question: str = typer.Argument(..., help="The question"),
    account: str = typer.Option(..., "--account", "-a", help="Account id, e.g. SYN-00001"),
    variant: str = typer.Option("header", help="fixed | recursive | clause | resolved | header"),
    mode: str = typer.Option("hybrid", help="dense | bm25 | hybrid"),
    rerank: bool = typer.Option(True, help="Cross-encoder rerank of the fused candidates"),
    definitions: bool = typer.Option(True, help="Pull in definitions of defined terms in the top hits"),
    as_of: str = typer.Option(None, help="Resolve the policy as of this date (YYYY-MM-DD)"),
    k: int = typer.Option(5),
    embedder: str = typer.Option("bge", help="bge | hash"),
) -> None:
    """Route a question; answer declarations facts from the record, everything else by retrieval."""
    from as_endorsed.eval.harness import _as_of_index, build_index, load_corpus
    from as_endorsed.retrieval.embed import make_embedder
    from as_endorsed.retrieval.index import SearchConfig, search
    from as_endorsed.retrieval.rerank import make_reranker
    from as_endorsed.retrieval.router import answer_declarations, retrieval_query, route

    corpus = load_corpus()
    acct = corpus.by_id[account]
    rt = route(question)
    when = date.fromisoformat(as_of) if as_of else rt.as_of
    rprint(f"[dim]route={rt.kind} field={rt.field} as_of={when}[/]")
    if rt.kind in ("declarations", "mixed") and rt.field:
        ans = answer_declarations(acct, rt.field, when)
        if ans:
            rprint(f"[bold]Declarations:[/] {ans.value}   [dim]({ans.citation})[/]")
        if rt.kind == "declarations":
            return
    emb = make_embedder(embedder)
    corpus.accounts = [acct]
    index = _as_of_index(corpus, acct, variant, when, emb) if (when and variant in ("resolved", "header")) else build_index(corpus, variant, emb)
    cfg = SearchConfig(mode=mode, rerank=rerank, k=k, pull_definitions=definitions)
    hits = search(index, emb, retrieval_query(question), account, cfg, make_reranker() if rerank else None, corpus.base)
    if hasattr(emb, "flush"):
        emb.flush()
    t = Table("#", "via", "paths", "lineage", "text", show_lines=True)
    for h in hits:
        t.add_row(str(h.rank), h.via, ", ".join(h.chunk.paths) or h.chunk.kind, ", ".join(h.chunk.lineage), (h.chunk.header + "\n" if h.chunk.header else "") + h.chunk.text[:360])
    rprint(t)


@eval_app.command("run")
def eval_run(
    rungs: str = typer.Option("all", help="Comma-separated rung ids, e.g. 3,6,7"),
    embedder: str = typer.Option("bge", help="bge | hash (hash is a feature-hashing stand-in, not a semantic model)"),
    reranker: str = typer.Option("minilm", help="minilm | bge | none"),
    k: int = typer.Option(5),
    accounts: int = typer.Option(None, help="Limit to the first N accounts"),
) -> None:
    """Build every variant's index, run the ladder, write data/eval/results.{json,md}."""
    from as_endorsed.eval.harness import RUNGS, run

    chosen = RUNGS if rungs == "all" else [r for r in RUNGS if r.id in {x.strip() for x in rungs.split(",")}]
    report = run(chosen, embedder_name=embedder, reranker_name=reranker, k=k, limit_accounts=accounts, log=lambda m: rprint(f"[dim]{m}[/]"))
    rprint()
    rprint(report.to_markdown())
    rprint(f"\n→ {settings.data_dir / 'eval' / 'results.md'}")


@eval_app.command("table")
def eval_table() -> None:
    """Print the last results table."""
    p = settings.data_dir / "eval" / "results.md"
    if not p.exists():
        rprint("[red]no results yet[/]; run: as-endorsed eval run")
        raise typer.Exit(1)
    rprint(p.read_text(encoding="utf-8"))
