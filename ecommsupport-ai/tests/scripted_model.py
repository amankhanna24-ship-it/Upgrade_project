"""A rule-based stand-in for the agent LLM, used by offline tests.

It reads the same prompt the real model receives (system prompt, memory
summary, facts, history, tool results) and makes tool calls by simple rules.
If it can find an order number from three turns earlier, that number reached
the model's context, which is what the memory test needs to prove.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

from tests.fakes import default_handler, message

ORDER_RE = re.compile(r"ORD-\d{4,8}", re.I)
POLICY_RE = re.compile(r"return|refund|warrant|novacare|damag|deliver|ship|fee", re.I)


def agent_handler(kwargs: dict[str, Any]) -> SimpleNamespace:
    msgs = kwargs["messages"]
    system = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else ""
    if "NOVASHOP_SYSTEM_PROMPT" not in system:
        return default_handler(kwargs)

    last = msgs[-1]
    if last["role"] == "tool":
        results = [json.loads(m["content"]) for m in msgs if m["role"] == "tool"]
        parts = []
        for r in results:
            if "order" in r:
                o = r["order"]
                parts.append(f"Order {o['order_id']} is {o['status']} (warranty plan: {o['warranty_plan']}).")
            elif "sections" in r:
                parts.append("Relevant policy: " + ", ".join(f"({s['section']})" for s in r["sections"][:3]))
            elif "products" in r:
                p = r["products"][0]
                parts.append(f"{p['name']} costs £{p['price_gbp']:.2f}.")
            elif r.get("ok") is False:
                parts.append(f"Sorry, {r.get('message', 'that did not work')}")
        return message(" ".join(parts) or "Done.")

    user = last["content"]
    calls = []
    orders = ORDER_RE.findall(user)
    if not orders and re.search(r"\b(it|that order|my order|this order)\b", user, re.I):
        # Look back through everything in context: memory notes and earlier turns.
        context = " ".join(m.get("content") or "" for m in msgs[1:-1])
        found = ORDER_RE.findall(context)
        orders = found[-1:] if found else []
    if orders:
        calls.append({"id": "c1", "name": "check_order_status", "args": {"order_id": orders[0]}})
    if POLICY_RE.search(user):
        calls.append({"id": "c2", "name": "search_policies", "args": {"query": user}})
    m = re.search(r"tell me about (?:your |the )?(.+?)[.?]?$", user, re.I)
    if m:
        calls.append({"id": "c3", "name": "get_product_details", "args": {"search": m.group(1)}})
    if "hallucinate" in user:
        calls.append({"id": "c4", "name": "check_order_status",
                      "args": {"order_id": "ORD-1001", "customer_id": "CUST-002", "admin": True}})
    if calls:
        return message(None, calls)
    if re.search(r"order", user, re.I):
        return message("Could you give me your order number? It looks like ORD-1001.")
    return message("Happy to help with your NovaShop order, products, returns or warranty.")
