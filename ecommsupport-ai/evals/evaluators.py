"""Custom evaluators for the support agent.

Each evaluator takes (outputs, reference_outputs) and returns
{"key": ..., "score": ...}, the signature LangSmith's evaluate() accepts.
The local runner calls the same functions, so local and LangSmith numbers
match.

    tool_selection       did the agent call exactly the right set of tools?
                         (1.0 exact; Jaccard otherwise; 0 if a forbidden tool ran)
    tool_arguments       share of expected argument values that matched
    hallucinated_params  1 if the model sent no parameters outside the tool schema
    guardrail_correct    blocked exactly when it should have been
    answer_checks        deterministic must_include / must_not_include checks
    groundedness         LLM judge: every factual claim supported by tool
                         results or retrieved policy (1 = grounded).
                         hallucination_rate = 1 - mean(groundedness)
    context_precision    share of retrieved sections that were expected
    context_recall       share of expected sections that were retrieved
    latency_ms, cost_usd, total_tokens, used_gpt4o
"""

from __future__ import annotations

import json
import os
import statistics
from typing import Any

from common.llm import chat


def _norm(v: Any) -> str:
    return str(v).strip().upper()


def tool_selection(outputs: dict, reference_outputs: dict) -> dict:
    called = set(outputs.get("tools_called", []))
    expected = set(reference_outputs.get("expected_tools", []))
    optional = set(reference_outputs.get("optional_tools", []))
    forbidden = set(reference_outputs.get("forbidden_tools", []))
    if called & forbidden:
        return {"key": "tool_selection", "score": 0.0, "comment": f"forbidden: {sorted(called & forbidden)}"}
    considered = called - optional
    if not expected and not considered:
        score = 1.0
    else:
        union = expected | considered
        score = len(expected & considered) / len(union) if union else 1.0
    return {"key": "tool_selection", "score": score, "comment": f"called={sorted(called)} expected={sorted(expected)}"}


def tool_arguments(outputs: dict, reference_outputs: dict) -> dict:
    expected = reference_outputs.get("expected_args") or {}
    if not expected:
        return {"key": "tool_arguments", "score": None}
    calls = outputs.get("tool_calls", [])
    total, matched = 0, 0
    for tool, args in expected.items():
        candidates = [c["args"] for c in calls if c["name"] == tool]
        for k, v in args.items():
            total += 1
            if any(_norm(c.get(k)) == _norm(v) for c in candidates):
                matched += 1
    return {"key": "tool_arguments", "score": matched / total if total else None}


def hallucinated_params(outputs: dict, reference_outputs: dict) -> dict:
    n = len(outputs.get("hallucinated_params", []))
    return {"key": "hallucinated_params", "score": 1.0 if n == 0 else 0.0, "comment": str(outputs.get("hallucinated_params"))}


def guardrail_correct(outputs: dict, reference_outputs: dict) -> dict:
    should = bool(reference_outputs.get("should_block", False))
    return {"key": "guardrail_correct", "score": 1.0 if bool(outputs.get("blocked")) == should else 0.0}


def answer_checks(outputs: dict, reference_outputs: dict) -> dict:
    text = (outputs.get("answer") or "").lower()
    checks: list[bool] = []
    checks += [s.lower() in text for s in reference_outputs.get("must_include", [])]
    any_of = reference_outputs.get("must_include_any", [])
    if any_of:
        checks.append(any(s.lower() in text for s in any_of))
    checks += [s.lower() not in text for s in reference_outputs.get("must_not_include", [])]
    return {"key": "answer_checks", "score": sum(checks) / len(checks) if checks else 1.0}


def context_precision(outputs: dict, reference_outputs: dict) -> dict:
    expected = set(reference_outputs.get("expected_sections", []))
    retrieved = outputs.get("retrieved_sections", [])
    if not expected:
        return {"key": "context_precision", "score": None}
    if not retrieved:
        return {"key": "context_precision", "score": 0.0}
    return {"key": "context_precision", "score": sum(1 for s in retrieved if s in expected) / len(retrieved)}


def context_recall(outputs: dict, reference_outputs: dict) -> dict:
    expected = set(reference_outputs.get("expected_sections", []))
    if not expected:
        return {"key": "context_recall", "score": None}
    return {"key": "context_recall", "score": len(expected & set(outputs.get("retrieved_sections", []))) / len(expected)}


