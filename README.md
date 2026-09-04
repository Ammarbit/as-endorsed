# As-Endorsed

**Question answering over insurance policies that reads them as they currently stand, not as the base form was printed.**

> **Live demo:** https://as-endorsed.wittybay-fdf1bbec.germanywestcentral.azurecontainerapps.io
> It scales to zero when idle, so the first request after a quiet spell waits about 25 seconds for the platform to start a container. The page shows a counter while that happens. Everything after it is immediate: the search index is built into the image, so the application itself starts in around two seconds.

![A cited answer with the clause boxed on the real FEMA form](docs/screenshot.png)

## The problem this solves

An insurance policy is not one document. It is a base form, identical for thousands of customers, plus a stack of endorsements that amend it for one customer. An endorsement says things like *"Paragraph 14 is deleted"*, *"Condition 4.a.(5) is replaced by the following"*, or *"the following exclusion is added"*.

So the words printed in the base form are frequently not what a given policy says any more.

Every document assistant built the usual way gets this wrong. It cuts all the documents into equal chunks, finds the chunks whose wording resembles the question, and hands them to a language model. The printed exclusion and the endorsement that deleted it are two separate chunks in the same pile, and nothing tells the system which one is in force. Ask *"does this policy exclude hot tubs?"* and it will confidently quote an exclusion that was removed a year ago.

That is a wrong answer about money, delivered with a citation, which is worse than no answer at all.

**This project resolves the endorsement stack before anyone asks a question.** It parses the base form into individual clauses, works out what each endorsement actually changes, rewrites the policy so it reads correctly for that customer on a given date, and keeps the original wording and the amendment history beside it. Answers then come from the resolved policy and cite the exact clause, highlighted on the real PDF page.

## See it working

