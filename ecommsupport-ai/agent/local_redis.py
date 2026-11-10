"""In-process stand-in for Upstash Redis (MEMORY_BACKEND=local). Not persistent; for dev and tests."""

from __future__ import annotations

from typing import Any


class LocalRedis:
    """Implements the subset of upstash_redis.Redis the memory layer uses."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self.data.get(key, [])
        end = len(items) if end == -1 else end + 1
        return items[start:end]

    def rpush(self, key: str, *values: str) -> int:
        self.data.setdefault(key, []).extend(values)
        return len(self.data[key])

    def ltrim(self, key: str, start: int, end: int) -> str:
        items = self.data.get(key, [])
        n = len(items)
        s = start if start >= 0 else max(n + start, 0)
        e = n if end == -1 else (end + 1 if end >= 0 else n + end + 1)
        self.data[key] = items[s:e]
        return "OK"

    def llen(self, key: str) -> int:
        return len(self.data.get(key, []))

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.data[key] = value
        return True

    def hset(self, key: str, field: str | None = None, value: str | None = None, values: dict | None = None) -> int:
        h = self.data.setdefault(key, {})
        if values:
            h.update(values)
        elif field is not None:
            h[field] = value
        return 1

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.data.get(key, {}))

    def expire(self, key: str, seconds: int) -> bool:
        return True

    def delete(self, *keys: str) -> int:
        return sum(1 for k in keys if self.data.pop(k, None) is not None)
