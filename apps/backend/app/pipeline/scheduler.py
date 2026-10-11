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
        if any(j.runtime_ref.get("cleanup") and j.status not in ACTIVE for j in jobs):
            return None
        active = [j for j in jobs if j.status in ACTIVE]
        revalidation, amendment = None, None
        spec_request, spec_replan, resume = None, None, None
        contract_revision, spec_revision = 1, 0
        if t.blocker:
            from .dependency_revalidation import pending_contract
            revalidation = pending_contract(s, t, p)
            if revalidation is None:
                return None
            stage, role, task = 'dependency_revalidation', 'qa', 'revalidate_dependency'
        elif t.phase in ("ready", "development"):
            if not self.workflow._eligible(s, t):
                return None
            plans = [j for j in jobs if j.status == 'succeeded' and
                     j.runtime_ref.get('payload', {}).get('task') == 'technical_plan']
            contract_revision = max((j.result.get('ui_contract_revision', 1) for j in plans), default=1)
            requests = [j for j in jobs if j.status == 'succeeded' and
                        j.result.get('contract_amendment', {}).get('revision') == contract_revision]
            qa_done = any(j.status == 'succeeded' and j.result.get('suite_artifact_id') and
                          j.result.get('ui_contract_revision', 1) == contract_revision and
                          j.runtime_ref.get('payload', {}).get('task') == 'qa_plan' for j in jobs)
            # UI/UX runs first for projects that enabled it (docs/decisions/ui-ux.md).
            # Scopes that already started planning without it keep the earlier order.
            specs = [j for j in jobs if j.status == 'succeeded' and
                     j.runtime_ref.get('payload', {}).get('task') == 'ui_spec']
            spec_revision = max((j.result.get('ui_spec_revision', 0) for j in specs), default=0)
            ui_ux = ((p.workflow.get('pipeline') or {}).get('ui_ux') is True and
                     not (any(j.stage == 'technical_plan' for j in jobs) and
                          not any(j.stage == 'ui_spec' for j in jobs)))
            spec_requests = [j for j in jobs if j.status == 'succeeded' and spec_revision and
                             (j.result or {}).get('spec_amendment', {}).get('revision') == spec_revision]
            planned_spec = max((j.result.get('ui_spec_revision', 0) for j in plans), default=0)
            if ui_ux and not specs:
                stage, role, task = 'ui_spec', 'ui-ux', 'ui_spec'
            elif ui_ux and spec_requests:
                spec_request = spec_requests[-1]
                stage, role, task = 'ui_spec', 'ui-ux', 'ui_spec'
            elif ui_ux and plans and spec_revision > planned_spec:
                # The spec was amended: TL revises the contract additively from it.
                spec_replan = next(j for j in reversed(specs) if j.result.get('ui_spec_revision') == spec_revision)
                stage, role, task = 'technical_plan', 'technical-lead', 'technical_plan'
            elif requests:
                amendment = requests[-1]
                stage, role, task = 'technical_plan', 'technical-lead', 'technical_plan'
            elif not plans:
                stage, role, task = 'technical_plan', 'technical-lead', 'technical_plan'
            elif not qa_done and not any(j.stage == 'qa_plan' and
                    j.runtime_ref.get('payload', {}).get('ui_contract_revision', 1) == contract_revision for j in jobs):
                stage, role, task = 'qa_plan', 'qa', 'qa_plan'
            else:
                current_qa = [j for j in jobs if j.stage == 'qa_plan' and
                    j.runtime_ref.get('payload', {}).get('ui_contract_revision', 1) == contract_revision]
                if not qa_done and current_qa and current_qa[-1].status in ('failed', 'stopped', 'cancelled'):
                    return None
                stage, role, task = 'development', 'developer', 'implement'
                last_development = next((j for j in reversed(jobs) if j.status == 'succeeded' and
                                         j.stage == 'development'), None)
                if last_development is not None:
                    request = (last_development.result or {}).get('spec_amendment', {})
                    if (request.get('checkpoint_artifact_id') and request.get('revision', 0) < spec_revision and
                            last_development.runtime_ref.get('payload', {}).get('repair_cycle', 0) ==
                            t.workflow.get('repair_cycles', 0)):
                        # Further QA-only amendments retain this draft. Once development
                        # submits a candidate, later repairs start from that candidate.
                        resume = last_development
        else:
            stage = t.phase
            role, task = ("technical-lead", "review") if stage == "technical_review" else ("qa", "verify")
        cycle = t.workflow.get("repair_cycles", 0) if stage == "development" else 0
        candidate = t.workflow.get("candidate_id") if stage in ("technical_review", "qa") else None
        key = f"pipeline:{t.id}:v{t.current_version}:{stage}:{cycle}:{candidate or '-'}"
        suite_repaired = False
        payload = {"task": task, "candidate_id": candidate}
        if stage == 'development':
            payload['repair_cycle'] = cycle
        if stage == 'qa_plan':
            payload['ui_contract_revision'] = contract_revision
        if spec_request is not None:
            payload['spec_amendment'] = spec_request.result['spec_amendment']
            payload['amendment_request_job_id'] = spec_request.id
            key += ':spec-amendment-' + spec_request.id
        if spec_replan is not None:
            payload['contract_amendment'] = {'revision': contract_revision, 'ui_spec_revision': spec_revision,
                'reason': 'UI spec amended to revision ' + str(spec_revision) + '; add its new controls.'}
            payload['amendment_request_job_id'] = spec_replan.id
            key += ':spec-r' + str(spec_revision)
        if stage == 'development' and spec_revision > 1:
            key += ':spec-r' + str(spec_revision)
            if resume is not None:
                payload['resume_checkpoint'] = resume.result['spec_amendment']['checkpoint_artifact_id']
        # Source-only planning and one fenced developer may overlap. Every
        # other stage stays serialized, including review, verify and cleanup.
        companions = ('development',) if stage in ('technical_plan', 'qa_plan') else ('qa_plan',) if stage == 'development' else ()
        if stage == 'ui_spec':
            companions = ()  # the spec precedes (or, when amended, pauses) every other stage
        if any(j.stage not in companions for j in active):
            return None
        legacy_key = key
        if amendment is not None:
            payload['contract_amendment'] = amendment.result['contract_amendment']
            payload['amendment_request_job_id'] = amendment.id
            key += ':amendment-' + amendment.id
            legacy_key = key
        elif stage == 'qa_plan' and contract_revision > 1:
            key += ':ui-r' + str(contract_revision)
            legacy_key = key
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
                    lane="interactive" if stage in ('ui_spec', 'technical_plan', 'technical_review', 'qa_plan') or
                        payload.get('task') == 'diagnose' else "execution",
                    role=role, stage=stage, runtime=self.runtime, idempotency_key=key, limits=caps,
                    budget_pool=BUDGET_POOL, payload=payload, actor="scheduler:pipeline")
