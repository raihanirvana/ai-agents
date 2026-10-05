"""Durable stage dispatch; multiple maintenance callers share the same DB transaction/key."""
from sqlalchemy import select
from app.persistence.models import Project, Ticket, Job

ACTIVE = ("queued", "running", "waiting_input", "waiting_quota")
DEFAULT_LIMITS = {"model_calls": 48, "tool_calls": 120, "active_s": 1800, "output_tokens": 4096,
                  "total_tokens": 200000}
BUDGET_POOL = "pipeline"  # separate from interactive PO/lead chat on the same scope


class PipelineScheduler:
    def __init__(self, db, queue, workflow, *, runtime="pipeline", limits=None):
        self.db, self.queue, self.workflow = db, queue, workflow
        self.runtime, self.limits = runtime, dict(limits or DEFAULT_LIMITS)

    def tick(self):
        dispatched = []
        with self.db.write() as s:
            tickets = list(s.scalars(select(Ticket).where(Ticket.phase.in_(
                ("ready", "development", "technical_review", "qa"))).order_by(Ticket.priority.desc(), Ticket.number)))
            for t in tickets:
                p = s.get(Project, t.project_id)
                # Only projects explicitly configured for this runner are touched.
                if not p.workflow.get("pipeline") or not p.workflow.get("accepted_tip") or t.blocker:
                    continue
                jobs = list(s.scalars(select(Job).where(Job.ticket_id == t.id, Job.scope_version == t.current_version)
                    .order_by(Job.created_at, Job.id)))
                if any(j.status in ACTIVE or j.runtime_ref.get("cleanup") for j in jobs):
                    continue
                if t.phase in ("ready", "development"):
                    if not self.workflow._eligible(s, t):
                        continue
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
                existing = [j for j in jobs if j.idempotency_key == key]
                # A permanent failure/budget stop is visible; never spin a new automatic job around it.
                if existing:
                    continue
                # Use the scope's already authorized pipeline caps (possibly extended through the user command).
                # A PO/lead chat on this scope has its own pool and never sets the execution budget.
                pool = [j for j in jobs if j.runtime_ref.get("budget_pool") == BUDGET_POOL]
                caps = {k: v for k, v in pool[-1].limits.items() if k != "budget_key"} if pool else self.limits
                job = self.queue.enqueue(session=s, project_id=t.project_id, ticket_id=t.id, expected_scope=t.current_version,
                    lane="execution", role=role, stage=stage, runtime=self.runtime, idempotency_key=key, limits=caps,
                    budget_pool=BUDGET_POOL,
                    payload={"task": task, "candidate_id": candidate}, actor="scheduler:pipeline")
                dispatched.append(job.id)
        return dispatched
