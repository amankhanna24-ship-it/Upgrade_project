"""Persistent conversational memory in Upstash Redis with rolling summaries.

Keys per session (all expire after SESSION_TTL_S of inactivity):

    novashop:{sid}:messages   list of JSON {"role", "content"}: the recent window
    novashop:{sid}:summary    rolling summary of everything older than the window
    novashop:{sid}:facts      hash of durable entities seen in the conversation
                              (order IDs, product IDs). Extracted with regex before
                              any trimming, so an order number given early on
                              survives summarisation word for word.

Context-window management
-------------------------
When the window holds more than MAX_MESSAGES messages, or more than
MAX_WINDOW_TOKENS tokens, everything except the newest KEEP_MESSAGES is folded
into the summary by gpt-4o-mini (old summary + evicted messages -> new summary)
and the list is trimmed. The prompt therefore stays bounded however long the
conversation runs:

    system prompt + summary (<= ~250 tokens) + facts + last KEEP_MESSAGES
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from common.llm import chat

SESSION_TTL_S = int(os.getenv("SESSION_TTL_S", str(7 * 24 * 3600)))
MAX_MESSAGES = int(os.getenv("MEMORY_MAX_MESSAGES", "12"))
KEEP_MESSAGES = int(os.getenv("MEMORY_KEEP_MESSAGES", "6"))
MAX_WINDOW_TOKENS = int(os.getenv("MEMORY_MAX_WINDOW_TOKENS", "1500"))

ORDER_RE = re.compile(r"\bORD-?\d{4,8}\b", re.IGNORECASE)
PRODUCT_RE = re.compile(r"\bNS-[A-Z]{3}-\d{3}\b", re.IGNORECASE)

SUMMARY_PROMPT = (
    "You are a conversation summariser for a customer-support chat. Merge the existing summary and the new "
    "messages into one updated summary of at most 120 words. Keep: order numbers, product names and IDs, what "
    "the customer asked for, what was resolved, what is still open and any commitments support made. Drop "
    "greetings and small talk. Do not include email addresses, phone numbers or street addresses. "
    "Write in the third person."
)


@lru_cache(maxsize=1)
def _encoder() -> Any:
    try:
        import tiktoken

        return tiktoken.encoding_for_model("gpt-4o")
    except Exception:  # no network for the encoding file, or tiktoken missing
        return None


def count_tokens(text: str) -> int:
    enc = _encoder()
    return len(enc.encode(text)) if enc else max(1, len(text) // 4)


@dataclass
class MemoryState:
    summary: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)
    facts: dict[str, str] = field(default_factory=dict)

    def as_prompt_messages(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        notes = []
        if self.summary:
            notes.append(f"Summary of the earlier conversation: {self.summary}")
        if self.facts:
            notes.append(
                "Identifiers from earlier in this session (from the customer or from tool results; "
                "use these instead of guessing): " + json.dumps(self.facts)
            )
        if notes:
            out.append({"role": "system", "content": "\n".join(notes)})
        return out + self.messages


class RedisMemory:
    def __init__(self, redis: Any | None = None, prefix: str = "novashop"):
        self.r = redis or _upstash()
        self.prefix = prefix

    def _k(self, sid: str, name: str) -> str:
        return f"{self.prefix}:{sid}:{name}"

    # ------------------------------------------------------------------ read
    def load(self, sid: str) -> MemoryState:
        raw = self.r.lrange(self._k(sid, "messages"), 0, -1) or []
        return MemoryState(
            summary=self.r.get(self._k(sid, "summary")) or "",
            messages=[json.loads(m) for m in raw],
            facts=self.r.hgetall(self._k(sid, "facts")) or {},
        )

    # ------------------------------------------------------------------ write
    def append(self, sid: str, role: str, content: str) -> None:
        key = self._k(sid, "messages")
        self.r.rpush(key, json.dumps({"role": role, "content": content}))
        self._remember_entities(sid, content)
        self._touch(sid)

    def _remember_entities(self, sid: str, text: str) -> None:
        found: dict[str, str] = {}
        orders = [o.upper().replace("ORD", "ORD-").replace("--", "-") for o in ORDER_RE.findall(text)]
        if orders:
            found["last_order_id"] = orders[-1]
        products = [p.upper() for p in PRODUCT_RE.findall(text)]
        if products:
            found["last_product_id"] = products[-1]
        if found:
            existing = self.r.hgetall(self._k(sid, "facts")) or {}
            all_orders = set(filter(None, existing.get("order_ids", "").split(","))) | set(orders)
            if all_orders:
                found["order_ids"] = ",".join(sorted(all_orders))
            self.r.hset(self._k(sid, "facts"), values=found)

    def remember_tool_results(self, sid: str, results: list[tuple[str, Any]]) -> None:
        """Keep identifiers that only appeared in tool output.

        Only dialogue text goes into the message list, so without this a product ID the
        agent looked up in one turn (e.g. GlideMouse -> NS-PER-002) is gone by the next turn,
        when the customer says "yes, place the order".
        """
        found: dict[str, str] = {}
        for name, r in results:
            if not isinstance(r, dict) or r.get("ok") is False:
                continue
            for p in (r.get("products") or [])[:3]:
                if isinstance(p, dict) and p.get("product_id") and p.get("name"):
                    found[f"product_id:{p['name']}"] = p["product_id"]
            order = r.get("order")
            if isinstance(order, dict) and order.get("order_id"):
                found["last_order_id"] = order["order_id"]
            if name == "place_order" and r.get("order_id"):
                found["last_placed_order_id"] = r["order_id"]
        if found:
            self.r.hset(self._k(sid, "facts"), values=found)

    def _touch(self, sid: str) -> None:
        for name in ("messages", "summary", "facts"):
            self.r.expire(self._k(sid, name), SESSION_TTL_S)

    def clear(self, sid: str) -> None:
        self.r.delete(*(self._k(sid, n) for n in ("messages", "summary", "facts")))

    # ------------------------------------------------------------------ compaction
    def needs_compaction(self, state: MemoryState) -> bool:
        tokens = sum(count_tokens(m["content"]) for m in state.messages)
        return len(state.messages) > MAX_MESSAGES or (
            tokens > MAX_WINDOW_TOKENS and len(state.messages) > KEEP_MESSAGES
        )

    def compact(self, sid: str) -> bool:
        """Fold older messages into the rolling summary. Returns True if it ran."""
        state = self.load(sid)
        if not self.needs_compaction(state):
            return False
        evicted = state.messages[:-KEEP_MESSAGES]
        transcript = "\n".join(f"{m['role']}: {m['content']}" for m in evicted)
        msg = chat(
            [
                {"role": "system", "content": SUMMARY_PROMPT},
                {
                    "role": "user",
                    "content": f"Existing summary:\n{state.summary or '(none)'}\n\nNew messages:\n{transcript}",
                },
            ],
            tier="fast",
            purpose="memory:summarise",
            max_tokens=220,
        )
        self.r.set(self._k(sid, "summary"), (msg.content or "").strip(), ex=SESSION_TTL_S)
        self.r.ltrim(self._k(sid, "messages"), -KEEP_MESSAGES, -1)
        return True


def _upstash() -> Any:
    if os.getenv("MEMORY_BACKEND", "upstash").lower() == "local":
        from agent.local_redis import LocalRedis

        return LocalRedis()
    from upstash_redis import Redis

    return Redis(url=os.environ["UPSTASH_REDIS_REST_URL"], token=os.environ["UPSTASH_REDIS_REST_TOKEN"])
