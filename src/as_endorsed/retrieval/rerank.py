"""Cross-encoder reranking behind one interface."""

from __future__ import annotations

from typing import Protocol


class Reranker(Protocol):
    name: str

    def score(self, query: str, texts: list[str]) -> list[float]: ...


class NoReranker:
    name = "none"

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [float(len(texts) - i) for i in range(len(texts))]  # keep incoming order


class FastEmbedReranker:
    """MiniLM cross-encoder by default (fast on CPU); BAAI/bge-reranker-base is the stronger option."""

    name = "Xenova/ms-marco-MiniLM-L-6-v2"

    def __init__(self, model_name: str | None = None) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.name = model_name or self.name
        self._model = TextCrossEncoder(self.name)

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        return [float(s) for s in self._model.rerank(query, texts)]


def make_reranker(name: str = "minilm") -> Reranker:
    if name in ("none", "off"):
        return NoReranker()
    if name in ("minilm", "Xenova/ms-marco-MiniLM-L-6-v2"):
        return FastEmbedReranker()
    if name in ("bge", "bge-reranker-base", "BAAI/bge-reranker-base"):
        return FastEmbedReranker("BAAI/bge-reranker-base")
    return FastEmbedReranker(name)
