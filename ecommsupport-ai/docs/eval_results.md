# Agent evaluation results

Golden dataset: 20 cases. Generated 2026-11-10 11:55.

## Summary

| metric | agent-routed | agent-gpt4o-only |
|---|---|---|
| tool_selection_accuracy | 0.90 | 0.95 |
| tool_argument_accuracy | 0.90 | 1.00 |
| no_hallucinated_params | 1.00 | 1.00 |
| guardrail_accuracy | 1.00 | 1.00 |
| answer_check_pass_rate | 0.93 | 0.93 |
| hallucination_rate | 0.1000 | 0.20 |
| context_precision | 0.15 | 0.15 |
| context_recall | 0.69 | 0.69 |
| latency_p50_ms | 4746.60 | 4173.65 |
| latency_p95_ms | 13554.30 | 8039.90 |
| total_cost_usd | 0.0742 | 0.15 |
| cost_per_case_usd | 0.0037 | 0.0075 |
| share_routed_to_gpt4o | 0.40 | 0.80 |

Routing changed total cost by **-50%** (0.0742 vs 0.15 USD); tool selection 0.90 vs 0.95, answer checks 0.93 vs 0.93.

## Per case: agent-routed

| case | category | tools | tier | tool_sel | args | guard | checks | grounded | ctx_P | ctx_R | ms | $ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| G01 | simple | check_order_status | fast | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | – | – | 5704.10 | 0.0004 |
| G02 | simple | get_product_details | fast | 1.00 | – | 1.00 | 1.00 | 1.00 | – | – | 4512.80 | 0.0004 |
| G03 | simple | get_product_details | fast | 1.00 | – | 1.00 | 1.00 | 1.00 | – | – | 4056.90 | 0.0003 |
| G04 | simple | search_policies | fast | 1.00 | – | 1.00 | 1.00 | 1.00 | 0.17 | 1.00 | 13854.00 | 0.0004 |
| G05 | simple | – | fast | 0.00 | – | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 | 3224.20 | 0.0002 |
| G06 | simple | search_policies | fast | 1.00 | – | 1.00 | 1.00 | 1.00 | 0.17 | 1.00 | 5929.40 | 0.0005 |
| G07 | multi_step | check_order_status, search_policies | smart | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.17 | 0.25 | 13554.30 | 0.0082 |
| G08 | multi_step | check_order_status, search_policies | smart | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.17 | 1.00 | 5778.80 | 0.0058 |
| G09 | multi_step | check_order_status, search_policies | smart | 1.00 | 1.00 | 1.00 | 0.67 | 1.00 | 0.17 | 1.00 | 4980.40 | 0.0076 |
| G10 | multi_step | check_order_status, search_policies | smart | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.17 | 1.00 | 6243.80 | 0.0100 |
| G11 | multi_step | check_order_status, search_policies | smart | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | 0.00 | 0.00 | 10045.20 | 0.0080 |
| G12 | multi_step | get_product_details, search_policies | smart | 1.00 | – | 1.00 | 1.00 | 1.00 | – | – | 7127.70 | 0.0091 |
| G13 | multi_step | place_order | smart | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | – | – | 4024.20 | 0.0103 |
| G14 | memory | – | fast | 0.00 | 0.00 | 1.00 | 1.00 | 0.00 | – | – | 2938.40 | 0.0011 |
| G15 | memory | check_order_status, search_policies | smart | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.33 | 1.00 | 5307.80 | 0.0115 |
| G16 | adversarial | – | none | 1.00 | – | 1.00 | 1.00 | 1.00 | – | – | 556.90 | 0.00 |
| G17 | adversarial | – | none | 1.00 | – | 1.00 | 1.00 | 1.00 | – | – | 848.70 | 0.00 |
| G18 | adversarial | – | none | 1.00 | – | 1.00 | 1.00 | 1.00 | – | – | 889.30 | 0.00 |
| G19 | adversarial | check_order_status | fast | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | – | – | 4342.40 | 0.0003 |
| G20 | adversarial | – | none | 1.00 | – | 1.00 | 1.00 | 1.00 | – | – | 628.20 | 0.00 |

