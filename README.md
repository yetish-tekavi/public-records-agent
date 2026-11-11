# public-records-agent
CMU capstone project - agentic AI assistant that examines a synthetic public-records request, searches publicly available NJ policies, analyzes synthetic records, identifies potentially sensitive information, recommends redactions, invokes appropriate tools and sends the results for human review

> **Prototype only.** The assistant recommends; a human reviewer decides. It never makes
> final legal disclosure decisions.

## Status

**Milestone 6: evaluation, reliability, guardrails, and ToT validation** (complete; see [docs/milestone_6](docs/milestone_6/milestone_summary.md))

| Stage | Description | Status |
|---|---|---|
| 1 | Python project, schemas, synthetic records | Done |
| 2 | Deterministic policy retrieval and PII detection tools | Done |
| 3 | Single ReAct agent | Done (rule-based planner verified; Claude planner tested with a fake client only) |
| 4 | MCP tool server | Done (local stdio server, 4 tools) |
| 5 | Orchestrator with policy, record-analysis, and QA workers | Done: LangGraph, 4 agents, bounded ToT ([docs/milestone_4](docs/milestone_4/milestone_summary.md)) |
| 6 | Human feedback and redaction workflow | Done: Streamlit reviewer UI, per-finding decisions, bounded re-evaluation, deterministic redaction, final authorization ([docs/milestone_5](docs/milestone_5/milestone_summary.md)) |
| 7 | Safety, evaluation, observability, final demo | Evaluation and safety done (22 scenarios, ReAct vs ToT, 4 defects fixed); final demo and report pending |
| 8 | Optional synthetic video | Not started |

All agents are **deterministic** (rule-based tool use); no LLM is required. The single-agent
Claude planner exists but has not been run against the live API (no credentials).

## Data safety

- Every request and record in `data/` is **fictional**. Names, addresses, and events are
  invented, and phone numbers use the reserved fictional range `555-0100` to `555-0199`.
- Every request and record file must carry a `Synthetic: yes` header. The loader refuses
  any file without it.
- `data/policy_documents/` holds public New Jersey OPRA/GRC and Attorney General documents
  downloaded from nj.gov. Their sources are listed in `manifest.json`.
- Do not add real police records, agency records, or personal data to this repository.

## Setup

Requires Python 3.11 or newer (developed on 3.14).

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` also installs the spaCy `en_core_web_md` model (about 40 MB) used by Presidio.
For the Claude planner, copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`.

The first semantic search downloads the all-MiniLM-L6-v2 embedding model (about 80 MB) to
`~/.cache/chroma/` and builds a local index in `outputs/policy_index/`, which takes about a minute.
Later runs reuse both. To skip the model entirely, use `--no-semantic`.

## Run

```bash
python -m app.main             # summarise all synthetic review cases
python -m app.main --verbose   # include debug logging for each file loaded
```

### Policy retrieval

```bash
python -m app.policy_search --demo                                   # five example queries
python -m app.policy_search "Is a driver license number confidential?"
python -m app.policy_search "redact an electronic document" --method keyword -k 5
python -m app.policy_search "juvenile information exemption" --json
python -m app.policy_search "..." --no-semantic                      # keyword only, no model
```

Each result shows the evidence status, then for every passage: document title, section, page,
effective date, source URL, scores, and the verbatim excerpt. If no passage meets the evidence
thresholds, the status is `INSUFFICIENT` with the message *"Human review required."* See
[docs/policy_retrieval.md](docs/policy_retrieval.md) for the design, the thresholds, and the limitations.

### Milestone 6: evaluation

```bash
python -m app.evaluation.run              # 22 scenarios x {ToT off, ToT on}: metrics, traces, reports
python -m app.evaluation.safety_report    # guardrail tests mapped to safety requirements
```

Results: [evaluation_results.md](docs/milestone_6/evaluation_results.md) ·
[tot_comparison.md](docs/milestone_6/tot_comparison.md) · [safety_test_results.md](docs/milestone_6/safety_test_results.md).

### Milestone 5: reviewer interface

```bash
streamlit run app/review_app.py           # run the agents on a synthetic request, review each finding, finalize
python -m app.m5_demo                     # three scripted review scenarios over MCP, audit reports, tests
```

In the app, enter your name, pick a synthetic request, and run the analysis. For each finding you can
approve, reject (with an optional reason), modify the exact spans (with a reason; spans must be
confirmed), or request policy re-evaluation (at most 2 times). For text the detector missed (QA flags
possible misses), add a redaction for the exact text, which must then be confirmed. Finalization is a separate step
that requires every finding to be resolved, every escalation acknowledged, and explicit authorization.
The derivative is written to `outputs/derivatives/`; the original record is never modified, and
nothing is published. See [docs/milestone_5/review_workflow.md](docs/milestone_5/review_workflow.md).

### Milestone 4: multi-agent workflow

```bash
python -m app.m4_demo                     # 4 scenarios through the LangGraph workflow, tools over MCP, then tests
python -m app.m4_demo --transport local   # same, tools in-process
python -m app.m4_demo --skip-tests
```

Each run writes traces (state transitions, tool calls, ToT branch scores, pruning, recommendations,
audit log) to [docs/milestone_4/execution_traces/](docs/milestone_4/execution_traces/README.md).
Design: [architecture](docs/milestone_4/architecture.md) · [Tree of Thought](docs/milestone_4/tot_design.md).

### Checkpoint 3.1: real-source retrieval demonstration

