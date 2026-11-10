"""MCP client: launches the NovaShop MCP server and exposes its tools to OpenAI.

Two security controls live here, between the LLM and the database tools:

* `customer_id` is removed from every tool schema the LLM sees and injected
  from the authenticated session at call time. The model cannot choose whose
  data to read or which account to charge.
* Arguments are checked against the tool's JSON schema before the call.
  Unknown (hallucinated) parameters are dropped and counted, and missing
  required parameters are returned to the model as an error, not guessed.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

from common.pii import get_logger

log = get_logger("novashop.mcp_client")
ROOT = Path(__file__).resolve().parent.parent
INJECTED_PARAMS = {"customer_id"}
# Called by the orchestrator itself, never offered to the model.
HIDDEN_TOOLS = {"get_customer_profile"}


def server_params() -> StdioServerParameters:
    env = {k: v for k, v in os.environ.items() if k.startswith(("MYSQL_", "PATH", "PYTHON"))}
    return StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"], env=env, cwd=str(ROOT))


class MCPToolbox:
    """Async context manager. Pass `target` (an MCPServer) to run in-process in tests."""

    def __init__(self, target: Any | None = None):
        self._target = target
        self._stack = AsyncExitStack()
        self.client: Client | None = None
        self.schemas: dict[str, dict[str, Any]] = {}
        self.descriptions: dict[str, str] = {}
        self.hallucinated_params: list[tuple[str, str]] = []

    async def __aenter__(self) -> "MCPToolbox":
        self.client = await self._stack.enter_async_context(Client(self._target or server_params()))
        listed = await self.client.list_tools()
        for t in listed.tools:
            self.schemas[t.name] = t.input_schema
            self.descriptions[t.name] = t.description or ""
        log.info("connected to MCP server, tools=%s", list(self.schemas))
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._stack.aclose()

    # ------------------------------------------------------------------ schemas for OpenAI
    def openai_tools(self) -> list[dict[str, Any]]:
        tools = []
        for name, schema in self.schemas.items():
            if name in HIDDEN_TOOLS:
                continue
            props = {k: _clean_prop(v) for k, v in schema.get("properties", {}).items() if k not in INJECTED_PARAMS}
            required = [r for r in schema.get("required", []) if r not in INJECTED_PARAMS]
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": self.descriptions[name],
                        "parameters": {
                            "type": "object",
                            "properties": props,
                            "required": required,
                            "additionalProperties": False,
                        },
                    },
                }
            )
        return tools

    # ------------------------------------------------------------------ calls
    def validate_args(
        self, name: str, args: dict[str, Any], dropped: list[tuple[str, str]] | None = None
    ) -> tuple[dict[str, Any], str | None]:
        schema = self.schemas[name]
        allowed = set(schema.get("properties", {})) - INJECTED_PARAMS
        clean = {}
        for k, v in args.items():
            if k in allowed:
                clean[k] = v
            else:
                self.hallucinated_params.append((name, k))
                if dropped is not None:
                    dropped.append((name, k))
                log.warning("dropped unknown parameter %s.%s", name, k)
        missing = [r for r in schema.get("required", []) if r not in INJECTED_PARAMS and r not in clean]
        if missing:
            return clean, f"Missing required argument(s): {', '.join(missing)}. Ask the customer; do not guess."
        return clean, None

    async def call(
        self, name: str, args: dict[str, Any], customer_id: str, internal: bool = False,
        dropped: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        if name not in self.schemas or (name in HIDDEN_TOOLS and not internal):
            return {"ok": False, "error": "unknown_tool", "message": f"No tool named {name}."}
        clean, problem = self.validate_args(name, args, dropped)
        if problem:
            return {"ok": False, "error": "invalid_arguments", "message": problem}
        if "customer_id" in self.schemas[name].get("properties", {}):
            clean["customer_id"] = customer_id
        assert self.client is not None
        result = await self.client.call_tool(name, clean)
        if getattr(result, "structured_content", None):
            data = result.structured_content
            # Some servers wrap non-object returns as {"result": ...}
            return data.get("result", data) if isinstance(data, dict) and set(data) == {"result"} else data
        text = "".join(getattr(c, "text", "") for c in result.content)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"ok": not getattr(result, "is_error", False), "text": text}


def _clean_prop(prop: dict[str, Any]) -> dict[str, Any]:
    """Collapse pydantic's anyOf[str, null] into something OpenAI handles cleanly."""
    prop = {k: v for k, v in prop.items() if k not in {"title", "default"}}
    if "anyOf" in prop:
        types = [t for t in prop.pop("anyOf") if t.get("type") != "null"]
        if len(types) == 1:
            prop.update(types[0])
    return prop
