"""Serve the UI with the fake model (no API keys) to check the front end.

    python -m scripts.offline_server   then open http://localhost:8000
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["MEMORY_BACKEND"] = "local"
os.environ["LANGSMITH_TRACING"] = "false"

import uvicorn  # noqa: E402

from common import llm  # noqa: E402
from rag.ingest import ingest  # noqa: E402
from rag.vectorstore import InMemoryStore, set_store  # noqa: E402
from tests.fakes import FakeOpenAI  # noqa: E402
from tests.scripted_model import agent_handler  # noqa: E402

llm.set_client(FakeOpenAI(agent_handler))
store = InMemoryStore()
set_store(store)
ingest(store)

if __name__ == "__main__":
    uvicorn.run("app.api:app", port=int(os.getenv("PORT", "8000")))
