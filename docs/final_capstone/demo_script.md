# Demo Script (about 8 minutes)

Three scenarios on synthetic records, run live through the real workflow:

- **A** standard ReAct path;
- **B** selective ToT;
- **C** missing evidence.

Every value quoted below was produced by an actual run and is saved in the repository. If the live
environment fails, use the [fallback](#fallback-recorded-outputs) instead.

> **Say this once, at the start:** "The agents are deterministic and rule-based. No LLM is called.
> Every tool call goes through the MCP server, and a human approves every output. These are
> synthetic records."

## Setup (do this before the presentation)

```powershell
git clone <repo-url> public-records-agent        # Windows: use a short path such as C:\src\pra (MAX_PATH)
cd public-records-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt                 # about 5 minutes; includes the spaCy en_core_web_md model
python -m app.policy_search --demo              # first run downloads all-MiniLM-L6-v2 (~80 MB) and builds the index (~1 min)
python -m pytest -q                             # expect: 314 passed
```

Pre-flight, about 2 minutes before you start:

```powershell
python -m app.m4_demo --skip-tests              # A, B, C, D through LangGraph over MCP; writes docs/milestone_4/execution_traces/
streamlit run app/review_app.py                 # opens http://localhost:8501
```

- Have open in an editor: [docs/milestone_4/execution_traces/scenario_B.md](../milestone_4/execution_traces/scenario_B.md).
- In the app sidebar, type a reviewer name, for example `Demo Reviewer`.

| Minute | Segment |
|---|---|
| 0:00–0:45 | Context and architecture slide |
| 0:45–3:00 | Scenario A: standard path, approval, redaction |
| 3:00–6:00 | Scenario B: selective ToT, scores, reviewer feedback |
| 6:00–7:30 | Scenario C: missing evidence, blocked finalization |
| 7:30–8:00 | Audit trail and wrap-up |

## Scenario A: standard ReAct (clear policy)

**Input.** Request `OPRA-TEST-201`, record `TEST-201`: *"During the interaction, the resident
stated: My personal phone number is 609-555-0142."*

1. **Run the analysis.** In the sidebar: Dataset **Milestone 4 scenarios** → Synthetic request
   **OPRA-TEST-201** → **Run agent analysis**.
2. **PII detection.** The record panel highlights `609-555-0142` as finding **P01**. The findings
   table shows PHONE_NUMBER at [73:85], confidence **0.75**.
3. **Real retrieval.** In the finding card, under "Supporting policy passages":
   - **Source:** *Open Public Records Act, N.J.S.A. 47:1A-1 et seq. (GRC Readable Version)*, section
     **N.J.S.A. 47:1A-5(a)**, p. 15, chunk `opra_statute:0052`.
   - **URL:** `https://www.nj.gov/grc/laws/act/Readable%20Version%20of%20OPRA%20(Sept%202024)(P.L.%202024,%20c.16).pdf`
   - **Excerpt** (in the decision summary): *"…personal telephone number, or driver license number of
     any person…"*
4. **Evidence-supported recommendation.** Proposed action **REDACT**; policy evidence **SUPPORTED**.
5. **ToT bypassed.** Reasoning method **"Standard tool workflow (rule-based ReAct)"**. Expand
   **Audit → Agent tool calls**: there are exactly four calls:

   | Agent | Tool |
   |---|---|
   | record_analysis | `detect_pii` |
   | policy_research | `search_policy` |
   | compliance_qa | `propose_redactions` |
   | compliance_qa | `validate_citations` (5/5 valid) |

   There is no ToT node.
6. **Reviewer approval.** On the **Approve** tab, click **Approve recommendation**. The status
   turns 🟢 approved.
7. **Redaction.** Under **Final authorization**, tick *"I authorize this final redaction plan…"*
   and click **Finalize derivative**. Show the output:
   `During the interaction, the resident stated: My personal phone number is [REDACTED: PHONE_NUMBER].`
   - The derivative is saved under `outputs/derivatives/OPRA-TEST-201/`.
   - The original record file is unchanged; the system checks its hash.
   - Nothing is published.

**Recorded evidence:** [screenshots/scenario_A_clear_policy_finalized.png](screenshots/scenario_A_clear_policy_finalized.png),
[execution_traces/scenario_A.md](../milestone_4/execution_traces/scenario_A.md),
[review_scenarios.md § A](../milestone_5/review_scenarios.md).

## Scenario B: selective ToT (ambiguous policy)

**Input.** Request `OPRA-TEST-202`, record `TEST-202`: *"A caller who identified himself as a
retired Superior Court judge reported a fallen tree limb outside his home at 9 Juniper Court…"*

1. **Run the analysis.** Sidebar: **OPRA-TEST-202** → **Run agent analysis**.
2. **Ambiguity trigger.** Open **Reasoning and Evidence** on finding P01 (STREET_ADDRESS, confidence
   0.6). The evidence assessment reads:
   - *"STREET_ADDRESS is not listed without qualification; application depends on conditions."*
   - *"A retrieved provision applies only to specific classes of person."*

   The Orchestrator therefore routes to `tot`. Reasoning method: **Bounded Tree of Thought**.
3. **Branch generation and real scores.** The "Branches considered" table. Scores are
   relevance / citation validity / consistency / explanation, out of 30 / 30 / 25 / 15:

   | Branch | Depth | Action | Provision | Rel | Cit | Cons | Expl | Total | Status |
   |---|---|---|---|---|---|---|---|---|---|
   | d1.1 | 1 | REDACT | RESTRICTED_CLASS | 20 | 15 | 13 | 15 | **63** | evaluated (assumption unverified) |
   | d1.2 | 1 | HUMAN_REVIEW_REQUIRED | CONDITIONAL_REVIEW | 15 | 30 | 25 | 15 | **85** | evaluated |
   | d1.1>2 | 2 | REDACT | RESTRICTED_CLASS | 30 | 30 | 25 | 15 | **100** | **selected** |

   **Point out:**
   - At depth 2, the "covered person" assumption was checked against the record
     ("retired Superior Court judge") and marked SUPPORTED.
   - The run's **one targeted retrieval** (`search_policy`: *"custodian shall redact home address
     judicial officer…"*) added the operative 47:1A-5(a) passage `opra_statute:0052`.
   - The selected branch cites 47:1A-1.1 (`opra_statute:0020`) and 47:1A-5(a) (`opra_statute:0052`).
   - The note under the table: scores are **not** probabilities of legal correctness.
4. **Pruning and selection.**
   - In this run nothing needed pruning; both candidates stayed supported.
   - Selection required a score ≥ 70 **and** every assumption SUPPORTED **and** an operative
     provision.
   - For pruning, show Scenario D in
     [execution_traces/scenario_D.md](../milestone_4/execution_traces/scenario_D.md). There a private
     resident's address branch is **rejected** for an unsupported factual assumption, and the case
     is escalated.
5. **Human review changes the outcome.** On the **Request policy re-evaluation** tab:
   - Reason: `The caller only identified himself as a retired judge; the record does not establish covered status.`
   - Factual condition `covered_person`: **unconfirmed**.
   - Click **Request re-evaluation**.

   ToT reruns. The REDACT branches can no longer be selected, so `d1.2` (85) is selected and the
   finding is 🔴 **escalated** (REDACT → HUMAN_REVIEW_REQUIRED). The re-evaluation counter shows
   **1/2**.
6. **No approval possible.** The Approve tab reads *"Nothing to approve: there is no supported
   redaction."*

**Recorded evidence:** [screenshots/scenario_B_tot_reasoning_and_evidence.png](screenshots/scenario_B_tot_reasoning_and_evidence.png)
shows the state after step 5,
[execution_traces/scenario_B.md](../milestone_4/execution_traces/scenario_B.md) shows step 3, and
[review_scenarios.md § B](../milestone_5/review_scenarios.md) shows both.

## Scenario C: missing evidence

**Input.** Request `OPRA-TEST-203`, record `TEST-203`: *"The complaint was submitted through the
online portal from IP address 203.0.113.45 at 9:14 p.m.…"*

1. **Run the analysis.** Sidebar: **OPRA-TEST-203** → **Run agent analysis**.
2. **Bounded retrieval attempts.** Expand **Audit → Agent tool calls**:

   | # | Call | Detail |
   |---|---|---|
   | 1 | `detect_pii` | IP_ADDRESS, 0.95 |
   | 2 | `search_policy` | "What New Jersey law applies when a public record contains a person's IP address?" |
   | 3 | `search_policy` | reformulated: "internet protocol address personal information" |
   | 4 | `propose_redactions` | HUMAN_REVIEW_REQUIRED |
   | 5 | `search_policy` | the Orchestrator's single targeted retry |
   | 6 | `propose_redactions` | still HUMAN_REVIEW_REQUIRED |

   Passages were returned, but **none names IP addresses**, so evidence is **MISSING**. The search
   stopped at the configured limits: 2 queries per category plus 1 targeted retry.
3. **No fabricated legal references.** Policy evidence **MISSING**. Supporting passages: *"none: no
   policy basis was established"*. Decision summary: *"Insufficient policy evidence … Human review
   required."* No statute or section is cited.
4. **Escalation.** Status 🔴 escalated. Approve is disabled.
5. **Finalization blocked.** Tick authorize and click **Finalize derivative**. The blockers are
   listed, for example *"TEST-203-P01 is not resolved (status ESCALATED)"*, and a
   `FINALIZATION_BLOCKED` event is recorded. To finish, the reviewer must **reject** or **modify**
   the item as their own decision.

**Recorded evidence:** [screenshots/scenario_C_missing_evidence_blocked.png](screenshots/scenario_C_missing_evidence_blocked.png),
[execution_traces/scenario_C.md](../milestone_4/execution_traces/scenario_C.md),
[review_scenarios.md § C](../milestone_5/review_scenarios.md).

## Wrap-up (30 seconds)

- Open **Audit → Review event log**. It is append-only and hash-chained, and each entry records the
  actor and the UTC time.
- **Download audit report (JSON)** produces the full record: findings, evidence, ToT branches, tool
  calls, decisions.
- Close with the evaluation slide: 22 scenarios; 0 unsupported redactions in either configuration;
  ToT improved one ambiguous case (S12) for 13 extra tool calls.

## Fallback: recorded outputs

If the install, model download, MCP subprocess or Streamlit fails, present these files, which were
generated by real runs. Say that they are recorded outputs.

| Need | File |
|---|---|
| Screenshots of the real UI (A, B, C) | `docs/final_capstone/screenshots/*.png`, captured from the running app with saved sessions |
| Load the same sessions in the app without re-running agents | `streamlit run app/review_app.py`, then open `http://localhost:8501/?session=docs/milestone_5/scenarios/OPRA-TEST-201-fa2f8610.json` (202-70804a4f for B with `&expand=1`, 203-23d4078c for C) |
| Full traces: transitions, tool calls, ToT node tables | `docs/milestone_4/execution_traces/scenario_{A,B,C,D,D_budget}.md` and `.json` |
| Review sessions and audit reports | `docs/milestone_5/review_scenarios.md`, `docs/milestone_5/scenarios/scenario_{A,B,C}_audit.json` |
| Real-source retrieval (Checkpoint 3.1) | `docs/checkpoint_3_1/checkpoint_3_1_demo.md`, `02_retrieval_results.txt` |
| Evaluation | `docs/milestone_6/evaluation_results.md`, `tot_comparison.md` |
| Without the embedding model | `python -m app.m4_demo --transport local --skip-tests` still needs the semantic index. Keyword-only mode classifies Scenario A as AMBIGUOUS (documented limitation), so **do not** use `--no-semantic` for the demo; use the recorded files |

A loaded session shows the recorded state. Any action you take on it is saved to
`outputs/reviews/` (git-ignored), never back to the committed file in `docs/`. Scenario A's session
is already finalized and cannot be changed.

## What not to claim during the demo

- That an LLM chose the tool calls. It did not; the agents are rule-based.
- That a ToT score is a legal probability.
- That the system makes disclosure decisions. A named human authorizes every output, and nothing is
  published.
- That the system handles audio or video. That is deferred.
