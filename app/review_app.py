"""Streamlit reviewer interface (Milestone 5).

    streamlit run app/review_app.py

Runs the Milestone 4 multi-agent workflow on a synthetic request, then lets a named reviewer
approve, reject, modify, or request re-evaluation of each finding, and finally authorize a
deterministically redacted derivative. Shows decision summaries and tool evidence only.
"""

from __future__ import annotations

import asyncio
import html
import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data_loader import PROJECT_ROOT, load_review_cases  # noqa: E402
from app.review.redact import RedactionSpan  # noqa: E402
from app.review.report import build_audit_report  # noqa: E402
from app.review.session import SESSION_DIR, ItemStatus, ReevaluationRequest, ReviewError, ReviewSession  # noqa: E402
from app.schemas import RedactionAction  # noqa: E402

DATASETS = {
    "Milestone 4 scenarios": PROJECT_ROOT / "data" / "milestone_4",
    "Core synthetic dataset": PROJECT_ROOT / "data",
    "Checkpoint 3.1": PROJECT_ROOT / "data" / "checkpoint_3_1",
}
STATUS_LABEL = {
    ItemStatus.PENDING: "🟡 pending", ItemStatus.APPROVED: "🟢 approved", ItemStatus.MODIFIED: "🔵 modified",
    ItemStatus.MODIFICATION_UNCONFIRMED: "🔵 modified (spans not confirmed)", ItemStatus.REJECTED: "⚪ rejected",
    ItemStatus.ESCALATED: "🔴 escalated", ItemStatus.REEVALUATION_IN_PROGRESS: "🟣 re-evaluating",
    ItemStatus.MANUAL_RESOLUTION_REQUIRED: "🔴 manual resolution required",
}
METHOD_LABEL = {"STANDARD": "Standard tool workflow (rule-based ReAct)", "TOT": "Bounded Tree of Thought"}
SCORE_NOTE = "Scores measure how well a branch is supported by retrieved text and record facts. They are not probabilities of legal correctness."


@st.cache_resource(show_spinner="Loading detection and retrieval models ...")
def get_orchestrator(data_dir: str):
    from app.agents.executors import LocalToolExecutor
    from app.tools.service import ToolService
    from app.workflows.graph import Orchestrator

    return Orchestrator(LocalToolExecutor(ToolService(data_dir=Path(data_dir))))


def save(session: ReviewSession) -> None:
    session.save()
    st.session_state["session"] = session


