"""Single-agent ReAct loop: plan -> call a tool -> observe -> decide, with hard bounds.

The planner (rule-based or LLM) only chooses the next action. This loop owns everything that
must not depend on model behaviour: the step limit, tool-failure limits, argument resolution,
finish guardrails, and the final structured report. Recommendations in the report always come
from the propose_redactions tool, never from planner free text.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.agents.executors import ToolExecutor
from app.agents.models import (
    AgentReport,
    AgentStatus,
    FinishRequest,
    ToolCallRequest,
    TraceStep,
)
from app.schemas import (
    PolicyRetrievalResult,
    PublicRecord,
    PublicRecordRequest,
    RedactionAction,
    RedactionProposal,
    SensitiveDataFinding,
)
from app.tools.redaction import propose_redactions
from app.tools.service import ToolError

logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 10
DEFAULT_MAX_FAILURES_PER_TOOL = 2


@dataclass
class AgentState:
    request: PublicRecordRequest
    record: PublicRecord
    findings: list[SensitiveDataFinding] | None = None
    searches: list[PolicyRetrievalResult] = field(default_factory=list)
    proposal: RedactionProposal | None = None
    failures: Counter = field(default_factory=Counter)
    escalations: list[str] = field(default_factory=list)
    steps: list[TraceStep] = field(default_factory=list)

    @property
    def evidence_chunk_ids(self) -> list[str]:
        ids = [p.chunk.chunk_id for r in self.searches for p in r.passages]
        return list(dict.fromkeys(ids))


class Planner(Protocol):
    name: str

    async def next_action(self, state: AgentState) -> ToolCallRequest | FinishRequest: ...

    def observe(self, call: ToolCallRequest, result: dict[str, Any] | None, error: ToolError | None) -> None: ...


def summarise_observation(tool: str, result: dict[str, Any]) -> str:
    """A short, display-safe summary of a tool result for the trace."""
    if tool == "detect_pii":
        cats = [f"{f['category']} ({f['confidence']:.2f})" for f in result["findings"]]
        return f"{len(cats)} candidate(s): {', '.join(cats) or 'none'}"
    if tool == "search_policy":
        hits = [f"{p['chunk']['chunk_id']} [{p['chunk'].get('section') or 'no section'}]" for p in result["passages"]]
        return f"{result['status']}: " + ("; ".join(hits) if hits else result["message"])
    if tool == "propose_redactions":
        counts = Counter(r["action"] for r in result["recommendations"])
        return ", ".join(f"{n} {a}" for a, n in sorted(counts.items())) or "no recommendations"
    return "ok"


def display_arguments(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Arguments as shown in the trace; bulky finding objects are reduced to their ids."""
    if tool == "propose_redactions" and "findings" in arguments:
        shown = {k: v for k, v in arguments.items() if k != "findings"}
        shown["finding_ids"] = [f["finding_id"] for f in arguments["findings"]]
        return shown
    return dict(arguments)


