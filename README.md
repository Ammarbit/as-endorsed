# As-Endorsed

Retrieval over insurance policies **as they currently read**, not as the base form was printed.

A policy is a declarations page, one or more base coverage forms, and a schedule of endorsements that replace, delete, or add clauses. Flat-chunk RAG retrieves whichever version of a clause scores highest. This system resolves the endorsement stack at ingest and answers from the resolved policy, citing the clause, the form it lives in, and the endorsement that last changed it.

> Status: **milestone 1 of 5** (corpus, clause parser, synthetic accounts). See [Roadmap](#roadmap).

## What works today

```bash
python -m venv .venv && .venv/Scripts/activate      # or source .venv/bin/activate
pip install -e ".[dev]"

as-endorsed corpus download        # public-domain FEMA flood forms
as-endorsed parse --all            # clause trees → data/parsed/*.json + outline.md
as-endorsed synth accounts -n 40   # declarations JSON + PDFs + ground-truth Q&A
pytest
```

### Clause parser

`as_endorsed.ingest` turns a numbered policy form PDF into a clause tree with stable IDs, no LLM involved.

| Form | Pages | Clauses | Parser warnings |
|---|---:|---:|---:|
| NFIP Dwelling Form, F-122 (Oct 2021) | 32 | 493 | 0 |
| NFIP General Property Form, F-123 (Oct 2021) | 29 | 465 | 0 |

Each clause carries a path in the form's own numbering (`II.C.6.b`), its parent, its own text (children are separate clauses), the defined term where it is a definition, and page plus bounding boxes for citation highlighting. Example record:

```json
{
  "clause_id": "NFIP-DWELLING@2021-10:II.C.5",
  "path": "II.C.5",
  "parent_path": "II.C",
  "term": "Basement",
  "text": "Basement. Any area of a building, including any sunken room or sunken portion of a room, having its floor below ground level on all sides.",
  "page_start": 4,
  "bboxes": [{"page": 4, "x0": 58.3, "y0": 307.3, "x1": 299.9, "y1": 346.3}]
}
```

How it stays honest:

- **Reading order is layout-derived.** Two-column pages with centred full-width headings are read band by band, left column then right. Header and footer lines are removed by repetition across pages, not by a form-specific pattern.
- **A label opens a clause only if it is the expected next label at its level.** `2.` inside running text never opens a bogus clause because the parser was expecting `B.`.
- **Continuation lines attach by indentation.** Wrapped text sits one step deeper than its label, so trailing text after a clause's children lands on the parent, not on the last child.
- **Failures are loud.** Anything the parser rejects is listed in `warnings`, and the test suite asserts the list is empty for the reference forms.

### Synthetic accounts

Real forms, synthetic declarations. `as_endorsed.synth` generates NFIP flood accounts deterministically from a seed: declarations, coverages within NFIP maximums, and for about forty percent of accounts a mid-term **General Change Endorsement** that alters a limit or deductible as of an effective date. Each account renders to a PDF that goes through the same ingestion path as real documents.

`data/synthetic/qa.jsonl` holds templated ground-truth questions with exact answers, including as-of questions whose answer depends on which side of the change date you ask about. This is the auto-labeled declarations category of the golden eval set.

## Repository layout

```
src/as_endorsed/
  corpus/registry.py   public form registry + downloader
  ingest/pdf.py        PDF → ordered lines with coordinates, header/footer strip
  ingest/clauses.py    clause tree parser
  synth/accounts.py    synthetic account generator
  synth/render.py      declarations + endorsement PDF renderer
  synth/qa.py          ground-truth question templates
  models.py            shared pydantic records (Clause, ParsedForm, ...)
  cli.py               `as-endorsed` entry point
tests/                 parser tests run against the real FEMA form
data/                  raw, parsed, synthetic (gitignored; regenerate with the CLI)
```

## Licensing

Only public-domain or freely published forms are in the registry. FEMA's Standard Flood Insurance Policy forms are US Government works. Forms owned by Insurance Services Office (ISO) are copyrighted and are never committed to this repository; the corpus README will point to public regulatory filings for local testing only.

## Roadmap

1. **Corpus and parser** (done): NFIP forms, clause tree, synthetic accounts, ground-truth Q&A.
2. **Endorsement engine**: operation extraction (replace / delete / add / amend definition / schedule fill), target validation against the clause tree, precedence resolution, held-ops review list. Citizens Florida homeowners forms and endorsement library join the corpus here.
3. **Retrieval ladder**: declarations router, hybrid dense + BM25 with reciprocal rank fusion, cross-encoder rerank, definition pull-in. Postgres + pgvector (`docker compose up db`). Eval harness with the full ablation table.
4. **Generation**: span-level citations, groundedness check, numeric guard, abstention, one bounded retrieve-again loop.
5. **Ship**: Next.js UI with PDF highlight, Docker, public deployment, README led by the numbers.

## Boundaries

The system reports what a policy says and where. It does not make coverage determinations, and it does not interpret ambiguity. Where two endorsements conflict beyond what the precedence rules resolve, both texts are surfaced as a conflict.
