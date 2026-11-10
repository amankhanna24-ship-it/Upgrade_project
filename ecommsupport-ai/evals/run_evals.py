"""Run the golden dataset through the agent and score it.

    python -m evals.run_evals                    # LangSmith experiment "agent-routed-..."
    python -m evals.run_evals --compare-routing  # two experiments: routed vs always-gpt-4o
    python -m evals.run_evals --local            # no upload; same metrics, markdown report
    python -m evals.run_evals --category adversarial

Results are written to docs/eval_results.md. In LangSmith, each case shows its
row-level scores and the full trace: guardrail, every LLM call with its model,
each MCP tool call and retrieval, all PII-redacted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from agent.mcp_client import MCPToolbox
from agent.memory import RedisMemory
from agent.orchestrator import SupportAgent
from common.llm import langsmith_client, tracing_enabled
from evals.evaluators import ROW_EVALUATORS, langsmith_summary_evaluator, summarise_rows

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).with_name("golden_dataset.json")
REPORT = ROOT / "docs" / "eval_results.md"
DATASET_NAME = "novashop-agent-golden"
REFERENCE_KEYS = (
    "expected_tools", "optional_tools", "forbidden_tools", "expected_args", "expected_sections",
    "must_include", "must_include_any", "must_not_include", "should_block", "reference",
)


def load_cases(category: str | None = None) -> list[dict[str, Any]]:
    cases = json.loads(GOLDEN.read_text())
    return [c for c in cases if not category or c["category"] == category]


def to_example(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "inputs": {"case_id": case["id"], "customer_id": case["customer_id"], "input": case["input"],
                   "history": case.get("history", [])},
        "outputs": {k: case[k] for k in REFERENCE_KEYS if k in case},
        "metadata": {"category": case["category"]},
    }


def ensure_dataset(cases: list[dict[str, Any]]) -> str:
    client = langsmith_client()
    if not client.has_dataset(dataset_name=DATASET_NAME):
        ds = client.create_dataset(DATASET_NAME, description="NovaShop support agent golden dataset (20 cases)")
        client.create_examples(dataset_id=ds.id, examples=[to_example(c) for c in cases])
    return DATASET_NAME


def make_target(agent: SupportAgent, records: dict[str, dict[str, Any]]):
    async def target(inputs: dict[str, Any]) -> dict[str, Any]:
        sid = f"eval-{inputs['case_id']}-{uuid.uuid4().hex[:8]}"
        run_cost, run_tokens = 0.0, 0
        for turn in inputs.get("history", []):  # set up the conversation first
            prior = await agent.handle(sid, inputs["customer_id"], turn)
            run_cost += prior.cost.get("total_usd", 0)
            run_tokens += prior.cost.get("total_tokens", 0)
        t = time.perf_counter()
        reply = await agent.handle(sid, inputs["customer_id"], inputs["input"])
        evidence = [{"tool": tc.name, "args": tc.args, "result": tc.result} for tc in reply.tool_calls]
        out = {
            "answer": reply.text,
            "blocked": reply.blocked,
            "tools_called": [tc.name for tc in reply.tool_calls],
            "tool_calls": [{"name": tc.name, "args": tc.args, "ok": tc.ok} for tc in reply.tool_calls],
            "hallucinated_params": [list(p) for p in reply.hallucinated_params],
            "retrieved_sections": sorted({s for r in reply.retrievals for s in r["sections"]}),
            "rag_strategies": [r["strategy"] for r in reply.retrievals],
            "evidence": evidence,
            "tier": reply.route.get("tier", "none"),
            "route_reasons": reply.route.get("reasons", []),
            "latency_ms": round((time.perf_counter() - t) * 1000, 1),
            "cost_usd": round(run_cost + reply.cost.get("total_usd", 0), 7),
            "total_tokens": run_tokens + reply.cost.get("total_tokens", 0),
        }
        agent.memory.clear(sid)
        records[inputs["case_id"]] = out
        return out

    return target


def score_locally(out: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for ev in ROW_EVALUATORS:
        res = ev(out, ref) if ev.__code__.co_argcount == 2 else ev(out)
        row[res["key"]] = res["score"]
        if res.get("comment") and res["key"] in {"groundedness", "tool_selection"}:
            row[f"{res['key']}_comment"] = res["comment"]
    return row


async def run_experiment(name: str, cases: list[dict[str, Any]], enable_routing: bool, use_langsmith: bool,
                         concurrency: int) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    async with MCPToolbox() as toolbox:
        agent = SupportAgent(toolbox, RedisMemory(), enable_routing=enable_routing)
        target = make_target(agent, records)
        rows: dict[str, dict[str, Any]] = {}
        if use_langsmith:
            from langsmith import aevaluate

            results = await aevaluate(
                target,
                data=ensure_dataset(load_cases()),
                evaluators=ROW_EVALUATORS,
                summary_evaluators=[langsmith_summary_evaluator],
                experiment_prefix=name,
                metadata={"routing": enable_routing},
                max_concurrency=concurrency,
                client=langsmith_client(),
            )
            async for r in results:
                cid = r["example"].inputs["case_id"]
                rows[cid] = {e.key: e.score for e in r["evaluation_results"]["results"]}
                for e in r["evaluation_results"]["results"]:
                    if e.comment and e.key in {"groundedness", "tool_selection"}:
                        rows[cid][f"{e.key}_comment"] = e.comment
        else:
            sem = asyncio.Semaphore(concurrency)

            async def one(case: dict[str, Any]) -> None:
                async with sem:
                    ex = to_example(case)
                    out = await target(ex["inputs"])
                    rows[case["id"]] = score_locally(out, ex["outputs"])

            await asyncio.gather(*(one(c) for c in cases))
    ordered = [rows[c["id"]] for c in cases if c["id"] in rows]
    return {"name": name, "rows": rows, "records": records, "summary": summarise_rows(ordered)}


def _fmt(v: Any) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        if v != v:
            return "–"
        return f"{v:.4f}" if abs(v) < 0.1 and v != 0 else f"{v:.2f}"
    return str(v)


def write_report(experiments: list[dict[str, Any]], cases: list[dict[str, Any]]) -> None:
    lines = ["# Agent evaluation results", "", f"Golden dataset: {len(cases)} cases. Generated {time.strftime('%Y-%m-%d %H:%M')}.", ""]
    keys = list(experiments[0]["summary"])
    lines += ["## Summary", "", "| metric | " + " | ".join(e["name"] for e in experiments) + " |",
              "|---" * (len(experiments) + 1) + "|"]
    for k in keys:
        lines.append(f"| {k} | " + " | ".join(_fmt(e["summary"][k]) for e in experiments) + " |")
    if len(experiments) == 2:
        a, b = experiments[0]["summary"], experiments[1]["summary"]
        if b["total_cost_usd"]:
            saving = 1 - a["total_cost_usd"] / b["total_cost_usd"]
            lines += ["", f"Routing changed total cost by **{-saving:+.0%}** "
                          f"({_fmt(a['total_cost_usd'])} vs {_fmt(b['total_cost_usd'])} USD); "
                          f"tool selection {_fmt(a['tool_selection_accuracy'])} vs {_fmt(b['tool_selection_accuracy'])}, "
                          f"answer checks {_fmt(a['answer_check_pass_rate'])} vs {_fmt(b['answer_check_pass_rate'])}."]
    for e in experiments:
        lines += ["", f"## Per case: {e['name']}", "",
                  "| case | category | tools | tier | tool_sel | args | guard | checks | grounded | ctx_P | ctx_R | ms | $ |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for c in cases:
            r, o = e["rows"].get(c["id"], {}), e["records"].get(c["id"], {})
            lines.append(
                f"| {c['id']} | {c['category']} | {', '.join(o.get('tools_called', [])) or '–'} | {o.get('tier', '–')} | "
                + " | ".join(_fmt(r.get(k)) for k in ("tool_selection", "tool_arguments", "guardrail_correct",
                                                       "answer_checks", "groundedness", "context_precision",
                                                       "context_recall"))
                + f" | {_fmt(o.get('latency_ms'))} | {_fmt(o.get('cost_usd'))} |"
            )
        flagged = [(cid, r["groundedness_comment"]) for cid, r in e["rows"].items()
                   if r.get("groundedness") == 0 and r.get("groundedness_comment")]
        if flagged:
            lines += ["", "Unsupported claims flagged by the judge:", ""]
            lines += [f"- **{cid}**: {txt}" for cid, txt in sorted(flagged)]
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")


async def main_async(args: argparse.Namespace) -> None:
    cases = load_cases(args.category)
    use_langsmith = not args.local and tracing_enabled()
    if args.category and use_langsmith:
        print("Note: --category filters local runs only; LangSmith runs use the whole dataset.")
    if not args.local and not use_langsmith:
        print("LangSmith is not configured; running locally.")
    plans = [("agent-routed", True)] + ([("agent-gpt4o-only", False)] if args.compare_routing else [])
    experiments = []
    for name, routing in plans:
        print(f"running {name} ...")
        experiments.append(await run_experiment(name, cases, routing, use_langsmith, args.concurrency))
    write_report(experiments, cases)
    print(REPORT.read_text())


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--local", action="store_true")
    p.add_argument("--compare-routing", action="store_true")
    p.add_argument("--category", choices=["simple", "multi_step", "memory", "adversarial"])
    p.add_argument("--concurrency", type=int, default=3)
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