class ReActAgent:
    def __init__(
        self,
        planner: Planner,
        executor: ToolExecutor,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_failures_per_tool: int = DEFAULT_MAX_FAILURES_PER_TOOL,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.planner = planner
        self.executor = executor
        self.max_steps = max_steps
        self.max_failures_per_tool = max_failures_per_tool

    def _resolve_arguments(self, state: AgentState, call: ToolCallRequest) -> dict[str, Any]:
        """Translate planner-level arguments into tool arguments.

        Planners refer to findings by id; the loop substitutes the detector's actual objects so
        no planner (or model) ever re-types record text or offsets.
        """
        args = dict(call.arguments)
        if call.tool == "propose_redactions":
            by_id = {f.finding_id: f for f in state.findings or []}
            wanted = args.pop("finding_ids", list(by_id))
            missing = [fid for fid in wanted if fid not in by_id]
            if missing:
                raise ToolError("INVALID_INPUT", f"unknown finding id(s): {missing}")
            args["findings"] = [by_id[fid].model_dump(mode="json") for fid in wanted]
            args.setdefault("record_id", state.record.record_id)
        return args

    def _record_success(self, state: AgentState, tool: str, result: dict[str, Any]) -> None:
        if tool == "detect_pii":
            state.findings = [SensitiveDataFinding.model_validate(f) for f in result["findings"]]
        elif tool == "search_policy":
            state.searches.append(PolicyRetrievalResult.model_validate(result))
        elif tool == "propose_redactions":
            state.proposal = RedactionProposal.model_validate(result)

    def _finish_guardrail(self, state: AgentState) -> str | None:
        if state.findings is None:
            return "Finish requested before the record was examined for sensitive information."
        if state.findings and state.proposal is None:
            return "Finish requested before redaction recommendations were produced."
        return None

    async def run(self, request: PublicRecordRequest, record: PublicRecord) -> AgentReport:
        state = AgentState(request=request, record=record)
        status: AgentStatus | None = None
        final_summary = ""
        logger.info("Agent start request=%s record=%s planner=%s", request.request_id, record.record_id, self.planner.name)

        for step in range(1, self.max_steps + 1):
            try:
                action = await self.planner.next_action(state)
            except Exception as exc:  # planner/LLM failure must not crash the run
                state.escalations.append(f"Planner failed: {exc}")
                state.steps.append(TraceStep(step=step, kind="guardrail", status="error",
                                             observation=str(exc), decision_summary="Planner error; stopping."))
                status = AgentStatus.ESCALATED
                final_summary = "Stopped because the planner failed."
                break

            if isinstance(action, FinishRequest):
                blocked = self._finish_guardrail(state)
                if blocked:
                    state.steps.append(TraceStep(step=step, kind="guardrail", status="blocked",
                                                 observation=blocked, decision_summary=action.decision_summary))
                    state.escalations.append(blocked)
                    status = AgentStatus.ESCALATED
                else:
                    state.steps.append(TraceStep(step=step, kind="finish", status="ok",
                                                 observation="Agent finished.", decision_summary=action.decision_summary))
                    state.escalations.extend(action.escalations)
                final_summary = action.decision_summary
                break

            arguments = dict(action.arguments)
            try:
                arguments = self._resolve_arguments(state, action)
                result = await self.executor.call(action.tool, arguments)
                self._record_success(state, action.tool, result)
                observation, error, ok = summarise_observation(action.tool, result), None, True
            except ToolError as exc:
                result, error, ok = None, exc, False
                observation = f"{exc.code}: {exc.message}"
            except Exception as exc:  # unexpected executor failure is treated like a tool error
                result, error, ok = None, ToolError("TOOL_FAILURE", str(exc)), False
                observation = f"TOOL_FAILURE: {exc}"

            state.steps.append(TraceStep(step=step, kind="tool_call", tool=action.tool,
                                         arguments=display_arguments(action.tool, arguments),
                                         status="ok" if ok else "error", observation=observation,
                                         decision_summary=action.decision_summary))
            logger.info("Step %d %s -> %s", step, action.tool, observation)
            self.planner.observe(action, result, error)

            if not ok:
                state.failures[action.tool] += 1
                if state.failures[action.tool] >= self.max_failures_per_tool:
                    state.escalations.append(
                        f"Tool {action.tool} failed {state.failures[action.tool]} time(s) ({error.code}); stopped."
                    )
                    status = AgentStatus.ESCALATED
                    final_summary = f"Stopped after repeated {action.tool} failures."
                    break
        else:
            state.escalations.append(f"Step limit of {self.max_steps} reached before the agent finished.")
            status = AgentStatus.STEP_LIMIT_REACHED
            final_summary = "Stopped at the step limit."

        return self._report(state, status, final_summary)

    def _report(self, state: AgentState, status: AgentStatus | None, final_summary: str) -> AgentReport:
        proposal = state.proposal
        if proposal is None and state.findings:
            # Incomplete run: every candidate still goes to a human, with no policy basis claimed.
            proposal = propose_redactions(state.record, state.findings, [])
            state.escalations.append("Recommendations were not completed by the agent; all findings need review.")

        recommendations = proposal.recommendations if proposal else []
        for rec in recommendations:
            if rec.action is RedactionAction.HUMAN_REVIEW_REQUIRED and not rec.policy_references:
                state.escalations.append(f"{rec.finding_id}: no policy basis found; human review required.")

        if status is None:
            status = AgentStatus.ESCALATED if state.escalations else AgentStatus.COMPLETED
        report = AgentReport(
            request_id=state.request.request_id,
            record_id=state.record.record_id,
            planner=self.planner.name,
            transport=self.executor.transport,
            status=status,
            steps=state.steps,
            findings=state.findings or [],
            policy_chunk_ids=state.evidence_chunk_ids,
            recommendations=recommendations,
            escalations=list(dict.fromkeys(state.escalations)),
            final_summary=final_summary or "No summary.",
        )
        logger.info("Agent end request=%s status=%s steps=%d", report.request_id, report.status.value, len(report.steps))
        return report
