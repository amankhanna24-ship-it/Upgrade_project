"""FastAPI backend and chat UI for the NovaShop support agent.

    uvicorn app.api:app --reload --port 8000     then open http://localhost:8000
"""

from __future__ import annotations

import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.mcp_client import MCPToolbox
from agent.memory import RedisMemory
from agent.orchestrator import SupportAgent

STATIC = Path(__file__).with_name("static")
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

# Demo sign-in. In production customer_id comes from your auth layer (a verified
# session or JWT), never from the request body; the agent treats it as trusted.
DEMO_CUSTOMERS = {
    "CUST-001": "Priya (Plus)", "CUST-002": "James", "CUST-003": "Sofia",
    "CUST-004": "Arjun (Plus)", "CUST-005": "Emma",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with MCPToolbox() as toolbox:
        app.state.agent = SupportAgent(
            toolbox, RedisMemory(), rag_strategy=os.getenv("RAG_STRATEGY", "dynamic"),
            enable_routing=os.getenv("MODEL_ROUTING", "on") != "off",
        )
        yield


app = FastAPI(title="eCommSupport-AI", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class ChatIn(BaseModel):
    session_id: str = Field(..., min_length=6, max_length=64)
    customer_id: str
    message: str = Field(..., min_length=1, max_length=4000)


def _check(session_id: str, customer_id: str) -> None:
    if not SESSION_RE.fullmatch(session_id):
        raise HTTPException(400, "bad session id")
    if customer_id not in DEMO_CUSTOMERS:
        raise HTTPException(401, "unknown customer")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/customers")
def customers() -> dict:
    return {"customers": [{"id": k, "label": v} for k, v in DEMO_CUSTOMERS.items()]}


@app.post("/api/session")
def new_session() -> dict:
    return {"session_id": uuid.uuid4().hex}


@app.post("/api/chat")
async def chat(body: ChatIn) -> dict:
    _check(body.session_id, body.customer_id)
    # Namespace memory by customer so switching accounts can't read another's history.
    sid = f"{body.customer_id}:{body.session_id}"
    reply = await app.state.agent.handle(sid, body.customer_id, body.message)
    return reply.to_dict()


@app.get("/api/memory/{customer_id}/{session_id}")
def memory(customer_id: str, session_id: str) -> dict:
    _check(session_id, customer_id)
    state = app.state.agent.memory.load(f"{customer_id}:{session_id}")
    return {"summary": state.summary, "facts": state.facts, "window_messages": len(state.messages)}


@app.delete("/api/memory/{customer_id}/{session_id}")
def clear_memory(customer_id: str, session_id: str) -> dict:
    _check(session_id, customer_id)
    app.state.agent.memory.clear(f"{customer_id}:{session_id}")
    return {"ok": True}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "tools": [t["function"]["name"] for t in app.state.agent.tools()]}
