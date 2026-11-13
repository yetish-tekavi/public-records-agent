# Presentation Outline (10 slides, about 12 minutes plus demo)

**Deck:** [final_presentation.pptx](final_presentation.pptx). It is built by
`python docs/final_capstone/build/build_presentation.py`.

**Sources for every figure:**
- `docs/milestone_6/evaluation_results.json` (commit `7a1a875`);
- the Milestone 4 execution traces;
- the Checkpoint 3.1 demo.

**Screenshots** are crops of real captures of the running app
(`docs/final_capstone/screenshots/`). Full speaker notes are in the deck's notes pane.

| # | Slide | Message | Visual | Evidence |
|---|---|---|---|---|
| 1 | Title and project overview | An agentic assistant that prepares evidence-grounded redaction recommendations for NJ OPRA requests, with a human in charge | Five capability chips | — |
| 2 | Problem and intended users | Records custodians redact by hand, and every redaction needs a legal basis; the assistant prepares, the human decides | Three numbered cards (users / does / never does) | Proposal (Checkpoint 1.1) |
| 3 | Why agentic AI | A single LLM prompt can invent law, cannot act, cannot recover, leaves no audit trail | Six-row "single prompt → agentic system" comparison | Design of `validate_citations`, provenance guard, audit log |
| 4 | Multi-agent logical architecture | Four deterministic agents on one LangGraph workflow; ToT is a conditional subgraph, not a fifth agent; MCP tool layer; human review | **Architecture diagram** (native shapes) | `app/workflows/graph.py`, `docs/final_capstone/architecture/` |
| 5 | ReAct and MCP tool workflow | Decide → act → observe → repeat, bounded; 4 read-only MCP tools; **rule-based, not LLM-driven** | Four step cards, four tool cards, the Scenario A trace | `docs/milestone_4/execution_traces/scenario_A.md` |
| 6 | Semantic policy retrieval and citations | Five official nj.gov documents, 496 chunks, hybrid BM25 + ChromaDB; the phone-number query returns 47:1A-5(a) at rank 2; MISSING → human review | Pipeline, executed-query card, **real UI screenshot** | `docs/checkpoint_3_1/` |
| 7 | Selective ToT architecture | Only ambiguous or conflicting evidence triggers ToT; 3 / 3 / 2 / ≥ 70; 30-30-25-15 rubric; mandatory checks; real Scenario B scores; implemented vs proposed | Route chips, rubric bars, **branch table (63 / 85 / 100)** | `docs/milestone_4/execution_traces/scenario_B.md` |
| 8 | Demonstration and actual evaluation results | 22 scenarios: A and B identical except S12 (+1 preferred decision); 0 unsupported redactions; ToT costs 13 tool calls; not a general accuracy claim | **Native A-vs-B bar chart** (7 metrics), four stat callouts, demo strip | `evaluation_results.json`, `react_vs_tot_comparison.md` |
| 9 | Guardrails, human approval, limitations | Six enforced guardrails; 16/16 safety checks, 40/40 guardrail tests, 314/314 tests; honest limitations | Numbered guardrails, **real UI screenshot of blocked finalization**, limitations card | `docs/milestone_6/safety_test_results.md` |
| 10 | Lessons learned, conclusion, roadmap | Baseline comparison found real defects; a score is not correctness; provenance beats breadth; roadmap clearly marked future | Three columns | `docs/milestone_6/milestone_summary.md` |

## Suggested timing

| Slides | Minutes |
|---|---|
| 1–3: context | 2.5 |
| 4–7: architecture, ReAct/MCP, retrieval, ToT | 5 |
| 8: results, then switch to the live demo ([demo_script.md](demo_script.md)) | 1.5 + 8 (demo) |
| 9–10: safety, limitations, close | 2.5 |

## Claims to avoid (all checked against the evidence)

- That an LLM plans or critiques. The agents are deterministic. The Claude planner was tested only
  with a fake client.
- That ToT improves accuracy in general. It improved 1 of 17 matched findings, with 7 ToT cases in
  total.
- That ToT scores are probabilities of legal correctness.
- That audio or video is supported. It is deferred.
- Any screenshot or number not listed in the evidence column.
