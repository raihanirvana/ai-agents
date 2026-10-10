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
    lease = queue.claim('worker', 'interactive', capacity=2, runtimes=('pipeline',))
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
        lane = 'execution' if expected == 'development' else 'interactive'
        lease = queue.claim('worker', lane, capacity=2 if lane == 'interactive' else 1, runtimes=('pipeline',))
        queue.reserve(lease, 'model')
        queue.complete(lease, {'suite_artifact_id': 'suite-fixture'} if expected == 'qa_plan' else {})
    with world.db.read() as s:
        jobs = list(s.scalars(select(Job).where(Job.ticket_id == t.id)))
        assert len({j.limits['budget_key'] for j in jobs}) == 1
        assert sum(j.usage['model_calls'] for j in jobs) == 3


def test_amendment_dispatches_light_tl_then_new_qa_revision_without_reset(world):
    t = world.approve(world.new())
    scheduler, queue = configure(world)
    def finish(result):
        job_id = scheduler.tick()[0]
        with world.db.read() as s:
            job = s.get(Job, job_id)
        lease = queue.claim('worker', job.lane, capacity=2 if job.lane == 'interactive' else 1,
                            runtimes=('pipeline',))
        queue.reserve(lease, 'model')
        queue.complete(lease, result)
        return job
    finish({'ui_contract_revision': 1})
    requested = finish({'contract_amendment': {'revision': 1, 'reason': 'Missing result control for UAC-1'},
                        'ui_contract_revision': 1})
    amendment = finish({'ui_contract_revision': 2})
    assert amendment.stage == 'technical_plan' and amendment.lane == 'interactive'
    assert amendment.runtime_ref['payload']['amendment_request_job_id'] == requested.id
    qa = finish({'suite_artifact_id': 'revision-2-suite', 'ui_contract_revision': 2})
    assert qa.stage == 'qa_plan' and ':ui-r2' in qa.idempotency_key
    with world.db.read() as s:
        rows = list(s.scalars(select(Job).where(Job.ticket_id == t.id)))
    assert len({j.limits['budget_key'] for j in rows}) == 1
    assert sum(j.usage.get('model_calls', 0) for j in rows) == 4
    next_id = scheduler.tick()[0]
    with world.db.read() as s:
        assert s.get(Job, next_id).stage == 'development'


def test_failed_amendment_does_not_spin_automatic_new_attempt(world):
    t = world.approve(world.new())
    scheduler, queue = configure(world)
    for result in ({'ui_contract_revision': 1},
                   {'contract_amendment': {'revision': 1, 'reason': 'Missing control for UAC-1'}}):
        job_id = scheduler.tick()[0]
        with world.db.read() as s:
            job = s.get(Job, job_id)
        lease = queue.claim('worker', job.lane, capacity=2 if job.lane == 'interactive' else 1,
                            runtimes=('pipeline',))
        queue.complete(lease, result)
    job_id = scheduler.tick()[0]
    lease = queue.claim('worker', 'interactive', capacity=2, runtimes=('pipeline',))
    queue.fail(lease, error='invalid contract amendment', retryable=False)
    assert scheduler.tick() == []


def test_qa_source_planning_and_developer_overlap_without_second_writer(world):
    t = world.approve(world.new())
    scheduler, queue = configure(world)
    scheduler.tick()
    lead = queue.claim('worker', 'interactive', capacity=2, runtimes=('pipeline',))
    queue.complete(lead, {'ui_contract_revision': 1})
    qa_id = scheduler.tick()[0]
    qa = queue.claim('qa-worker', 'interactive', capacity=2, runtimes=('pipeline',))
    queue.begin_run(qa, {})
    dev_id = scheduler.tick()[0]
    dev = queue.claim('dev-worker', 'execution', capacity=1, runtimes=('pipeline',))
    assert qa is not None and dev is not None
    assert scheduler.tick() == []
    with world.db.read() as s:
        assert s.get(Job, qa_id).stage == 'qa_plan'
        assert s.get(Job, dev_id).stage == 'development'
        assert s.get(Job, qa_id).limits['budget_key'] == s.get(Job, dev_id).limits['budget_key']
    queue.complete(qa, {'suite_artifact_id': 'suite-fixture', 'ui_contract_revision': 1})
    assert scheduler.tick() == []
    queue.finish_cleanup(qa.job_id, qa.generation)
    assert scheduler.tick() == []
