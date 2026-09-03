"""Indexes and search.

`MemoryIndex` holds one variant's chunks for all accounts: a dense matrix for
cosine search and a per-account BM25 for lexical search. Every query is hard
filtered to one account before ranking; cross-account leakage is not a
retrieval-quality problem, it is a data breach, so the filter is not optional.

`PgIndex` is the same interface over Postgres + pgvector for serving. It is
exercised only when a database URL is configured.

Hybrid search fuses dense and BM25 rankings with reciprocal rank fusion, then
optionally reranks the fused candidates with a cross-encoder. Definition
pull-in appends the definitions of defined terms that appear in the top hits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Protocol

import numpy as np
from rank_bm25 import BM25Okapi

from as_endorsed.models import ParsedForm
from as_endorsed.retrieval.chunking import Chunk
from as_endorsed.retrieval.embed import Embedder
from as_endorsed.retrieval.rerank import Reranker

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.,]\d+)*")
RRF_K = 60


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower().replace("$", " $"))


@dataclass
class Hit:
    chunk: Chunk
    score: float
    rank: int
    via: str


class Index(Protocol):
    variant: str

    def dense(self, qvec: np.ndarray, account_id: str, k: int) -> list[Hit]: ...

    def bm25(self, query: str, account_id: str, k: int) -> list[Hit]: ...


class MemoryIndex:
    def __init__(self, variant: str, chunks: list[Chunk], embeddings: np.ndarray) -> None:
        assert len(chunks) == len(embeddings)
        self.variant = variant
        self.chunks = chunks
        self.emb = embeddings.astype(np.float32)
        self._by_account: dict[str, list[int]] = {}
        for i, c in enumerate(chunks):
            self._by_account.setdefault(c.account_id, []).append(i)
        self._bm25: dict[str, BM25Okapi] = {}

    def accounts(self) -> list[str]:
        return list(self._by_account)

    def dense(self, qvec: np.ndarray, account_id: str, k: int) -> list[Hit]:
        idx = self._by_account.get(account_id, [])
        if not idx:
            return []
        sub = self.emb[idx]
        scores = sub @ qvec.astype(np.float32)
        order = np.argsort(-scores)[:k]
        return [Hit(self.chunks[idx[j]], float(scores[j]), r + 1, "dense") for r, j in enumerate(order)]

    def bm25(self, query: str, account_id: str, k: int) -> list[Hit]:
        idx = self._by_account.get(account_id, [])
        if not idx:
            return []
        if account_id not in self._bm25:
            self._bm25[account_id] = BM25Okapi([tokenize(self.chunks[i].embed_text) for i in idx])
        scores = self._bm25[account_id].get_scores(tokenize(query))
        order = np.argsort(-scores)[:k]
        return [Hit(self.chunks[idx[j]], float(scores[j]), r + 1, "bm25") for r, j in enumerate(order) if scores[j] > 0]


def rrf(rankings: Iterable[list[Hit]], k: int = RRF_K) -> list[Hit]:
    fused: dict[str, tuple[float, Hit, list[str]]] = {}
    for hits in rankings:
        for h in hits:
            score, first, vias = fused.get(h.chunk.chunk_id, (0.0, h, []))
            fused[h.chunk.chunk_id] = (score + 1.0 / (k + h.rank), first, vias + [h.via])
    out = sorted(fused.values(), key=lambda t: -t[0])
    return [Hit(h.chunk, s, r + 1, "+".join(dict.fromkeys(v))) for r, (s, h, v) in enumerate(out)]


@dataclass
class SearchConfig:
    mode: str = "hybrid"  # dense | bm25 | hybrid
    rerank: bool = False
    k: int = 5
    candidates: int = 30
    pull_definitions: bool = False
    max_definitions: int = 2


def search(index: Index, embedder: Embedder, query: str, account_id: str, cfg: SearchConfig,
           reranker: Reranker | None = None, base: ParsedForm | None = None) -> list[Hit]:
    n = cfg.candidates if cfg.rerank else cfg.k
    rankings: list[list[Hit]] = []
    if cfg.mode in ("dense", "hybrid"):
        qvec = embedder.embed_queries([query])[0]
        rankings.append(index.dense(qvec, account_id, n))
    if cfg.mode in ("bm25", "hybrid"):
        rankings.append(index.bm25(query, account_id, n))
    hits = rrf(rankings) if len(rankings) > 1 else (rankings[0] if rankings else [])
    if cfg.rerank and reranker is not None and hits:
        scores = reranker.score(query, [h.chunk.embed_text for h in hits])
        order = np.argsort(-np.asarray(scores))
        hits = [Hit(hits[j].chunk, float(scores[j]), r + 1, hits[j].via + "+rerank") for r, j in enumerate(order)]
    hits = hits[: cfg.k]
    if cfg.pull_definitions and base is not None:
        hits += pull_definitions(hits, index, account_id, base, cfg.max_definitions)
    return hits


def pull_definitions(hits: list[Hit], index: Index, account_id: str, base: ParsedForm, limit: int) -> list[Hit]:
    """Definitions of defined terms that appear in the top hits, appended as extra hits."""
    have = {p for h in hits for p in h.chunk.paths}
    terms = [(c.term, c.path) for c in base.clauses if c.term and len(c.term) >= 4]
    text = " ".join(h.chunk.text for h in hits).lower()
    wanted: list[str] = []
    for term, path in sorted(terms, key=lambda t: -len(t[0])):
        if path in have or path in wanted:
            continue
        if re.search(r"\b" + re.escape(term.lower()) + r"\b", text):
            wanted.append(path)
        if len(wanted) >= limit:
            break
    out: list[Hit] = []
    if not wanted or not isinstance(index, MemoryIndex):
        return out
    for i in index._by_account.get(account_id, []):
        c = index.chunks[i]
        if c.kind == "clause" and c.paths and c.paths[0] in wanted:
            out.append(Hit(c, 0.0, len(hits) + len(out) + 1, "definition"))
    return out[:limit]


class PgIndex:
    """Postgres + pgvector backend with the same interface. Requires DATABASE_URL."""

    def __init__(self, variant: str, dsn: str, dim: int) -> None:
        import psycopg
        from pgvector.psycopg import register_vector

        self.variant = variant
        self.conn = psycopg.connect(dsn, autocommit=True)
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(self.conn)
        with self.conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id text PRIMARY KEY, variant text NOT NULL, account_id text NOT NULL,
                    kind text, source text, paths text[], lineage text[], header text, text text NOT NULL,
                    active boolean DEFAULT true, embedding vector({dim}),
                    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(header, '') || ' ' || text)) STORED
                );
                CREATE INDEX IF NOT EXISTS chunks_account ON chunks (variant, account_id);
                CREATE INDEX IF NOT EXISTS chunks_tsv ON chunks USING gin (tsv);
            """)

    def upsert(self, chunks: list[Chunk], embeddings: np.ndarray) -> None:
        with self.conn.cursor() as cur:
            for c, e in zip(chunks, embeddings):
                cur.execute(
                    """INSERT INTO chunks (chunk_id, variant, account_id, kind, source, paths, lineage, header, text, active, embedding)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (chunk_id) DO UPDATE SET text = EXCLUDED.text, header = EXCLUDED.header,
                       paths = EXCLUDED.paths, lineage = EXCLUDED.lineage, active = EXCLUDED.active, embedding = EXCLUDED.embedding""",
                    (c.chunk_id, c.variant, c.account_id, c.kind, c.source, c.paths, c.lineage, c.header, c.text, c.active, e),
                )

    def _row_to_chunk(self, r) -> Chunk:
        return Chunk(r[0], r[2], r[1], r[8], r[3], r[4], list(r[5] or []), list(r[6] or []), r[7], r[9])

    def dense(self, qvec: np.ndarray, account_id: str, k: int) -> list[Hit]:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT chunk_id, variant, account_id, kind, source, paths, lineage, header, text, active, 1 - (embedding <=> %s) AS score
                   FROM chunks WHERE variant = %s AND account_id = %s ORDER BY embedding <=> %s LIMIT %s""",
                (qvec, self.variant, account_id, qvec, k),
            )
            return [Hit(self._row_to_chunk(r), float(r[10]), i + 1, "dense") for i, r in enumerate(cur.fetchall())]

    def bm25(self, query: str, account_id: str, k: int) -> list[Hit]:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT chunk_id, variant, account_id, kind, source, paths, lineage, header, text, active,
                          ts_rank_cd(tsv, plainto_tsquery('english', %s)) AS score
                   FROM chunks WHERE variant = %s AND account_id = %s AND tsv @@ plainto_tsquery('english', %s)
                   ORDER BY score DESC LIMIT %s""",
                (query, self.variant, account_id, query, k),
            )
            return [Hit(self._row_to_chunk(r), float(r[10]), i + 1, "bm25") for i, r in enumerate(cur.fetchall())]
