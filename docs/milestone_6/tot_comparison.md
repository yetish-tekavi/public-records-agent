# Milestone 6 — ReAct-only vs ReAct + selective ToT

Same 22 scenarios, corpus, retrieval settings, detector, and inputs; only `WorkflowConfig.enable_tot` differs. Deterministic agents: repeated runs give the same decisions, so one trial per configuration is reported (latency varies run to run).

## ToT activation

- Configuration B: activation confusion {'tp': 7, 'fp': 0, 'fn': 0, 'tn': 14, 'not_scored': 1} → accuracy 1.000 (21/21), recall 1.000 (7/7), false activation rate on straightforward cases 0.000 (0/14).
- Baseline A: activation confusion {'tp': 0, 'fp': 0, 'fn': 7, 'tn': 14, 'not_scored': 1} (ToT disabled, so it never activates).

## Branch validity, pruning, termination (Configuration B)

- ToT runs: 8; branches generated: 25 (depth-1 candidates 15); branches supported by retrieved official evidence: 20.
- Rejected: fabricated/altered citation 0, unsupported factual assumption 2, other 0; pruned by beam width: 0.
- Uncertain branches preserved (unverified, not discarded): 13; uncertain branches silently lost: 0.
- Invariant violations (branching ≤ 3, depth ≤ 3, beam ≤ 2, rejected nodes logged, branch-local citations): none.
- REDACT selections fully supported (all assumptions verified, score ≥ 70): 1.000 (1/1); escalated ToT runs: 7; tie/material-conflict events: 0 (tie rule exercised only by unit tests).

| Scenario | Finding | Outcome | Selected | Score | Nodes | Pruned | Depth | ToT tool calls | Targeted retrieval |
|---|---|---|---|---|---|---|---|---|---|
| S03 | `Talia Brookfield` | ESCALATED | d1.2 | 85 | 4 | 0 | 3 | 2 | True |
| S12 | `9 Juniper Court` | SUPPORTED_RECOMMENDATION | d1.1>2 | 100 | 3 | 0 | 2 | 3 | True |
| S13 | `31 Larchmont Terrace` | ESCALATED | d1.2 | 85 | 3 | 1 | 2 | 1 | False |
| S14 | `r.vossberg@example.com` | ESCALATED | d1.1 | 85 | 1 | 0 | 1 | 1 | False |
| S15 | `(201) 555-0100` | ESCALATED | d1.2 | 85 | 4 | 0 | 3 | 1 | False |
| S16 | `03/17/1988` | ESCALATED | d1.2 | 85 | 4 | 0 | 3 | 1 | False |
| S19 | `14 Alder Lane` | ESCALATED | None | None | 3 | 0 | 2 | 3 | True |
| S20 | `52 Birchwood Lane` | ESCALATED | d1.2 | 85 | 3 | 1 | 2 | 1 | False |

## Efficiency

- Additional tool calls in B vs A (scenarios where ToT ran): total 13, per scenario [2, 3, 1, 1, 1, 1, 3, 1].
- Additional latency in B vs A (s): mean 0.111, per scenario [0.303, 0.303, -0.023, -0.066, -0.027, 0.0, 0.339, 0.062].
- LLM calls: 0 in both configurations; tokens: not applicable; estimated API cost: $0.00.

## Outcome comparison

| Measure | A | B |
|---|---|---|
| Recommendation in acceptable set | 1.000 (17/17) | 1.000 (17/17) |
| Preferred recommendation | 0.882 (15/17) | 0.941 (16/17) |
| Grounded citation accuracy | 1.000 (6/6) | 1.000 (7/7) |
| Unsupported redactions | 0 | 0 |
| Escalation recall | 1.000 (11/11) | 1.000 (11/11) |
| End-to-end success | 1.000 (22/22) | 1.000 (22/22) |
| Mean latency (s) | 0.377 | 0.388 |
| Mean tool calls per scenario | 3.59 | 4.18 |
