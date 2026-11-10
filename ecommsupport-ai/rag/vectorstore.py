"""Vector store backends: Pinecone for real use, in-memory for tests."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from common.llm import EMBED_DIM

INDEX_NAME = os.getenv("PINECONE_INDEX", "novashop-policies")
NAMESPACE = os.getenv("PINECONE_NAMESPACE", "policies-v1")


@dataclass
class Hit:
    id: str
    score: float
    metadata: dict[str, Any]


class VectorStore(Protocol):
    def upsert(self, items: list[tuple[str, list[float], dict[str, Any]]]) -> None: ...
    def query(self, vector: list[float], top_k: int) -> list[Hit]: ...


class PineconeStore:
    def __init__(self, index_name: str = INDEX_NAME, namespace: str = NAMESPACE, create: bool = False):
        from pinecone import Pinecone, ServerlessSpec

        self.pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
        self.namespace = namespace
        if create and not self.pc.has_index(index_name):
            self.pc.create_index(
                name=index_name,
                dimension=EMBED_DIM,
                metric="cosine",
                spec=ServerlessSpec(
                    cloud=os.getenv("PINECONE_CLOUD", "aws"), region=os.getenv("PINECONE_REGION", "us-east-1")
                ),
            )
            time.sleep(5)
        self.index = self.pc.index(index_name)

    def upsert(self, items: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        vectors = [{"id": i, "values": v, "metadata": m} for i, v, m in items]
        self.index.upsert(vectors=vectors, namespace=self.namespace, batch_size=50, show_progress=False)

    def query(self, vector: list[float], top_k: int) -> list[Hit]:
        res = self.index.query(vector=vector, top_k=top_k, namespace=self.namespace, include_metadata=True)
        return [Hit(m.id, float(m.score), dict(m.metadata or {})) for m in res.matches]


class InMemoryStore:
    """Exact cosine search with numpy. Used by tests and the offline smoke test."""

    def __init__(self) -> None:
        self.ids: list[str] = []
        self.meta: list[dict[str, Any]] = []
        self.matrix = np.zeros((0, 0))

    def upsert(self, items: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        for i, v, m in items:
            vec = np.asarray(v, dtype=float)
            vec = vec / (np.linalg.norm(vec) or 1.0)
            if i in self.ids:
                idx = self.ids.index(i)
                self.matrix[idx] = vec
                self.meta[idx] = m
                continue
            self.ids.append(i)
            self.meta.append(m)
            self.matrix = vec[None, :] if self.matrix.size == 0 else np.vstack([self.matrix, vec])

    def query(self, vector: list[float], top_k: int) -> list[Hit]:
        if not self.ids:
            return []
        q = np.asarray(vector, dtype=float)
        q = q / (np.linalg.norm(q) or 1.0)
        scores = self.matrix @ q
        order = np.argsort(-scores)[:top_k]
        return [Hit(self.ids[i], float(scores[i]), self.meta[i]) for i in order]


_store: VectorStore | None = None


def get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = PineconeStore()
    return _store


def set_store(store: VectorStore) -> None:
    global _store
    _store = store
