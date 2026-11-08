# Tools, MCP, and the Single ReAct Agent (Milestone 3)

## Overview

```
                 ┌──────────────── ReActAgent (app/agents/react_agent.py) ───────────────┐
 request +       │  loop (≤ max_steps):                                                   │
 record  ──────► │    planner.next_action(state) ──► ToolCallRequest | FinishRequest      │
                 │    resolve ids → executor.call(tool, args) → observe → update state    │
                 │  guardrails: step limit · per-tool failure limit · finish checks       │
                 └───────┬───────────────────────────────────────────────┬──────────────┘
                         │ Planner                                        │ ToolExecutor
          ┌──────────────┴──────────────┐                  ┌──────────────┴───────────────┐
          │ RuleBasedPlanner (offline)  │                  │ LocalToolExecutor (in-process)│
          │ ClaudePlanner (Messages API)│                  │ McpToolExecutor ──stdio──► MCP │
          └─────────────────────────────┘                  │            server (mcp_server) │
                                                           └──────────────┬───────────────┘
                                                                          ▼
                                             ToolService (app/tools/service.py): validation,
                                             id resolution, error mapping
                                               ├─ search_policy  → PolicyRetriever (M2)
                                               ├─ detect_pii     → Presidio PiiDetector
                                               └─ propose_redactions → rule logic
```

## Tools

| Tool | Input | Output | Notes |
|---|---|---|---|
| `search_policy` | `query`, `k` (1–10), `method` | `PolicyRetrievalResult` | Verbatim passages with title, section, page, URL; or `INSUFFICIENT` with none |
| `detect_pii` | exactly one of `text` / `record_id` | `PiiDetectionResult` | Presidio candidates with category, exact span, offsets and confidence |
| `propose_redactions` | `record_id`, `findings`, `policy_chunk_ids` | `RedactionProposal` | Each finding gets `REDACT` or `HUMAN_REVIEW_REQUIRED`, never `RELEASE` |

All three are implemented once in `ToolService` and exposed in two ways: in-process, and through
the MCP server. Errors come back as `ToolError(code, message)` with one of these codes:
`INVALID_INPUT`, `NOT_FOUND`, `TOOL_FAILURE`, `TOOL_UNAVAILABLE`, `UNKNOWN_TOOL`, `DATA_ERROR`.

**Identifiers instead of content.** Tools resolve record ids and policy `chunk_id`s themselves.
A caller therefore cannot alter record text or supply its own policy excerpt.
`propose_redactions` rejects unknown chunk ids, and rejects findings whose text does not match
the record at the stated offsets.

### detect_pii (Presidio)

- spaCy `en_core_web_md` for names, plus Presidio's phone, US driver licence, SSN and email recognizers.
- Three added pattern recognizers: US street addresses, the spaced NJ licence format, and dates of birth
  (counted only when "DOB", "birth" or "born" appears nearby).
- `DATE_TIME`, `ORGANIZATION` and `LOCATION` are not requested, because on the test records they flagged
  timestamps, work orders and facility names.
- Overlapping spans are resolved by highest score, then longest span. The score threshold is 0.4.
- On the three synthetic records, the detector matches the ground truth exactly
  (5 of 5 findings, 0 false positives, including the no-PII record).

### propose_redactions (rules, separate from detection)

A finding is `REDACT` only when all three conditions hold:

1. Its category is one the retrieved OPRA text lists without qualification: phone numbers,
   driver licence numbers, SSNs, or month and day of birth.
2. A supplied passage names the category **outside** a narrower provision. Mentions inside firearms
   background checks or convicts' access to victim information do not count.
3. Detector confidence is at least 0.6.

Supporting passages are listed primary source first: the statute, then the GRC exemption list,
handbook and guidance pages. **Exception language** ("except for", "unless", "shall not apply",
"provided, however") is quoted verbatim as an open question when it appears after the category
mention, either in the supporting passage or in the following chunks of the same subsection.
ToolService supplies those chunks as section context. For example, 47:1A-5(a)'s "except for:" list
sits in the chunk after the telephone clause.

Every other finding is `HUMAN_REVIEW_REQUIRED`, with any relevant evidence attached and open
questions listed. This covers names (privacy-interest balancing), home addresses (Daniel's Law
covers specific people only), personal email (conditional), low-confidence detections, and
findings with no evidence. Rationales quote the passage text, so the citation and its wording can
be traced.

