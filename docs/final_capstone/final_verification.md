# Final Verification (Milestone 7)

**When:** 10–12 November 2026.
**Where:** the development machine (Windows 10, Python 3.14.7).
**Code verified:** the final evaluation commit `7a1a875`, plus the uncommitted Milestone 7 changes:
- `app/review_app.py`: open a saved session from the URL (`?session=`, `&expand=1`), used for the
  screenshots;
- `app/evaluation/verify.py` (new);
- this folder;
- the README;
- `requirements-docs.txt`.

**Status legend:**

| Status | Meaning |
|---|---|
| **PASSED** | Ran and met the expectation |
| **FAILED** | Ran and did not |
| **SKIPPED** | Deliberately not run, with the reason |
| **UNTESTED** | Not verified in Milestone 7 |

Raw logs are in [verification_logs/](verification_logs/).

## Summary

| Outcome | Count |
|---|---|
| PASSED | 25: 14 required checks (rows 1–13 and 2b) + 11 additional |
| FAILED | 1: the live nj.gov download in the Checkpoint 3.1 demo; this environment had no network or DNS |
| SKIPPED | 1: the Claude planner, live API (no API key, by design) |
| UNTESTED | 4: interactive browser click-through of the demo script; macOS/Linux install; Python 3.11–3.13; a machine with an empty model cache |

## Required checks

| # | Check | Method | Result | Status |
|---|---|---|---|---|
| 1 | Full test suite | `python -m pytest -q` on the working tree, including the M7 `review_app.py` change | **314 passed** in 109.9 s, 0 failed, 0 skipped (`00_full_test_suite.log`) | PASSED |
| 2 | Checkpoint 3.1 retrieval still works | `python -m app.checkpoint_3_1` | Retrieval, agent and missing-evidence checks all pass; 47:1A-5(a) at rank 2, similarity 0.6537; checkpoint tests 9/9. **The network download of the OPRA PDF failed** (`getaddrinfo failed`), and the demo fell back to the genuine local copy: 18/19 checks, exit 1 (`07_checkpoint_3_1.log`) | PASSED (retrieval) / **FAILED** (live download, environmental) |
| 2b | Same, offline mode | `python -m app.checkpoint_3_1 --offline` | 18/19. Offline mode still reports the download check as failed and exits 1 by design (`07b_checkpoint_3_1_offline.log`) | PASSED (behaves as designed) |
| 3 | Four-agent coordination operates as documented | `python -m app.m4_demo` (MCP transport) | Scenarios A, B, C, D and D-budget: all checks PASS; transitions and tool calls match `docs/milestone_4/architecture.md`; then 314 passed (`08_m4_demo.log`) | PASSED |
| 4 | Standard cases bypass ToT | Scenario A trace; evaluation | Scenario A route `standard`, "ToT not invoked" PASS; evaluation 14/14 straightforward cases bypassed | PASSED |
| 5 | Ambiguous cases invoke ToT | Scenario B; evaluation | Scenario B route `tot`, selected d1.1>2 at 100; evaluation activation 7/7, 0 false activations | PASSED |
| 6 | Beam width, depth, scoring and pruning limits enforced | `tests/test_m4_tot.py` (18), `test_tot_limits_cannot_be_raised_beyond_design`; invariant check over all ToT runs | All pass; 0 invariant violations (branching ≤ 3, depth ≤ 3, beam ≤ 2, prune reasons logged, branch-local citations); D-budget scenario enforces the tool budget | PASSED |
| 7 | Missing evidence causes safe escalation | Scenario C (M4, M5); evaluation S10, S11, S18 | One targeted retry, then HUMAN_REVIEW_REQUIRED with no citations; finalization blocked in M5; evaluation escalation recall 11/11 | PASSED |
| 8 | Original records remain unchanged | `test_original_record_is_not_modified`, `test_source_change_after_analysis_blocks_finalization`; M5 demo check; safety check over 44 runs | All pass; "original record unchanged" PASS in `m5_demo`; safety check PASS | PASSED |
| 9 | Finalization requires explicit human approval | `test_unauthorized_finalization_is_blocked_and_recorded`, `test_only_human_approval_authorizes_disclosure`, `test_approve_without_reviewer_name_is_refused`; M5 Scenario C | All pass; finalization blocked and recorded | PASSED |
| 10 | Prompt injection in retrieved documents cannot override agent instructions | S20 (injected non-official document), S22 (instructions in record) in the evaluation rerun; F1 tests | Injected document never cited; injected citation `47:1A-99` never repeated (A and B); 16/16 safety checks. Scope: agents are rule-based, so this shows the evidence controls, not LLM resistance | PASSED |
| 11 | All examples and demo commands work | 12 documented commands run by script (`verification_logs/summary.txt`) | 11 of 12 exit 0. The exception is `app.checkpoint_3_1` (exit 1, network only; see row 2) | PASSED except row 2 |
| 12 | Repository contains no secrets or sensitive records | Regex scan of tracked and new files and all git history for API keys, tokens and private keys; `.env` check; synthetic-data checks | No matches. No `.env` on disk or in git (`.env`, `.env.*` ignored; only `.env.example` with an empty key). Every request and record file has `Synthetic: yes`. Phones are all 555-01xx; IPs 203.0.113.45 and 198.51.100.23 (RFC 5737); email `example.com`; no SSN-like strings | PASSED |
| 13 | Setup works in a clean environment | Fresh `git clone` of `7a1a875` into a new folder, new venv, `pip install -r requirements.txt`, `pytest` | Install exit 0 in 272 s; **314 passed** (`00_clean_clone_install_and_tests.log`). Caveats: same machine (the embedding model was already in `~/.cache/chroma`); a first attempt in a very deep folder failed with the Windows 260-character path limit (onnxruntime), now documented in the README | PASSED (with caveats) |

