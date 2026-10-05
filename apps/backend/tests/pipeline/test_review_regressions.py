"""DEV-010 review regressions and guard cases (Claude/Codex)."""
from sqlalchemy import select
from app.agents import Threads
from app.http.application import DEFAULT_LIMITS as CHAT_LIMITS
from app.persistence.models import Job
from app.pipeline.scheduler import DEFAULT_LIMITS
from tests.domain.conftest import world, db, db_path, store  # noqa: F401
from tests.pipeline.test_scheduler import configure


def chat_revise(queue, t, key):
    """Exactly what POST /projects/{id}/messages enqueues for task=revise."""
    return queue.enqueue(project_id=t.project_id, role="po", stage="chat", lane="interactive", runtime="structured",
                         limits=CHAT_LIMITS, ticket_id=t.id, idempotency_key=key,
                         payload={"task": "revise", "request": "Tighten the criteria"})


def test_a_po_chat_on_the_approved_scope_does_not_set_the_pipeline_budget(world):
    """Before: the scheduler copied jobs[-1].limits, i.e. the PO chat caps (8 calls, 120 s)."""
    t = world.new()
    scheduler, queue = configure(world)
    chat_revise(queue, t, "api-chat:rejected-proposal")
    lease = queue.claim("worker", "interactive", capacity=2, runtimes=("structured",))
    queue.reserve(lease, "model")
    queue.complete(lease, {})  # the PO answered; the user rejected the proposal and approved this version
    world.approve(t)
    job_id = scheduler.tick()[0]
    with world.db.read() as s:
        job = s.get(Job, job_id)
        assert job.stage == "technical_plan"
        assert {k: v for k, v in job.limits.items() if k != "budget_key"} == DEFAULT_LIMITS


def test_a_po_chat_during_development_is_not_refused_by_the_pipeline_budget_policy(world):
    """Before: enqueue raised 'scope budget caps must match' because both used ticket:<id>:v<n>."""
    t = world.approve(world.new())
    scheduler, queue = configure(world)
    scheduler.tick()
    chat = chat_revise(queue, t, "api-chat:during-development")
    with world.db.read() as s:
        pipeline = s.scalar(select(Job).where(Job.ticket_id == t.id, Job.stage == "technical_plan"))
        assert chat.limits["budget_key"] != pipeline.limits["budget_key"]
        assert pipeline.limits["budget_key"].startswith(f"ticket:{t.id}:v{t.current_version}")


def test_a_lead_reply_to_a_pipeline_question_spends_the_pipeline_budget(world):
    t = world.approve(world.new())
    scheduler, queue = configure(world)
    job_id = scheduler.tick()[0]
    lease = queue.claim("worker", "execution", capacity=1, runtimes=("pipeline",))
    identity = queue.verify(lease)
    reply_id = Threads(world.db, queue)._enqueue_reply("message-1", {**identity, "fake": False}, "technical-lead", "long")
    with world.db.read() as s:
        reply, origin = s.get(Job, reply_id), s.get(Job, job_id)
        assert reply.limits == origin.limits


def test_pipeline_budget_extension_preserves_spending_and_does_not_extend_chat(world):
    import pytest
    from app.workers.queue import BudgetExhausted
    t = world.approve(world.new())
    scheduler, queue = configure(world)
    scheduler.limits = {**DEFAULT_LIMITS, "model_calls": 1}
    job_id = scheduler.tick()[0]
    chat = chat_revise(queue, t, "api-chat:independent-extension")
    lease = queue.claim("worker", "execution", capacity=1, runtimes=("pipeline",))
    queue.reserve(lease, "model")
    with pytest.raises(BudgetExhausted):
        queue.reserve(lease, "model")
    retry_id = queue.extend_budget(job_id, user="user:review", additions={"model_calls": 1},
                                  authorization_id="review-extension")
    with world.db.read() as s:
        retry = s.get(Job, retry_id)
        assert retry.limits["model_calls"] == 2
        assert retry.runtime_ref["budget_pool"] == "pipeline"
        assert queue.budget_usage(s, retry)["model_calls"] == 1
        assert s.get(Job, chat.id).limits["model_calls"] == CHAT_LIMITS["model_calls"]
    resumed = queue.claim("worker", "execution", capacity=1, runtimes=("pipeline",))
    assert resumed.job_id == retry_id
    queue.reserve(resumed, "model")
    with pytest.raises(BudgetExhausted):
        queue.reserve(resumed, "model")


def test_a_rejection_completes_its_job_in_the_publication_transaction(world, tmp_path):
    """Before: request_changes committed while the job stayed running. A worker crash in between made
    recovery retry the review against a ticket already back in development: a spurious needs_human run."""
    import pytest
    pytest.importorskip("fcntl")
    from types import SimpleNamespace
    from app.agents import Redactor
    from app.pipeline.runtime import PipelineRuntime
    from app.pipeline.workspace import ProductWorkspace
    from app.workers import JobQueue
    from app.workers.queue import Lease, StaleLease
    t, candidate = world.submitted()
    actor, ref = world.job(t, "technical-lead")
    with world.db.write() as s:
        job = s.get(Job, ref.job_id)
        job.runtime_ref = {"role": "technical-lead", "runtime": "pipeline:fake", "fake": True, "payload": {"task": "review"}}
    queue = JobQueue(world.db, startable=world.w.startable)
    ctx = SimpleNamespace(queue=queue, lease=Lease(ref.job_id, actor.id, 1), actor=lambda: actor)
    runtime = object.__new__(PipelineRuntime)
    runtime.db, runtime.workflow = world.db, world.w
    runtime.workspace = ProductWorkspace(world.db, world.store, world.w, tmp_path, None, Redactor([]))
    outcome = runtime._reject(ctx, queue.verify(ctx.lease), candidate, "Missing price validation")
    assert outcome.status == "succeeded"
    with world.db.read() as s:
        job = s.get(Job, ref.job_id)
        assert job.status == "succeeded"
        assert job.result["pipeline_completion"] == {"job_id": ref.job_id, "generation": 1}
    assert world.ticket(t.id).phase == "development" and world.ticket(t.id).workflow["repair_cycles"] == 1
    try:  # what the supervisor does next; _durable_completion turns this into "succeeded"
        queue.complete(ctx.lease, outcome.result)
        raise AssertionError("a completed job must not be completed twice")
    except StaleLease:
        pass
