# ReAct-only vs ReAct + Selective ToT

**Source.** The Milestone 6 final run at commit `7a1a875`:
`docs/milestone_6/evaluation_results.json`, `tot_comparison.md` and the per-run traces.

**Setup.** The same 22 scenarios, corpus, retrieval settings, detector, inputs and reviewer scripts.
Only `WorkflowConfig.enable_tot` differs.

**"ReAct" in this project means a rule-based loop.** The agents are deterministic: each runs a
bounded decide → call tool → observe → decide loop driven by rules. **No LLM chooses the actions in
either configuration.** The Milestone 3 Claude planner exists, but it was tested only with a fake
client and is not used in the workflow.

## Short answer

| Question | Answer from the measurements |
|---|---|
| Did ToT improve decision quality? | **On one scenario only.** The preferred-recommendation rate rose from 15/17 to 16/17. That scenario is S12, a retired judge's home address: B selected a fully verified REDACT branch citing 47:1A-1.1 and 47:1A-5(a), while A abstained to human review. Both are acceptable outcomes; REDACT was the preferred one |
| Did ToT improve evidence coverage? | **Slightly, in the same case.** In S12, B used its one targeted retrieval to add the operative 47:1A-5(a) passage. Grounded citation accuracy was 1.000 in both (A 6/6, B 7/7) |
| Did ToT change escalation behaviour? | **No.** Escalation recall was 11/11 and the false escalation rate 0/9 in both. 7 of 8 ToT runs ended in escalation, as did the equivalent A runs |
| Did ToT reduce safety? | **No, in the final run.** Unsupported redactions were 0 in both, and 16/16 safety checks passed. **Before the evaluation-driven fixes, it did:** B produced 2 unsupported redactions and 3 false activations, A none |
| What did ToT cost? | 13 extra tool calls across the 8 ToT runs (1–3 each); +0.111 s mean latency per ToT scenario; mean tool calls per scenario 3.59 → 4.18. 0 LLM calls, so $0 |
| Is the result conclusive? | **No.** With 7 ToT-positive scenarios, the evidence supports only this: *selective ToT, after the fixes, did not reduce safety and improved one ambiguous case at a small tool-call cost.* It does **not** show that ToT improves accuracy in general |

## Side by side

| Measure | A: ReAct-only | B: ReAct + ToT | Difference |
|---|---|---|---|
| Recommendation in acceptable set | 1.000 (17/17) | 1.000 (17/17) | none |
| Preferred recommendation | 0.882 (15/17) | 0.941 (16/17) | +1 finding (S12) |
| Conservative abstentions | 2 (S12, S16) | 1 (S16) | −1 |
| Grounded citation accuracy | 1.000 (6/6) | 1.000 (7/7) | +1 grounded REDACT |
| Unsupported redactions | 0 | 0 | none |
| Escalation recall | 1.000 (11/11) | 1.000 (11/11) | none |
| False escalation rate | 0.000 (0/9) | 0.000 (0/9) | none |
| End-to-end success | 1.000 (22/22) | 1.000 (22/22) | none |
| Tool failure safe handling / recovery | 3/3, 3/3 | 3/3, 3/3 | none |
| Mean latency (s) | 0.377 | 0.388 | +0.011 overall; +0.111 on ToT scenarios |
| Mean tool calls per scenario | 3.59 | 4.18 | +0.59 |
| LLM calls / cost | 0 / $0 | 0 / $0 | none |

## ToT behaviour per scenario (configuration B)

| Scenario | Finding | ToT outcome | Selected (score) | Nodes | Rejected/pruned | Depth | ToT tool calls | Targeted retrieval |
|---|---|---|---|---|---|---|---|---|
| S03 | `Talia Brookfield` (name) | ESCALATED | d1.2 review (85) | 4 | 0 | 3 | 2 | yes |
| S12 | `9 Juniper Court` (retired judge) | **SUPPORTED REDACT** | d1.1>2 (100) | 3 | 0 | 2 | 3 | yes |
| S13 | `31 Larchmont Terrace` (resident) | ESCALATED | d1.2 review (85) | 3 | 1 | 2 | 1 | no |
| S14 | `r.vossberg@example.com` | ESCALATED | d1.1 (85) | 1 | 0 | 1 | 1 | no |
| S15 | `(201) 555-0100` (agency line) | ESCALATED | d1.2 review (85) | 4 | 0 | 3 | 1 | no |
| S16 | `03/17/1988` (date of birth) | ESCALATED | d1.2 review (85) | 4 | 0 | 3 | 1 | no |
| S19 | `14 Alder Lane` (validation fails) | ESCALATED | none | 3 | 0 | 2 | 3 | yes |
| S20 | `52 Birchwood Lane` (injection) | ESCALATED | d1.2 review (85) | 3 | 1 | 2 | 1 | no |

- **Activation.** 7 true positives, 0 false positives, 0 false negatives and 14 true negatives
  (S16 not scored). All 14 straightforward cases bypassed ToT.
- **Invalid branches.** 2 were rejected for an unsupported factual assumption (S13 and S20: the
  restricted-class "covered person" assumption had no support in the record). 0 were rejected for
  fabricated citations, because citations can only be chunk ids returned by retrieval.
- **Uncertainty.** 13 uncertain branches were kept as UNVERIFIED, none silently lost. This matches
  the Checkpoint 4.1 risk mitigation: mark branches as uncertain rather than prune them prematurely.
- **Termination.** No run exceeded branching 3, depth 3, beam 2 or its tool budget. S19 (citation
  validation fails) shows budget-safe failure: no branch could be validated, so nothing was
  selectable and the case escalated.

## What the rubric score does and does not mean

Before the fixes, the ToT's own check rated **all four** of its REDACT selections "fully supported",
including the two that were wrong:
- S15: an agency phone line detected at low confidence;
- S20: an address supported only by an injected non-official document.

A rubric score measures **internal consistency with retrieved text**, not legal correctness. That
is why the design keeps the score from authorizing anything on its own. Redaction requires:
- every assumption SUPPORTED;
- an operative provision from an official source;
- a score of at least 70;
- **human approval**.

## Conclusion

Selective ToT is **useful but narrow** in this prototype:
- It converted one ambiguous case (S12) from an abstention into a fully verified, cited
  recommendation, and still required a human to approve it.
- Everywhere else it changed nothing measurable except cost.
- Its main value in the evaluation was **diagnostic**: comparing A against B exposed four defects
  (F1–F4). Two of them (F1 and F2) produced unsupported redactions only when ToT was enabled; under
  A, F1 showed up only as a non-official citation on an escalated item.
- The comparison is **inconclusive for general accuracy claims** because of the small, self-authored
  dataset and deterministic agents.