## MCP server

- `app/mcp_server.py` uses the official MCP Python SDK 2.x (`MCPServer`, the renamed FastMCP) over stdio.
- All three tools are annotated read-only and idempotent.
- Heavy resources (policy index, Presidio) load on the first call.
- Logs go to stderr at WARNING by default, because stdout carries the protocol.
- `PRA_SEMANTIC=0` selects keyword-only retrieval.

`python -m app.mcp_client_demo` starts the server and demonstrates the protocol from the client
side: it discovers the tools, calls each one, and shows the structured error returned for an
invented chunk id.

## ReAct loop

The planner only chooses the next action. The loop owns everything that must not depend on model behaviour:

| Guardrail | Behaviour |
|---|---|
| Step limit (default 10) | Stops the run with `STEP_LIMIT_REACHED` and an escalation |
| Tool failures | The failure is returned to the planner, which may retry. After 2 failures of the same tool the run stops with `ESCALATED` |
| Finish checks | Finishing before `detect_pii`, or before `propose_redactions` when findings exist, is blocked |
| Incomplete run | Any unprocessed findings get `HUMAN_REVIEW_REQUIRED` with no policy basis claimed |
| Id resolution | Planners refer to findings by id; the loop substitutes the detector's objects |
| Planner crash | Includes LLM, authentication and refusal errors. Contained and reported as an escalation |
| Recommendations | Taken only from `propose_redactions` output, never from planner text |

Escalations list unresolved issues: findings with no policy basis, tool failures, guardrail stops,
step limits, and anything the planner flags.

### Planners

- **RuleBasedPlanner** (deterministic, used by the tests and the default demo). It runs
  `detect_pii`, then for each category `search_policy` with a templated question. If no returned
  passage explicitly names the category, it searches once more with a reformulated query. It then
  calls `propose_redactions` with every retrieved chunk id, and finishes.
- **ClaudePlanner** (`--planner claude`). This is a manual tool-use loop over the Anthropic Messages API:
  - Model `claude-opus-5-5` at effort `medium`, with server-side refusal fallback (`fallbacks: "default"`).
  - Strict tool schemas, and a `finish` tool. Every tool takes a `decision_summary` argument, which is
    the only model-written text shown in the trace.
  - Thinking blocks and free text are passed back to the API unchanged but are never displayed or logged.
  - Record and passage text is wrapped in tags, and the model is told to treat it as data.
  - Parallel tool calls are executed one per step, and their results are returned together in one message.

## Trace example (rule-based, TEST-002, via MCP)

```
1. detect_pii(record_id="TEST-002")  [ok]
   Decision: Request OPRA-TEST-002 asks for record TEST-002; scanning it for candidate sensitive information first.
   Result:   3 candidate(s): PERSON_NAME (0.85), STREET_ADDRESS (0.95), PHONE_NUMBER (0.75)
3. search_policy(query="Is a person's home address exempt from public access under OPRA?", k=3)  [ok]
   Decision: Found STREET_ADDRESS; looking for policy that addresses it.
   Result:   SUFFICIENT: grc_handbook:0018 [...]; grc_handbook:0043 [...]; grc_handbook:0009 [...]
4. search_policy(query="home address personal information privacy", k=3)  [ok]
   Decision: Previous search did not name STREET_ADDRESS; trying a reformulated query.
   Result:   SUFFICIENT: grc_exemptions:0025 [exemption 20: Personal identifying information]; ...
6. propose_redactions(...)  [ok]  →  2 HUMAN_REVIEW_REQUIRED, 1 REDACT
```

## Limitations

- **The Claude planner has not been run against the live API.** No credentials were available. Its
  message protocol, parsing and failure handling are tested with a scripted fake client.
- **The rule logic encodes a narrow, conservative reading of the retrieved text.** It decides only
  whether a passage *names* a data type. It does not interpret exceptions (for example "use by any
  government agency"), or who the requester is.
- **The narrow-provision filter is a keyword window.** It can be fooled by unusual phrasing.
- **The detector's ground-truth match covers only 5 findings in 3 short records.** That is not an
  accuracy estimate. Names that spaCy misses, unusual phone formats, and context-free dates of birth
  will be missed.
- **The rule planner's queries are templates.** Categories outside the templates fall back to a
  generic query.
