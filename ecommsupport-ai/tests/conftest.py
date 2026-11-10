import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-offline")

from common import llm  # noqa: E402
from rag.ingest import ingest  # noqa: E402
from rag.vectorstore import InMemoryStore, set_store  # noqa: E402
from tests.fakes import FakeOpenAI, FakeRedis  # noqa: E402
from tests.scripted_model import agent_handler  # noqa: E402


def _db_available() -> bool:
    try:
        from mcp_server import db

        db.query_one("SELECT 1 AS ok")
        return True
    except Exception:
        return False


DB_AVAILABLE = _db_available()
needs_db = pytest.mark.skipif(not DB_AVAILABLE, reason="MySQL not reachable; run `docker compose up -d mysql`")


@pytest.fixture
def fake_openai():
    client = FakeOpenAI(agent_handler)
    llm.set_client(client)
    yield client
    llm.set_client(None)


@pytest.fixture
def vector_store(fake_openai):
    store = InMemoryStore()
    set_store(store)
    ingest(store)
    return store


@pytest.fixture
def fake_redis():
    return FakeRedis()
