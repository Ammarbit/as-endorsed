# As-Endorsed

Retrieval over insurance policies **as they currently read**, not as the base form was printed.

A policy is a declarations page, one or more base coverage forms, and a schedule of endorsements that replace, delete, or add clauses. Flat-chunk RAG retrieves whichever version of a clause scores highest. This system resolves the endorsement stack at ingest and answers from the resolved policy, citing the clause, the form it lives in, and the endorsement that last changed it.

> Status: **milestone 2 of 5** (corpus, clause parser, synthetic accounts, endorsement engine). See [Roadmap](#roadmap).

## What works today

```bash
python -m venv .venv && .venv/Scripts/activate      # or source .venv/bin/activate
pip install -e ".[dev]"

as-endorsed corpus download        # FEMA flood forms (public domain) + TWIA policy and endorsements
as-endorsed parse --all            # clause trees → data/parsed/*.json + outline.md
as-endorsed synth accounts -n 40   # declarations + endorsement PDFs + ground-truth Q&A
as-endorsed endorse synthetic      # extract ops from the synthetic library, score vs ground truth
as-endorsed endorse extract --all  # extract ops from the real TWIA endorsements
as-endorsed resolve                # apply attached endorsements to every account
as-endorsed review                 # held and unresolved ops → data/resolved/review.md
pytest
```

### Clause parser

`as_endorsed.ingest` turns a numbered policy form PDF into a clause tree with stable IDs, no LLM involved.

| Form | Numbering style | Pages | Clauses | Parser warnings |
|---|---|---:|---:|---:|
| NFIP Dwelling Form, F-122 (Oct 2021) | Roman sections, I.A.1.a.(1).(a).(i) | 32 | 498 | 0 |
| NFIP General Property Form, F-123 (Oct 2021) | Roman sections | 29 | 470 | 0 |
| TWIA Dwelling Policy (Aug 2023) | Word headings (CONDITIONS.4.a.(5)), quoted-term definitions | 17 | 255 | 4 |

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
- **Two numbering styles.** Roman-numeral sections and word headings (`CONDITIONS`, `COVERAGE A (Dwelling)`) both become sections; unlabeled text under a section is split into paragraph clauses on vertical gaps, so a definitions section written as quoted-term paragraphs still yields one clause per term.
- **Failures are loud.** Anything the parser rejects is listed in `warnings`, and the test suite asserts the list is empty for the NFIP forms.

### Synthetic accounts

Real forms, synthetic declarations. `as_endorsed.synth` generates NFIP flood accounts deterministically from a seed: declarations, coverages within NFIP maximums, and for about forty percent of accounts a mid-term **General Change Endorsement** that alters a limit or deductible as of an effective date. Each account renders to a PDF that goes through the same ingestion path as real documents.

Accounts also attach zero to three endorsements from a synthetic library written against the real NFIP form (see below). `data/synthetic/qa.jsonl` holds 636 templated ground-truth rows: the declarations category (lookups, as-of questions, unanswerables) and the endorsement-resolved category, whose answers differ depending on which endorsements are attached and, for mid-term attachments, on the as-of date.

### Endorsement engine

`as_endorsed.endorse` turns endorsement prose into operations against the base form's clause tree and applies them in precedence order. This is the part no flat-chunk RAG has.

**Extraction** is rule-based first. The industry idiom is formulaic ("Condition 4.a.(5) is replaced by the following:", "The following section c. is added to Loss Settlement Condition 6.:", "Paragraph IV.14 is deleted."), so directives are matched sentence by sentence and the restated text after each directive is captured with the label structure the endorsement gave it. Targets are resolved deterministically against the clause tree by explicit path, by section name plus path, by heading words, or by defined term. Nothing is guessed: what the rules cannot place becomes an **unresolved** op (applied as a flagged sibling) or a **held** op (not applied; listed for review). An LLM extractor (`pip install -e ".[llm]"`, `--llm`) is wired in for text the rules cannot read, and its proposals go through the same resolver.

| Corpus | Ops | Resolved | Unresolved | Held | Note |
|---|---:|---:|---:|---:|---|
| Synthetic library (8 endorsements, ground truth known) | 8 | 6 | 1 | 1 | 8 of 8 expected ops extracted exactly; the unresolved one names no clause by design, the held one has schedule blanks |
| TWIA endorsements (11 real forms) | 20 | 17 | 0 | 3 | Held: a scanned PDF, a notice page, and an "It is agreed that" clause with no target |

**Resolution** applies REPLACE, DELETE, ADD, AMEND_DEF and schedule-filled ops per account. An endorsement controls over the base form; between endorsements the later effective date controls; same-date changes to the same clause are applied in schedule order and recorded as a conflict with both texts. Every changed clause keeps its original text and a lineage of the ops that touched it, and resolution can be run as of any date. Across the 40 synthetic accounts: 73 ops, 66 resolved, 7 unresolved, 0 held once schedule values are supplied, 64 clauses changed or added.

The scanned TWIA form is a real held case: there is no text layer, so the op is held with that reason rather than silently skipped. OCR is the milestone-three fix.

## Repository layout

```
src/as_endorsed/
  corpus/registry.py     public form registry + downloader (FEMA, TWIA)
  ingest/pdf.py          PDF → ordered lines with coordinates, header/footer strip
  ingest/clauses.py      clause tree parser (Roman and word-heading styles, paragraphs)
  endorse/refs.py        clause reference → path resolver
  endorse/extract.py     rule-based op extraction
  endorse/llm.py         optional LLM extraction (same resolver, same schema)
  endorse/resolve.py     precedence resolver, as-of materialisation
  endorse/pipeline.py    data/ plumbing shared by CLI and tests
  synth/accounts.py      synthetic account generator
  synth/endorsements.py  synthetic endorsement library with ground truth
  synth/render.py        declarations + change endorsement PDF renderer
  synth/qa.py            ground-truth question templates
  models.py              shared pydantic records (Clause, ParsedForm, ...)
  cli.py, cli_endorse.py `as-endorsed` entry point
tests/                   parser and engine tests run against the real forms
data/                  raw, parsed, synthetic (gitignored; regenerate with the CLI)
```

## Licensing

Only public-domain or openly published forms are in the registry. FEMA's Standard Flood Insurance Policy forms are US Government works. The Texas Windstorm Insurance Association publishes its dwelling policy and endorsements openly on twia.org; they are downloaded by script and not committed. Forms owned by Insurance Services Office (ISO) are copyrighted and are never committed to this repository. Citizens Florida keeps its forms behind an agent login and is therefore not a corpus source.

## Roadmap

1. **Corpus and parser** (done): NFIP forms, clause tree, synthetic accounts, ground-truth Q&A.
2. **Endorsement engine** (done): operation extraction, target validation against the clause tree, precedence resolution, held-ops review list, TWIA policy and endorsement library, endorsement-resolved ground truth.
3. **Retrieval ladder**: declarations router, hybrid dense + BM25 with reciprocal rank fusion, cross-encoder rerank, definition pull-in. Postgres + pgvector (`docker compose up db`). Eval harness with the full ablation table.
4. **Generation**: span-level citations, groundedness check, numeric guard, abstention, one bounded retrieve-again loop.
5. **Ship**: Next.js UI with PDF highlight, Docker, public deployment, README led by the numbers.

## Boundaries

The system reports what a policy says and where. It does not make coverage determinations, and it does not interpret ambiguity. Where two endorsements conflict beyond what the precedence rules resolve, both texts are surfaced as a conflict.
