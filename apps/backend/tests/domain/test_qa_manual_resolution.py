"""Synthetic trusted receipts exercise policy; these are not real QA/provider evidence."""
from dataclasses import replace
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from app.domain import Actor, Invalid, Conflict, Forbidden
from app.domain.qa_resolution import qualify
from app.persistence import pin_owners, ArtifactUnavailable
from app.persistence.models import Candidate, Verification, QaWaiver, Approval, TicketVersion
from app.pipeline.contracts import QaPlan
from .conftest import world, db, db_path, store  # noqa: F401


def inconclusive(world, *, fault='unknown', smoke=True, gate='passed', fake=False, infra=False, skipped=False):
    t, c = world.submitted()
    suite = QaPlan.model_validate({'kind': 'qa_plan', 'summary': 'Original approved feature', 'tests': [
        {'id': 'feature-add', 'purpose': 'feature', 'uac': ['UAC-1'],
         'steps': [{'action': 'assert_text', 'selector': '#name', 'value': 'Coffee'}]}]})
    with world.db.write() as s:
        a = world.store.put_json(s, project_id=t.project_id, kind='report', name='suite.json', document=suite.model_dump())
    target = world.target(t, c, suite_artifact_id=a.id, runner_manifest_digest=suite.digest, runner={'runner_code_digest': 'a' * 64, 'runner_image_id': 'synthetic-test-image'})
    lead, attempt = world.job(t, 'technical-lead')
    world.w.approve_review(lead, t.id, world.ticket(t.id).revision, attempt, c.id)
    with world.db.write() as s:
        c = s.get(Candidate, c.id)
        report = {'schema': 1, 'invocation_id': 'synthetic-runner-receipt', 'target_digest': target.checksum,
            'suite_digest': suite.digest, 'smoke_passed': smoke,
            'tests': [{'id': 'feature-add', 'uac': ['UAC-1'], 'status': 'skipped' if skipped else 'failed'}],
            'discovered': 1, 'executed': 1, 'passed': 0, 'failed': 1, 'skipped': 0}
        proof = {'status': 'failed', 'invocation_id': report['invocation_id'], 'report': report,
                 'required_checks': {'status': gate}, 'infrastructure_failure': infra,
                 'runner': {'runner_code_digest': 'a' * 64, 'runner_image_id': 'synthetic-test-image'},
                 'commands': [{'argv': ['isolated-browser-runner', report['invocation_id']], 'exit_code': 1}]}
        a = world.store.put_json(s, project_id=t.project_id, kind='report', name='acceptance.json',
            document=proof, meta={'producer': 'verification'})
        v = Verification(candidate_id=c.id, target_artifact_id=target.id, target_digest=target.checksum,
            commit_artifact_id=c.commit_artifact_id, build_artifact_id=c.build_artifact_id,
            context_artifact_id=c.context_artifact_id, evidence_id=report['invocation_id'], suite_digest=suite.digest,
            expected_test_ids=['feature-add'], counts={k: report[k] for k in ('discovered', 'executed', 'passed', 'failed', 'skipped')},
            uac_coverage={'UAC-1': ['feature-add']}, status='failed', evidence_artifact_ids=[a.id],
            results={'fake_provider': fake, 'infrastructure_failure': infra, 'executed_test_ids': ['feature-add'],
                     'commands': proof['commands']})
        s.add(v); s.flush()
        diagnosis = world.store.put_json(s, project_id=t.project_id, kind='report', name='diagnosis.json',
            document={'kind': 'qa_diagnosis', 'fault': fault, 'summary': 'Synthetic diagnosis contract', 'findings': [
                {'test_id': 'feature-add', 'fault': fault, 'expected': 'Coffee', 'observed': 'Ambiguous locator',
                 'reason': 'The evidence does not establish an application fault.'}]},
            meta={'producer': 'qa-diagnosis', 'verification_id': v.id, 'target_digest': target.checksum, 'fake': fake})
        args = dict(candidate_id=c.id, verification_id=v.id, target_artifact_id=target.id, target_digest=target.checksum,
                    diagnosis_artifact_id=diagnosis.id, evidence_ids=[a.id, diagnosis.id], manual_uac_ids=['UAC-1'],
                    reason='I will check this inconclusive feature manually.')
    return world.ticket(t.id), c, v, args


def decide(world, t, args, actor=None):
    return world.w.waive_uncertain_qa(actor or Actor('user:local', 'user', t.project_id),
                                    t.id, world.ticket(t.id).revision, **args)


