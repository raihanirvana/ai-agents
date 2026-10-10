"""QA profile inheritance and user decisions for stuck QA shapes.

Synthetic trusted receipts exercise policy only; they are not real QA/provider evidence.
"""
import pytest
from sqlalchemy import select

from app.domain import Actor, Invalid
from app.domain.qa_resolution import available
from app.persistence.models import Candidate, QaWaiver, TicketVersion, Verification
from app.pipeline.contracts import QaPlan
from .conftest import SCOPE, world, db, db_path, store  # noqa: F401
from .test_qa_manual_resolution import decide


def version(world, t):
    with world.db.read() as s:
        return s.scalar(select(TicketVersion).where(TicketVersion.ticket_id == t.id,
                                                    TicketVersion.version == world.ticket(t.id).current_version))


def manual_ticket(world):
    t = world.new({**SCOPE, 'qa_profile': 'manual'})
    assert version(world, t).uac[0]['mode'] == 'manual'
    return t


def test_user_scope_edit_without_profile_keeps_manual_qa(world):
    t = manual_ticket(world)
    edited = world.w.edit_scope(world.user, t.id, world.ticket(t.id).revision,
                                {**SCOPE, 'title': 'Menu v2', 'uac': [{'id': 'UAC-1', 'text': 'Add tea'}]})
    current = version(world, edited)
    assert current.version == 2 and current.scope['qa_profile'] == 'manual'
    assert current.uac == [{'id': 'UAC-1', 'text': 'Add tea', 'mode': 'manual'}]
    # An explicit user choice still switches the profile.
    edited = world.w.edit_scope(world.user, t.id, world.ticket(t.id).revision, {**SCOPE, 'qa_profile': 'lightweight'})
    assert version(world, edited).uac[0].get('mode', 'automated') == 'automated'


def test_po_revision_inherits_profile_and_cannot_switch_it(world):
    t = manual_ticket(world)
    po, _ = world.job(t, 'po', bind=False)
    for document in ({**SCOPE, 'title': 'PO revision'}, {**SCOPE, 'title': 'PO switch', 'qa_profile': 'lightweight'}):
        proposal = world.w.propose_scope(po, t.id, world.ticket(t.id).revision, document)
        decided = world.w.decide_proposal(world.user, t.id, world.ticket(t.id).revision, proposal, True)
        current = version(world, decided)
        assert current.scope['qa_profile'] == 'manual' and current.uac[0]['mode'] == 'manual'
        po, _ = world.job(decided, 'po', bind=False)


