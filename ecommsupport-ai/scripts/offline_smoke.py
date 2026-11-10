"""Run the eval harness with no API keys: fake LLM, in-memory vectors, local memory.

Proves the plumbing (MCP over stdio, MySQL, guardrails, routing, memory,
evaluators, report) end to end. The scores reflect the rule-based stand-in
model, not GPT-4o; run `python -m evals.run_evals` for real numbers.

    MEMORY_BACKEND=local python -m scripts.offline_smoke
"""

import asyncio
import os
import sys
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["MEMORY_BACKEND"] = "local"
os.environ["LANGSMITH_TRACING"] = "false"

from common import llm  # noqa: E402
from evals import run_evals  # noqa: E402
from rag.ingest import ingest  # noqa: E402
from rag.vectorstore import InMemoryStore, set_store  # noqa: E402
from tests.fakes import FakeOpenAI  # noqa: E402
from tests.scripted_model import agent_handler  # noqa: E402

llm.set_client(FakeOpenAI(agent_handler))
store = InMemoryStore()
set_store(store)
ingest(store)
run_evals.REPORT = Path("/tmp/offline_eval_results.md")
asyncio.run(run_evals.main_async(Namespace(local=True, compare_routing=True, category=None, concurrency=3)))