def test_manual_decision_keeps_failed_qa_requires_fresh_uat_and_release_confirmation(world):
    t, c, v, args = inconclusive(world)
    t = decide(world, t, args)
    assert t.phase == 'uat'
    with world.db.read() as s:
        assert s.get(Verification, v.id).status == 'failed'
        current = s.get(Candidate, c.id)
        assert current.status == 'verified'
        waiver = s.scalar(select(QaWaiver))
        assert not list(s.scalars(select(Approval).where(Approval.type == 'uat')))
        assert pin_owners(s, waiver.diagnosis_artifact_id)
        evidence_ids = current.evidence_artifact_ids
    user = Actor('user:local', 'user', t.project_id)
    with pytest.raises(Invalid, match='manual UAC'):
        world.w.accept_uat(user, t.id, t.revision, c.id, 1, args['target_artifact_id'], args['target_digest'], v.id, evidence_ids)
    operation = world.w.accept_uat(user, t.id, t.revision, c.id, 1, args['target_artifact_id'], args['target_digest'],
                                  v.id, evidence_ids, ['UAC-1'])
    world.w.finish_integration(world.actor('integrator'), t.id, world.ticket(t.id).revision, c.id,
                               operation['operation_id'], c.commit_sha)
    _, entries = world.w.freeze_release_scope(user)
    assert entries[0]['uac'][0]['mode'] == 'manual'
    assert entries[0]['checklist'] == [t.id + ':UAC-1']
    assert entries[0]['qa_waiver']['excluded_test_ids'] == ['feature-add']
    with world.db.read() as s:
        original = s.scalar(select(TicketVersion).where(TicketVersion.ticket_id == t.id))
        assert original.uac[0].get('mode', 'automated') == 'automated'


@pytest.mark.parametrize('settings', [dict(fault='application'), dict(fault='infrastructure'), dict(smoke=False),
    dict(gate='failed'), dict(fake=True), dict(infra=True), dict(skipped=True)])
def test_cannot_waive_real_bugs_missing_execution_or_infrastructure(world, settings):
    t, _, _, args = inconclusive(world, **settings)
    with pytest.raises(Invalid):
        decide(world, t, args)
    assert world.ticket(t.id).phase == 'qa'
    with world.db.read() as s:
        assert s.scalar(select(QaWaiver.id)) is None


def test_waiver_fences_identity_evidence_scope_and_user_role(world):
    t, _, _, args = inconclusive(world)
    for patch in ({'target_digest': 'f' * 64}, {'evidence_ids': args['evidence_ids'][:1]}, {'manual_uac_ids': []}):
        with pytest.raises((Conflict, Invalid)):
            decide(world, t, {**args, **patch})
    with pytest.raises(Forbidden):
        decide(world, t, args, world.actor('qa'))
    with world.db.write() as s:
        s.execute(text("UPDATE artifacts SET availability='unavailable', unavailable_reason='missing' WHERE id=:id"),
                  {'id': args['diagnosis_artifact_id']})
    with pytest.raises(ArtifactUnavailable):
        decide(world, t, args)


def test_manual_decision_cannot_be_mutated_or_deleted(world):
    t, _, _, args = inconclusive(world)
    decide(world, t, args)
    for operation in ('UPDATE qa_waivers SET reason=\'changed\'', 'DELETE FROM qa_waivers'):
        with pytest.raises(IntegrityError, match='immutable'):
            with world.db.write() as s:
                s.execute(text(operation))


def test_manual_project_preset_applies_only_to_new_proposals(world):
    earlier = world.approve(world.new())
    with world.db.read() as s:
        from app.persistence.models import Project
        revision = s.get(Project, world.project.id).revision
    world.w.set_qa_profile(world.user, revision, 'manual')
    new = world.new()
    with world.db.read() as s:
        old = s.scalar(select(TicketVersion).where(TicketVersion.ticket_id == earlier.id))
        fresh = s.scalar(select(TicketVersion).where(TicketVersion.ticket_id == new.id))
        assert old.uac[0].get('mode', 'automated') == 'automated'
        assert fresh.uac[0]['mode'] == 'manual'
        assert fresh.scope['qa_profile'] == 'manual'
    assert world.ticket(earlier.id).phase == 'ready'
    assert new.phase == 'scope_review'


def test_mixed_release_suite_excludes_only_user_owned_failures_and_runs_fresh_smoke(world):
    t, c, v, args = inconclusive(world, fault='test')
    t = decide(world, t, args)
    with world.db.read() as s:
        ids = s.get(Candidate, c.id).evidence_artifact_ids
    user = Actor('user:local', 'user', t.project_id)
    op = world.w.accept_uat(user, t.id, t.revision, c.id, 1, args['target_artifact_id'], args['target_digest'], v.id, ids, ['UAC-1'])
    world.w.finish_integration(world.actor('integrator'), t.id, world.ticket(t.id).revision, c.id, op['operation_id'], c.commit_sha)
    _, entries = world.w.freeze_release_scope(user)
    from app.release.runtime import ReleaseRuntime
    runtime = object.__new__(ReleaseRuntime)
    runtime.db, runtime.store = world.db, world.store
    plan, required = runtime.combined_suite(entries)
    assert required == set()
    assert len(plan.tests) == 1 and plan.tests[0].purpose == 'smoke'
    assert plan.tests[0].uac == []
    assert plan.tests[0].id != 'feature-add'


def test_manual_decision_refuses_active_qa_and_changed_base(world):
    t, c, _, args = inconclusive(world)
    qa_actor, qa_attempt = world.job(t, 'qa')
    with pytest.raises(Conflict, match='active'):
        decide(world, t, args)
    from app.persistence.models import Job, Project
    with world.db.write() as s:
        job = s.get(Job, qa_attempt.job_id)
        job.status, job.lease_owner, job.lease_expires_at = 'failed', None, None
        project = s.get(Project, t.project_id)
        project.workflow = {**project.workflow, 'accepted_tip': 'e' * 40}
    with pytest.raises(Conflict, match='base changed'):
        decide(world, t, args)