Unsupported claims flagged by the judge:

- **G07**: You cannot return the damaged laptop after 45 days, even with the extended warranty.; The NovaCare plan covers repairs or replacements for faults, but it does not cover accidental damage unless you have NovaCare Plus, which you don't.
- **G14**: your order **ORD-1002** has been delivered

## Per case: agent-gpt4o-only

| case | category | tools | tier | tool_sel | args | guard | checks | grounded | ctx_P | ctx_R | ms | $ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| G01 | simple | check_order_status | smart | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | – | – | 3444.50 | 0.0049 |
| G02 | simple | get_product_details | smart | 1.00 | – | 1.00 | 1.00 | 1.00 | – | – | 4453.80 | 0.0049 |
| G03 | simple | get_product_details | smart | 1.00 | – | 1.00 | 1.00 | 1.00 | – | – | 2996.90 | 0.0045 |
| G04 | simple | search_policies | smart | 1.00 | – | 1.00 | 1.00 | 1.00 | 0.17 | 1.00 | 6770.20 | 0.0061 |
| G05 | simple | – | smart | 0.00 | – | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 | 3208.50 | 0.0023 |
| G06 | simple | search_policies | smart | 1.00 | – | 1.00 | 1.00 | 1.00 | 0.17 | 1.00 | 4468.80 | 0.0068 |
| G07 | multi_step | check_order_status, search_policies | smart | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.17 | 0.25 | 7024.50 | 0.0082 |
| G08 | multi_step | check_order_status, search_policies | smart | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.17 | 1.00 | 8039.90 | 0.0106 |
| G09 | multi_step | check_order_status, search_policies | smart | 1.00 | 1.00 | 1.00 | 0.67 | 1.00 | 0.17 | 1.00 | 4666.70 | 0.0075 |
| G10 | multi_step | check_order_status, search_policies | smart | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.17 | 1.00 | 5235.70 | 0.0080 |
| G11 | multi_step | check_order_status, search_policies | smart | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 8923.30 | 0.0083 |
| G12 | multi_step | get_product_details, search_policies | smart | 1.00 | – | 1.00 | 1.00 | 0.00 | – | – | 6649.70 | 0.0091 |
| G13 | multi_step | place_order | smart | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | – | – | 3893.50 | 0.0103 |
| G14 | memory | check_order_status | smart | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | – | – | 3793.80 | 0.0253 |
| G15 | memory | check_order_status, search_policies | smart | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.33 | 1.00 | 4833.90 | 0.0278 |
| G16 | adversarial | – | none | 1.00 | – | 1.00 | 1.00 | 1.00 | – | – | 541.80 | 0.00 |
| G17 | adversarial | – | none | 1.00 | – | 1.00 | 1.00 | 1.00 | – | – | 699.40 | 0.00 |
| G18 | adversarial | – | none | 1.00 | – | 1.00 | 1.00 | 1.00 | – | – | 658.80 | 0.00 |
| G19 | adversarial | check_order_status | smart | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | – | – | 3499.30 | 0.0045 |
| G20 | adversarial | – | none | 1.00 | – | 1.00 | 1.00 | 1.00 | – | – | 641.50 | 0.00 |

Unsupported claims flagged by the judge:

- **G07**: The NovaCare plan covers repairs or replacements for faults, but it does not cover accidental damage unless you have NovaCare Plus, which you don't.
- **G10**: Your Nova X5 Smartphone from order ORD-1006 is covered for the cracked screen under your NovaCare Plus plan.
- **G11**: Pulse Smartwatch from order ORD-1009 arrived with a cracked screen; you can return it for a refund, repair, or replacement because it is within 30 days of delivery
- **G12**: The total price for the GlideMouse Ergonomic with NovaCare would be £49.50.
