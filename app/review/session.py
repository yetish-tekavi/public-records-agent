"""Human review session: append-only review events, per-finding decisions, and final authorization.

A session is created from a Milestone 4 workflow that paused at ``human_review``. It never edits
recommendations in place: every reviewer action is an appended ``ReviewEvent`` (hash-chained so an
earlier decision cannot be silently rewritten), and the current status of each finding is derived
by replaying those events.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.data_loader import PROJECT_ROOT
from app.review.redact import RedactionOutput, RedactionSpan, apply_redactions, sha256_text, validate_spans
from app.schemas import PolicyChunk, RedactionAction, RedactionRecommendation, SensitiveDataFinding
from app.workflows.state import RequestContext, ToTResult

MAX_REEVALUATIONS = 2
DERIVATIVE_DIR = PROJECT_ROOT / "outputs" / "derivatives"
SESSION_DIR = PROJECT_ROOT / "outputs" / "reviews"
EVIDENCE_STATUS = {"CLEAR": "SUPPORTED", "AMBIGUOUS": "AMBIGUOUS", "CONFLICTING": "CONFLICTING", "MISSING": "MISSING"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ItemStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    MODIFICATION_UNCONFIRMED = "MODIFICATION_UNCONFIRMED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"
    REEVALUATION_IN_PROGRESS = "REEVALUATION_IN_PROGRESS"
    MANUAL_RESOLUTION_REQUIRED = "MANUAL_RESOLUTION_REQUIRED"


RESOLVED = {ItemStatus.APPROVED, ItemStatus.MODIFIED, ItemStatus.REJECTED}

EventAction = Literal[
    "SESSION_CREATED", "APPROVED", "REJECTED", "MODIFICATION_PROPOSED", "MODIFICATION_CONFIRMED",
    "REEVALUATION_REQUESTED", "REEVALUATION_COMPLETED", "REEVALUATION_FAILED", "MANUAL_RESOLUTION_REQUIRED",
    "ESCALATION_ACKNOWLEDGED", "MANUAL_REDACTION_PROPOSED", "MANUAL_REDACTION_CONFIRMED", "MANUAL_REDACTION_WITHDRAWN",
    "FINALIZATION_BLOCKED", "FINALIZED",
]


class ManualRedaction(BaseModel):
    """A reviewer-added redaction for text the detector did not report. Human decision; no policy basis is claimed."""

    manual_id: str
    span: RedactionSpan
    category: str
    reason: str
    proposed_by: str
    status: Literal["PROPOSED", "CONFIRMED", "WITHDRAWN"]


class ReviewError(ValueError):
    """A reviewer action that is not allowed in the current state."""


class ReviewEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int
    event_id: str
    timestamp: str
    actor: str
    action: EventAction
    finding_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str
    hash: str


class ReviewItem(BaseModel):
    """Immutable snapshot of one finding as produced by the agents."""

    finding: SensitiveDataFinding
    recommendation: RedactionRecommendation
    reasoning_method: Literal["STANDARD", "TOT"]
    evidence_status: Literal["SUPPORTED", "AMBIGUOUS", "CONFLICTING", "MISSING", "UNKNOWN"]
    assessment_reasons: list[str] = Field(default_factory=list)
    evidence: list[PolicyChunk] = Field(default_factory=list)
    tot_result: ToTResult | None = None


class SessionEscalation(BaseModel):
    escalation_id: str
    text: str


class ReevaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)
    factual_confirmations: dict[str, Literal["confirmed", "denied", "unconfirmed"]] = Field(default_factory=dict)
    additional_query: str | None = Field(default=None, max_length=300)


class ItemState(BaseModel):
    finding_id: str
    status: ItemStatus
    recommendation: RedactionRecommendation
    reasoning_method: Literal["STANDARD", "TOT"]
    evidence_status: str
    tot_result: ToTResult | None
    evidence: list[PolicyChunk]
    reevaluation_attempts: int
    modification: list[RedactionSpan] | None
    last_decision: dict[str, Any] | None
    history: list[ReviewEvent]


class FinalizationResult(BaseModel):
    finalized: bool
    reasons: list[str] = Field(default_factory=list)
    plan: list[RedactionSpan] = Field(default_factory=list)
    derivative_path: str | None = None
    derivative_sha256: str | None = None
    redaction: RedactionOutput | None = None


class ReviewSession(BaseModel):
    session_id: str
    created_at: str
    request: RequestContext
    record_id: str
    record_path: str
    record_text: str
    source_text_sha256: str
    source_file_sha256: str
    items: list[ReviewItem]
    session_escalations: list[SessionEscalation] = Field(default_factory=list)
    workflow: dict[str, Any] = Field(default_factory=dict)
    events: list[ReviewEvent] = Field(default_factory=list)

    # --- construction -------------------------------------------------------

    @classmethod
    def from_workflow(cls, state: dict[str, Any], record_path: Path, actor: str = "system") -> ReviewSession:
        findings = state.get("findings", [])
        recs = {r.finding_id: r for r in state.get("recommendations", [])}
        assessments = {a.finding_id: a for a in state["qa"].assessments} if state.get("qa") else {}
        tots = {t.finding_id: t for t in state.get("tot_results", [])}
        evidence = {e.category: e.passages for e in state.get("evidence", [])}
        items = []
        for f in findings:
            a = assessments.get(f.finding_id)
            rec = recs.get(f.finding_id) or RedactionRecommendation(
                recommendation_id=f"{f.record_id}-R00", finding_id=f.finding_id, action=RedactionAction.HUMAN_REVIEW_REQUIRED,
                rationale="No recommendation was produced for this finding.")
            items.append(ReviewItem(
                finding=f, recommendation=rec, reasoning_method="TOT" if f.finding_id in tots else "STANDARD",
                evidence_status=EVIDENCE_STATUS.get(a.decision.value, "UNKNOWN") if a else "UNKNOWN",
                assessment_reasons=a.reasons if a else [], evidence=evidence.get(f.category, []), tot_result=tots.get(f.finding_id)))
        finding_ids = {f.finding_id for f in findings}
        general = [e for e in state.get("escalations", []) if not any(e.startswith(fid) for fid in finding_ids)]
        file_bytes = Path(record_path).read_bytes()
        session = cls(
            session_id=f"{state['request'].request_id}-{uuid.uuid4().hex[:8]}", created_at=now(), request=state["request"],
            record_id=state["request"].record_id, record_path=str(record_path), record_text=state["record_text"],
            source_text_sha256=sha256_text(state["record_text"]), source_file_sha256=hashlib.sha256(file_bytes).hexdigest(),
            items=items, session_escalations=[SessionEscalation(escalation_id=f"E{i}", text=t) for i, t in enumerate(general, 1)],
            workflow={
                "route": state.get("route"), "transitions": list(state.get("transitions", [])),
                "tool_calls": [t.model_dump(mode="json") for t in state.get("tool_calls", [])],
                "audit": [e.model_dump(mode="json") for e in state.get("audit", [])],
                "escalations": list(state.get("escalations", [])),
                "record_analysis_status": state["record_analysis"].status if state.get("record_analysis") else None,
                "completion_at_handoff": getattr(state.get("completion"), "value", state.get("completion")),
            },
        )
        session._append(actor, "SESSION_CREATED", None, {"findings": [f.finding_id for f in findings], "source_text_sha256": session.source_text_sha256})
        return session

    # --- append-only events -----------------------------------------------------

    @staticmethod
    def _event_hash(prev_hash: str, body: dict[str, Any]) -> str:
        return hashlib.sha256((prev_hash + json.dumps(body, sort_keys=True, default=str)).encode()).hexdigest()

    def _append(self, actor: str, action: str, finding_id: str | None, payload: dict[str, Any]) -> ReviewEvent:
        prev = self.events[-1].hash if self.events else "GENESIS"
        body = {"seq": len(self.events) + 1, "event_id": uuid.uuid4().hex, "timestamp": now(), "actor": actor,
                "action": action, "finding_id": finding_id, "payload": payload, "prev_hash": prev}
        event = ReviewEvent(**body, hash=self._event_hash(prev, body))
        self.events.append(event)
        return event

    def verify_chain(self) -> bool:
        prev = "GENESIS"
        for e in self.events:
            body = e.model_dump(exclude={"hash"})
            if e.prev_hash != prev or e.hash != self._event_hash(prev, body):
                return False
            prev = e.hash
        return True

    # --- derived state -----------------------------------------------------------

    def item(self, finding_id: str) -> ReviewItem:
        for it in self.items:
            if it.finding.finding_id == finding_id:
                return it
        raise ReviewError(f"unknown finding {finding_id}")

    def item_state(self, finding_id: str) -> ItemState:
        it = self.item(finding_id)
        rec, tot, method, evidence_status, evidence = it.recommendation, it.tot_result, it.reasoning_method, it.evidence_status, list(it.evidence)
        status = ItemStatus.PENDING if rec.action is RedactionAction.REDACT else ItemStatus.ESCALATED
        attempts, modification, last = 0, None, None
        history = [e for e in self.events if e.finding_id == finding_id]
        for e in history:
            p = e.payload
            if e.action == "APPROVED":
                status, last = ItemStatus.APPROVED, {"action": "approve", "reviewer": e.actor, "at": e.timestamp}
            elif e.action == "REJECTED":
                status, last = ItemStatus.REJECTED, {"action": "reject", "reviewer": e.actor, "at": e.timestamp, "reason": p.get("reason")}
            elif e.action == "MODIFICATION_PROPOSED":
                status, modification = ItemStatus.MODIFICATION_UNCONFIRMED, [RedactionSpan.model_validate(s) for s in p["spans"]]
                last = {"action": "modify", "reviewer": e.actor, "at": e.timestamp, "reason": p.get("reason")}
            elif e.action == "MODIFICATION_CONFIRMED":
                status = ItemStatus.MODIFIED
            elif e.action == "REEVALUATION_REQUESTED":
                status, attempts = ItemStatus.REEVALUATION_IN_PROGRESS, p["attempt"]
            elif e.action == "REEVALUATION_COMPLETED":
                rec = RedactionRecommendation.model_validate(p["recommendation"])
                tot = ToTResult.model_validate(p["tot_result"]) if p.get("tot_result") else None
                method = p["reasoning_method"]
                evidence_status = p["evidence_status"]
                evidence = [PolicyChunk.model_validate(c) for c in p["evidence"]]
                status = ItemStatus.PENDING if rec.action is RedactionAction.REDACT else ItemStatus.ESCALATED
                modification, last = None, None  # earlier decisions are preserved in history, not carried forward
            elif e.action == "REEVALUATION_FAILED":
                status = ItemStatus.ESCALATED
            elif e.action == "MANUAL_RESOLUTION_REQUIRED":
                status = ItemStatus.MANUAL_RESOLUTION_REQUIRED
        return ItemState(finding_id=finding_id, status=status, recommendation=rec, reasoning_method=method, evidence_status=evidence_status,
                         tot_result=tot, evidence=evidence, reevaluation_attempts=attempts, modification=modification,
                         last_decision=last, history=history)

    def states(self) -> list[ItemState]:
        return [self.item_state(it.finding.finding_id) for it in self.items]

    @property
    def finalized(self) -> bool:
        return any(e.action == "FINALIZED" for e in self.events)

    def acknowledged(self) -> set[str]:
        return {e.payload["escalation_id"] for e in self.events if e.action == "ESCALATION_ACKNOWLEDGED"}

    # --- reviewer actions -----------------------------------------------------------

    def _guard(self, finding_id: str, reviewer: str) -> ItemState:
        if not reviewer or not reviewer.strip():
            raise ReviewError("a reviewer name is required")
        if self.finalized:
            raise ReviewError("the session is finalized; no further changes are allowed")
        state = self.item_state(finding_id)
        if state.status is ItemStatus.REEVALUATION_IN_PROGRESS:
            raise ReviewError(f"{finding_id} is being re-evaluated")
        return state

    def approve(self, finding_id: str, reviewer: str, note: str | None = None) -> ReviewEvent:
        state = self._guard(finding_id, reviewer)
        if state.recommendation.action is not RedactionAction.REDACT:
            raise ReviewError(f"{finding_id} has no redaction recommendation to approve (action {state.recommendation.action.value}); "
                              "reject it or modify it with explicit spans")
        return self._append(reviewer, "APPROVED", finding_id, {"recommendation_id": state.recommendation.recommendation_id, "note": note})

    def reject(self, finding_id: str, reviewer: str, reason: str | None = None) -> ReviewEvent:
        state = self._guard(finding_id, reviewer)
        return self._append(reviewer, "REJECTED", finding_id, {"recommendation_id": state.recommendation.recommendation_id, "reason": reason})

    def propose_modification(self, finding_id: str, reviewer: str, spans: list[RedactionSpan | dict], reason: str) -> ReviewEvent:
        state = self._guard(finding_id, reviewer)
        if not reason or not reason.strip():
            raise ReviewError("a reason is required for a modification")
        spans = [RedactionSpan.model_validate(s) if isinstance(s, dict) else s for s in spans]
        if not spans:
            raise ReviewError("a modification needs at least one span; use reject to decline redaction")
        errors = validate_spans(self.record_text, spans)
        if errors:
            raise ReviewError("; ".join(errors))
        spans = [s.model_copy(update={"finding_ids": [finding_id], "source": "reviewer_modification"}) for s in spans]
        return self._append(reviewer, "MODIFICATION_PROPOSED", finding_id,
                            {"recommendation_id": state.recommendation.recommendation_id, "reason": reason,
                             "spans": [s.model_dump() for s in spans]})

    def confirm_modification(self, finding_id: str, reviewer: str) -> ReviewEvent:
        state = self._guard(finding_id, reviewer)
        if state.status is not ItemStatus.MODIFICATION_UNCONFIRMED or not state.modification:
            raise ReviewError(f"{finding_id} has no unconfirmed modification")
        errors = validate_spans(self.record_text, state.modification)
        if errors:
            raise ReviewError("; ".join(errors))
        return self._append(reviewer, "MODIFICATION_CONFIRMED", finding_id,
                            {"spans": [{"start": s.start, "end": s.end, "expected_text": s.expected_text} for s in state.modification]})

    def request_reevaluation(self, request: ReevaluationRequest) -> ReviewEvent:
        state = self._guard(request.finding_id, request.reviewer)
        if state.reevaluation_attempts >= MAX_REEVALUATIONS:
            return self._append(request.reviewer, "MANUAL_RESOLUTION_REQUIRED", request.finding_id,
                                {"reason": f"re-evaluation limit of {MAX_REEVALUATIONS} reached", "request": request.model_dump()})
        return self._append(request.reviewer, "REEVALUATION_REQUESTED", request.finding_id,
                            {"attempt": state.reevaluation_attempts + 1, "request": request.model_dump(),
                             "previous_recommendation": state.recommendation.model_dump(mode="json")})

    def record_reevaluation(self, finding_id: str, outcome: dict[str, Any], actor: str = "orchestrator") -> ReviewEvent:
        action = "REEVALUATION_FAILED" if outcome.get("error") else "REEVALUATION_COMPLETED"
        return self._append(actor, action, finding_id, outcome)

    # --- reviewer-added redactions (text the detector missed) ---------------------------

    def find_occurrences(self, text: str) -> list[tuple[int, int]]:
        """Every exact occurrence of ``text`` in the original record, as (start, end)."""
        if not text:
            return []
        out, i = [], self.record_text.find(text)
        while i >= 0:
            out.append((i, i + len(text)))
            i = self.record_text.find(text, i + 1)
        return out

    def manual_redactions(self) -> list[ManualRedaction]:
        """Current reviewer-added redactions, derived by replaying events."""
        current: dict[str, ManualRedaction] = {}
        for e in self.events:
            p = e.payload
            if e.action == "MANUAL_REDACTION_PROPOSED":
                current[p["manual_id"]] = ManualRedaction(manual_id=p["manual_id"], span=RedactionSpan.model_validate(p["span"]),
                                                          category=p["category"], reason=p["reason"], proposed_by=e.actor, status="PROPOSED")
            elif e.action == "MANUAL_REDACTION_CONFIRMED" and p["manual_id"] in current:
                current[p["manual_id"]].status = "CONFIRMED"
            elif e.action == "MANUAL_REDACTION_WITHDRAWN" and p["manual_id"] in current:
                current[p["manual_id"]].status = "WITHDRAWN"
        return list(current.values())

    def add_manual_redaction(self, reviewer: str, start: int, end: int, category: str, reason: str,
                             replacement: str | None = None) -> ReviewEvent:
        if not reviewer or not reviewer.strip():
            raise ReviewError("a reviewer name is required")
        if self.finalized:
            raise ReviewError("the session is finalized; no further changes are allowed")
        if not reason or not reason.strip():
            raise ReviewError("a reason is required to add a redaction")
        if not category or not category.strip():
            raise ReviewError("a category is required to add a redaction")
        if not (0 <= start < end <= len(self.record_text)):
            raise ReviewError(f"span [{start}:{end}] is outside the original text")
        span = RedactionSpan(start=start, end=end, expected_text=self.record_text[start:end],
                             replacement=replacement or f"[REDACTED: {category}]", source="reviewer_addition")
        errors = validate_spans(self.record_text, [span])
        if errors:
            raise ReviewError("; ".join(errors))
        manual_id = f"MANUAL-{len([e for e in self.events if e.action == 'MANUAL_REDACTION_PROPOSED']) + 1:02d}"
        span = span.model_copy(update={"finding_ids": [manual_id]})
        return self._append(reviewer, "MANUAL_REDACTION_PROPOSED", None,
                            {"manual_id": manual_id, "span": span.model_dump(), "category": category, "reason": reason,
                             "note": "Reviewer-added redaction for text the detector did not report; no policy basis is claimed by the system."})

    def confirm_manual_redaction(self, manual_id: str, reviewer: str) -> ReviewEvent:
        if not reviewer or not reviewer.strip():
            raise ReviewError("a reviewer name is required")
        if self.finalized:
            raise ReviewError("the session is finalized; no further changes are allowed")
        m = next((x for x in self.manual_redactions() if x.manual_id == manual_id), None)
        if m is None or m.status != "PROPOSED":
            raise ReviewError(f"{manual_id} is not a proposed reviewer redaction")
        errors = validate_spans(self.record_text, [m.span])
        if errors:
            raise ReviewError("; ".join(errors))
        return self._append(reviewer, "MANUAL_REDACTION_CONFIRMED", None,
                            {"manual_id": manual_id, "start": m.span.start, "end": m.span.end, "expected_text": m.span.expected_text})

    def withdraw_manual_redaction(self, manual_id: str, reviewer: str, reason: str | None = None) -> ReviewEvent:
        if not reviewer or not reviewer.strip():
            raise ReviewError("a reviewer name is required")
        if self.finalized:
            raise ReviewError("the session is finalized; no further changes are allowed")
        m = next((x for x in self.manual_redactions() if x.manual_id == manual_id), None)
        if m is None or m.status == "WITHDRAWN":
            raise ReviewError(f"{manual_id} is not an active reviewer redaction")
        return self._append(reviewer, "MANUAL_REDACTION_WITHDRAWN", None, {"manual_id": manual_id, "reason": reason})

    def acknowledge_escalation(self, escalation_id: str, reviewer: str, note: str) -> ReviewEvent:
        if not reviewer.strip() or not note.strip():
            raise ReviewError("reviewer and note are required to acknowledge an escalation")
        if escalation_id not in {e.escalation_id for e in self.session_escalations}:
            raise ReviewError(f"unknown escalation {escalation_id}")
        return self._append(reviewer, "ESCALATION_ACKNOWLEDGED", None, {"escalation_id": escalation_id, "note": note})

    # --- finalization -------------------------------------------------------------

    def redaction_plan(self) -> list[RedactionSpan]:
        plan = []
        for s in self.states():
            f = self.item(s.finding_id).finding
            if s.status is ItemStatus.APPROVED:
                plan.append(RedactionSpan(start=f.start, end=f.end, expected_text=f.text, replacement=f"[REDACTED: {f.category.value}]",
                                          finding_ids=[f.finding_id]))
            elif s.status is ItemStatus.MODIFIED and s.modification:
                plan.extend(s.modification)
        plan.extend(m.span for m in self.manual_redactions() if m.status == "CONFIRMED")
        return plan

    def finalization_blockers(self, reviewer: str, authorize: bool) -> list[str]:
        reasons = []
        if self.finalized:
            reasons.append("the session is already finalized")
        for s in self.states():
            if s.status not in RESOLVED:
                reasons.append(f"{s.finding_id} is not resolved (status {s.status.value})")
        open_escalations = [e for e in self.session_escalations if e.escalation_id not in self.acknowledged()]
        reasons += [f"escalation {e.escalation_id} is not acknowledged: {e.text}" for e in open_escalations]
        reasons += [f"reviewer redaction {m.manual_id} [{m.span.start}:{m.span.end}] is not confirmed"
                    for m in self.manual_redactions() if m.status == "PROPOSED"]
        if not reviewer or not reviewer.strip():
            reasons.append("a named reviewer must authorize finalization")
        if not authorize:
            reasons.append("the reviewer has not authorized the final redaction plan")
        if not self.verify_chain():
            reasons.append("the review event log failed its integrity check")
        if hashlib.sha256(Path(self.record_path).read_bytes()).hexdigest() != self.source_file_sha256:
            reasons.append("the source record file changed after analysis")
        return reasons

    def finalize(self, reviewer: str, authorize: bool, output_dir: Path | None = None) -> FinalizationResult:
        output_dir = output_dir or DERIVATIVE_DIR
        reasons = self.finalization_blockers(reviewer, authorize)
        plan = self.redaction_plan()
        redaction = None
        if not reasons:
            redaction = apply_redactions(self.record_text, plan, expected_sha256=self.source_text_sha256)
            reasons += [f"validation: {e}" for e in redaction.errors]
        if reasons:
            if not self.finalized:
                self._append(reviewer or "unknown", "FINALIZATION_BLOCKED", None, {"reasons": reasons})
            return FinalizationResult(finalized=False, reasons=reasons, plan=plan, redaction=redaction)

        folder = output_dir / self.request.request_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.record_id}.redacted.{redaction.output_sha256[:12]}.txt"
        if path.resolve() == Path(self.record_path).resolve():
            raise ReviewError("refusing to overwrite the original record")
        path.write_text(redaction.text, encoding="utf-8")
        self._append(reviewer, "FINALIZED", None, {
            "authorized_by": reviewer, "derivative_path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/") if path.is_relative_to(PROJECT_ROOT) else str(path),
            "derivative_sha256": redaction.output_sha256, "source_text_sha256": self.source_text_sha256,
            "plan": [s.model_dump() for s in redaction.applied], "published": False,
        })
        return FinalizationResult(finalized=True, plan=plan, derivative_path=str(path), derivative_sha256=redaction.output_sha256, redaction=redaction)

    # --- persistence ----------------------------------------------------------------

    def save(self, folder: Path | None = None) -> Path:
        folder = folder or SESSION_DIR
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.session_id}.json"
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> ReviewSession:
        session = cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
        if not session.verify_chain():
            raise ReviewError(f"{path}: review event log failed its integrity check")
        return session
