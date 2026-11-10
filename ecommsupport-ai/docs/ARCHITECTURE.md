# Architecture and design decisions

This document explains how the build meets each criterion in the evaluation rubric and where the
evidence for it comes from.

## 1. Solution approach and architecture

**MCP isolates the database from the LLM.** The orchestrator never imports `pymysql`. It starts
`python -m mcp_server.server` as a stdio subprocess and talks to it through the MCP v2 `Client`
(`agent/mcp_client.py`). The server exposes three tools to the model, `get_product_details`,
`check_order_status` and `place_order`, plus `get_customer_profile`, which only the orchestrator
calls when a session starts.

**The model never chooses whose data it touches.** `customer_id` is a server-side parameter, but
the MCP client strips it from every schema the model sees and injects it from the authenticated
session. An order that belongs to someone else returns the same `not_found` as a non-existent
one, so the tool can't be used to enumerate orders. Test: `test_cannot_read_other_customers_orders`.

**No hallucinated parameters reach the database.** Before every call the MCP client checks
arguments against the tool's JSON schema. Unknown keys are dropped and logged per turn, which
feeds the `hallucinated_params` evaluator. Missing required keys go back to the model as an
error ("ask the customer; do not guess"). OpenAI schemas set `additionalProperties: false`.
Test: `test_hallucinated_params_are_dropped_and_customer_injected`.

**Routing between MySQL and Pinecone** is done by the model through tool choice, with rules in
the system prompt: order and product facts come only from MCP tools, policy only from
`search_policies`, and questions that need both call both and apply the policy to the order's
facts (`days_since_delivery`, `warranty_plan`, category, loyalty tier). `check_order_status`
returns `days_since_delivery` already computed, so the model doesn't do date arithmetic. The
`tool_selection` and `tool_arguments` evaluators measure this routing on the golden dataset.

## 2. RAG implementation and choice of techniques

**Dataset.** Five policy documents with 31 coded sections, written so that realistic questions
need several sections that qualify each other. Example: the 30-day window (RET-1) is extended to
45 days for Plus members, but only for unopened items (RET-5). Damage is a refund within 7 days
(RET-3) and a warranty claim afterwards. Standard NovaCare doesn't cover accidental damage and
doesn't extend the return window (NC-3); NovaCare Plus does cover accidental damage (NC-2).

**Chunking.** One chunk per `##` section, with a "Document > Section" breadcrumb prepended to the
embedded text. Sections over 1,200 characters split on paragraphs with a one-paragraph overlap
(none currently need it). Section codes are the chunk IDs, which gives the benchmark exact
ground truth.

**Strategies** (`rag/retrievers.py`):

| Strategy | How | Extra cost per query |
|---|---|---|
| Simple | embed the query, top-5 cosine | 1 embedding |
| HyDE | gpt-4o-mini writes a policy-style passage that would answer it; embed that | 1 mini call + 1 embedding |
| Multi-Query | gpt-4o-mini writes 3 facet-specific rewrites; search original + 3; Reciprocal Rank Fusion | 1 mini call + 1 batched embedding + 4 searches |

