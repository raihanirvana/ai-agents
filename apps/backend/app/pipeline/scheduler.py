"""Durable stage dispatch; multiple maintenance callers share the same DB transaction/key."""
from sqlalchemy import select
from app.persistence.models import Project, Ticket, Job, Candidate, Artifact

ACTIVE = ("queued", "running", "waiting_input", "waiting_quota")
DEFAULT_LIMITS = {"model_calls": 48, "tool_calls": 120, "active_s": 1800, "output_tokens": 4096,
                  "total_tokens": 200000}
BUDGET_POOL = "pipeline"  # separate from interactive PO/lead chat on the same scope


class PipelineScheduler:
    def __init__(self, db, queue, workflow, *, runtime="pipeline", limits=None):
        self.db, self.queue, self.workflow = db, queue, workflow
        self.runtime, self.limits = runtime, dict(limits or DEFAULT_LIMITS)

    def tick(self):
        # Idle polling holds only a WAL read snapshot. Close it before trying
        # to acquire the writer; never upgrade a stale SQLite read transaction.
        pending = []
        with self.db.read() as s:
            tickets = s.scalars(select(Ticket).where(Ticket.phase.in_(
                ("ready", "development", "technical_review", "qa"))).order_by(Ticket.priority.desc(), Ticket.number))
            for ticket in tickets:
                proposal = self._proposal(s, ticket)
                if proposal is not None:
                    pending.append((ticket.id, ticket.revision, proposal['idempotency_key']))
        dispatched = []
        for ticket_id, revision, key in pending:
            # Another scheduler/user/worker can act between snapshots. Re-read
            # approval/dependencies, candidate/target, jobs, keys and budget caps
            # while holding the writer, then enqueue in that same transaction.
            with self.db.write() as s:
                ticket = s.get(Ticket, ticket_id)
                if ticket is None or ticket.revision != revision:
                    continue
                proposal = self._proposal(s, ticket)
                if proposal is None or proposal['idempotency_key'] != key:
                    continue
                job = self.queue.enqueue(session=s, **proposal)
                dispatched.append(job.id)
        return dispatched

    def _proposal(self, s, t):
        """Read-only dispatch decision, re-used under the writer for authorization."""
        if t.phase not in ("ready", "development", "technical_review", "qa"):
            return None
        p = s.get(Project, t.project_id)
        # Only projects explicitly configured for this runner are touched.
        if p is None or not p.workflow.get("pipeline") or not p.workflow.get("accepted_tip"):
            return None
        jobs = list(s.scalars(select(Job).where(Job.ticket_id == t.id, Job.scope_version == t.current_version)
            .order_by(Job.created_at, Job.id)))
        if any(j.status in ACTIVE or j.runtime_ref.get("cleanup") for j in jobs):
            return None
        revalidation = None
        if t.blocker:
            from .dependency_revalidation import pending_contract
            revalidation = pending_contract(s, t, p)
            if revalidation is None:
                return None
            stage, role, task = 'dependency_revalidation', 'qa', 'revalidate_dependency'
        elif t.phase in ("ready", "development"):
            if not self.workflow._eligible(s, t):
                return None
            done = {j.runtime_ref.get("payload", {}).get("task") for j in jobs if j.status == "succeeded"}
            if "technical_plan" not in done:
                stage, role, task = "technical_plan", "technical-lead", "technical_plan"
            elif "qa_plan" not in done:
                stage, role, task = "qa_plan", "qa", "qa_plan"
            else:
                stage, role, task = "development", "developer", "implement"
        else:
            stage = t.phase
            role, task = ("technical-lead", "review") if stage == "technical_review" else ("qa", "verify")
        cycle = t.workflow.get("repair_cycles", 0) if stage == "development" else 0
        candidate = t.workflow.get("candidate_id") if stage in ("technical_review", "qa") else None
        key = f"pipeline:{t.id}:v{t.current_version}:{stage}:{cycle}:{candidate or '-'}"
        legacy_key = key
        suite_repaired = False
        payload = {"task": task, "candidate_id": candidate}
        if revalidation:
            from .contracts import digest_of
            payload = revalidation
            key += ':' + digest_of(revalidation)
            legacy_key = key
        if stage in ('technical_review', 'qa') and candidate:
            current = s.get(Candidate, candidate)
            if current and current.target_digest:
                key += '@target-' + current.target_digest
                target_row = s.get(Artifact, current.target_artifact_id)
                suite_repaired = bool(target_row and target_row.meta.get('qa_repair_job_id'))
                if stage == 'qa':
                    pending = next((j for j in reversed(jobs) if j.status == 'succeeded'
                        and (j.result or {}).get('diagnosis_required') is True
                        and j.result.get('candidate_id') == candidate
                        and j.result.get('target_digest') == current.target_digest
                        and j.result.get('verification_id')), None)
                    if pending is not None:
                        key += ':diagnose:' + pending.result['verification_id']
                        # Diagnosis is a separate durable QA attempt. It
                        # reuses failed evidence, never reruns the browser
                        # merely because a model/provider call retried.
                        legacy_key = key
                        payload = {'task': 'diagnose', 'candidate_id': candidate,
                                   'verification_id': pending.result['verification_id']}
        if stage == "development":  # work on an older accepted base must be redone on the new one
            key += "@" + p.workflow["accepted_tip"][:12]
        existing = [j for j in jobs if j.idempotency_key == key]
        if stage in ('technical_review', 'qa') and not suite_repaired:
            # Upgrading keys does not grant another attempt to a legacy
            # permanent failure. Explicit suite repair is a new target.
            existing += [j for j in jobs if j.idempotency_key == legacy_key]
        # A permanent failure/budget stop is visible; never spin a new automatic job around it.
        if existing:
            return None
        # Use the scope's already authorized pipeline caps (possibly extended through the user command).
        # A PO/lead chat on this scope has its own pool and never sets the execution budget.
        pool = [j for j in jobs if j.runtime_ref.get("budget_pool") == BUDGET_POOL]
        from .budgets import project_limits
        caps = ({k: v for k, v in pool[-1].limits.items() if k != "budget_key"} if pool else
                {**project_limits(p, self.limits), **p.workflow['pipeline'].get('budget_limits', {})})
        return dict(project_id=t.project_id, ticket_id=t.id, expected_scope=t.current_version,
                    lane="interactive" if stage in ('technical_plan', 'technical_review') or
                        payload.get('task') == 'diagnose' else "execution",
                    role=role, stage=stage, runtime=self.runtime, idempotency_key=key, limits=caps,
                    budget_pool=BUDGET_POOL, payload=payload, actor="scheduler:pipeline")
