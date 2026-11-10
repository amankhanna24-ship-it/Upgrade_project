"""Offline stand-ins for OpenAI and Upstash so the pipeline runs without keys.

These check plumbing (tool-call loops, routing, memory, guardrails, metrics),
not model quality. Real quality numbers come from running the benchmark and
the evals against the live APIs.
"""

from __future__ import annotations

import hashlib
import json
import re
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np

DIM = 512
_STOP = set("a an the i my me is it to of and or for on in do does can be if with you your this that have has am".split())


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w[:6] for w in words if w not in _STOP]  # crude stemming


def fake_embedding(text: str) -> list[float]:
    vec = np.zeros(DIM)
    for tok in _tokens(text):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % DIM] += 1.0
    n = np.linalg.norm(vec)
    return (vec / n if n else vec).tolist()


def _usage(p: int = 50, c: int = 20) -> SimpleNamespace:
    return SimpleNamespace(prompt_tokens=p, completion_tokens=c, total_tokens=p + c)


def message(content: str | None = None, tool_calls: list[dict] | None = None) -> SimpleNamespace:
    calls = None
    if tool_calls:
        calls = [
            SimpleNamespace(
                id=tc.get("id", f"call_{i}"),
                type="function",
                function=SimpleNamespace(name=tc["name"], arguments=json.dumps(tc.get("args", {}))),
            )
            for i, tc in enumerate(tool_calls)
        ]
    return SimpleNamespace(role="assistant", content=content, tool_calls=calls)


ChatHandler = Callable[[dict[str, Any]], SimpleNamespace]


def default_handler(kwargs: dict[str, Any]) -> SimpleNamespace:
    """Rule-based responses keyed off the system prompt, for the helper LLM calls."""
    system = next((m["content"] for m in kwargs["messages"] if m["role"] == "system"), "")
    user = next((m["content"] for m in reversed(kwargs["messages"]) if m["role"] == "user"), "")
    if "policy documents" in system and "Write the passage" in system:  # HyDE
        return message(f"Policy: {user} Items may be returned, refunded, repaired or replaced under warranty.")
    if "exactly 3 search queries" in system:  # multi-query
        return message(json.dumps({"queries": [f"{user} return window", f"{user} warranty cover", f"{user} damage"]}))
    if "conversation summariser" in system.lower():
        return message("Customer discussed order ORD-1002 (NovaBook Pro 14) and asked about returns.")
    return message("OK.")


class FakeOpenAI:
    def __init__(self, handler: ChatHandler | None = None, model_echo: bool = True):
        self.handler = handler or default_handler
        self.calls: list[dict[str, Any]] = []
        outer = self

        class _Completions:
            def create(self, **kwargs: Any) -> SimpleNamespace:
                kwargs.pop("langsmith_extra", None)
                outer.calls.append(kwargs)
                msg = outer.handler(kwargs)
                return SimpleNamespace(model=kwargs["model"], choices=[SimpleNamespace(message=msg)], usage=_usage())

        class _Embeddings:
            def create(self, model: str, input: list[str]) -> SimpleNamespace:
                outer.calls.append({"model": model, "embed": len(input)})
                return SimpleNamespace(
                    data=[SimpleNamespace(embedding=fake_embedding(t)) for t in input],
                    usage=_usage(sum(len(t.split()) for t in input), 0),
                )

        self.chat = SimpleNamespace(completions=_Completions())
        self.embeddings = _Embeddings()

    def models_used(self) -> list[str]:
        return [c["model"] for c in self.calls if "messages" in c]


from agent.local_redis import LocalRedis as FakeRedis  # noqa: E402,F401