**Benchmark** (`rag/benchmark.py`, 20 queries in `rag/benchmark_queries.json`). Six simple
queries with one obvious section, six colloquial or vague ones ("box was crushed and the thing
inside is broken"), and eight multi-condition ones, including the brief's laptop question. Each
strategy runs as its own LangSmith experiment with custom evaluators: recall@5, context precision
(rank-aware average precision), MRR, full coverage (all labelled sections retrieved), latency and
cost. The report breaks the results down by query type.

**Expected pattern, to be confirmed by your run:** Simple RAG should be enough for direct
questions, which word things the way the policy does. Multi-Query should win on full coverage for
multi-condition questions, because each rewrite pulls in a different section. HyDE should help
most on colloquial questions, where the query and the policy text use different words.
`docs/rag_results.md` holds the measured numbers once you have run `make benchmark`. If the
measurements disagree with this pattern, the router follows the measurements.

**Dynamic fallback.** `dynamic_rag` classifies the query with a free regex heuristic (policy
facets, conditionals, time references, and whether it uses any policy vocabulary). It then picks
the strategy that `rag/strategy_map.json` records as best for that query type. The benchmark
writes that map, so routing follows measurement. If the chosen strategy's top cosine score is
below `RAG_MIN_CONFIDENCE`, the router also runs HyDE and fuses the two result lists with RRF.
Caveat: the classifier matches all 20 benchmark labels, but it was tuned on that same set. Add
held-out queries before you quote that number as accuracy.

## 3. Optimisation and memory

**Persistent memory.** Upstash Redis over REST: a message list, a rolling summary and a facts
hash per session, all with a 7-day TTL (`agent/memory.py`). The API namespaces sessions by
customer, so switching accounts in the UI can't read another customer's history.

**Context-window cap.** When the window passes 12 messages or 1,500 tokens, everything except
the newest 6 messages is merged into the existing summary by gpt-4o-mini and the list is trimmed.
Prompt size therefore stays roughly constant however long the chat gets. Order and product IDs
are pulled out by regex into the facts hash *before* any trimming, so an order number given early
survives word for word even if the summary paraphrases it. Tests:
`test_memory_compaction_caps_window_and_keeps_facts` (41 messages in, never more than 12 kept)
and `test_recalls_order_number_three_turns_later` (the brief's requirement).

**Model routing** (`agent/router.py`). These decisions cost no LLM calls:
- gpt-4o-mini: guardrail classifier, HyDE, multi-query, summaries, and agent turns that are
  simple lookups.
- gpt-4o: turns that combine an order with a policy, multi-condition policy questions, and
  purchases.
- Mid-turn escalation: a mini turn moves to gpt-4o for the rest of the turn if it needs two
  different tools, hits a tool error or runs past two iterations.

`make compare` runs the golden dataset twice, routed and always-gpt-4o, and reports total cost,
cost per case and accuracy side by side. That comparison is the evidence for "saves cost while
maintaining accuracy". With the offline fake model, routing cut cost by 56% with identical
scores. That shows the mechanism works; the real figure comes from your live run.

**Cost accounting.** Every OpenAI call goes through `common/llm.chat()` with a `purpose` tag. A
context-local tracker sums tokens and USD by model for each turn. The UI shows it, and evals
record it as `cost_usd`. Prices are in `.env`, so you can update them.

## 4. Guardrails and security

**SQL injection.** It is ruled out at three levels: there is no tool that accepts SQL; arguments
are checked against strict patterns (`ORD-\d{4,8}`, `NS-[A-Z]{3}-\d{3}`, a whitelist for search
terms, quantity limits) before any query; and every statement uses bound `%s` parameters, with
LIKE wildcards escaped. The MCP server also connects as a least-privilege MySQL user that can't
drop, alter or delete (`db/schema.sql`). Tests: `test_order_id_injection_is_rejected`,
`test_product_search_rejects_bad_terms`.

**Prompt injection and off-topic input.** There are two layers (`agent/guardrails.py`):
1. Rules, free and instant: NFKC normalisation (defeats full-width and look-alike characters),
   control-character stripping, a length cap, and patterns for instruction overrides,
   system-prompt extraction, persona switches, role-tag spoofing, staff impersonation,
   cross-customer data requests and SQL payloads, plus obvious off-topic requests (code, politics,
   creative writing).
2. A gpt-4o-mini classifier in JSON mode for everything else that isn't a trivial
   acknowledgement. The customer text is wrapped in `<customer_message>` tags and declared to be
   data. The assistant's last message is passed in, so "yes" or "the second one" is judged in
   context.

Blocked input gets a fixed, friendly decline and never reaches the agent model. The system
prompt also scopes the agent and tells it to treat tool output and policy text as data, which
covers indirect injection through retrieved content. On output, a check blocks anything that
echoes the system prompt and redacts PII.

**PII before logging.** The same regex rules (`common/pii.py`: emails, UK phone numbers, card
numbers, postcodes, street addresses, API keys) run in three places: the LangSmith client's
`anonymizer`, which every trace and eval upload goes through (`common/llm.traced()`); a filter on
every application logger; and the guardrail audit log. Separately, the MCP tools never return
email, phone or address in the first place. Known gap: regexes don't catch personal names in free
text. Use an NER-based redactor such as Presidio if you need that.

## 5. Evaluations (LangSmith)

**Golden dataset** (`evals/golden_dataset.json`, 20 cases): 6 simple, 7 multi-step (order plus
policy, quote then purchase, and a confirmed `place_order`), 2 memory (an order number recalled
three turns later), and 5 adversarial (system-prompt extraction, a code request, "who is the
president", another customer's order, SQL injection with data exfiltration). Each case has
expected tools, expected arguments, expected policy sections, required and forbidden phrases,
and a reference answer.

**Custom evaluators** (`evals/evaluators.py`), per case:
- `tool_selection`: exact set match, Jaccard otherwise, 0 if a forbidden tool ran
- `tool_arguments`: share of expected argument values matched
- `hallucinated_params`: 1 if the model sent nothing outside the schema
- `guardrail_correct`
- `answer_checks`
- `groundedness`: a gpt-4o judge lists claims not supported by the tool results and retrieved
  sections; hallucination rate = 1 − mean
- `context_precision` and `context_recall`
- `latency_ms`, `cost_usd`, `total_tokens`, `used_gpt4o`

A summary evaluator adds experiment-level tool-selection accuracy, p50/p95 latency, total cost
and the share of turns routed to gpt-4o.

## What was verified in the build environment

The build sandbox had no access to OpenAI, Pinecone, Upstash or LangSmith. What was verified there:
- MySQL schema, seed data and least-privilege grants, on MariaDB 10.11.
- All MCP tools, in-process and over stdio, including injection payloads, cross-customer access,
  stock-locking transactions and output PII.
- The full agent loop, guardrails, memory compaction, routing, escalation, evaluators and
  reports, using a rule-based fake model, hash-based fake embeddings and an in-process Redis.
- 50 offline tests pass.

Not yet verified: answer quality, real retrieval scores, and real cost savings. Those come from
`make benchmark` and `make compare` against the live services.