```bash
python -m app.checkpoint_3_1             # ingest OPRA from nj.gov, semantic search, agent over MCP, missing-evidence run, tests
python -m app.checkpoint_3_1 --offline   # same, using the genuine local copy if nj.gov is unreachable
python -m app.policies.ingest            # ingestion only: provenance record for N.J.S.A. 47:1A-5(a)
```

All outputs, including the generated report, go to [docs/checkpoint_3_1/](docs/checkpoint_3_1/checkpoint_3_1_demo.md).

### MCP server and agent

```bash
python -m app.mcp_client_demo                             # start the MCP server, discover tools, call each one
python -m app.agent_demo                                  # ReAct agent on all requests (rule-based planner)
python -m app.agent_demo --request OPRA-TEST-002 --transport mcp   # same agent, tools called through MCP
python -m app.agent_demo --planner claude                 # Claude chooses the tool calls (needs API key)
python -m app.agent_demo --json                           # full structured reports
```

The agent demo prints the tool-call trace (each tool, its arguments, a result summary, and a short
decision summary), then the recommendations with citations and open questions, and finally the
escalations. Model reasoning is never displayed. See [docs/agent_architecture.md](docs/agent_architecture.md).

Expected output (abridged):

```
Loaded 3 synthetic review case(s) from ...\data

OPRA-TEST-001 -> TEST-001 (interaction_transcript)
  Requires redaction: yes
  Expected findings:  2 [PHONE_NUMBER, DRIVERS_LICENSE_NUMBER]
...
```

The command exits with code `1` and logs the offending file if any data file is malformed.

## Test

```bash
python -m pytest                          # everything
python -m pytest -m "not semantic_model"  # skip tests that need the embedding model
python -m pytest -m "not integration"     # skip tests that start the MCP server subprocess
python -m pytest tests/test_m4_tot.py tests/test_m4_workflow.py tests/test_m4_scenarios.py -v   # Milestone 4 only
python -m pytest tests/test_m5_redact.py tests/test_m5_review.py tests/test_m5_app.py -v          # Milestone 5 only
```

The `semantic_model` tests are skipped automatically until the model has been downloaded once.

## Repository structure

```
app/
  schemas.py          Pydantic models: request, record, finding, policy reference,
                      redaction recommendation, expected result
  data_loader.py      Loads and cross-validates requests, records, and ground truth
  main.py             Command-line entry point (review cases)
  policy_search.py    Command-line entry point (policy retrieval demo)
  mcp_server.py       MCP server exposing the three tools over stdio
  mcp_client_demo.py  MCP client: discover and call the tools
  agent_demo.py       Run the ReAct agent and print its trace
  console.py          Console output helpers
  policies/
    extract.py        Manifest loading; PDF/HTML text extraction and cleaning
    chunker.py        Section-aware chunking with provenance (cached)
    keyword.py        BM25 keyword index
    semantic.py       ChromaDB semantic index
    retriever.py      Hybrid retrieval with evidence thresholds
  tools/
    service.py        ToolService: validated search_policy / detect_pii / propose_redactions
    pii.py            Presidio detector configuration
    redaction.py      Conservative redaction-recommendation rules
  agents/
    react_agent.py    Bounded ReAct loop and guardrails (Milestone 3 single agent)
    planners.py       Rule-based and Claude planners
    executors.py      In-process and MCP tool executors
    models.py         Actions, trace, and report models
  workflows/          Milestone 4 multi-agent workflow
    state.py          Typed LangGraph state and schemas
    agents.py         Record Analysis, Policy Research, Compliance & QA agents
    evidence.py       Evidence classification (CLEAR / AMBIGUOUS / CONFLICTING / MISSING)
    tot.py            Bounded Tree of Thought subgraph (beam search)
    graph.py          Orchestrator and the LangGraph workflow
    runner.py         Start a case, pause at human review, resume with a decision
  m4_demo.py          Milestone 4 scenarios and trace capture
  review/             Milestone 5 human review
    session.py        Review session, append-only hash-chained events, decisions, finalization checks
    reevaluation.py   Reviewer-requested bounded re-evaluation through the Orchestrator's agents
    redact.py         Deterministic span redaction and validation
    report.py         Structured audit report
  review_app.py       Streamlit reviewer interface
  evaluation/         Milestone 6 evaluation: scenarios, dataset builder, harness, metrics, reports
  m5_demo.py          Milestone 5 review scenarios and evidence capture
data/
  synthetic_requests/ request_NNN.txt          OPRA-style requests
  synthetic_records/  record_NNN.txt           Matching synthetic records
  expected_results/   record_NNN.expected.json Ground-truth findings
  policy_documents/   Public NJ policy documents + manifest.json
docs/                 Architecture, scope, and policy retrieval notes
outputs/              Generated files: policy index, derivatives (git-ignored)
tests/                pytest suite (synthetic fixtures only)
```

## Data format

Requests and records are plain text: a `Key: Value` header block, a blank line, then the body.

```
Record ID: TEST-001
Record Type: interaction_transcript
Title: Synthetic Police Interaction Record
Record Date: 2026-09-14
Synthetic: yes

Officer: Can you confirm your contact details?
...
```

Ground-truth findings give character offsets into the record **body**, the text after the
header. The loader checks that `body[start:end]` equals each finding's `text`.

## Test cases

| Request | Record | Scenario | Expected findings |
|---|---|---|---|
| OPRA-TEST-001 | Police interaction transcript | Phone number and synthetic driver's license number | 2 |
| OPRA-TEST-002 | Non-emergency dispatch call | Name, street address, phone number; caller asks for anonymity | 3 |
| OPRA-TEST-003 | Public Works inspection log | No obvious PII; internal asset and work-order IDs as decoys | 0 |
