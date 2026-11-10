"""Chunk the policy documents, embed them and upsert into Pinecone.

    python -m rag.ingest            # creates the index if needed
"""

from __future__ import annotations

from common.llm import embed
from rag.chunking import load_policy_chunks
from rag.vectorstore import PineconeStore, VectorStore


def ingest(store: VectorStore) -> int:
    chunks = load_policy_chunks()
    vectors = embed([c.embed_text for c in chunks], purpose="ingest")
    store.upsert([(c.id, v, c.metadata()) for c, v in zip(chunks, vectors)])
    return len(chunks)


if __name__ == "__main__":
    n = ingest(PineconeStore(create=True))
    print(f"Upserted {n} policy chunks.")
