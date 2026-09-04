"""Persist a built index so a process can start without re-embedding the corpus.

Embedding 20,000 clauses takes minutes; loading them back takes about a second.
The image bakes the index in at build time, so a cold container serves almost
immediately instead of doing the work again.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from as_endorsed.config import settings
from as_endorsed.retrieval.chunking import Chunk
from as_endorsed.retrieval.index import MemoryIndex

FORMAT = 1


def index_dir() -> Path:
    return settings.data_dir / "index"


def paths(variant: str, embedder_name: str) -> tuple[Path, Path]:
    # Build the names by concatenation: a model name like "bge-small-en-v1.5" contains a
    # dot, and with_suffix would treat ".5" as the extension and overwrite it.
    safe = embedder_name.replace("/", "_")
    stem = f"{variant}.{safe}"
    return index_dir() / f"{stem}.chunks.jsonl", index_dir() / f"{stem}.vectors.npy"


def save_index(index: MemoryIndex, embedder_name: str) -> Path:
    chunks_path, vectors_path = paths(index.variant, embedder_name)
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    with chunks_path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"format": FORMAT, "variant": index.variant, "embedder": embedder_name, "count": len(index.chunks)}) + "\n")
        for c in index.chunks:
            fh.write(json.dumps(asdict(c), separators=(",", ":")) + "\n")
    np.save(vectors_path, index.emb)
    return chunks_path


def load_index(variant: str, embedder_name: str) -> MemoryIndex | None:
    """Return the saved index, or None when it is absent or does not match."""
    chunks_path, vectors_path = paths(variant, embedder_name)
    if not (chunks_path.exists() and vectors_path.exists()):
        return None
    try:
        with chunks_path.open(encoding="utf-8") as fh:
            header = json.loads(fh.readline())
            if header.get("format") != FORMAT or header.get("embedder") != embedder_name:
                return None
            chunks = [Chunk(**json.loads(line)) for line in fh if line.strip()]
        emb = np.load(vectors_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if len(chunks) != len(emb):
        return None
    return MemoryIndex(variant, chunks, emb)
