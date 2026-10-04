from datetime import timedelta
import pytest
from sqlalchemy import select
from app.domain import Actor, ApprovalItem, Forbidden, Conflict, Invalid
from app.persistence import NotFound, RevisionConflict, read_events
from app.persistence.columns import utcnow
from app.persistence.models import Approval, Candidate, Dependency, Job, Project, TicketVersion
from tests.persistence import factories as f
from .conftest import SCOPE


def state(world):
    with world.db.read() as s:
        return [(e.cursor, e.type) for e in read_events(s)]


def test_full_flow_only_integrator_receipt_accepts(world):
    t, c, op = world.integrating()
    assert t.phase == "integrating"
    with world.db.read() as s:
        assert s.get(Candidate, c.id).status == "verified"
    with pytest.raises(Forbidden):
        world.w.finish_integration(world.user, t.id, t.revision, c.id, op["operation_id"], c.commit_sha)
    with pytest.raises(Conflict):
        world.w.finish_integration(world.actor("integrator"), t.id, t.revision, c.id, op["operation_id"], f.SHA_C)
    world.w.finish_integration(world.actor("integrator"), t.id, t.revision, c.id, op["operation_id"], c.commit_sha)
    assert world.ticket(t.id).phase == "accepted"
    with world.db.read() as s:
        assert s.get(Candidate, c.id).integration["status"] == "done"
        assert s.get(Project, t.project_id).workflow["accepted_tip"] == c.commit_sha


@pytest.mark.parametrize("role", ["po", "developer", "qa", "technical-lead", "scheduler", "builder", "verification", "integrator"])
def test_only_user_can_approve_scope(world, role):
    t = world.new()
    before = state(world)
    with pytest.raises(Forbidden):
        world.w.approve_scope(world.actor(role), [ApprovalItem(t.id, 1, t.revision)])
    assert state(world) == before
    assert not world.w.eligible(world.user, t.id)


def test_batch_stale_version_revision_rolls_back_all(world):
    one, two = world.new(), world.new()
    before = state(world)
    for bad in (ApprovalItem(two.id, 2, two.revision), ApprovalItem(two.id, 1, two.revision - 1)):
        with pytest.raises((Conflict, RevisionConflict)):
            world.w.approve_scope(world.user, [ApprovalItem(one.id, 1, one.revision), bad])
        assert state(world) == before
        assert world.ticket(one.id).phase == "scope_review"
    batch = world.w.approve_scope(world.user, [ApprovalItem(one.id, 1, one.revision), ApprovalItem(two.id, 1, two.revision)])
    with world.db.read() as s:
        assert {a.batch_id for a in s.scalars(select(Approval).where(Approval.type == "scope"))} == {batch}


def test_dependency_waits_for_integrated_acceptance(world):
    up = world.approve(world.new())
    down = world.approve(world.new({**SCOPE, "dependencies": [up.id]}))
    assert not world.w.eligible(world.user, down.id)
    t, c, op = world.integrating(up)
    assert not world.w.eligible(world.user, down.id)
    world.w.finish_integration(world.actor("integrator"), t.id, t.revision, c.id, op["operation_id"], c.commit_sha)
    assert world.w.eligible(world.user, down.id)
    with world.db.read() as s:
        d = s.scalar(select(Dependency).where(Dependency.ticket_id == down.id))
        assert (d.accepted_scope_version, d.accepted_candidate_id, d.integration_sha) == (1, c.id, c.commit_sha)


def test_missing_cross_project_self_cycle_dependencies(world):
    t = world.new()
    with world.db.write() as s:
        foreign = f.project(s)
        other = f.ticket(s, foreign)
    for dep in (t.id, "missing", other.id):
        with pytest.raises((Invalid, Forbidden, NotFound)):
            world.w.edit_scope(world.user, t.id, t.revision, {**SCOPE, "dependencies": [dep]})
    two = world.new({**SCOPE, "dependencies": [t.id]})
    with pytest.raises(Invalid, match="cycle"):
        world.w.edit_scope(world.user, t.id, t.revision, {**SCOPE, "dependencies": [two.id]})
    assert world.ticket(t.id).current_version == 1


def test_scope_revision_cancels_old_attempt_and_preserves_history(world):
    t = world.approve(world.new())
    actor, ref = world.job(t, "developer")
    before = world.ticket(t.id)
    revised = world.w.edit_scope(world.user, t.id, before.revision, {**SCOPE, "title": "Revised"})
    assert (revised.current_version, revised.phase) == (2, "scope_review")
    assert not world.w.eligible(world.user, t.id)
    with world.db.read() as s:
        assert s.get(Job, ref.job_id).status == "cancelled"
        assert s.get(Job, ref.job_id).lease_generation == 2
        assert len(s.scalars(select(TicketVersion).where(TicketVersion.ticket_id == t.id)).all()) == 2
        assert s.scalar(select(Approval.id).where(Approval.ticket_id == t.id))
    with pytest.raises(Forbidden):
        world.w.submit_candidate(actor, t.id, revised.revision, ref, commit_artifact_id="unused",
            commit_receipt_id="unused", base_sha=f.SHA_B, submission_key="old")


