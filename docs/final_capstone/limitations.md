# Limitations, Safety Assumptions and Future Work

This is a **prototype for a course capstone**. It recommends; a human reviewer decides. It is not
legal advice and must not be used on real records.

## 1. Reasoning and agents

| Limitation | Consequence | Evidence |
|---|---|---|
| **All agents are deterministic and rule-based.** No LLM plans, critiques or explains in the workflow | The "ReAct" loops are rule-driven decide → act → observe cycles. Results say nothing about LLM tool selection, LLM hallucination or prompt-injection resistance of a model | `docs/milestone_6/milestone_summary.md`; `environment.llm = null` in `evaluation_results.json` |
| The Claude planner (Milestone 3) was **tested only with a fake client** | No live-API evidence; it is not used by the multi-agent workflow | `tests/test_claude_planner.py` (8 tests, fake client) |
| The ToT critic is a deterministic rubric | "Explanation quality" mostly checks that uncertainty is stated, not how clear the reasoning is | `docs/milestone_4/tot_design.md` |
| Fact matching for ToT assumptions uses a fixed term list | "judge" counts as a judicial officer; unusual wording is missed; the reviewer must confirm each matched condition | `app/workflows/tot.py` (`check_fact`) |
| Narrow interpretation set | ToT generates interpretations only for provision types found in this corpus (restricted class, general operative, definition or balancing). Exceptions such as "use by any government agency" become open questions | `docs/milestone_4/tot_design.md` |
| Rubric scores are not probabilities of legal correctness | Before the fixes, ToT rated wrong selections "fully supported" (S15, S20) | `docs/milestone_6/tot_comparison_prefix.md` |

## 2. Detection and retrieval

| Limitation | Consequence |
|---|---|
| Presidio misses a phone number spelled out in words (S04) and a 7-digit number without an area code (S05). Recall is 17/19 | The QA sweep flags these two formats and blocks finalization until a reviewer acknowledges them. Other formats would still be missed silently |
| Retrieval returns only the top 4 passages | Some outcomes depend on wording (defect F4). Keyword-only mode misses 47:1A-5(a) for the phone case, so semantic or hybrid retrieval is required |
| Five policy documents: OPRA statute (GRC readable version), GRC exemptions list, GRC redaction guidance page, GRC Custodian's Handbook, and AG Directive 2022-1 on body-worn cameras | Many real exemptions, case law and agency rules are absent. Absent policy → escalation, never invention |
| The provenance guard trusts the `nj.gov` URL prefix in the manifest | It protects against injected local documents, not against a compromised official source |
| Text records only | No audio transcription, OCR or video redaction |

## 3. Human review and workflow

| Limitation | Consequence |
|---|---|
| Single reviewer; the name is self-entered; no authentication or access control | Not suitable for multi-user or real use |
| Sessions are local JSON files; LangGraph uses an in-memory checkpointer | A saved session can be reviewed and finalized in a new process, but the original graph run cannot be resumed there |
| Modifications are exact character offsets into the original text | No re-detection after edits |
| Reviewer factual answers map only to the conditions ToT defines (`covered_person`, `not_agency_number`, `detection_correct`) | Other facts must be handled by reject or modify |
| Rejection never triggers automatic re-evaluation | Differs from the proposal ("if a proposed redaction is rejected, the agent can review the evidence again"). Re-evaluation is an explicit reviewer request, limited to 2 per finding |

## 4. Evaluation

| Limitation | Consequence |
|---|---|
| 22 self-authored scenarios, 19 expected findings, 7 ToT cases | Behaviour on these cases only; no statistical generalization |
| Scripted (simulated) reviewer | Usability with real records staff was not measured |
| Evaluation used the in-process tool executor | MCP transport is covered separately (integration tests, Milestone 4 and 5 demos) |
| Latency measured on one Windows 10 laptop | Latency values are indicative only |

## 5. Safety assumptions

These are assumed true for the prototype to be safe to demonstrate:

1. Every input record is synthetic. The loader refuses files without `Synthetic: yes`, but cannot
   prove a record is fictional.
2. Policy documents are public nj.gov material listed in `data/policy_documents/manifest.json`.
3. A human reads the evidence before approving. The system blocks finalization without a named,
   explicit authorization but cannot verify that the reviewer understood it.
4. Outputs stay local. Derivatives go to `outputs/derivatives/` with `published: false`; nothing is
   transmitted or released.
5. No API keys are needed. `.env` is git-ignored, and no key is present in the repository (see
   [final_verification.md](final_verification.md)).

## 6. Future improvements

Labelled as **future architecture** in the diagrams; none of these is implemented.

- **LLM-assisted components behind the same guardrails.** For example, an LLM thought generator or
  critic whose outputs must still pass `validate_citations`, the provenance guard and the
  mandatory-evidence checks. Evaluate it against the same 22 scenarios plus new ones.
- **Detector improvements** for spelled-out and partial numbers, measured on a larger, independently
  written test set.
- **More policy sources** (agency-specific rules, GRC decisions), with provenance checks.
- **Audio and video**: transcription (for example Whisper) and frame redaction (OpenCV/FFmpeg) on
  synthetic media, the original Stage 8.
- **Multi-user review**: authentication, roles, second-reviewer sign-off, persistent checkpoints.
- **User study** with records staff to measure time saved and error rates.