Open the [live demo](https://as-endorsed.wittybay-fdf1bbec.germanywestcentral.azurecontainerapps.io), pick account **SYN-00001**, and try these in order.

| Ask this | What to look for |
|---|---|
| *Does the policy exclude hot tubs, spas and swimming pools?* | The base form excludes them. The answer says the clause was **deleted by an endorsement**, and boxes it on page 14 of the FEMA form with the printed text struck through beside the current text. |
| *What is the most the policy will pay for sandbags and labor to protect the building from flood?* | The printed form says $1,000. On an account carrying the loss-avoidance endorsement the answer is the amended figure, and the amount is refused unless it appears in the cited clause. |
| *How does the policy define 'basement' as of 2026-09-19?* | Two endorsements amend that definition on different dates. Asking as of a past date returns the wording that was in force **then**, not the current one. |
| *What is the building deductible on policy NFP-2026-1725448?* | Facts like limits and deductibles are read from the declarations record rather than searched for, and are exact. |

The other tabs show every clause the account's endorsements changed, the queue of endorsement instructions the system refused to apply on its own with the reason for each, and the full evaluation tables.

## The data it was tested on

The single most important thing to understand before reading the results.

### Real documents

Every base policy form and every real endorsement is a public document, downloaded by script and parsed by the same code path.

| Source | What | Role in the results |
|---|---|---|
| FEMA, NFIP Dwelling Form F-122 (Oct 2021) | 32 pages, 498 clauses | The base form behind all 40 accounts |
| FEMA, NFIP General Property Form F-123 (Oct 2021) | 29 pages, 470 clauses | Second form, proves the parser is not tuned to one document |
| TWIA Dwelling Policy (Aug 2023) | 17 pages, 255 clauses | A different numbering convention entirely |
| 11 TWIA endorsements | Real published amendments | The amendment-extraction result: 17 of 20 instructions resolved, 3 held |

### Synthetic documents, and why

| | Count | Why it is synthetic |
|---|---:|---|
| Accounts, each with a declarations page rendered to PDF | 40 | Real declarations pages are customer records. Generating them from a seed gives exact ground truth and no personal data |
| Endorsements attached to those accounts | 8 | No public corpus exists of real policies with their endorsement schedules attached, because that pairing is private customer data |
| Mid-term change endorsements | 17 accounts | The real NFIP mechanism for changing a limit mid-term, which is what makes as-of questions meaningful |

Thirty of the 40 accounts carry at least one endorsement, ranging from one to five.

### The questions

636 questions, all with answers known by construction rather than by annotation. They come from **37 templates** applied across the 40 accounts, not 636 independently written questions.

| Category | Questions | Templates | How the answer is known |
|---|---:|---:|---|
| Declarations lookups and as-of | 447 | 22 | Read directly from the generated account record |
| Endorsement-resolved | 189 | 15 | Written per endorsement with both answers: the one that applies when it is attached, and the one that applies when it is not |

### What this establishes, and what it does not

**The parser result is real.** It runs on genuine published forms with two different numbering conventions, and the warnings list is asserted empty in the test suite.

**The amendment-extraction result is real.** 17 of 20 instructions resolved across 11 genuinely published TWIA endorsements, with the failures held and named rather than hidden.

**The headline retrieval result, 52% against 89%, is measured on synthetic endorsements.** They are written in the industry's own idiom and were checked against the real TWIA wording, so the mechanism being tested is real. But the comparison itself has not been run against real policies with real endorsements attached, because that data is private. This is the honest limit of the number, and it is the first thing to say if someone asks what would make it stronger.

**The question set is narrower than 636 suggests.** Thirty-seven templates across forty accounts tests whether the system handles the same question shapes over varying data. It does not test paraphrase robustness, adversarial phrasing, or the questions a real broker would actually type. A held-out set written by someone other than the author would be the natural next step.

**Nothing is hand-annotated,** so no labelling error is possible, and equally nothing captures how a real user asks.

## Results

Forty synthetic accounts built on real public forms, 636 questions with known correct answers, measured on an ordinary CPU. The live demo runs this exact configuration: no hosted model, no API key, so what you can click is what was measured. Read [The data it was tested on](#the-data-it-was-tested-on) first: the retrieval comparison below runs on synthetic endorsements, and that is the honest limit of it.

Every table below is produced by `as-endorsed eval run` and `as-endorsed eval generate`, and the output files record the commit and the time they were measured. That does not stop a number from going stale; it stops one from lying about its age. Catching a stale number still means re-running and comparing.

These were measured on 2026-09-04 at commit `8c32eb1`. Re-running after later code changes produced identical figures, which confirms those changes did not touch retrieval. The pipeline is deterministic, so that is a check that nothing drifted rather than evidence of robustness.

The two kinds of measurement are kept apart on purpose, because they answer different questions.

### Retrieval: was the currently correct clause found?

| Measurement, top 5 results | Result |
|---|---:|
| Clause questions where a retrieved chunk carried the currently correct text, conventional chunking | 52% |
| Same questions, same search engine and reranker, index holding the policy **as endorsed** | **89%** |
| Questions about a clause an endorsement had amended, as-endorsed index | **100%** |

Row two against row one is the whole argument. Same search engine, same reranker, same questions. The only difference is that the index holds the policy as it currently reads.

**These are retrieval figures.** They say the right clause was put in front of the generator. They do not say the final answer was worded correctly, and "89% accurate" would be an overclaim.

### End to end: was the answer right?

| Measurement, full pipeline | Result |
|---|---:|
| Limits, deductibles, dates and identifiers, exact match, n=447 | **100%** |
| Money and short-text answers, exact match, n=433 | **100%** |
| Long-form answers, lexical similarity to the reference, n=199 | 62.3% |
| First citation points at an expected clause | 64.6% |

**What the money result does and does not prove.** The extractive generator returns the cited clause verbatim, so any amount it reports came from that clause by construction. The figure shows the pipeline does not corrupt a number in transit. It does **not** demonstrate that the numeric guard works, because on this path the guard has nothing to catch.

The guard is evidenced separately and narrowly. Two scenarios are unit-tested against a stub client on the hosted-generator path: a fabricated $9,999 that does not appear in the cited clause, and a claim the cited clause does not support. Both are withheld. That is a correct test of the guard logic and it is the whole of the evidence. **The system has not been red-teamed,** and two hand-picked hallucination patterns are not a coverage claim.

Separately, the amendment engine resolved 17 of 20 instructions across 11 real published endorsements, holding the other 3 for review with a stated reason rather than guessing.

### What these numbers do not cover

Worth saying before anyone asks.

- **Every figure above came from the extractive generator,** which returns the cited clause verbatim instead of writing prose. The hosted-model generator is implemented and covered by tests against a stub client, but it has never been run against the evaluation set. The quality of generated prose is therefore unmeasured. Running `as-endorsed eval generate --generator claude --judge` with an API key fills that gap, and the results will be published here when it is.
- **Long-form correctness is the weakest number here,** and 62.3% is a lexical proxy rather than a judgement of meaning. That is precisely the number a hosted model should improve, and precisely the claim I cannot yet make.
- **Retrieval is measured far more rigorously than generation.** That is an honest description of where the engineering effort went.
- **When the hosted-model evaluation is run, its long-form score will be graded by a language model** comparing the answer against a reference, not by exact match. That is a legitimate method and a weaker kind of evidence than the exact-match figures above. It will be labelled as such when published, not quoted as though it were the same currency.

Full tables, the metric definitions, and the changes that did **not** improve anything are in [How it works](#how-it-works). Nothing measured is hidden.

### Why the first request is slow

The container scales to zero when nobody is using it. That is a deliberate cost decision: keeping one warm around the clock would cost roughly $33 to $72 a month, which a portfolio demo does not justify.

What it causes, in order of how likely you are to notice:

- The first request after an idle spell waits about **25 seconds** while the platform schedules and starts a container. The page shows a counter so it does not look broken.
- Everything after that is immediate, and the container stays warm through about five minutes of inactivity.
- There is a single replica by design, so two visitors arriving during a cold start both wait. That same cap is what makes the hosting bill bounded.
- It used to be far worse. A build bug meant every container start re-embedded all 20,102 clauses, so a cold request took **145 seconds**. The search index is now built into the image, and the application starts in about two seconds. The remaining wait is platform scheduling, not this code.

Removing the wait entirely means paying for an always-on instance. [`deploy/README.md`](deploy/README.md) has the commands.

## What this demonstrates

If you are evaluating me for a document or retrieval project, this is what it is evidence of.

- **Retrieval systems that are correct, not just plausible.** Hybrid search, reciprocal rank fusion, cross-encoder reranking, and a router that answers structured facts from records instead of guessing at text.
- **Getting real structure out of real PDFs.** A parser that turns two-column legal forms into an addressable clause tree with page coordinates, handling two different numbering conventions, with zero unexplained failures on the reference forms.
- **Domain logic that generic tools miss.** The amendment engine is the difference between a demo and something a broker could use. Most of the value in a document project lives in this kind of domain rule, not in the model.
- **Evaluation before claims.** A ground-truth set, an ablation ladder where each rung adds one thing, and published numbers including the changes that did nothing.
- **Systems that refuse to be wrong.** Claim-level citations, a groundedness check, a numeric guard, and abstention as a valid answer.
- **Shipping.** Tested, containerised, continuously built, deployed, security-reviewed, documented.

The same architecture transfers directly to contracts with amendments, regulatory documents with revisions, technical specifications with addenda, and any corpus where the current text differs from the printed text.

## How it works

Models run in-process through ONNX (`fastembed`): BAAI/bge-small-en-v1.5 for embeddings, a MiniLM cross-encoder for reranking. No API key is needed for anything up to and including the evaluation. A hosted model is optional and only writes the final prose.

### Clause parser

`as_endorsed.ingest` turns a numbered policy form into a clause tree with stable identifiers. No language model is involved.

| Form | Numbering style | Pages | Clauses | Parser warnings |
|---|---|---:|---:|---:|
| NFIP Dwelling Form, F-122 (Oct 2021) | Roman sections, I.A.1.a.(1).(a).(i) | 32 | 498 | 0 |
| NFIP General Property Form, F-123 (Oct 2021) | Roman sections | 29 | 470 | 0 |
| TWIA Dwelling Policy (Aug 2023) | Word headings, quoted-term definitions | 17 | 255 | 4 |

Each clause carries its path in the form's own numbering, its parent, its own text, the defined term where it is a definition, and the page and bounding boxes used to highlight it:

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

- **Reading order comes from the page layout.** Two-column pages with centred full-width headings are read band by band. Running headers and footers are dropped because they repeat across pages, not because of a form-specific rule.
- **A label opens a clause only if it is the expected next label at its level,** so a `2.` inside running text never opens a bogus clause.
- **Continuation lines attach by indentation,** so trailing text after a clause's children lands on the parent rather than the last child.
- **Two numbering conventions are supported,** and unlabeled text under a section is split into paragraph clauses on vertical gaps, so a definitions section written as quoted-term paragraphs still yields one clause per term.
- **Failures are loud.** Anything rejected is listed in `warnings`, and the test suite asserts that list is empty for the reference forms.

### Endorsement engine

`as_endorsed.endorse` turns endorsement prose into operations against the clause tree and applies them in precedence order. This is the part a conventional pipeline does not have.

**Extraction is rule-based first.** The industry idiom is formulaic, so directives are matched sentence by sentence and the restated text is captured with the label structure the endorsement gave it. Targets resolve deterministically against the clause tree by explicit path, by section name plus path, by heading words, or by defined term. Nothing is guessed: what the rules cannot place becomes **unresolved** (attached as a flagged sibling) or **held** (not applied, listed for review). An optional model-based extractor handles text the rules cannot read, and its proposals go through the same deterministic resolver.

| Corpus | Ops | Resolved | Unresolved | Held | Note |
|---|---:|---:|---:|---:|---|
| Synthetic library, ground truth known | 8 | 6 | 1 | 1 | All 8 expected operations extracted exactly; the unresolved one names no clause by design, the held one has schedule blanks |
| 11 real published TWIA endorsements | 20 | 17 | 0 | 3 | Held: a scanned PDF, a notice page, and an "It is agreed that" clause naming no target |

**Resolution** applies replace, delete, add, amend-definition and schedule-fill operations per account. An endorsement controls over the base form; between endorsements the later effective date controls; same-date changes to one clause are applied in schedule order and recorded as a conflict with both texts preserved. Every changed clause keeps its original wording and the lineage of operations that touched it, and resolution runs as of any date.

The scanned endorsement is a genuine held case: there is no text layer, so it is held with that reason rather than silently skipped. Optical character recognition is still open.

### Retrieval and evaluation

- **Router.** Limits, deductibles, premiums and dates are typed facts, answered from the account record with as-of dates honoured. Everything else goes to retrieval.
- **Hard account filter.** Every search is scoped to one account before ranking. Cross-account leakage is a breach, not a relevance problem, so the filter is not optional.
- **Hybrid search.** Dense and keyword rankings fused with reciprocal rank fusion, an optional cross-encoder rerank, and optional pull-in of definitions referenced by the top hits.
- **Five chunk variants** so the comparison is fair, from fixed windows through to the as-endorsed index, all recording which clause paths they cover.
- **As-of views on demand.** For a question about a past date the account is re-resolved as of that date and indexed for that query.

Embedder `BAAI/bge-small-en-v1.5`, reranker `Xenova/ms-marco-MiniLM-L-6-v2`, k=5, 40 accounts. Declarations questions: **100%** exact on 447 questions.

| Rung | Configuration | Chunks | hit@k | MRR | answer@k | p50 ms |
|---|---|---:|---:|---:|---:|---:|
| 1 | fixed windows + dense | 1,793 | 60.3% | 0.31 | 73.1% | 69 |
| 2 | recursive windows + dense | 1,673 | 23.8% | 0.16 | 56.0% | 69 |
| 3 | clause-aware + dense | 20,258 | 56.1% | 0.50 | 42.3% | 70 |
| 4 | clause-aware + hybrid | 20,258 | 56.1% | 0.39 | 42.3% | 72 |
| 5 | clause-aware + hybrid + rerank | 20,258 | 61.4% | 0.40 | 52.2% | 1394 |
| 6 | **as endorsed** + hybrid + rerank | 20,102 | 83.1% | 0.72 | **89.0%** | 1409 |
| 7 | as endorsed + contextual header | 20,102 | 83.1% | 0.71 | 89.0% | 1456 |
| 7d | as endorsed + header + definitions | 20,102 | 83.1% | 0.71 | 89.0% | 1470 |

`answer@k` is the metric that matters: a retrieved chunk carries the *currently correct* answer, not merely a relevant-looking one.

Reading the table honestly. Rung 1 scores well because a 512-token window covers eight or more clauses at once and gets credit whenever the whole endorsement lands in one window; a generator would still have to reconcile the two. Hybrid search did not beat dense alone on these short paraphrased questions, and the contextual header neither helped nor hurt. Both are reported as measured. The move from rung 5 to rung 6 is the thesis: same retrieval, same reranker, but the index holds the policy as endorsed.

Reranking dominates the cost: about 1.4 seconds at the median against roughly 70 milliseconds without it, because a cross-encoder scores 30 candidates on a laptop CPU. On this question set it bought 9 points of `answer@k` at rung 5 and nothing at all at rungs 6 and 7, so on a latency budget it is the first thing to drop. An earlier version of this README quoted 620 ms here, taken from a run under a configuration that cached query embeddings. Three consecutive runs since measure about 1.4 seconds, and that is the number that stands.

### Generation with checks

```
route ─► declarations lookup (typed facts, cited to the declarations page)
     └► retrieve (account-scoped, as-of aware) ─► draft ─► checks ─► answer
                    ▲                               │
                    └── one rewrite-and-retry ◄── can't answer
```

- **Every sentence is a claim with citations,** tied to chunk identifiers rather than free text.
- **Groundedness check.** A claim survives only if a cited chunk supports it: most of its content words, and every amount in it, appear in that chunk. Unsupported claims are dropped and an answer with nothing left is withheld.
- **Numeric guard.** The number an answer turns on must appear in a cited clause. A fabricated amount is never released.
- **Abstention.** "The policy does not address this" is a valid, cited outcome, and the evaluation rewards it.
- **One retry, hard-capped.** When the generator cannot answer, a grader names what is missing and rewrites the query once. It never loops twice.
- **Two generators, one contract.** A hosted-model generator, and an extractive one that uses no model so the whole pipeline runs and is measured without credentials. The hosted path is covered by tests against a stub client; its live numbers are still to be run.

Extractive generator over all 636 questions, retrieval rung 7d:

| Metric | Value |
|---|---:|
| Exact match, money and dates and short text, n=433 | **100.0%** |
| Lexical correctness proxy, long text, n=199 | 62.3% |
| Citation@1 hits an expected clause | 64.6% |
| Abstained | 6.9% |
| Withheld by the checks | 0.0% |

Read this table carefully, because it is easy to over-read. Every money answer is exact because this generator hands back the clause verbatim, so the amount came from the clause by construction. Nothing was withheld for the same structural reason: its claims *are* the cited chunk, so they ground trivially and the checks have nothing to catch. Neither result is evidence that the checks work. The checks are exercised instead by tests against a stub client, which reject a fabricated figure and an unsupported claim on the hosted-generator path. Long-form answers are where a real generator earns its cost, and 62.3% against a lexical proxy is the honest floor rather than a quality score.

## Run it yourself

```bash
docker compose up api
```

Then open http://localhost:8000. Or without Docker:

```bash
python -m venv .venv && .venv/Scripts/activate      # or source .venv/bin/activate
pip install -e ".[dev]"
as-endorsed bootstrap        # public forms, parsing, synthetic accounts, resolution, search index
uvicorn as_endorsed.api:app --port 8000
```

The command line exposes every stage independently:

```bash
as-endorsed parse --all                            # clause trees from the public forms
as-endorsed endorse extract --all                  # amendment operations from real endorsements
as-endorsed review                                 # what the engine refused to apply, and why
as-endorsed resolve                                # apply endorsements to every account
as-endorsed eval run                               # the full ablation ladder
as-endorsed ask "Does the policy exclude hot tubs?" -a SYN-00001
pytest                                             # 45 tests
```

To use a hosted model for the prose, set `ANTHROPIC_API_KEY`. Without it the extractive generator answers and says so. [`deploy/README.md`](deploy/README.md) covers deployment.

## Build log

Five milestones, then the consequences: deployment, a security review, and correcting claims of my own that had gone stale. Written up because how a thing was built, and what went wrong while building it, says more than a feature list.

About 5,800 lines of Python across 41 modules and 45 tests, with no machine-learning framework and no orchestration library.

### Stack, and why each piece

| Layer | Choice | Why |
|---|---|---|
| PDF reading | PyMuPDF | Text with per-line coordinates, which the clause highlighting depends on |
| Data model | Pydantic, pydantic-settings | Typed records and environment-driven config |
| Embeddings, reranking | fastembed on ONNX Runtime | bge-small for vectors, a MiniLM cross-encoder for reranking, both in-process |
| Keyword search | rank_bm25, numpy | BM25 beside dense cosine, fused with reciprocal rank fusion |
| Serving | FastAPI, uvicorn | The API is the product; the page is a thin client of it |
| Interface | Plain HTML, CSS, JavaScript, pdf.js | No build step, ships in the same container |
| Command line | Typer, Rich | Every pipeline stage runnable and inspectable on its own |
| Synthetic documents | ReportLab | Generated declarations go through the same parser as real forms |
| Optional | anthropic, psycopg + pgvector | A hosted generator and a Postgres backend, neither required |

Two choices did real work. **No LangChain**, because the orchestration is about fifty lines and hand-written retrieval is easier to defend in review. **ONNX rather than PyTorch**, forced by a machine running Python 3.14 where torch has no wheels, and better anyway: the whole evaluation runs with no API key and no GPU.

### Sequence

1. **Corpus and parser.** Public form registry and downloader, PDF to a clause tree with stable paths, deterministic synthetic accounts, ground-truth questions.
2. **Endorsement engine.** Extract amendment instructions from prose, resolve their targets against the clause tree, apply them in precedence order, hold what cannot be placed.
3. **Retrieval ladder.** Five chunk variants, a declarations router, hybrid search, reranking, and the harness that compares them.
4. **Generation.** Claim-level citations, groundedness and numeric checks, abstention, one bounded retry.
5. **Ship.** API, client with PDF highlighting, container, continuous build, deployment.

Everything after that was consequence: deployment fixes, a security review, and correcting claims that had gone stale.

### Parsing real forms

Two-column pages with centred headings were read in the wrong order, so clauses interleaved. Fixed by splitting each page into bands at full-width lines. Numbers inside running text opened false clauses, fixed by accepting a label only when it is the expected next one at its level. Soft hyphens split words across line breaks. A clause wrapping from one column to the next produced a bounding box spanning the whole page, so highlights covered unrelated text; there is now one box per column run.

Then the Texas policy arrived with an entirely different convention: word headings instead of Roman numerals, and definitions written as quoted paragraphs rather than numbered items. Supporting both is the difference between a parser and a one-form script.

### Domain logic

Real endorsements append the next instruction to the end of restated text, so scanning clause by clause missed most of them. Rewritten to scan sentence by sentence. One published endorsement is a scan with no text layer; it is held for review with that reason rather than silently skipped.

### Sources

Citizens Florida keeps its forms behind an agent login, so it was dropped as a source. FEMA's edge blocks non-browser downloads, so the public-domain forms ship in `corpus/fema/`. The Texas association rejects the default Python user agent, so the downloader tries several in turn.

### Retrieval quality

The first evaluation looked poor until the questions turned out to embed the policy number and the as-of date. Those are filters, not search terms, and are now stripped before searching. The ground truth had a bug too: when two endorsements amend the same clause, the answer before the later one is the earlier amendment, not the printed form.

### Deployment

Hugging Face made Docker Spaces paid mid-task. Azure blocked its own cloud image build on a student subscription, so the image is built by GitHub Actions and pulled from there. Docker Desktop failed locally with a read-only store. Git Bash rewrote `/data` in command arguments into a Windows path, which crashed startup until `MSYS_NO_PATHCONV` was set.

The worst was a real bug. The image never pre-computed the search index, so every container start re-embedded all 20,102 clauses and a cold request took **145 seconds**. The index is now built into the image and the application starts in about two seconds.

### Security review

Two genuine findings. An open endpoint allocated and cached an index per distinct as-of date with no limit, which is a memory-exhaustion lever. And a file path was assembled from request input; traversal did not actually work, but it was safe by accident rather than by design. Both fixed, plus a per-client rate limit, a strict content security policy, and documented limits. Details in [Security posture](#security-posture).

### Evaluation integrity

Most of the corrections landed here, and they are the ones worth reading.

- The headline **89% is a retrieval measure**, and a qualifier slipped out of one table row so it read as end-to-end accuracy. The results are now split into two labelled tables.
- A latency figure of **620 ms** stayed in this README after query caching was removed. Three runs since measure about 1.4 seconds. Corrected, with the reason it went wrong recorded next to it.
- The perfect money score **does not demonstrate the numeric guard**, because on the no-model path the guard has nothing to catch. Those two claims are now stated separately, each with its own evidence.
- Results files now **stamp themselves** with the commit and time that produced them. That does not stop a number going stale; it stops one lying about its age.

### Still open

Generated prose quality is unmeasured, because no hosted model has been run against the evaluation set. Optical character recognition is not implemented, which is why one real endorsement sits in the review queue. The Postgres backend exists with the same interface as the in-memory index but has not been exercised against a real database.

## Security posture

The demo serves synthetic accounts, so there is nothing to steal, but the surface is treated as though there were.

- **No personal data.** Every account, name and address is generated from a seed. The only real documents are public government and association forms.
- **Account scoping is enforced in retrieval,** not in a prompt: every search hard-filters to one account before ranking.
- **File access goes through an allowlist.** No request builds a filesystem path, so no input can walk the tree.
- **Bounded work per request.** Length-capped questions, as-of dates validated against the policy term, a fixed-size cache for on-demand indexes, and a per-client rate limit on the expensive endpoint. Platform scaling is capped, so a flood cannot run up a bill.
- **Browser hardening.** A strict content security policy with no inline scripts or styles, `nosniff`, `frame-ancestors 'none'` and `no-referrer`. Everything rendered is escaped.
- **Container.** Non-root user, no credentials inside, no network needed at runtime. Any model key is injected as a platform secret.
- **Dependencies** audited with `pip-audit`, no known vulnerabilities at the time of writing.

There is deliberately no authentication: it is a public demo. Serving real policies would need authentication, per-tenant isolation at the storage layer, and an audit log of who asked what, none of which this repository implements.

## Boundaries

The system reports what a policy says and where. It does not decide whether a claim will be paid, and it does not interpret ambiguous wording. Where two endorsements conflict beyond what the precedence rules resolve, both texts are surfaced as a conflict for a human to settle. Optical character recognition for scanned endorsements is not implemented, which is why one real form sits in the review queue rather than being silently skipped.

## Licensing

Only public-domain or openly published forms are in the registry. FEMA's Standard Flood Insurance Policy forms are US Government works and a copy ships in `corpus/fema/`, because fema.gov's edge intermittently blocks non-browser downloads. The Texas Windstorm Insurance Association publishes its dwelling policy and endorsements openly; they are downloaded by script and not committed. Forms owned by Insurance Services Office are copyrighted and never enter this repository.

## Built by

Ammar Faisal. Available for freelance work on document understanding, retrieval systems and evaluation. The quickest way to judge the work is the [live demo](https://as-endorsed.wittybay-fdf1bbec.germanywestcentral.azurecontainerapps.io) and the results tables above.
