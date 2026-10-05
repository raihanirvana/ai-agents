"""Explicit public projections: never expose credential hashes, runtime refs or storage paths."""
from sqlalchemy import func, select

from app.persistence import NotFound, latest_cursor
from app.persistence.models import (Project, Ticket, TicketVersion, Candidate, Verification, Artifact,
                                    Approval, Dependency, Job, Message)
from app.preview import requests as previews
from app.release import requests as releases


def not_runtime_log():
    """SQL filter: conversation messages only (supervisor log lines carry metadata.runtime_log)."""
    return func.json_extract(Message.meta, "$.runtime_log").is_(None)


def row(s, model, identity, project_id=None):
    value = s.get(model, identity)
    if value is None or project_id is not None and value.project_id != project_id:
        raise NotFound(model.__tablename__, identity)
    return value


def project(p):
    return {"id": p.id, "name": p.name, "mode": p.mode, "brief": p.brief, "brief_version": p.brief_version,
            "revision": p.revision, "onboarding": p.workflow.get("onboarding", "pending"),
            "accepted_tip": p.workflow.get("accepted_tip"),
            "onboarding_detail": p.workflow.get("onboarding_detail")}


def ticket(t):
    return {"id": t.id, "project_id": t.project_id, "number": t.number, "title": t.title, "phase": t.phase,
            "revision": t.revision, "scope_version": t.current_version, "priority": t.priority,
            "blocker": t.blocker, "repair_cycles": t.workflow.get("repair_cycles", 0),
            "repair_limit": t.workflow.get("repair_limit", 3)}


def run(j, s, peers=None, usage_by_key=None):
    """peers/usage_by_key let a caller that renders many runs (the board) load them once."""
    from app.workers import JobQueue
    key = j.limits.get("budget_key")
    if peers is None:
        peers = list(s.scalars(select(Job).where(Job.project_id == j.project_id)))
    if not key:
        usage = j.usage
    elif usage_by_key is not None:
        if key not in usage_by_key:  # (setdefault would run the query for every run: its argument is eager)
            usage_by_key[key] = JobQueue.budget_usage(s, j)
        usage = usage_by_key[key]
    else:
        usage = JobQueue.budget_usage(s, j)
    unknown = sorted({name for peer in peers if peer.limits.get("budget_key") == key
                      for name in peer.usage.get("_unknown", [])})
    if unknown: usage = {**usage, "_unknown": unknown}
    return {"id": j.id, "project_id": j.project_id, "ticket_id": j.ticket_id, "scope_version": j.scope_version,
            "revision": j.revision, "generation": j.lease_generation, "role": j.runtime_ref.get("role"),
            "runtime": j.runtime_ref.get("runtime"), "fake": bool(j.runtime_ref.get("fake")),
            "lane": j.lane, "stage": j.stage, "status": j.status, "attempt": j.attempt,
            "usage": usage, "attempt_usage": j.usage, "limits": j.limits, "result": j.result, "request_id": j.waiting_request_id,
            "context_artifact_id": j.context_artifact_id, "available_at": j.available_at}


def message(s, m, threads):
    result = {"id": m.id, "project_id": m.project_id, "ticket_id": m.ticket_id, "thread_id": m.thread_id,
              "seq": m.seq, "sender": m.sender, "recipient": m.recipient, "kind": m.kind, "body": m.body,
              "reply_to": m.reply_to, "metadata": m.meta, "attachment_ids": m.attachment_ids,
              "created_at": m.created_at}
    if m.kind == "input_request":
        from dataclasses import asdict
        result["input"] = asdict(threads.input_request(s, m.id))
        escalation = s.scalar(select(Message).where(Message.reply_to == m.id,
                               Message.kind == "input_request", Message.recipient == "user"))
        if escalation and escalation.meta.get("intent") == "user_escalation":
            result["input"]["status"] = "escalated"
            result["input"]["escalated_request_id"] = escalation.id
    return result


