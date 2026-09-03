"""Embedders behind one interface, with a content-addressed disk cache.

`FastEmbedEmbedder` runs BAAI/bge-small-en-v1.5 in-process through ONNX. The
`HashEmbedder` is a deterministic feature-hashing stand-in for tests and for
running the harness without model downloads; it is not a real semantic model
and the eval output says so.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Protocol

import numpy as np

from as_endorsed.config import settings

_TOKEN_RE = re.compile(r"[a-z0-9$]+(?:\.\d+)?")


class Embedder(Protocol):
    name: str
    dim: int

    def embed_passages(self, texts: list[str]) -> np.ndarray: ...

    def embed_queries(self, texts: list[str]) -> np.ndarray: ...


class HashEmbedder:
    """Unigram + bigram feature hashing, L2-normalised. Deterministic, dependency-free."""

    name = "hash-512"
    dim = 512

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        toks = _TOKEN_RE.findall(text.lower())
        grams = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
        for g in grams:
            h = int(hashlib.blake2b(g.encode(), digest_size=8).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 63) else -1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)

    embed_queries = embed_passages


class FastEmbedEmbedder:
    name = "BAAI/bge-small-en-v1.5"
    dim = 384

    def __init__(self, model_name: str | None = None) -> None:
        from fastembed import TextEmbedding

        self.name = model_name or self.name
        self._model = TextEmbedding(self.name)

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), np.float32)
        return np.stack([np.asarray(v, dtype=np.float32) for v in self._model.embed(texts, batch_size=64)])

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), np.float32)
        return np.stack([np.asarray(v, dtype=np.float32) for v in self._model.query_embed(texts)])


class CachedEmbedder:
    """Wraps an embedder with an on-disk cache keyed by (model, text hash)."""

    def __init__(self, inner: Embedder, cache_dir: Path | None = None) -> None:
        self.inner = inner
        self.name, self.dim = inner.name, inner.dim
        safe = re.sub(r"[^A-Za-z0-9.-]+", "_", inner.name)
        self.dir = (cache_dir or settings.data_dir / "cache") / safe
        self.dir.mkdir(parents=True, exist_ok=True)
        self._keys_path = self.dir / "keys.json"
        self._vecs_path = self.dir / "vectors.npy"
        if self._keys_path.exists() and self._vecs_path.exists():
            self._keys: dict[str, int] = json.loads(self._keys_path.read_text(encoding="utf-8"))
            self._vecs = np.load(self._vecs_path)
        else:
            self._keys, self._vecs = {}, np.zeros((0, self.dim), np.float32)
        self._dirty = False

    @staticmethod
    def _key(kind: str, text: str) -> str:
        return kind + ":" + hashlib.sha1(text.encode("utf-8")).hexdigest()

    def _embed(self, kind: str, texts: list[str]) -> np.ndarray:
        keys = [self._key(kind, t) for t in texts]
        missing = [t for k, t in zip(keys, texts) if k not in self._keys]
        missing = list(dict.fromkeys(missing))
        if missing:
            fn = self.inner.embed_queries if kind == "q" else self.inner.embed_passages
            new = fn(missing)
            base = len(self._keys)
            for i, t in enumerate(missing):
                self._keys[self._key(kind, t)] = base + i
            self._vecs = np.vstack([self._vecs, new]) if len(self._vecs) else new
            self._dirty = True
        return self._vecs[[self._keys[k] for k in keys]] if keys else np.zeros((0, self.dim), np.float32)

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return self._embed("p", texts)

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        # Queries are never cached: latency numbers must include the query embedding.
        return self.inner.embed_queries(texts)

    def flush(self) -> None:
        if self._dirty:
            np.save(self._vecs_path, self._vecs)
            self._keys_path.write_text(json.dumps(self._keys), encoding="utf-8")
            self._dirty = False


def make_embedder(name: str = "bge") -> Embedder:
    if name in ("hash", "test"):
        return HashEmbedder()
    if name in ("bge", "bge-small", "BAAI/bge-small-en-v1.5"):
        return CachedEmbedder(FastEmbedEmbedder())
    return CachedEmbedder(FastEmbedEmbedder(name))
