# Milestone 4 — Selective Tree of Thought

Implementation: `app/workflows/tot.py` (LangGraph subgraph), configured by `ToTConfig`.

## When ToT runs

Only when the Compliance & QA Agent classifies a finding's evidence as **AMBIGUOUS** or
**CONFLICTING** (`app/workflows/evidence.py`):

| Decision | Rule (from retrieved text and the record only) |
|---|---|
| CLEAR | All of these hold: the category is one OPRA lists without qualification (phone, driver licence, SSN, month and day of birth); a retrieved passage names it with operative language ("shall redact", "exempt from public access", "deemed to be confidential", …), and not inside a restricted-class or narrower provision; detection confidence is at least 0.6; and no exclusion clause applies |
| AMBIGUOUS | Evidence exists but depends on conditions: who the person is, a definition or balancing test, or low detection confidence |
| CONFLICTING | A passage requires redaction while a retrieved exclusion clause may apply given the record context (for example "shall not include … telephone number of a public agency" next to a township main line) |
| MISSING | No retrieved passage names the category. This goes to the targeted-retry path, not ToT |

CLEAR cases bypass ToT entirely (Scenario A).

## Terms

- **Thought:** an evidence-based interpretation (for example, "Redact: the provision protects this home address if the person is a covered person").
- **Node (`ThoughtNode`):** a recommended action (`REDACT` or `HUMAN_REVIEW_REQUIRED`), the provision type, cited chunk ids with a *branch-local copy* of the passages, assumptions (FACTUAL or LEGAL, each with a status), unresolved questions, evaluation status and score.
- **Branch:** an alternative interpretation or verification path.
- **Search:** beam search, run as a generate → evaluate → prune loop.

## Levels

| Depth | Stage | What happens |
|---|---|---|
| 1 | INTERPRETATION | Up to `max_branching` (3) candidates, built only from retrieved provisions: a restricted-class provision (with a FACTUAL covered-person assumption), a general operative provision, a definition or balancing provision (with a LEGAL assumption), and always a conservative `HUMAN_REVIEW_REQUIRED` candidate. When an exclusion applies, each REDACT candidate also carries a FACTUAL "not an agency number" assumption |
| 2 | VERIFICATION | Each retained branch checks its FACTUAL assumptions against the record text near the finding. If it still lacks operative evidence or has a LEGAL assumption, it may use the run's **single** targeted retrieval through the Policy Research Agent. No retrieval is spent on a branch whose facts already failed |
| 3 | CONCLUSION | Branches that still have open gaps are finalised with those gaps recorded as unresolved |

A branch with nothing left to verify becomes terminal early (the review candidate at depth 1;
fully verified branches at depth 2).

## Scoring (Compliance & QA critic, deterministic)

| Criterion | Points | How |
|---|---|---|
| Policy relevance | 30 | 15 if a cited passage names the category. For REDACT, +10 for operative language and +5 for a primary-source citation (the statute). An escalation candidate earns only the 15, because it does not apply the evidence |
| Citation validity | 30 | All cited chunk ids and excerpts are checked with the `validate_citations` MCP tool. 30 if valid. 15 for a REDACT candidate whose valid citations lack operative language. 0 if they could not be validated (budget) |
| Consistency with facts and conditions | 25 | 25 minus 12 for each assumption that is still UNVERIFIED |
| Explanation and uncertainty | 15 | Stated interpretation (5), unresolved questions listed whenever a gap remains (5), and the human-approval condition attached (5) |

**Deterministic checks reject a branch immediately:**
- a fabricated citation (the chunk doesn't exist) or an altered excerpt;
- a FACTUAL assumption that the record does not support;
- a REDACT candidate that cites no evidence.

**Mandatory constraint at selection:** a REDACT branch is selectable only if every assumption is
SUPPORTED and an operative provision is cited. A high score alone cannot authorise redaction,
and no branch can authorise disclosure.

## Pruning and selection

- Rejected branches are pruned immediately, with the reason recorded.
- After each depth, at most `beam_width` (2) eligible branches are kept. The rest are pruned with a "beam width" reason.
- Branches scoring below `min_score` (70) are never selected.
- Selection takes the highest score among terminal (or still-open) selectable branches. **Ties** go to stronger primary-source evidence (statute, then the GRC exemptions list, then the handbook). A remaining tie between *different actions* is an unresolved material conflict and is **escalated**.
- If no branch qualifies, the result is `ESCALATED` with the unresolved gaps listed.

## Budgets

`ToTConfig` defaults:
- `max_branching` = 3
- `max_depth` = 3
- `beam_width` = 2
- `min_score` = 70
- `max_tool_calls` = 6 (citation validations plus the targeted retrieval)
- `time_budget_s` = 60
- `allow_targeted_retrieval` = True

When a budget runs out, the event is recorded and the branch stays unverified. Scenario D-budget
runs with `max_tool_calls=0`: citations can't be validated, nothing can be selected, and the case
is escalated.

## Observed behaviour (from the execution traces)

| Scenario | Branches | Result |
|---|---|---|
| B: retired judge, home address | The restricted-class branch scored 63 at depth 1. At depth 2 its covered-person assumption was SUPPORTED by the record ("retired Superior Court judge"), and the targeted retrieval added the operative 47:1A-5(a) text, giving 100. The review branch scored 85 | REDACT recommendation (score 100), pending human approval |
| D: resident, home address | The restricted-class branch was **rejected** at depth 2 (unsupported factual assumption; no retrieval spent). The review branch scored 85 | Escalated to human review |
| D-budget | No citation validation possible | No branch selectable; escalated |

In Scenario B no branch needed pruning, because both candidates stayed supported. Pruning is
shown by Scenario D (rejection) and by unit tests for beam-width pruning, fabricated or altered
citations, the conflicting-exclusion block, and tie handling.

## Limitations

- **Heuristic fact matching.** Facts are matched against a fixed term list. For example, "judge" is
  treated as indicating a "judicial officer". The reviewer is asked to confirm each matched condition.
- **Deterministic qualitative evaluation.** No LLM judges explanation quality, and the explanation
  criterion mostly checks that uncertainty is stated.
- **Narrow interpretation set.** Interpretations cover the provision types found in this corpus
  (restricted class, general operative, definition or balancing). Exceptions such as "use by any
  government agency" are surfaced as open questions, not reasoned about.
- **Scores are not legal correctness.** They measure how well a branch is supported by retrieved
  text and record facts.