def artifact(a):
    return {"id": a.id, "project_id": a.project_id, "kind": a.kind, "storage": a.storage,
            "checksum": a.checksum, "size_bytes": a.size_bytes, "availability": a.availability,
            "unavailable_reason": a.unavailable_reason, "metadata": a.meta}


def candidate(s, c):
    return {"id": c.id, "ticket_id": c.ticket_id, "scope_version": c.scope_version, "commit_sha": c.commit_sha,
            "base_sha": c.base_sha, "status": c.status, "target_artifact_id": c.target_artifact_id,
            "target_digest": c.target_digest, "evidence_ids": c.evidence_artifact_ids, "preview": c.preview,
            "commit_artifact_id": c.commit_artifact_id, "build_artifact_id": c.build_artifact_id,
            "live_preview": (lambda p: previews.public(p) if p else None)(previews.latest_for(s, c.id)),
            "integrated_sha": c.integrated_sha, "integration": integration(c.integration),
            "verifications": [{"id": v.id, "status": v.status, "target_digest": v.target_digest,
                "evidence_ids": v.evidence_artifact_ids, "counts": v.counts, "results": v.results,
                "uac_coverage": v.uac_coverage} for v in s.scalars(
                select(Verification).where(Verification.candidate_id == c.id))]}


def integration(op):
    """Public view of the integration operation (no internal approval/artifact plumbing beyond IDs)."""
    if not op or not op.get("operation_id"):
        return None
    return {k: op.get(k) for k in ("operation_id", "status", "expected_base", "target_sha", "observed_tip", "reason",
                                   "evidence_artifact_id")}


def board(s, project_id):
    p = row(s, Project, project_id)
    return {"project": project(p), "tickets": [ticket(t) for t in s.scalars(select(Ticket).where(
            Ticket.project_id == project_id).order_by(Ticket.priority.desc(), Ticket.number))],
            "runs": _runs(s, project_id),
            "preview": (lambda rows: previews.public(rows[0]) if rows else None)(previews.active(s, project_id)),
            "releases": [releases.public(r, s) for r in releases.releases(s, project_id)[:5]],
            "cursor": latest_cursor(s, project_id=project_id)}


def _runs(s, project_id):
    jobs = list(s.scalars(select(Job).where(Job.project_id == project_id).order_by(Job.created_at)))
    usage_by_key = {}
    return [run(j, s, jobs, usage_by_key) for j in jobs]


def detail(s, ticket_id, threads):
    t = row(s, Ticket, ticket_id)
    return {"ticket": ticket(t), "versions": [{"version": v.version, "title": v.title, "description": v.description,
            "uac": v.uac, "scope": v.scope} for v in s.scalars(select(TicketVersion).where(TicketVersion.ticket_id == t.id)
                                                                    .order_by(TicketVersion.version))],
            "dependencies": [{"upstream_id": d.depends_on_ticket_id, "state": d.state,
                "scope_version": d.accepted_scope_version, "candidate_id": d.accepted_candidate_id,
                "integration_sha": d.integration_sha, "revalidation": d.revalidation} for d in s.scalars(
                select(Dependency).where(Dependency.ticket_id == t.id))],
            "approvals": [{"id": a.id, "type": a.type, "scope_version": a.scope_version,
                "candidate_id": a.candidate_id, "target_digest": a.target_digest,
                "evidence_ids": a.evidence_artifact_ids, "details": a.details} for a in s.scalars(
                select(Approval).where(Approval.ticket_id == t.id))],
            "candidates": [candidate(s, c) for c in s.scalars(select(Candidate).where(Candidate.ticket_id == t.id))],
            "messages": [message(s, m, threads) for m in s.scalars(select(Message).where(Message.ticket_id == t.id, not_runtime_log())
                .order_by(Message.created_at, Message.thread_id, Message.seq))],
            "cursor": latest_cursor(s, project_id=t.project_id)}
