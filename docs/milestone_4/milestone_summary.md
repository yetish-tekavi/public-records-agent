# Milestone 4 — Summary

**Multi-agent orchestration with selective Tree of Thought.** Results below are taken from the
run recorded in [execution_traces/](execution_traces/README.md) and [test_results.md](test_results.md)
(`python -m app.m4_demo`, tools called through the MCP server).

## Completion criterion

| Criterion | Evidence | Status |
|---|---|---|
| One working normal multi-agent workflow | Scenario A: 7 steps, standard route, REDACT citing N.J.S.A. 47:1A-5(a), ToT not invoked | Met |
| One working ToT workflow | Scenario B: ToT invoked; 3 nodes; selected branch scored 100 with verified assumptions; REDACT recommended for human approval | Met |
| One safe missing-evidence scenario | Scenario C: one targeted retry, then HUMAN_REVIEW_REQUIRED with no citations and no invented statute | Met |
| Passing regression tests | 242 passed (199 earlier + 43 new) | Met |

## What was built

| Stage | Files | Functionality |
|---|---|---|
| 1. Four roles | `app/workflows/state.py`, `agents.py`, `evidence.py`; `validate_citations` added to `app/tools/service.py` and `app/mcp_server.py` | Typed schemas; Record Analysis, Policy Research and Compliance & QA agents with bounded tool loops and timeouts; evidence classification |
| 2. LangGraph execution | `app/workflows/graph.py`, `runner.py` | Orchestrator nodes, typed `WorkflowState` with accumulating audit, tool-call and transition logs, in-memory checkpointer |
| 3. Conditional routing | `graph.py` `evidence_decision` | CLEAR → standard; AMBIGUOUS / CONFLICTING → ToT; MISSING → one targeted retry; ERROR → bounded recovery; step limit → human review |
| 4. Bounded ToT | `app/workflows/tot.py` | Generate, evaluate and prune subgraph; beam 2, depth 3, branching 3, minimum 70; 30/30/25/15 rubric; citation validation through MCP; fabricated-citation and unsupported-assumption rejection; tie rule; tool and time budgets; isolated branch state |
| 5. Reviewer escalation | `graph.py` `human_review`, `runner.resume` | LangGraph `interrupt()` before any completion; validated `approve` / `reject` / `return`; only `approve` sets `disclosure_authorized` |
| 6. Evidence | `app/m4_demo.py`, `data/milestone_4/`, `tests/test_m4_*.py`, `docs/milestone_4/` | Four scenarios plus a budget-limited variant, traces, tests, docs |

## Scenario results

| Scenario | Path | ToT | Final action | Checks |
|---|---|---|---|---|
| A — straightforward phone number | standard (7 steps) | not invoked | REDACT (47:1A-5(a)) | 5/5 |
| B — retired judge's home address | tot (8 steps) | SUPPORTED_RECOMMENDATION, score 100 | REDACT | 5/5 |
| C — IP address, no policy in corpus | targeted retry, then standard (11 steps) | not invoked | HUMAN_REVIEW_REQUIRED, no citations | 5/5 |
| D — resident's home address | tot (8 steps) | ESCALATED, 1 branch rejected | HUMAN_REVIEW_REQUIRED | 5/5 |
| D-budget — same, tool budget 0 | tot (8 steps) | ESCALATED, nothing selectable | HUMAN_REVIEW_REQUIRED | 4/4 |

All five runs end paused at human review with `disclosure_authorized = false`. Scenario A was also
resumed with a **scripted reviewer input**, labelled as simulated, to show that only that decision
sets `disclosure_authorized = true`.

## Reused, not rebuilt

- **Tools and retrieval:** ToolService and its tools, the MCP server and executors, hybrid semantic retrieval and the Checkpoint 3.1 subsection labels.
- **Rules and queries:** Presidio detection, the `propose_redactions` rules, and the policy query templates.
- **Milestone 3 agent:** the single ReAct agent and its tests are unchanged and still pass.
- **Checkpoint 3.1:** its tests pass. Its committed evidence files were not regenerated.

## Implemented vs. not implemented

- **Implemented and tested:** everything listed under "What was built".
- **Not implemented:**
  - LLM-driven agents or an LLM qualitative critic. All agents are deterministic, and nothing in
    this milestone should be described as LLM-driven ReAct.
  - A reviewer revision loop that re-runs analysis after "return" (Stage 6).
  - Persistent checkpointing across processes.

## Known issues and limitations

1. **Scenario B didn't exercise pruning.** Both candidates stayed supported. Pruning is shown in
   Scenario D (rejection) and in unit tests for beam width, fabricated or altered citations,
   conflicting exclusions and ties.
2. **Keyword-only retrieval misclassifies Scenario A.** Keyword search misses the 47:1A-5(a) phone
   passage, so A would be classified AMBIGUOUS and go to ToT. The workflow depends on semantic or
   hybrid retrieval, consistent with the Checkpoint 3.1 finding.
3. **Fact checks use a fixed term list.** For example, "judge" is treated as indicating a
   "judicial officer". The reviewer is asked to confirm every matched condition.
4. **The evidence rules are small and hand-written.** They cover the provisions in this corpus
   (restricted class, general operative, definition or balancing, the agency-number exclusion).
   Statutory exceptions are surfaced as open questions, not interpreted.
5. **Scores are not legal correctness.** They measure support from retrieved text and record facts only.

## Remaining tasks (toward November 19)

- **Stage 6:** a reviewer "return / modify" loop that feeds corrections back into the workflow,
  and a redaction rendering of the approved working copy.
- **Stage 7:** an evaluation set beyond the 4 + 3 + 1 synthetic cases, observability summary,
  final demo script and report.
- **Optional:** a live Claude critic or planner, if credentials become available, clearly reported
  as a separate, measured variant.