def coverage_gap(world, *, base_status='passed', candidate_status='passed', infra=False, gate='passed',
                 diagnosis=True, test_ids=('feature-add',)):
    t, c = world.submitted()
    suite = QaPlan.model_validate({'kind': 'qa_plan', 'summary': 'Original approved feature', 'tests': [
        {'id': 'feature-add', 'purpose': 'feature', 'uac': ['UAC-1'],
         'steps': [{'action': 'assert_text', 'selector': '#name', 'value': 'Coffee'}]}]})
    runner = {'runner_code_digest': 'a' * 64, 'runner_image_id': 'synthetic-test-image'}
    with world.db.write() as s:
        a = world.store.put_json(s, project_id=t.project_id, kind='report', name='suite.json', document=suite.model_dump())
    target = world.target(t, c, suite_artifact_id=a.id, runner_manifest_digest=suite.digest, runner=runner)
    lead, attempt = world.job(t, 'technical-lead')
    world.w.approve_review(lead, t.id, world.ticket(t.id).revision, attempt, c.id)
    passed = candidate_status == 'passed'
    with world.db.write() as s:
        c = s.get(Candidate, c.id)
        report = {'schema': 1, 'invocation_id': 'synthetic-runner-receipt', 'target_digest': target.checksum,
            'suite_digest': suite.digest, 'smoke_passed': True,
            'tests': [{'id': 'feature-add', 'uac': ['UAC-1'], 'status': candidate_status}],
            'discovered': 1, 'executed': 1, 'passed': int(passed), 'failed': int(not passed), 'skipped': 0}
        base = {'status': base_status, 'infrastructure_failure': False, 'report': {
            'tests': [{'id': 'feature-add', 'status': 'passed'}], 'discovered': 1, 'executed': 1, 'skipped': 0}}
        proof = {'status': 'incomplete', 'invocation_id': report['invocation_id'], 'report': report,
                 'required_checks': {'status': gate}, 'infrastructure_failure': infra, 'runner': runner,
                 'baseline': {'execution': base},
                 'commands': [{'argv': ['isolated-browser-runner', report['invocation_id']], 'exit_code': 0 if passed else 1}]}
        a = world.store.put_json(s, project_id=t.project_id, kind='report', name='acceptance.json',
            document=proof, meta={'producer': 'verification'})
        v = Verification(candidate_id=c.id, target_artifact_id=target.id, target_digest=target.checksum,
            commit_artifact_id=c.commit_artifact_id, build_artifact_id=c.build_artifact_id,
            context_artifact_id=c.context_artifact_id, evidence_id=report['invocation_id'], suite_digest=suite.digest,
            expected_test_ids=['feature-add'], counts={k: report[k] for k in ('discovered', 'executed', 'passed', 'failed', 'skipped')},
            uac_coverage={'UAC-1': ['feature-add']}, status='incomplete', evidence_artifact_ids=[a.id],
            results={'fake_provider': False, 'infrastructure_failure': infra, 'executed_test_ids': ['feature-add'],
                     'commands': proof['commands']})
        s.add(v); s.flush()
        ids = [a.id]
        d = None
        if diagnosis:
            # Shape written by PipelineRuntime._unknown_diagnosis when revision cannot resolve the gap.
            d = world.store.put_json(s, project_id=t.project_id, kind='report', name='qa-diagnosis.json',
                document={'kind': 'qa_diagnosis', 'fault': 'unknown', 'summary': 'Supervisor: coverage gap', 'findings': [
                    {'test_id': test_id, 'fault': 'unknown', 'expected': 'Unresolved by automated QA',
                     'observed': 'See authoritative runner evidence', 'reason': 'Feature assertion passes on base.'}
                    for test_id in test_ids]},
                meta={'producer': 'qa-diagnosis', 'verification_id': v.id, 'target_digest': target.checksum,
                      'fake': False, 'supervisor_derived': 'coverage_gap_unresolved'})
            ids.append(d.id)
        args = dict(candidate_id=c.id, verification_id=v.id, target_artifact_id=target.id, target_digest=target.checksum,
                    diagnosis_artifact_id=d.id if d else None, evidence_ids=ids, manual_uac_ids=['UAC-1'],
                    reason='The automated test cannot discriminate this feature; I will test it manually.')
    return world.ticket(t.id), c, v, args


def test_proven_coverage_gap_reaches_a_user_decision(world):
    t, c, v, args = coverage_gap(world)
    with world.db.read() as s:
        offer = available(s, world.store, world.ticket(t.id))
    assert offer['eligible'] is True and offer['verification_id'] == v.id
    t = decide(world, t, args)
    assert t.phase == 'uat'
    with world.db.read() as s:
        assert s.get(Verification, v.id).status == 'incomplete'  # QA itself never becomes passed.
        assert s.scalar(select(QaWaiver)).excluded_test_ids == ['feature-add']


@pytest.mark.parametrize('settings', [
    dict(base_status='incomplete'),          # Base run did not complete: not a proven gap.
    dict(candidate_status='failed'),         # Incomplete with a failing candidate is not a gap decision.
    dict(infra=True), dict(gate='failed'),
    dict(test_ids=('other-test',)),          # Diagnosis must cover exactly the gap tests.
])
def test_incomplete_qa_without_a_clean_proven_gap_cannot_be_decided(world, settings):
    t, _, _, args = coverage_gap(world, **settings)
    with pytest.raises(Invalid):
        decide(world, t, args)
    with world.db.read() as s:
        assert s.scalar(select(QaWaiver.id)) is None


def test_missing_diagnosis_without_active_qa_is_not_an_endless_wait(world):
    t, _, _, _ = coverage_gap(world, diagnosis=False)
    with world.db.read() as s:
        offer = available(s, world.store, world.ticket(t.id))
    assert offer['eligible'] is False and 'jalankan ulang QA' in offer['reason']