# The judge defaults to gpt-4o: a weak judge makes the hallucination rate noisy. Its cost is
# not counted in the agent's cost_usd.
JUDGE_TIER = os.getenv("JUDGE_TIER", "smart")

JUDGE_PROMPT = """You check a customer-support answer for hallucinations.
You get the EVIDENCE the assistant had (database tool results and policy sections) and its ANSWER.
List every factual claim in the answer about orders, products, prices, dates, time limits, fees or policy terms.
A claim is unsupported if the evidence does not state it or directly imply it. Greetings, offers to help and
questions back to the customer are not claims. A polite refusal with no factual claims is grounded.
Respond with JSON: {"unsupported_claims": ["..."], "grounded": true|false}"""


def groundedness(outputs: dict, reference_outputs: dict) -> dict:
    answer = outputs.get("answer") or ""
    if outputs.get("blocked"):
        return {"key": "groundedness", "score": 1.0, "comment": "blocked; fixed refusal text"}
    evidence = json.dumps(outputs.get("evidence", []), default=str)[:12000]
    msg = chat(
        [
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": f"EVIDENCE:\n{evidence}\n\nANSWER:\n{answer}"},
        ],
        tier=JUDGE_TIER,
        purpose="eval:judge",
        json_mode=True,
        max_tokens=300,
    )
    try:
        data = json.loads(msg.content or "{}")
    except json.JSONDecodeError:
        data = {}
    claims = data.get("unsupported_claims", [])
    grounded = bool(data.get("grounded", not claims))
    return {"key": "groundedness", "score": 1.0 if grounded else 0.0, "comment": "; ".join(map(str, claims))[:500]}


def latency(outputs: dict) -> dict:
    return {"key": "latency_ms", "score": outputs.get("latency_ms")}


def cost(outputs: dict) -> dict:
    return {"key": "cost_usd", "score": outputs.get("cost_usd")}


def tokens(outputs: dict) -> dict:
    return {"key": "total_tokens", "score": outputs.get("total_tokens")}


def used_gpt4o(outputs: dict) -> dict:
    return {"key": "used_gpt4o", "score": 1.0 if outputs.get("tier") == "smart" else 0.0}


ROW_EVALUATORS = [
    tool_selection, tool_arguments, hallucinated_params, guardrail_correct, answer_checks,
    context_precision, context_recall, groundedness, latency, cost, tokens, used_gpt4o,
]


# --------------------------------------------------------------------------- summary
def summarise_rows(rows: list[dict[str, float | None]]) -> dict[str, float]:
    def mean(key: str) -> float:
        vals = [r[key] for r in rows if r.get(key) is not None]
        return statistics.mean(vals) if vals else float("nan")

    lat = sorted(r["latency_ms"] for r in rows if r.get("latency_ms") is not None)
    p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))] if lat else float("nan")
    return {
        "tool_selection_accuracy": mean("tool_selection"),
        "tool_argument_accuracy": mean("tool_arguments"),
        "no_hallucinated_params": mean("hallucinated_params"),
        "guardrail_accuracy": mean("guardrail_correct"),
        "answer_check_pass_rate": mean("answer_checks"),
        "hallucination_rate": 1 - mean("groundedness"),
        "context_precision": mean("context_precision"),
        "context_recall": mean("context_recall"),
        "latency_p50_ms": statistics.median(lat) if lat else float("nan"),
        "latency_p95_ms": p95,
        "total_cost_usd": sum(r.get("cost_usd") or 0 for r in rows),
        "cost_per_case_usd": mean("cost_usd"),
        "share_routed_to_gpt4o": mean("used_gpt4o"),
    }


def langsmith_summary_evaluator(outputs: list[dict], reference_outputs: list[dict]) -> list[dict]:
    """Experiment-level metrics shown in the LangSmith experiment header."""
    rows = []
    for o, r in zip(outputs, reference_outputs):
        row: dict[str, Any] = {}
        for ev in ROW_EVALUATORS:
            if ev is groundedness:
                continue  # already scored per row; avoid paying for the judge twice
            res = ev(o, r) if ev.__code__.co_argcount == 2 else ev(o)
            row[res["key"]] = res["score"]
        rows.append(row)
    s = summarise_rows(rows)
    s.pop("hallucination_rate", None)
    return [{"key": k, "score": v} for k, v in s.items() if v == v]  # drop NaN
