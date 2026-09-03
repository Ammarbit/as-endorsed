"""Generation tests: the extractive generator, the groundedness and numeric
checks, abstention, the rewrite loop, and the Claude generator against a stub
client (no network)."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from as_endorsed.config import settings
from as_endorsed.corpus import registry
from as_endorsed.endorse.extract import extract_ops
from as_endorsed.endorse.resolve import ScheduledEndorsement, resolve_policy
from as_endorsed.generate.context import supported
from as_endorsed.generate.extractive import ExtractiveGenerator
from as_endorsed.generate.llm import ClaudeGenerator
from as_endorsed.generate.pipeline import GenConfig, Resources, answer_question
from as_endorsed.generate.schema import Claim, Draft, Sufficiency
from as_endorsed.ingest.clauses import parse_form
from as_endorsed.retrieval.chunking import build_chunks
from as_endorsed.retrieval.embed import HashEmbedder
from as_endorsed.retrieval.index import MemoryIndex, SearchConfig
from as_endorsed.synth.accounts import ScheduledEndorsement as SE, generate_accounts
from as_endorsed.synth.endorsements import EDITION, LIBRARY, render_library

NFIP = settings.raw_dir / registry.get("NFIP-DWELLING@2021-10").filename
pytestmark = pytest.mark.skipif(not NFIP.exists(), reason="corpus not downloaded")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    spec = registry.get("NFIP-DWELLING@2021-10")
    nfip = parse_form(NFIP, form_id=spec.form_id, edition=spec.edition, title=spec.title)
    pdfs = render_library(tmp_path_factory.mktemp("endorsements"))
    forms, ext = {}, {}
    for s in LIBRARY:
        e = parse_form(pdfs[s.form_id], form_id=s.form_id, edition=EDITION, title=s.title, strict_sequence=False, root_paragraphs=True)
        forms[s.key], ext[s.key] = e, extract_ops(e, nfip)
    acct = generate_accounts(1, seed=5)[0]
    acct.policy.endorsement_forms = [SE(form_id="SYN-END-05", edition=EDITION, title="Increased Loss Avoidance Limit", effective_date=acct.policy.term_start)]
    attached = [ScheduledEndorsement(extraction=ext["SYN-END-05@2026-01"], effective_date=acct.policy.term_start, order=0)]
    rp = resolve_policy(account_id=acct.account_id, base=nfip, attached=attached, as_of=acct.policy.term_end)
    chunks = build_chunks(acct, "header", nfip, resolved=rp, endorsement_forms=forms)
    emb = HashEmbedder()
    index = MemoryIndex("header", chunks, emb.embed_passages([c.embed_text for c in chunks]))
    res = Resources(index=index, embedder=emb, reranker=None, base=nfip)
    return acct, res


CFG = GenConfig(search=SearchConfig(mode="bm25", rerank=False, k=5), loop=True)


def test_supported_requires_words_and_numbers():
    chunk = "We will pay up to $2,500 for costs you incur to protect the insured building from a flood."
    assert supported("The policy pays up to $2,500 to protect the building from flood.", chunk)
    assert not supported("The policy pays up to $9,999 to protect the building from flood.", chunk)
    assert not supported("Hot tubs and spas are excluded from coverage.", chunk)


def test_declarations_route_is_cited_and_exact(world):
    acct, res = world
    p = acct.policy
    ans = answer_question(f"What is the building deductible on policy {p.policy_number}?", acct, res, ExtractiveGenerator(), CFG)
    assert ans.route == "declarations" and ans.status == "answered"
    assert ans.numeric_value == p.coverage("building").deductible
    assert ans.citations[0].source == "declarations"


def test_extractive_answers_with_citation_and_amount(world):
    acct, res = world
    ans = answer_question("What is the most the policy will pay for sandbags, supplies and labor to protect the building from flood?", acct, res, ExtractiveGenerator(), CFG)
    assert ans.status == "answered", ans.reason
    assert ans.numeric_value == 2500.0
    assert any("III.C.2.a.(1)" in c.paths for c in ans.citations)
    assert ans.checks == {"groundedness": True, "numeric": True}


def test_extractive_abstains_when_nothing_matches(world):
    acct, res = world
    ans = answer_question("Does the policy cover quantum flux capacitor calibration?", acct, res, ExtractiveGenerator(), CFG)
    assert ans.status == "abstain" and ans.reason


class StubClient:
    """Mimics anthropic.Anthropic().messages.parse for the schemas the generator uses."""

    def __init__(self, drafts: list[Draft], sufficiency: Sufficiency | None = None):
        self.drafts, self.sufficiency, self.calls = list(drafts), sufficiency, []
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kw):
        self.calls.append(kw)
        schema = kw["output_format"]
        if schema is Sufficiency:
            return SimpleNamespace(parsed_output=self.sufficiency, stop_reason="end_turn")
        return SimpleNamespace(parsed_output=self.drafts.pop(0), stop_reason="end_turn")


def _chunk_id(res, path: str) -> str:
    return next(c.chunk_id for c in res.index.chunks if path in c.paths)


def test_claude_path_grounds_and_guards_numbers(world):
    acct, res = world
    q = "What is the most the policy will pay for sandbags, supplies and labor to protect the building from flood?"
    ids = [_chunk_id(res, "III.C.2.a.(1)")]
    good = Draft(can_answer=True, answer="The policy pays up to $2,500 for sandbags, supplies and labor.",
                 claims=[Claim(text="The policy pays up to $2,500 for sandbags, supplies and labor to protect the insured building from flood.", chunk_ids=[ids[0]])],
                 numeric_value=2500)
    gen = ClaudeGenerator(client=StubClient([good]), model="stub")
    ans = answer_question(q, acct, res, gen, CFG)
    assert ans.status == "answered" and ans.numeric_value == 2500 and ans.citations[0].chunk_id == ids[0]

    bad_number = good.model_copy(update={"numeric_value": 9999.0, "answer": "The policy pays up to $9,999.",
                                         "claims": [Claim(text="The policy pays up to $9,999 for sandbags and supplies to protect the building from flood.", chunk_ids=[ids[0]])]})
    ans = answer_question(q, acct, res, ClaudeGenerator(client=StubClient([bad_number]), model="stub"), CFG)
    # The fabricated amount fails support (numbers must match the cited chunk), so the claim is dropped
    # and the answer is withheld; nothing with $9,999 in it is ever released.
    assert ans.status == "withheld" and not (ans.checks["groundedness"] and ans.checks["numeric"])
    assert "9999" not in ans.answer

    unsupported = Draft(can_answer=True, answer="Hot tubs are excluded.", claims=[Claim(text="Hot tubs and spas and swimming pools are excluded from coverage entirely.", chunk_ids=[ids[0]])])
    ans = answer_question(q, acct, res, ClaudeGenerator(client=StubClient([unsupported]), model="stub"), CFG)
    assert ans.status == "withheld" and ans.checks["groundedness"] is False


def test_claude_path_rewrite_loop_fires_once(world):
    acct, res = world
    q = "How much does the policy pay toward emergency flood protection supplies?"
    first = Draft(can_answer=False, answer="", missing="the loss avoidance limit")
    ids = [_chunk_id(res, "III.C.2.a.(1)")]
    second = Draft(can_answer=True, answer="Up to $2,500.", claims=[Claim(text="We will pay up to $2,500 for costs you incur to protect the insured building from a flood.", chunk_ids=[ids[0]])], numeric_value=2500)
    stub = StubClient([first, second], Sufficiency(sufficient=False, missing="loss avoidance measures", rewritten_query="sandbags supplies labor protect the insured building"))
    ans = answer_question(q, acct, res, ClaudeGenerator(client=stub, model="stub"), CFG)
    assert ans.loop_used and ans.rewritten_query.startswith("sandbags")
    assert ans.status == "answered" and ans.numeric_value == 2500
    assert len(stub.calls) == 3  # draft, grader, draft
    ans = answer_question(q, acct, res, ClaudeGenerator(client=StubClient([first]), model="stub"), GenConfig(search=CFG.search, loop=False))
    assert ans.status == "abstain" and not ans.loop_used
