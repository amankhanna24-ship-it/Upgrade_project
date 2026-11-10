"""OpenAI access, model routing tiers, cost accounting and LangSmith wiring.

Everything that talks to OpenAI goes through `chat()` or `embed()` so that:
* each call is tagged with a *purpose* (guardrail, router, hyde, agent ...),
* the model is chosen by *tier* ("fast" or "smart"), not hard-coded,
* token usage and cost are recorded per request for the evals and the UI.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterator

from dotenv import load_dotenv

load_dotenv()

FAST_MODEL = os.getenv("FAST_MODEL", "gpt-4o-mini")
SMART_MODEL = os.getenv("SMART_MODEL", "gpt-4o")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1536"))

# USD per 1M tokens. Check https://openai.com/api/pricing and override in .env if they change.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (float(os.getenv("PRICE_MINI_IN", "0.15")), float(os.getenv("PRICE_MINI_OUT", "0.60"))),
    "gpt-4o": (float(os.getenv("PRICE_4O_IN", "2.50")), float(os.getenv("PRICE_4O_OUT", "10.00"))),
    "text-embedding-3-small": (float(os.getenv("PRICE_EMBED", "0.02")), 0.0),
}

TIERS = {"fast": FAST_MODEL, "smart": SMART_MODEL}


# --------------------------------------------------------------------------- cost tracking
@dataclass
class Usage:
    model: str
    purpose: str
    prompt_tokens: int
    completion_tokens: int

    @property
    def cost_usd(self) -> float:
        p_in, p_out = PRICES.get(self.model, (0.0, 0.0))
        return (self.prompt_tokens * p_in + self.completion_tokens * p_out) / 1_000_000


@dataclass
class CostTracker:
    calls: list[Usage] = field(default_factory=list)

    def add(self, usage: Usage) -> None:
        self.calls.append(usage)

    @property
    def total_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def total_tokens(self) -> int:
        return sum(c.prompt_tokens + c.completion_tokens for c in self.calls)

    def by_model(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for c in self.calls:
            m = out.setdefault(c.model, {"calls": 0, "tokens": 0, "cost_usd": 0.0})
            m["calls"] += 1
            m["tokens"] += c.prompt_tokens + c.completion_tokens
            m["cost_usd"] += c.cost_usd
        return out

    def summary(self) -> dict[str, Any]:
        return {
            "total_usd": round(self.total_usd, 6),
            "total_tokens": self.total_tokens,
            "by_model": self.by_model(),
            "calls": [
                {"model": c.model, "purpose": c.purpose, "in": c.prompt_tokens, "out": c.completion_tokens}
                for c in self.calls
            ],
        }


_tracker: contextvars.ContextVar[CostTracker | None] = contextvars.ContextVar("cost_tracker", default=None)


@contextlib.contextmanager
def track_costs() -> Iterator[CostTracker]:
    tracker = CostTracker()
    token = _tracker.set(tracker)
    try:
        yield tracker
    finally:
        _tracker.reset(token)


def _record(model: str, purpose: str, usage: Any) -> None:
    tracker = _tracker.get()
    if tracker is None or usage is None:
        return
    tracker.add(
        Usage(
            model=_base_model(model),
            purpose=purpose,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
    )


def _base_model(model: str) -> str:
    for known in sorted(PRICES, key=len, reverse=True):
        if model.startswith(known):
            return known
    return model


# --------------------------------------------------------------------------- clients
_client_override: Any = None


def set_client(client: Any) -> None:
    """Inject a fake client in tests."""
    global _client_override
    _client_override = client


def tracing_enabled() -> bool:
    return os.getenv("LANGSMITH_TRACING", "false").lower() == "true" and bool(os.getenv("LANGSMITH_API_KEY"))


@lru_cache(maxsize=1)
def _real_client() -> Any:
    from openai import OpenAI

    client = OpenAI()
    if tracing_enabled():
        from langsmith.wrappers import wrap_openai

        client = wrap_openai(client)
    return client


def client() -> Any:
    return _client_override or _real_client()


@lru_cache(maxsize=1)
def langsmith_client() -> Any:
    """LangSmith client that redacts PII from every trace before upload."""
    from langsmith import Client

    from common.pii import langsmith_anonymizer

    return Client(anonymizer=langsmith_anonymizer)


@contextlib.contextmanager
def traced(**metadata: Any) -> Iterator[None]:
    """Route all traces in this block through the PII-redacting client."""
    if not tracing_enabled():
        yield
        return
    import langsmith

    with langsmith.tracing_context(client=langsmith_client(), metadata=metadata or None):
        yield


# --------------------------------------------------------------------------- calls
def chat(
    messages: list[dict[str, Any]],
    *,
    tier: str = "fast",
    purpose: str = "general",
    tools: list[dict[str, Any]] | None = None,
    json_mode: bool = False,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> Any:
    model = TIERS[tier]
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if tools:
        kwargs["tools"] = tools
        kwargs["parallel_tool_calls"] = True
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    if tracing_enabled():
        kwargs["langsmith_extra"] = {"metadata": {"purpose": purpose, "tier": tier}, "name": f"llm:{purpose}"}
    resp = client().chat.completions.create(**kwargs)
    _record(getattr(resp, "model", model), purpose, getattr(resp, "usage", None))
    return resp.choices[0].message


def embed(texts: list[str], purpose: str = "embed") -> list[list[float]]:
    resp = client().embeddings.create(model=EMBED_MODEL, input=texts)
    _record(EMBED_MODEL, purpose, getattr(resp, "usage", None))
    return [d.embedding for d in resp.data]
