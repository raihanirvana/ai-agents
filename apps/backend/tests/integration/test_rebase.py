"""Rebase of a superseded candidate onto a new accepted base (real Git), and per-base development jobs."""
from types import SimpleNamespace

from sqlalchemy import select

from app.persistence.models import Job, Project
from app.pipeline.scheduler import PipelineScheduler
from app.pipeline.workspace import rebase_onto
from app.workers import JobQueue
from app.workspace.gitbroker import ACCEPTED_REF


def advance(env, files):
    """Another integration moved accepted: a real fast-forward commit on top of the current base."""
    sha = env.commit(files)
    env.broker._bare("update-ref", ACCEPTED_REF, sha, env.broker.accepted_sha())
    return sha


def test_a_candidate_diff_is_reapplied_cleanly_on_the_new_base(env, tmp_path):
    base = env.base
    candidate = SimpleNamespace(base_sha=base, commit_sha=env.commit({"menu.js": "latte\n"}))
    new_base = advance(env, {"cart.js": "total\n"})
    dest = tmp_path / "rebased"
    assert rebase_onto(env.broker, candidate, new_base, dest) is True
    assert (dest / "menu.js").read_text() == "latte\n" and (dest / "cart.js").read_text() == "total\n"


def test_a_conflicting_candidate_leaves_a_pristine_new_base_instead_of_a_half_applied_tree(env, tmp_path):
    first = env.commit({"menu.js": "latte\n"})
    env.broker._bare("update-ref", ACCEPTED_REF, first, env.base)
    candidate = SimpleNamespace(base_sha=first, commit_sha=env.commit({"menu.js": "mocha\n"}))
    new_base = advance(env, {"menu.js": "espresso\n"})
    dest = tmp_path / "rebased"
    assert rebase_onto(env.broker, candidate, new_base, dest) is False
    assert (dest / "menu.js").read_text() == "espresso\n"
    assert sorted(p.name for p in dest.iterdir()) == ["menu.js"]


def test_a_new_accepted_base_gets_a_new_development_job_instead_of_being_stuck_on_the_old_one(env):
    w = env.world
    with env.db.write() as s:
        p = s.get(Project, env.pid)
        p.workflow = {**p.workflow, "pipeline": {"manifest": {}}}
    t = w.approve(w.new())
    queue = JobQueue(env.db, startable=w.w.startable)
    scheduler = PipelineScheduler(env.db, queue, w.w)
    for _ in ("technical_plan", "qa_plan", "development"):
        scheduler.tick()
        lease = queue.claim("worker", "execution", capacity=1, runtimes=("pipeline",))
        queue.complete(lease, {})
    with env.db.read() as s:
        old = s.scalar(select(Job).where(Job.ticket_id == t.id, Job.stage == "development"))
    assert old.idempotency_key.endswith("@" + env.base[:12])
    assert scheduler.tick() == []  # same base: the finished development job is not repeated
    new_base = advance(env, {"other.txt": "x\n"})
    with env.db.write() as s:
        p = s.get(Project, env.pid)
        p.workflow = {**p.workflow, "accepted_tip": new_base}
    [job_id] = scheduler.tick()
    with env.db.read() as s:
        job = s.get(Job, job_id)
    assert job.stage == "development" and job.idempotency_key.endswith("@" + new_base[:12])
    assert job.limits["budget_key"] == old.limits["budget_key"]  # same scope budget: nothing is reset
