from sqlalchemy import select
from app.pipeline.scheduler import PipelineScheduler, DEFAULT_LIMITS
from app.persistence.models import Project, Job
from tests.domain.conftest import world, db, db_path, store  # noqa: F401
from app.workers import JobQueue


def configure(world):
    with world.db.write() as s:
        p = s.get(Project, world.project.id)
        p.workflow = {**p.workflow, 'pipeline': {'manifest': {}}}
    queue = JobQueue(world.db, startable=world.w.startable)
    return PipelineScheduler(world.db, queue, world.w), queue


def test_scheduler_only_starts_approved_configured_scope_and_dispatches_once(world):
    t = world.new()
    scheduler, queue = configure(world)
    assert scheduler.tick() == []
    world.approve(t)
    ids = scheduler.tick()
    assert len(ids) == 1
    assert scheduler.tick() == []
    with world.db.read() as s:
        job = s.get(Job, ids[0])
        assert (job.stage, job.runtime_ref['role']) == ('technical_plan', 'technical-lead')


def test_scheduler_does_not_go_around_failed_or_exhausted_plan(world):
    world.approve(world.new())
    scheduler, queue = configure(world)
    job_id = scheduler.tick()[0]
    lease = queue.claim('worker', 'execution', capacity=1, runtimes=('pipeline',))
    queue.fail(lease, error='invalid plan', retryable=False)
    assert scheduler.tick() == []


def test_plan_then_qa_plan_then_developer_share_scope_budget(world):
    t = world.approve(world.new())
    scheduler, queue = configure(world)
    for expected in ('technical_plan', 'qa_plan', 'development'):
        job_id = scheduler.tick()[0]
        with world.db.read() as s:
            j = s.get(Job, job_id)
            assert j.stage == expected
        lease = queue.claim('worker', 'execution', capacity=1, runtimes=('pipeline',))
        queue.reserve(lease, 'model')
        queue.complete(lease, {})
    with world.db.read() as s:
        jobs = list(s.scalars(select(Job).where(Job.ticket_id == t.id)))
        assert len({j.limits['budget_key'] for j in jobs}) == 1
        assert sum(j.usage['model_calls'] for j in jobs) == 3
