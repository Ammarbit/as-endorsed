"""API tests with the hash embedder and no reranker, so they need no model downloads."""

from __future__ import annotations

import os

import pytest

from as_endorsed.config import settings

pytestmark = pytest.mark.skipif(
    not (settings.synthetic_dir / "accounts.json").exists() or not (settings.parsed_dir / "NFIP-DWELLING@2021-10.json").exists(),
    reason="bootstrap data not generated",
)


@pytest.fixture(scope="module")
def client():
    os.environ["AS_ENDORSED_EMBEDDER"] = "hash"
    os.environ["AS_ENDORSED_RERANKER"] = "none"
    from fastapi.testclient import TestClient

    from as_endorsed.api import app

    with TestClient(app) as c:
        yield c


def test_health_and_accounts(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["accounts"] >= 1 and h["embedder"] == "hash-512"
    accts = client.get("/api/accounts").json()
    assert accts and {"account_id", "policy_number", "endorsements"} <= set(accts[0])


def test_account_detail_lists_changes(client):
    accts = client.get("/api/accounts").json()
    with_changes = next(a for a in accts if a["clauses_changed"])
    d = client.get(f"/api/accounts/{with_changes['account_id']}").json()
    assert d["changed"] and d["examples"]
    ch = d["changed"][0]
    assert {"path", "text_as_endorsed", "lineage"} <= set(ch)


def test_ask_declarations_is_exact_and_cited(client):
    a = client.get("/api/accounts").json()[0]
    r = client.post("/api/ask", json={"account_id": a["account_id"], "question": f"What is the building deductible on policy {a['policy_number']}?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"]["route"] == "declarations" and body["answer"]["status"] == "answered"
    assert body["citations"][0]["pdf_url"].endswith("/pdf")


def test_ask_clause_question_returns_bboxes(client):
    accts = client.get("/api/accounts").json()
    a = next(x for x in accts if any(e["form_id"] == "SYN-END-05" for e in x["endorsements"]))
    r = client.post("/api/ask", json={"account_id": a["account_id"], "generator": "extractive",
                                      "question": "What is the most the policy will pay for sandbags, supplies and labor to protect the building from flood?"})
    body = r.json()
    assert body["answer"]["status"] == "answered", body["answer"]
    cite = next(c for c in body["citations"] if "III.C.2.a.(1)" in c["paths"])
    assert cite["page"] and cite["bboxes"] and cite["pdf_url"] == "/api/forms/NFIP-DWELLING@2021-10/pdf"
    assert "2,500" in cite["text_as_endorsed"] and cite["lineage"]


def test_pdf_and_clause_endpoints(client):
    r = client.get("/api/forms/NFIP-DWELLING@2021-10/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    c = client.get("/api/forms/NFIP-DWELLING@2021-10/clauses/II.C.5").json()
    assert c["term"] == "Basement" and c["bboxes"]
    assert client.get("/api/forms/NOPE@1/pdf").status_code == 404


def test_review_and_eval(client):
    assert isinstance(client.get("/api/review").json(), list)
    assert "retrieval" in client.get("/api/eval").json()
    assert client.get("/").status_code == 200
