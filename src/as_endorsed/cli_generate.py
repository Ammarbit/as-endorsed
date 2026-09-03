"""`as-endorsed ask` and `as-endorsed eval generate`."""

from __future__ import annotations

from datetime import date

import typer
from rich import print as rprint
from rich.panel import Panel

from as_endorsed.config import settings


def _generator(name: str):
    if name == "extractive":
        from as_endorsed.generate.extractive import ExtractiveGenerator

        return ExtractiveGenerator()
    if name == "claude":
        from as_endorsed.generate.llm import ClaudeGenerator, claude_available

        if not claude_available():
            rprint("[red]no Anthropic credentials found[/] (set ANTHROPIC_API_KEY); falling back to the extractive generator")
            from as_endorsed.generate.extractive import ExtractiveGenerator

            return ExtractiveGenerator()
        return ClaudeGenerator()
    raise typer.BadParameter(f"unknown generator {name}")


def ask_cmd(
    question: str = typer.Argument(...),
    account: str = typer.Option(..., "--account", "-a"),
    generator: str = typer.Option("claude", help="claude | extractive (claude falls back when no credentials are present)"),
    variant: str = typer.Option("header"),
    loop: bool = typer.Option(True, help="Allow one rewrite-and-retry when the generator cannot answer"),
    as_of: str = typer.Option(None, help="Resolve the policy as of this date (YYYY-MM-DD)"),
    embedder: str = typer.Option("bge"),
) -> None:
    """Answer one question about one account, with citations and the checks that ran."""
    from as_endorsed.eval.harness import _as_of_index, build_index, load_corpus
    from as_endorsed.generate.pipeline import GenConfig, Resources, answer_question
    from as_endorsed.retrieval.embed import make_embedder
    from as_endorsed.retrieval.rerank import make_reranker
    from as_endorsed.retrieval.router import route

    corpus = load_corpus()
    acct = corpus.by_id[account]
    corpus.accounts = [acct]
    emb = make_embedder(embedder)
    when = date.fromisoformat(as_of) if as_of else route(question).as_of
    index = _as_of_index(corpus, acct, variant, when, emb) if (when and variant in ("resolved", "header")) else build_index(corpus, variant, emb)
    res = Resources(index=index, embedder=emb, reranker=make_reranker(), base=corpus.base)
    ans = answer_question(question, acct, res, _generator(generator), GenConfig(loop=loop), as_of=when)
    if hasattr(emb, "flush"):
        emb.flush()
    colour = {"answered": "green", "abstain": "yellow", "withheld": "red"}[ans.status]
    body = ans.answer if ans.status == "answered" else f"{ans.status.upper()}: {ans.reason}"
    rprint(Panel(body, title=f"[{colour}]{ans.status}[/] · route={ans.route} · {ans.generator}" + (" · loop used" if ans.loop_used else ""), border_style=colour))
    for c in ans.citations:
        where = ", ".join(c.paths) or c.source
        lin = f"  [dim](as amended by {', '.join(c.lineage)})[/]" if c.lineage else ""
        rprint(f"  [bold]{where}[/]{lin}\n    [dim]{c.quote[:200]}[/]")
    rprint(f"[dim]checks={ans.checks} latency={ans.latency_ms:.0f}ms[/]")


def eval_generate_cmd(
    generator: str = typer.Option("extractive", help="extractive | claude"),
    rung: str = typer.Option("7d", help="Retrieval rung id from the ladder"),
    loop: bool = typer.Option(True),
    judge: bool = typer.Option(False, help="Score long-text answers with an LLM judge (needs credentials)"),
    accounts: int = typer.Option(None, help="Limit to the first N accounts"),
    embedder: str = typer.Option("bge"),
    reranker: str = typer.Option("minilm"),
) -> None:
    """Run the full answer pipeline over the ground-truth set and write data/eval/generation-*.{json,md}."""
    from as_endorsed.eval.generation import run

    gen = _generator(generator)
    judge_fn = None
    if judge:
        from as_endorsed.generate.llm import ClaudeGenerator, claude_available

        if claude_available():
            judge_fn = ClaudeGenerator().judge
        else:
            rprint("[yellow]no credentials; running without the LLM judge[/]")
    report = run(generator=gen, rung=rung, loop=loop, limit_accounts=accounts, judge=judge_fn, embedder_name=embedder, reranker_name=reranker,
                 log=lambda m: rprint(f"[dim]{m}[/]"))
    rprint()
    rprint(report.to_markdown())
    rprint(f"\n→ {settings.data_dir / 'eval'}")
