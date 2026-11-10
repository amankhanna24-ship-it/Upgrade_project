"""Benchmark Simple RAG vs HyDE vs Multi-Query (and the dynamic router).

    python -m rag.benchmark                 # runs as LangSmith experiments
    python -m rag.benchmark --local         # no LangSmith upload, same metrics
    python -m rag.benchmark --strategies simple hyde

Each strategy becomes one LangSmith experiment on the dataset
"novashop-rag-benchmark", so they can be compared side by side in the
LangSmith UI. Custom evaluators score every query:

    recall@k           share of the labelled sections that were retrieved
    context_precision  rank-aware precision (average precision at k): rewards
                       putting the relevant sections at the top
    mrr                1 / rank of the first relevant section
    full_coverage      1 if *every* relevant section was retrieved; this is the
                       metric that matters for multi-condition questions
    latency_ms, cost_usd

Results are also written to docs/rag_results.md, broken down by query type,
and the best strategy per query type is written to rag/strategy_map.json,
which the dynamic router reads. So the routing is set by measurement
rather than by assumption.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from common.llm import langsmith_client, track_costs, tracing_enabled
from rag.retrievers import STRATEGIES_WITH_DYNAMIC, STRATEGY_MAP_PATH

ROOT = Path(__file__).resolve().parent.parent
QUERIES_PATH = Path(__file__).with_name("benchmark_queries.json")
RESULTS_MD = ROOT / "docs" / "rag_results.md"
DATASET_NAME = "novashop-rag-benchmark"
K = 5


# --------------------------------------------------------------------------- metrics
def _base(section_id: str) -> str:
    return section_id.split("#")[0]


def score(retrieved: list[str], relevant: list[str], k: int = K) -> dict[str, float]:
    retrieved = [_base(r) for r in retrieved[:k]]
    rel = set(relevant)
    hits = [1 if r in rel else 0 for r in retrieved]
    found = rel.intersection(retrieved)
    recall = len(found) / len(rel) if rel else 0.0
    # Average precision@k, normalised by the number of relevant items that could fit in k.
    ap_sum, seen = 0.0, 0
    for i, h in enumerate(hits, start=1):
        if h:
            seen += 1
            ap_sum += seen / i
    ap = ap_sum / min(len(rel), k) if rel else 0.0
    first = next((i for i, h in enumerate(hits, start=1) if h), None)
    return {
        "recall_at_k": recall,
        "context_precision": ap,
        "mrr": 1.0 / first if first else 0.0,
        "full_coverage": 1.0 if found == rel else 0.0,
    }


def _metric_evaluator(name: str):
    def evaluator(outputs: dict, reference_outputs: dict) -> dict:
        return {"key": name, "score": score(outputs["retrieved"], reference_outputs["relevant"])[name]}

    evaluator.__name__ = name
    return evaluator


def latency_evaluator(outputs: dict) -> dict:
    return {"key": "latency_ms", "score": outputs["latency_ms"]}


def cost_evaluator(outputs: dict) -> dict:
    return {"key": "cost_usd", "score": outputs["cost_usd"]}


EVALUATORS = [_metric_evaluator(m) for m in ("recall_at_k", "context_precision", "mrr", "full_coverage")] + [
    latency_evaluator,
    cost_evaluator,
]


# --------------------------------------------------------------------------- running
def make_target(strategy: str, records: dict[str, dict[str, Any]]):
    fn = STRATEGIES_WITH_DYNAMIC[strategy]

    def target(inputs: dict) -> dict:
        t = time.perf_counter()
        with track_costs() as cost:
            result = fn(inputs["query"], K)
        out = {
            "retrieved": result.ids,
            "strategy": result.strategy,
            "query_type_detected": result.query_type,
            "latency_ms": round((time.perf_counter() - t) * 1000, 1),
            "cost_usd": round(cost.total_usd, 7),
        }
        records[inputs["qid"]] = out
        return out

    target.__name__ = f"rag_{strategy}"
    return target


def ensure_dataset(queries: list[dict]) -> str:
    client = langsmith_client()
    if not client.has_dataset(dataset_name=DATASET_NAME):
        ds = client.create_dataset(DATASET_NAME, description="NovaShop policy retrieval benchmark")
        client.create_examples(
            dataset_id=ds.id,
            examples=[
                {
                    "inputs": {"qid": q["id"], "query": q["query"]},
                    "outputs": {"relevant": q["relevant"]},
                    "metadata": {"query_type": q["type"]},
                }
                for q in queries
            ],
        )
    return DATASET_NAME


def run(strategies: list[str], local: bool) -> dict[str, dict[str, dict[str, Any]]]:
    queries = json.loads(QUERIES_PATH.read_text())
    use_langsmith = not local and tracing_enabled()
    if not local and not use_langsmith:
        print("LangSmith is not configured (LANGSMITH_TRACING/LANGSMITH_API_KEY); running locally.")
    all_records: dict[str, dict[str, dict[str, Any]]] = {}
    for strategy in strategies:
        records: dict[str, dict[str, Any]] = {}
        target = make_target(strategy, records)
        if use_langsmith:
            from langsmith import evaluate

            evaluate(
                target,
                data=ensure_dataset(queries),
                evaluators=EVALUATORS,
                experiment_prefix=f"rag-{strategy}",
                metadata={"strategy": strategy, "k": K},
                max_concurrency=4,
                client=langsmith_client(),
            )
        else:
            for q in queries:
                target({"qid": q["id"], "query": q["query"]})
        all_records[strategy] = records
        print(f"  finished {strategy}")
    return all_records


# --------------------------------------------------------------------------- reporting
def summarise(all_records: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    queries = {q["id"]: q for q in json.loads(QUERIES_PATH.read_text())}
    table: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    per_query: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for strategy, records in all_records.items():
        buckets: dict[str, list[dict[str, float]]] = defaultdict(list)
        for qid, out in records.items():
            q = queries[qid]
            s = score(out["retrieved"], q["relevant"])
            s["latency_ms"], s["cost_usd"] = out["latency_ms"], out["cost_usd"]
            per_query[qid][strategy] = s
            buckets[q["type"]].append(s)
            buckets["all"].append(s)
        for qtype, rows in buckets.items():
            table[qtype][strategy] = {m: statistics.mean(r[m] for r in rows) for m in rows[0]}

    best: dict[str, str] = {}
    for qtype in ("simple", "multi_nuanced", "vague"):
        cands = {s: v for s, v in table.get(qtype, {}).items() if s != "dynamic"}
        if cands:
            # Rank on coverage and precision; break near-ties in favour of the cheaper strategy.
            best[qtype] = max(
                cands,
                key=lambda s: (
                    round(cands[s]["full_coverage"] + cands[s]["recall_at_k"] + cands[s]["context_precision"], 2),
                    -cands[s]["cost_usd"],
                ),
            )
    return {"table": table, "per_query": per_query, "best": best}


def write_report(summary: dict[str, Any]) -> None:
    lines = [
        "# RAG benchmark results",
        "",
        f"k = {K}. Generated by `python -m rag.benchmark` on {time.strftime('%Y-%m-%d %H:%M')}.",
        "",
    ]
    metrics = ["recall_at_k", "context_precision", "mrr", "full_coverage", "latency_ms", "cost_usd"]
    for qtype in ("all", "simple", "vague", "multi_nuanced"):
        rows = summary["table"].get(qtype)
        if not rows:
            continue
        lines += [f"## {qtype}", "", "| strategy | " + " | ".join(metrics) + " |", "|---" * (len(metrics) + 1) + "|"]
        for strategy, m in rows.items():
            cells = [f"{m[k]:.0f}" if k == "latency_ms" else f"{m[k]:.5f}" if k == "cost_usd" else f"{m[k]:.2f}" for k in metrics]
            lines.append(f"| {strategy} | " + " | ".join(cells) + " |")
        lines.append("")
    lines += ["## Best strategy by query type (written to rag/strategy_map.json)", ""]
    lines += [f"- **{t}**: {s}" for t, s in summary["best"].items()]
    lines += ["", "## Per-query full coverage", "", "| query | " + " | ".join(next(iter(summary["per_query"].values())).keys()) + " |"]
    lines.append("|---" * (1 + len(next(iter(summary["per_query"].values())))) + "|")
    for qid, by_s in sorted(summary["per_query"].items()):
        lines.append(f"| {qid} | " + " | ".join("yes" if v["full_coverage"] else "no" for v in by_s.values()) + " |")
    RESULTS_MD.parent.mkdir(exist_ok=True)
    RESULTS_MD.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="skip LangSmith, compute metrics locally")
    parser.add_argument("--strategies", nargs="+", default=["simple", "hyde", "multi_query", "dynamic"])
    parser.add_argument("--no-update-map", action="store_true", help="don't overwrite rag/strategy_map.json")
    args = parser.parse_args()

    records = run(args.strategies, args.local)
    summary = summarise(records)
    write_report(summary)
    if not args.no_update_map and summary["best"]:
        STRATEGY_MAP_PATH.write_text(json.dumps({"best_strategy_by_type": summary["best"], "k": K}, indent=2))
    print(RESULTS_MD.read_text())


if __name__ == "__main__":
    main()
