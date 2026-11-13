# Checkpoint Traceability

How each course checkpoint maps to what was built, and where the evidence is.

**Sources.**
- **In the repository:** `docs/checkpoint_3_1/`, `docs/checkpoint_4_1/`, `docs/checkpoint_5_1/`, the
  milestone documents, and the code.
- **Outside the repository:** the original project proposal (two versions) and the Checkpoint 3.1
  submission PDF, in the parent `capstone/` folder.

> **Gaps in the record.** The Checkpoint 1.1 and 2.1 submissions are not in the repository.
> - **1.1:** treated as the original project proposal
>   (`capstone-project-agentic-public-records-compliance-assistant-v2.pdf`), which has the shape of a
>   1.1 submission. This mapping is an assumption.
> - **2.1:** its content is known only from later references. Checkpoint 3.1 says it builds "on
>   Checkpoints 1.1 and 2.1", and Checkpoint 4.1 says earlier checkpoints introduced "a ReAct-based
>   reasoning design, short-term and long-term memory, and external tools."
>
> Please confirm both mappings against your own submissions.

## Evolution at a glance

| Checkpoint | Design decision | Implemented as | Status | Evidence |
|---|---|---|---|---|
| **1.1** Problem and agent proposal (assumed: original proposal) | Research-assistant agent for OPRA redaction review on video, audio and documents; policy-backed reasons; human approves, rejects or modifies; escalate when unclear; synthetic or public data only | Text-record assistant with cited recommendations and a mandatory human gate; synthetic records with `Synthetic: yes` headers | Implemented for **text**; audio and video **deferred** | `app/schemas.py`, `app/data_loader.py`, `data/`, `tests/test_data_loader.py` |
| **2.1** Reasoning, memory and tools (per 4.1's summary) | ReAct-style reasoning; short-term memory (current request, findings, tool outputs); long-term memory (indexed policies); external tools | Bounded **rule-based** ReAct loop (Milestone 3); typed workflow state as short-term memory; ChromaDB + BM25 index as long-term policy memory | Implemented; **ReAct is deterministic, not LLM-driven** | `app/agents/react_agent.py`, `app/agents/planners.py`, `tests/test_agent.py` |
| **3.1** Executed semantic retrieval | Real nj.gov OPRA ingestion; semantic search for "What New Jersey law applies when a public record contains someone's personal telephone number?"; agent run over MCP; missing-evidence run | Same, reproducible with `python -m app.checkpoint_3_1`. N.J.S.A. 47:1A-5(a) retrieved as chunk `opra_statute:0052`; missing-evidence run escalates with no citation | Implemented and **still passing** | `docs/checkpoint_3_1/`, `tests/test_checkpoint_3_1.py` (9 tests) |
| **4.1** Tree of Thought | Two versions were written. One rejects ToT, the other proposes **selective** ToT. The selective version was implemented: ToT only for ambiguous or conflicting evidence; 3 branches, depth 3, beam 2; 30/30/25/15 rubric; minimum 70; reject fabricated citations and unsupported facts; keep uncertain branches; one targeted retrieval; escalate unresolved conflicts | LangGraph ToT subgraph (`app/workflows/tot.py`) with exactly those parameters | Implemented, with **changes**: deterministic critic instead of a CrewAI/LangChain critic agent; LangGraph instead of CrewAI + LangChain | `docs/milestone_4/tot_design.md`, `tests/test_m4_tot.py` (18 tests), Milestone 6 ToT metrics |
| **5.1** Multi-agent architecture | Four agents: Orchestrator, Record Analysis, Policy Research, Compliance & QA; hybrid graph (sequential, parallel, bounded loops); communication through shared structured state; QA can request another passage through the orchestrator; ToT activated from the QA step | LangGraph `StateGraph` with those four agents; typed `WorkflowState`; MISSING evidence triggers one targeted retrieval; ToT reuses QA as critic and Policy Research for retrieval | Implemented. Record analysis and policy research run **sequentially** (research depends on findings); categories are researched in parallel | `app/workflows/`, `docs/milestone_4/architecture.md`, `tests/test_m4_workflow.py` (15), `tests/test_m4_scenarios.py` (5) |

## Course concepts → implementation

| Course concept | Where it is demonstrated | Visible example | Automated test |
|---|---|---|---|
| LLM limitations and failure handling | Missing-evidence escalation; no invented citations; tool-failure handling | `docs/checkpoint_3_1/05_missing_evidence_trace.txt`; Scenario C | `test_missing_evidence_retries_once_then_escalates_without_citations`, `test_agent_without_relevant_policy_does_not_invent_one` |
| Tool calling through MCP | `app/mcp_server.py` (official MCP SDK 2.3.0, stdio, 4 read-only tools) | `python -m app.mcp_client_demo`; Milestone 4 and 5 demos run every tool over MCP | `tests/test_mcp_integration.py`, `test_every_mcp_tool_is_read_only` |
| Single agent with a ReAct loop | `app/agents/react_agent.py` (Milestone 3) | `python -m app.agent_demo` | `tests/test_agent.py` (13) |
| RAG and knowledge/memory | `app/policies/` (BM25 + ChromaDB hybrid), workflow state | `python -m app.policy_search --demo` | `tests/test_policy_retrieval.py`, `test_policy_semantic.py`, `test_policy_chunking.py` |
| Multi-agent coordination with an orchestrator | `app/workflows/graph.py` | `python -m app.m4_demo` → `docs/milestone_4/execution_traces/` | `tests/test_m4_workflow.py`, `test_m4_scenarios.py` |
| Independent worker responsibilities | `app/workflows/agents.py` (separate agent classes, each with its own `ToolCaller` and bounded loop) | Tool-call tables in the traces show which agent called which tool | `tests/test_m4_workflow.py` |
| Human feedback changing later actions | `app/review/` + `app/review_app.py` (re-evaluation with structured feedback) | Scenario B: feedback changed REDACT → HUMAN_REVIEW_REQUIRED (`docs/milestone_5/review_scenarios.md`) | `tests/test_m5_review.py` (20), `test_m5_app.py` (6) |
| Guardrails, error recovery, evaluation | Provenance guard, QA veto, budgets, finalization blockers; `app/evaluation/` | `docs/milestone_6/` | `docs/milestone_6/safety_test_results.md` (40 guardrail tests), `tests/test_m6_*.py` |
| Advanced reasoning (ToT) | `app/workflows/tot.py` | Scenario B trace and screenshot | `tests/test_m4_tot.py` (18) |

## Milestones → commits

| Milestone | Commit | Content |
|---|---|---|
| 1 | `d42e63e` | Schemas, synthetic dataset, loader, tests |
| 2 | `83271e9` | NJ policy retrieval (BM25 + ChromaDB hybrid), evidence thresholds |
| 3 | `af4c925` | Tools, MCP server, single ReAct agent with guardrails |
| Checkpoint 3.1 | `bf3db49` | Executed real-source retrieval demonstration |
| Checkpoint 5.1 | `8f7b0d1` | Architecture submission artifacts |
| 4 | `cd5dccf` | LangGraph four-agent workflow, selective ToT |
| 5 | `3221e85` | Human review, re-evaluation, deterministic redaction, audit |
| 6 | `7a1a875` | Evaluation, guardrails, ToT validation, reviewer-added redactions (**final evaluation commit**) |
| 7 | not yet committed | Final deliverables (this folder), `app/evaluation/verify.py`, review-app session loading by URL |