def test_po_proposal_requires_user_decision(world):
    t = world.approve(world.new())
    po, _ = world.job(t, "po", bind=False)
    proposal = world.w.propose_scope(po, t.id, t.revision, {**SCOPE, "title": "Proposal"})
    current = world.ticket(t.id)
    assert current.current_version == 1 and current.phase == "ready"
    with pytest.raises(Forbidden):
        world.w.decide_proposal(po, t.id, current.revision, proposal, True)
    accepted = world.w.decide_proposal(world.user, t.id, current.revision, proposal, True)
    assert accepted.current_version == 2 and accepted.phase == "scope_review"
    assert not world.w.eligible(world.user, t.id)


def test_rejected_proposal_keeps_scope(world):
    t = world.new()
    po, _ = world.job(t, "po", bind=False)
    pid = world.w.propose_scope(po, t.id, t.revision, {**SCOPE, "title": "No"})
    t = world.ticket(t.id)
    current = world.w.decide_proposal(world.user, t.id, t.revision, pid, False)
    assert current.title == "Menu" and current.current_version == 1
    with pytest.raises(Conflict):
        world.w.decide_proposal(world.user, t.id, current.revision, pid, True)


@pytest.mark.parametrize("phase", ["integrating", "accepted"])
def test_integrating_and_accepted_cannot_cancel_or_revise(world, phase):
    t, c, op = world.integrating()
    if phase == "accepted":
        world.w.finish_integration(world.actor("integrator"), t.id, t.revision, c.id, op["operation_id"], c.commit_sha)
        t = world.ticket(t.id)
    for command in (lambda: world.w.cancel(world.user, t.id, t.revision),
                    lambda: world.w.edit_scope(world.user, t.id, t.revision, SCOPE)):
        with pytest.raises(Conflict):
            command()


def test_revert_is_a_new_ticket_not_history_deletion(world):
    t, c = world.accepted()
    revert = world.new({**SCOPE, "title": "Revert menu", "reverts_candidate_id": c.id})
    assert revert.id != t.id and world.ticket(t.id).phase == "accepted"
    with world.db.read() as s:
        version = s.scalar(select(TicketVersion).where(TicketVersion.ticket_id == revert.id))
        assert version.scope["reverts_candidate_id"] == c.id


def test_cancellation_fences_scope_and_jobs(world):
    t = world.approve(world.new())
    actor, ref = world.job(t, "developer")
    world.w.cancel(world.user, t.id, world.ticket(t.id).revision)
    assert not world.w.eligible(world.user, t.id)
    with pytest.raises(Forbidden):
        world.w.submit_candidate(actor, t.id, world.ticket(t.id).revision, ref,
            commit_artifact_id="unused", commit_receipt_id="unused", base_sha=f.SHA_B, submission_key="old")


def test_repair_limit_cumulative_and_extension_requires_user(world):
    t = world.approve(world.new())
    for n in range(3):
        t, c = world.submitted(t)
        lead, ref = world.job(t, "technical-lead")
        world.w.request_changes(lead, t.id, world.ticket(t.id).revision, c.id, "Fix menu", ref)
        t = world.ticket(t.id)
        assert t.workflow["repair_cycles"] == n + 1
    assert t.blocker["reason"] == "needs_human" and not world.w.eligible(world.user, t.id)
    with pytest.raises(Forbidden):
        world.w.authorize_repair(world.actor("scheduler"), t.id, t.revision)
    extended = world.w.authorize_repair(world.user, t.id, t.revision, 1)
    assert extended.workflow["repair_cycles"] == 3 and extended.workflow["repair_limit"] == 4
    assert world.w.eligible(world.user, t.id)


def test_no_arbitrary_status_setter_or_cross_project_intent(world):
    t = world.new()
    assert not hasattr(world.w, "set_status")
    alien = Actor("other-user", "user", "other-project")
    with pytest.raises(NotFound):
        world.w.cancel(alien, t.id, t.revision)


@pytest.mark.parametrize("mode", ["generation", "expired", "borrowed"])
def test_attempt_expired_generation_and_borrowed_job_rejected(world, mode):
    t = world.approve(world.new())
    actor, ref = world.job(t, "developer")
    t = world.ticket(t.id)
    if mode == "borrowed":
        actor, _ = world.job(t, "developer", bind=False)
    else:
        with world.db.write() as s:
            j = s.get(Job, ref.job_id)
            if mode == "generation":
                j.lease_generation += 1
            else:
                j.lease_expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(Forbidden):
        world.w.submit_candidate(actor, t.id, t.revision, ref, commit_artifact_id="unused",
            commit_receipt_id="unused", base_sha=f.SHA_B, submission_key="stale")


def test_revision_conflict_has_no_state_or_event(world):
    t = world.new()
    before = state(world)
    with pytest.raises(RevisionConflict):
        world.w.edit_scope(world.user, t.id, t.revision - 1, SCOPE)
    assert state(world) == before
