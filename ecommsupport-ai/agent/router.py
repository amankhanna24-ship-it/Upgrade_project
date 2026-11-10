"""Model routing: start cheap, escalate when the turn turns out to be hard.

Routing decision 1, before the first model call (free, no LLM call):
    smart (gpt-4o) if the message
      * combines an order reference with a policy question
        ("is ORD-1002 still returnable?"), or
      * is a multi-condition policy question (rag.classify_query), or
      * is an attempt to buy something (place_order is the only write tool)
    otherwise fast (gpt-4o-mini).

Routing decision 2, during the tool loop (escalation):
    switch fast -> smart for the rest of the turn if the model
      * calls two or more *different* tools, i.e. it has to combine
        structured and unstructured data,
      * gets a tool error back, or
      * needs more than 2 iterations.

Helper calls (guardrail classifier, HyDE, multi-query, summaries) always
use the fast tier; see their `tier="fast"` arguments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rag.retrievers import FACETS, classify_query

ORDER_REF = re.compile(r"\bORD-?\d{4,8}\b|\bmy order\b|\bthat order\b|\bthe order\b", re.I)
BUY_INTENT = re.compile(
    r"\b(buy|purchase|order (a|an|one|two|\d)|place (an|the|my|this) order|confirm (the|my) order|i'?ll take|add to (my )?order)\b",
    re.I,
)
POLICY_WORDS = re.compile("|".join(FACETS[f] for f in ("return", "refund", "warranty", "damage", "fault", "fees")), re.I)


@dataclass
class Route:
    tier: str
    reasons: list[str] = field(default_factory=list)
    escalated: bool = False

    def as_dict(self) -> dict:
        return {"tier": self.tier, "reasons": self.reasons, "escalated": self.escalated}


def initial_route(message: str, has_order_in_memory: bool = False) -> Route:
    reasons: list[str] = []
    order_ref = bool(ORDER_REF.search(message)) or (has_order_in_memory and bool(re.search(r"\b(it|that|this)\b", message, re.I)))
    policy = bool(POLICY_WORDS.search(message))
    if order_ref and policy:
        reasons.append("order + policy reasoning")
    if policy and classify_query(message) == "multi_nuanced":
        reasons.append("multi-condition policy question")
    if BUY_INTENT.search(message):
        reasons.append("purchase (write action)")
    return Route("smart" if reasons else "fast", reasons or ["simple lookup"])


def maybe_escalate(route: Route, tools_called: list[str], had_error: bool, iteration: int) -> Route:
    if route.tier == "smart":
        return route
    reason = None
    if len(set(tools_called)) >= 2:
        reason = "multiple tools needed"
    elif had_error:
        reason = "tool error"
    elif iteration >= 2:
        reason = "long tool loop"
    if reason:
        return Route("smart", route.reasons + [f"escalated: {reason}"], escalated=True)
    return route