## Additional checks

| Check | Result | Status |
|---|---|---|
| Reported metrics match raw traces | `python -m app.evaluation.verify`: **33/33** metrics match (`11_eval_verify.log`) | PASSED |
| Evaluation is reproducible | `python -m app.evaluation.run --tag m7check`: 44 runs, 0 errors; every headline metric identical to the committed final run; ToT activation identical; safety 16/16 (`10_eval_run.log`, `10_eval_run_m7check_results.md`) | PASSED |
| Guardrail test mapping | `python -m app.evaluation.safety_report`: 40/40 guardrail tests passed; full suite 314 passed in 229.8 s (`12_safety_report.log`). The duration in `summary.txt` (170575 s) is wrong because the system clock changed during the run; pytest's own timing is correct | PASSED |
| Milestone 5 review scenarios | `python -m app.m5_demo`: A (approve, finalize, redact), B (ToT, feedback, re-run, comparison), C (blocked) all PASS (`09_m5_demo.log`) | PASSED |
| MCP discovery and calls | `python -m app.mcp_client_demo`: 4 tools discovered and called (`04_mcp_client.log`) | PASSED |
| Single ReAct agent, local and MCP | `python -m app.agent_demo`; `--request OPRA-TEST-002 --transport mcp` (`05_*`, `06_*`) | PASSED |
| Retrieval CLI | `python -m app.policy_search --demo`; phone-number query (`02_*`, `03_*`) | PASSED |
| Data loader CLI | `python -m app.main` (`01_main.log`) | PASSED |
| Streamlit reviewer renders recorded sessions | App launched (`streamlit run app/review_app.py`); headless Edge captured A, B and C from saved sessions (`build/capture_screenshots.py`); screenshots inspected | PASSED |
| Reruns change only timings | `git diff` after the demo reruns showed only millisecond timings and timestamps; the committed evidence was restored to the `7a1a875` versions | PASSED |
| Deliverable builds | Diagram (draw.io XML parsed; PNG exported); deck (10 slides, rendered to PNG through PowerPoint and inspected); report DOCX → PDF through Word (18 pages, pages rendered and inspected) | PASSED |

## Skipped and untested

| Item | Status | Why / what to do |
|---|---|---|
| Claude planner against the live API (`--planner claude`) | SKIPPED | No API key, by design. It is documented everywhere as "tested with a fake client only" |
| Interactive click-through of [demo_script.md](demo_script.md) in a browser | UNTESTED | The UI paths are covered by `tests/test_m5_app.py` (Streamlit AppTest) and the screenshots, but nobody has clicked through the script live. **Rehearse it on 18 November** |
| Install on macOS or Linux | UNTESTED | Only Windows 10 was used |
| Python 3.11–3.13 | UNTESTED | Only 3.14.7 was used. `requirements.txt` pins versions released for current Python |
| First run with an empty `~/.cache/chroma` | UNTESTED in M7 | The model download path worked in earlier milestones; it needs network access |

## Known issues carried into the final submission

1. **The Checkpoint 3.1 demo exits 1 without network access,** including with `--offline`, because
   the download check is counted as a failure. All retrieval checks pass. *Not changed:* behaviour is
   documented; changing it now is not a critical fix.
2. **Detector recall is 17/19.** Spelled-out and 7-digit phone numbers are flagged by QA for the
   reviewer, not detected.
3. **Uncommitted changes.** The Milestone 7 changes are not yet committed or pushed. This awaits your
   approval.