def run_action(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ReviewError as exc:
        st.session_state["flash"] = ("error", str(exc))
        st.rerun()  # show the refusal immediately
    save(st.session_state["session"])
    st.session_state["flash"] = ("success", "Recorded.")
    st.rerun()


# --- sidebar: start or load a session --------------------------------------------------


def sidebar() -> str:
    st.sidebar.header("Reviewer")
    reviewer = st.sidebar.text_input("Your name (recorded with every action)", key="reviewer")
    st.sidebar.header("Start a review")
    dataset = st.sidebar.selectbox("Dataset", list(DATASETS), key="dataset")
    data_dir = DATASETS[dataset]
    cases = {c.request.request_id: c for c in load_review_cases(data_dir)}
    request_id = st.sidebar.selectbox("Synthetic request", list(cases), key="request_id")
    if st.sidebar.button("Run agent analysis", key="run-analysis"):
        from app.workflows.runner import start

        case = cases[request_id]
        with st.spinner("Running the multi-agent workflow ..."):
            run = asyncio.run(start(get_orchestrator(str(data_dir)), case.request, case.record))
        record_path = next(p for p in (data_dir / "synthetic_records").glob("record_*.txt")
                           if p.read_text(encoding="utf-8").startswith(f"Record ID: {case.record.record_id}"))
        session = ReviewSession.from_workflow(run.state, record_path, actor="orchestrator")
        st.session_state["data_dir"] = str(data_dir)
        save(session)
        st.rerun()
    saved = sorted(SESSION_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if SESSION_DIR.exists() else []
    if saved:
        st.sidebar.header("Saved sessions")
        choice = st.sidebar.selectbox("Session", [p.name for p in saved], key="saved")
        if st.sidebar.button("Load session", key="load-session"):
            st.session_state["session"] = ReviewSession.load(SESSION_DIR / choice)
            st.rerun()
    return reviewer


# --- main panels ---------------------------------------------------------------------------


def highlighted_record(session: ReviewSession) -> str:
    text, out, cursor = session.record_text, [], 0
    for it in sorted(session.items, key=lambda i: i.finding.start):
        f = it.finding
        out.append(html.escape(text[cursor:f.start]))
        out.append(f'<mark title="{f.finding_id} {f.category.value}">{html.escape(text[f.start:f.end])}</mark>'
                   f'<sup>{f.finding_id.rsplit("-", 1)[-1]}</sup>')
        cursor = f.end
    out.append(html.escape(text[cursor:]))
    return '<div style="white-space: pre-wrap; font-family: monospace;">' + "".join(out) + "</div>"


def tot_panel(tot) -> None:
    selected = next((n for n in tot.nodes if n.node_id == tot.selected_node_id), None)
    st.markdown(f"**ToT outcome:** `{tot.outcome}` → `{tot.action}`. {tot.reason}")
    if selected:
        st.markdown(f"**Selected branch:** `{selected.node_id}` ({selected.provision_type}), proposed action `{selected.action}`, "
                    f"score {selected.score.total if selected.score else '—'}/100 — {selected.interpretation}")
    else:
        st.markdown("**Selected branch:** none (no branch met the selection rules)")
    st.caption(SCORE_NOTE)
    rows = [{"branch": n.node_id, "parent": n.parent_id or "—", "depth": n.depth, "stage": n.stage, "action": n.action,
             "provision": n.provision_type, "status": n.status,
             "policy relevance (30)": n.score.policy_relevance if n.score else None, "citation validity (30)": n.score.citation_validity if n.score else None,
             "consistency (25)": n.score.consistency if n.score else None, "explanation (15)": n.score.explanation if n.score else None,
             "total": n.score.total if n.score else None, "cites": ", ".join(n.cited_chunk_ids)} for n in tot.nodes]
    st.markdown("**Branches considered**")
    st.dataframe(rows, hide_index=True, width="stretch")
    st.markdown("**Pruning and rejection**")
    for p in tot.pruned or []:
        st.markdown(f"- `{p.node_id}` (depth {p.depth}): {p.reason}")
    if not tot.pruned:
        st.markdown("- none")
    st.markdown("**Unresolved questions**")
    for q in tot.unresolved or ["none"]:
        st.markdown(f"- {q}")
    st.markdown(f"**Additional retrieval performed:** {'yes' if tot.targeted_retrieval_used else 'no'}; "
                f"tool calls used {tot.tool_calls_used}; depth reached {tot.depth_reached}")
    for e in tot.budget_events:
        st.markdown(f"- {e}")
    for n in tot.nodes:
        for a in n.assumptions:
            if a.evidence:
                st.markdown(f"- `{n.node_id}` condition *{a.text}* → **{a.status}** ({a.evidence})")


def finding_card(session: ReviewSession, state, reviewer: str) -> None:
    item = session.item(state.finding_id)
    f, rec = item.finding, state.recommendation
    with st.container(border=True):
        st.subheader(f"{f.finding_id} · {f.category.value}")
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"**Detected text:** `{f.text}`  \n**Position:** [{f.start}:{f.end}]  \n**Detection confidence:** {f.confidence}")
        c2.markdown(f"**Proposed action:** `{rec.action.value}`  \n**Policy evidence:** {state.evidence_status}  \n"
                    f"**Reasoning method:** {METHOD_LABEL[state.reasoning_method]}")
        c3.markdown(f"**Status:** {STATUS_LABEL[state.status]}  \n**Re-evaluations:** {state.reevaluation_attempts}/2")
        st.markdown(f"**Decision summary:** {rec.rationale}")
        st.markdown("**Supporting policy passages**")
        for ref in rec.policy_references:
            st.markdown(f"- {ref.citation}, p. {ref.page} — [{ref.source_url}]({ref.source_url}) (`{ref.chunk_id}`)")
        if not rec.policy_references:
            st.markdown("- none: no policy basis was established")
        for q in rec.open_questions:
            st.markdown(f"- ❓ {q}")

        with st.expander("Reasoning and Evidence"):
            st.markdown("**Evidence assessment:** " + ("; ".join(item.assessment_reasons) or "—"))
            st.markdown("**Retrieved passages (verbatim excerpts)**")
            for c in state.evidence:
                st.markdown(f"- `{c.chunk_id}` {c.title}, {c.section}, p. {c.page_start}")
                st.code(c.text[:600] + (" ..." if len(c.text) > 600 else ""), language=None)
            if state.tot_result:
                tot_panel(state.tot_result)
            for e in state.history:
                if e.action == "REEVALUATION_COMPLETED":
                    cmp = e.payload["comparison"]
                    st.markdown(f"**Re-evaluation {e.payload['attempt']}** ({e.timestamp}): {cmp['action_before']} → {cmp['action_after']}; "
                                f"citations added {cmp['citations_added']}, removed {cmp['citations_removed']}; "
                                f"ToT score {cmp['tot_score_before']} → {cmp['tot_score_after']}; "
                                f"additional retrieval: {'yes' if e.payload['retrieval_performed'] else 'no'}")
            st.markdown("**Review history**")
            for e in state.history:
                st.markdown(f"- #{e.seq} {e.timestamp} · {e.actor} · {e.action}")

        if session.finalized:
            return
        if state.status is ItemStatus.MODIFICATION_UNCONFIRMED and state.modification:
            st.warning("Confirm the exact replacement spans before they can be applied:")
            for s in state.modification:
                st.markdown(f"- [{s.start}:{s.end}] `{s.expected_text}` → `{s.replacement}`")
            if st.button("Confirm spans", key=f"confirm-{f.finding_id}"):
                run_action(session.confirm_modification, f.finding_id, reviewer)
        a1, a2, a3, a4 = st.tabs(["Approve", "Reject", "Modify", "Request policy re-evaluation"])
        with a1:
            can_approve = rec.action is RedactionAction.REDACT
            st.caption("Approves redacting exactly the detected span." if can_approve
                       else "Nothing to approve: there is no supported redaction. Reject, modify with explicit spans, or request re-evaluation.")
            if st.button("Approve recommendation", key=f"approve-{f.finding_id}", disabled=not can_approve):
                run_action(session.approve, f.finding_id, reviewer)
        with a2:
            reason = st.text_input("Reason (optional)", key=f"reject-reason-{f.finding_id}")
            if st.button("Reject recommendation", key=f"reject-{f.finding_id}"):
                run_action(session.reject, f.finding_id, reviewer, reason or None)
        with a3:
            start = st.number_input("Start offset", min_value=0, max_value=len(session.record_text), value=f.start, key=f"mod-start-{f.finding_id}")
            end = st.number_input("End offset", min_value=1, max_value=len(session.record_text), value=f.end, key=f"mod-end-{f.finding_id}")
            replacement = st.text_input("Replacement", value=f"[REDACTED: {f.category.value}]", key=f"mod-repl-{f.finding_id}")
            st.caption(f"Original text at these offsets: `{session.record_text[int(start):int(end)]}`")
            mreason = st.text_input("Reason (required)", key=f"mod-reason-{f.finding_id}")
            if st.button("Propose modification", key=f"modify-{f.finding_id}"):
                span = RedactionSpan(start=int(start), end=int(end), expected_text=session.record_text[int(start):int(end)] or " ",
                                     replacement=replacement or "[REDACTED]")
                run_action(session.propose_modification, f.finding_id, reviewer, [span], mreason)
        with a4:
            st.caption("Sends this finding back to the Orchestrator. Limited to 2 attempts; reviewer statements count as facts, never as legal authority.")
            rreason = st.text_input("What should be reconsidered? (required)", key=f"re-reason-{f.finding_id}")
            query = st.text_input("Additional policy search (optional)", key=f"re-query-{f.finding_id}")
            keys = sorted({a.key for n in (state.tot_result.nodes if state.tot_result else []) for a in n.assumptions if a.kind == "FACTUAL" and a.key})
            facts = {}
            for k in keys:
                answer = st.selectbox(f"Factual condition '{k}'", ["no answer", "confirmed", "denied", "unconfirmed"], key=f"re-fact-{f.finding_id}-{k}")
                if answer != "no answer":
                    facts[k] = answer
            if st.button("Request re-evaluation", key=f"reeval-{f.finding_id}"):
                try:
                    request = ReevaluationRequest(finding_id=f.finding_id, reviewer=reviewer, reason=rreason, factual_confirmations=facts,
                                                  additional_query=query or None)
                except ValueError as exc:
                    st.session_state["flash"] = ("error", f"Re-evaluation request is incomplete: {exc}")
                    st.rerun()
                from app.review.reevaluation import reevaluate

                orch = get_orchestrator(st.session_state.get("data_dir", str(Path(session.record_path).parent.parent)))
                with st.spinner("Re-evaluating ..."):
                    try:
                        asyncio.run(reevaluate(orch, session, request))
                    except ReviewError as exc:
                        st.session_state["flash"] = ("error", str(exc))
                        st.rerun()
                save(session)
                st.rerun()


def finalization_panel(session: ReviewSession, reviewer: str) -> None:
    st.header("Final authorization")
    plan = session.redaction_plan()
    st.markdown("**Redaction plan** (applied to the original text by exact offsets)")
    st.dataframe([{"start": s.start, "end": s.end, "original": s.expected_text, "replacement": s.replacement,
                   "findings": ", ".join(s.finding_ids), "source": s.source} for s in plan] or [{"plan": "empty"}], hide_index=True)
    if session.finalized:
        e = next(e for e in session.events if e.action == "FINALIZED")
        st.success(f"Finalized by {e.payload['authorized_by']} at {e.timestamp}. Derivative `{e.payload['derivative_path']}` "
                   f"(sha256 {e.payload['derivative_sha256'][:16]}…). Not published or transmitted.")
        path = PROJECT_ROOT / e.payload["derivative_path"]
        if path.exists():
            st.code(path.read_text(encoding="utf-8"), language=None)
        return
    authorize = st.checkbox("I authorize this final redaction plan for the synthetic derivative", key="authorize")
    blockers = session.finalization_blockers(reviewer, authorize)
    for b in blockers:
        st.markdown(f"- ⛔ {b}")
    if st.button("Finalize derivative", key="finalize"):
        result = session.finalize(reviewer, authorize)
        save(session)
        st.session_state["flash"] = ("success", "Finalized.") if result.finalized else ("error", "Finalization blocked: " + "; ".join(result.reasons))
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="Public Records Review", layout="wide")
    st.title("Public Records Compliance Review")
    st.caption("Synthetic records only. The assistant recommends; a human reviewer decides. Nothing is published or transmitted.")
    reviewer = sidebar()
    if "flash" in st.session_state:
        kind, msg = st.session_state.pop("flash")
        (st.error if kind == "error" else st.success)(msg)
    session: ReviewSession | None = st.session_state.get("session")
    if session is None:
        st.info("Choose a synthetic request in the sidebar and run the agent analysis, or load a saved session.")
        return

    st.markdown(f"**Request** `{session.request.request_id}` · **Record** `{session.record_id}` · "
                f"**Source sha256** `{session.source_text_sha256[:16]}…` · **Workflow route** `{session.workflow.get('route')}`")
    left, right = st.columns(2)
    with left:
        st.markdown("**Synthetic request**")
        st.write(session.request.description)
    with right:
        st.markdown("**Original synthetic record** (unchanged; findings highlighted)")
        st.markdown(highlighted_record(session), unsafe_allow_html=True)

    states = session.states()
    st.header("Findings")
    st.dataframe([{"finding": s.finding_id, "category": session.item(s.finding_id).finding.category.value,
                   "text": session.item(s.finding_id).finding.text,
                   "position": f"[{session.item(s.finding_id).finding.start}:{session.item(s.finding_id).finding.end}]",
                   "confidence": session.item(s.finding_id).finding.confidence, "evidence": s.evidence_status,
                   "method": METHOD_LABEL[s.reasoning_method], "proposed": s.recommendation.action.value, "status": STATUS_LABEL[s.status]}
                  for s in states] or [{"finding": "none detected"}], hide_index=True)
    for s in states:
        finding_card(session, s, reviewer)

    if session.session_escalations:
        st.header("Escalations")
        acknowledged = session.acknowledged()
        for e in session.session_escalations:
            if e.escalation_id in acknowledged:
                st.markdown(f"- ✅ {e.escalation_id}: {e.text}")
            else:
                st.markdown(f"- 🔴 {e.escalation_id}: {e.text}")
                note = st.text_input(f"Resolution note for {e.escalation_id}", key=f"ack-note-{e.escalation_id}")
                if st.button(f"Acknowledge {e.escalation_id}", key=f"ack-{e.escalation_id}"):
                    run_action(session.acknowledge_escalation, e.escalation_id, reviewer, note)

    finalization_panel(session, reviewer)

    st.header("Audit")
    with st.expander("Review event log (append-only, hash-chained)"):
        st.markdown(f"Integrity check: **{'passed' if session.verify_chain() else 'FAILED'}**")
        st.dataframe([{"seq": e.seq, "time": e.timestamp, "actor": e.actor, "action": e.action, "finding": e.finding_id or "—"}
                      for e in session.events], hide_index=True)
    with st.expander("Agent tool calls (workflow)"):
        st.dataframe(session.workflow.get("tool_calls", []), hide_index=True)
    st.download_button("Download audit report (JSON)", json.dumps(build_audit_report(session), indent=2, ensure_ascii=False),
                       file_name=f"{session.session_id}_audit.json", mime="application/json", key="download-report")


main()
