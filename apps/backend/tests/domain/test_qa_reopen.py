"""Trusted QA correction cannot approve or overwrite previously accepted evidence."""
import pytest
from app.domain import Forbidden, Conflict, Invalid
from app.persistence.models import Candidate, Verification, Job
from .conftest import world, db, db_path, store  # noqa: F401


def test_reopen_preserves_reviewed_candidate_and_immutable_old_evidence(world):
    t, c, target, v, ids = world.uat()
    with world.db.read() as s:
        source_generation = s.get(Job, c.job_id).lease_generation
    qa_id = t.workflow['attempts']['qa']['job_id']
    reopened = world.w.reopen_qa(world.actor('verification'), t.id, t.revision, c.id, v.id,
                                'Missing explicit stock assertion')
    assert reopened.phase == 'qa' and reopened.workflow['repair_cycles'] == 0
    assert reopened.workflow['candidate_id'] == c.id and 'qa' not in reopened.workflow['attempts']
    with world.db.read() as s:
        current = s.get(Candidate, c.id)
        assert current.status == 'review_approved' and current.preview == {}
        assert current.target_artifact_id == target.id and current.commit_sha == c.commit_sha
        assert s.get(Verification, v.id).status == 'passed'
        assert s.get(Job, c.job_id).lease_generation == source_generation
        assert s.get(Job, qa_id).status == 'cancelled'
    with pytest.raises(Conflict):
        world.w.accept_uat(world.user, t.id, reopened.revision, c.id, t.current_version,
                          target.id, target.checksum, v.id, ids)


@pytest.mark.parametrize('role', ['user', 'qa', 'developer', 'technical-lead', 'builder'])
def test_agents_and_user_cannot_withdraw_qa_evidence_via_service_command(world, role):
    t, c, _, v, _ = world.uat()
    with pytest.raises(Forbidden):
        world.w.reopen_qa(world.actor(role), t.id, t.revision, c.id, v.id, 'Correction')
    assert world.ticket(t.id).phase == 'uat'


def test_reopen_requires_reason_and_current_verification(world):
    t, c, target, v, _ = world.uat()
    with pytest.raises(Invalid):
        world.w.reopen_qa(world.actor('verification'), t.id, t.revision, c.id, v.id, ' ')
    wrong, _ = world.proof(t, c, target)
    with pytest.raises(Conflict):
        world.w.reopen_qa(world.actor('verification'), t.id, t.revision, c.id, wrong.id, 'Correction')


def test_qa_correction_cannot_undo_user_uat_approval(world):
    t, c, _ = world.integrating()
    with pytest.raises(Conflict):
        world.w.reopen_qa(world.actor('verification'), t.id, t.revision, c.id, 'obsolete', 'Correction')
