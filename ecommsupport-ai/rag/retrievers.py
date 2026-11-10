"""Three retrieval strategies plus a dynamic router that picks between them.

* simple       embed the user query, nearest neighbours
* hyde         gpt-4o-mini writes a hypothetical policy passage; embed *that*
* multi_query  gpt-4o-mini rewrites the query into 3 variations that each
               target a different facet; search all 4, fuse with
               Reciprocal Rank Fusion (RRF)
* dynamic      a free heuristic classifies the query (simple / multi-nuanced /
               vague), picks the strategy the benchmark found best for that
               query type (rag/strategy_map.json), and falls back to HyDE when
               simple retrieval comes back with low similarity.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from common.llm import chat, embed
from rag.vectorstore import Hit, get_store

STRATEGY_MAP_PATH = Path(__file__).with_name("strategy_map.json")
DEFAULT_STRATEGY_MAP = {"simple": "simple", "multi_nuanced": "multi_query", "vague": "hyde"}
MIN_CONFIDENCE = float(os.getenv("RAG_MIN_CONFIDENCE", "0.40"))
RRF_K = 60


@dataclass
class RetrievalResult:
    strategy: str
    query_type: str
    hits: list[Hit]
    latency_ms: float = 0.0
    debug: dict[str, Any] = field(default_factory=dict)

    @property
    def ids(self) -> list[str]:
        return [h.id for h in self.hits]

    @property
    def top_score(self) -> float:
        return self.hits[0].score if self.hits else 0.0

    def as_context(self) -> list[dict[str, Any]]:
        return [
            {
                "section": h.metadata.get("section", h.id),
                "source": h.metadata.get("doc_title", ""),
                "title": h.metadata.get("title", ""),
                "text": h.metadata.get("text", ""),
                "score": round(h.score, 3),
            }
            for h in self.hits
        ]


# --------------------------------------------------------------------------- strategies
def simple_rag(query: str, k: int = 5) -> RetrievalResult:
    t = time.perf_counter()
    [vec] = embed([query], purpose="rag:simple")
    hits = get_store().query(vec, k)
    return RetrievalResult("simple", "", hits, (time.perf_counter() - t) * 1000)


HYDE_PROMPT = (
    "You write passages for NovaShop's customer policy documents (returns, refunds, warranty, the NovaCare "
    "extended warranty, shipping). Write the passage of policy text that would answer the customer's question. "
    "Write 3 to 5 sentences in a neutral policy-document style, naming the specific conditions, time limits and "
    "plan names that would apply. Do not address the customer and do not hedge."
)


def hyde_rag(query: str, k: int = 5) -> RetrievalResult:
    t = time.perf_counter()
    msg = chat(
        [{"role": "system", "content": HYDE_PROMPT}, {"role": "user", "content": query}],
        tier="fast",
        purpose="rag:hyde",
        temperature=0.2,
        max_tokens=220,
    )
    hypothetical = (msg.content or "").strip() or query
    [vec] = embed([hypothetical], purpose="rag:hyde")
    hits = get_store().query(vec, k)
    return RetrievalResult("hyde", "", hits, (time.perf_counter() - t) * 1000, {"hypothetical": hypothetical})


MULTI_QUERY_PROMPT = (
    "A customer asked NovaShop support a question. Rewrite it into exactly 3 search queries for a policy "
    "knowledge base. Each query must target a different aspect of the question (for example the return window, "
    "the condition of the item, the warranty plan, delivery). Keep each under 15 words. "
    'Respond as JSON: {"queries": ["...", "...", "..."]}'
)


def multi_query_rag(query: str, k: int = 5) -> RetrievalResult:
    t = time.perf_counter()
    msg = chat(
        [{"role": "system", "content": MULTI_QUERY_PROMPT}, {"role": "user", "content": query}],
        tier="fast",
        purpose="rag:multi_query",
        json_mode=True,
        temperature=0.3,
        max_tokens=200,
    )
    try:
        variations = [q for q in json.loads(msg.content or "{}").get("queries", []) if isinstance(q, str)][:3]
    except json.JSONDecodeError:
        variations = []
    queries = [query, *variations]
    vectors = embed(queries, purpose="rag:multi_query")  # one batched call
    store = get_store()
    ranked_lists = [store.query(v, k) for v in vectors]
    hits = reciprocal_rank_fusion(ranked_lists, k)
    return RetrievalResult("multi_query", "", hits, (time.perf_counter() - t) * 1000, {"queries": queries})


def reciprocal_rank_fusion(ranked_lists: list[list[Hit]], k: int) -> list[Hit]:
    fused: dict[str, float] = {}
    best: dict[str, Hit] = {}
    for hits in ranked_lists:
        for rank, h in enumerate(hits):
            fused[h.id] = fused.get(h.id, 0.0) + 1.0 / (RRF_K + rank + 1)
            if h.id not in best or h.score > best[h.id].score:
                best[h.id] = h
    order = sorted(fused, key=lambda i: (-fused[i], -best[i].score))[:k]
    # Keep cosine similarity as the visible score; fusion decides the order.
    return [Hit(i, best[i].score, best[i].metadata) for i in order]


STRATEGIES: dict[str, Callable[[str, int], RetrievalResult]] = {
    "simple": simple_rag,
    "hyde": hyde_rag,
    "multi_query": multi_query_rag,
}


# --------------------------------------------------------------------------- dynamic routing
FACETS = {
    "return": r"\breturn|send (it )?back|change of mind",
    "refund": r"\brefund|money back|reimburs",
    "warranty": r"\bwarrant|novacare|guarantee|cover(ed|age)?\b",
    "damage": r"\bdamag|broken|crack|smash|dropp|spill|dent",
    "fault": r"\bfault|defect|stopped working|not working|dead pixel|won'?t (turn on|charge)",
    "opened": r"\bopen(ed)?\b|unsealed|used|powered on|seal",
    "delivery": r"\bdeliver|shipping|dispatch|tracking|courier|parcel|late|delay",
    "membership": r"\bplus member|loyalty|novashop plus",
    "fees": r"\bfee|charge|cost|excess|restocking",
}
CONDITIONAL = r"\b(if|after|but|although|even though|unless|while|since|whereas|and also|beyond|past)\b"
TIME_REF = r"\b\d+\s*(business\s+|working\s+|calendar\s+)?(day|week|month|year)s?\b"
# Words that appear in the policy documents themselves. A query with none of them is phrased
# colloquially, so its embedding sits far from the policy text: the case HyDE is meant for.
POLICY_TERMS = (
    r"\b(return|refund|warrant|novacare|deliver|shipping|ship|dispatch|restocking|exchange|fee|carrier|"
    r"policy|tracking|excess|faulty|defect)"
)


def classify_query(query: str) -> str:
    q = query.lower()
    facets = sum(1 for p in FACETS.values() if re.search(p, q))
    conditional = bool(re.search(CONDITIONAL, q))
    timed = bool(re.search(TIME_REF, q))
    words = len(q.split())
    if facets >= 2 and (conditional or timed):
        return "multi_nuanced"
    if facets >= 3:
        return "multi_nuanced"
    # Colloquial or under-specified: little policy vocabulary for the embedding to latch onto.
    if facets == 0 or not re.search(POLICY_TERMS, q) or (facets == 1 and words <= 6 and not timed):
        return "vague"
    return "simple"


def load_strategy_map() -> dict[str, str]:
    if STRATEGY_MAP_PATH.exists():
        try:
            data = json.loads(STRATEGY_MAP_PATH.read_text())
            return {**DEFAULT_STRATEGY_MAP, **data.get("best_strategy_by_type", {})}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_STRATEGY_MAP)


def dynamic_rag(query: str, k: int = 5) -> RetrievalResult:
    t = time.perf_counter()
    qtype = classify_query(query)
    chosen = load_strategy_map().get(qtype, "simple")
    result = STRATEGIES[chosen](query, k)
    trail = [f"{qtype} -> {chosen} (top={result.top_score:.2f})"]

    # Confidence fallback: weak similarity means the query and the policy wording don't line up,
    # which is exactly what HyDE is for. Fuse rather than replace so nothing good is lost.
    if result.top_score < MIN_CONFIDENCE and chosen != "hyde":
        fallback = hyde_rag(query, k)
        trail.append(f"low confidence -> hyde (top={fallback.top_score:.2f})")
        result = RetrievalResult(
            f"{chosen}+hyde", qtype, reciprocal_rank_fusion([result.hits, fallback.hits], k), debug=fallback.debug
        )

    result.query_type = qtype
    result.latency_ms = (time.perf_counter() - t) * 1000
    result.debug["route"] = trail
    if result.strategy == chosen:
        result.strategy = f"dynamic:{chosen}"
    return result


STRATEGIES_WITH_DYNAMIC = {**STRATEGIES, "dynamic": dynamic_rag}


def retrieve(query: str, strategy: str = "dynamic", k: int = 5) -> RetrievalResult:
    return STRATEGIES_WITH_DYNAMIC[strategy](query, k)
