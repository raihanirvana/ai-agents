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


def test_withheld_application_claim_is_shown_and_pinned_with_the_decision(world):
    from .test_qa_manual_resolution import inconclusive
    t, c, v, args = inconclusive(world, fault='application')
    with world.db.write() as s:
        model_id = args['diagnosis_artifact_id']
        derived = world.store.put_json(s, project_id=t.project_id, kind='report', name='qa-diagnosis.json',
            document={'kind': 'qa_diagnosis', 'fault': 'unknown', 'summary': 'Supervisor: attribution withheld',
                      'findings': [{'test_id': 'feature-add', 'fault': 'unknown', 'expected': 'Unresolved by automated QA',
                                    'observed': 'See authoritative runner evidence', 'reason': 'withheld'}]},
            meta={'producer': 'qa-diagnosis', 'verification_id': v.id, 'target_digest': args['target_digest'],
                  'fake': False, 'supervisor_derived': 'application_attribution_withheld', 'derived_from': model_id,
                  'application_repair_issues': ['feature-add: source_excerpt not found in shipped source']})
        # A model diagnosis written later never displaces the supervisor-derived decision basis.
        world.store.put_json(s, project_id=t.project_id, kind='report', name='late.json',
            document={'kind': 'qa_diagnosis', 'fault': 'application', 'summary': 'late', 'findings': [
                {'test_id': 'feature-add', 'fault': 'application', 'expected': 'x', 'observed': 'y', 'reason': 'z'}]},
            meta={'producer': 'qa-diagnosis', 'verification_id': v.id, 'target_digest': args['target_digest'], 'fake': False})
    with world.db.read() as s:
        offer = available(s, world.store, world.ticket(t.id))
    assert offer['eligible'] is True and offer['diagnosis_artifact_id'] == derived.id
    shown = offer['diagnosis']['withheld_application']
    assert shown['artifact_id'] == model_id and shown['fault'] == 'application'
    assert shown['validator_issues'] == ['feature-add: source_excerpt not found in shipped source']
    assert shown['findings'][0]['observed'] == 'Ambiguous locator'
    assert model_id in offer['evidence_ids']
    decided = decide(world, t, {**args, 'diagnosis_artifact_id': derived.id, 'evidence_ids': offer['evidence_ids']})
    assert decided.phase == 'uat'
    with world.db.read() as s:
        assert model_id in s.scalar(select(QaWaiver)).evidence_artifact_ids


def test_panel_offers_one_more_revision_only_for_a_failed_test_contract_run(world):
    from .test_qa_manual_resolution import inconclusive
    from tests.persistence import factories as f
    t, c, v, args = inconclusive(world, fault='test')
    with world.db.read() as s:
        assert 'revision_retry' not in available(s, world.store, world.ticket(t.id))  # no failed QA run yet
    with world.db.write() as s:
        job = f.job(s, world.project, world.ticket(t.id), stage='qa', status='failed',
                    result={'failure_kind': 'test_contract', 'user_decision_required': True})
    with world.db.read() as s:
        offer = available(s, world.store, world.ticket(t.id))
    assert offer['eligible'] is True
    assert offer['revision_retry'] == {'run_id': job.id, 'run_revision': job.revision}
    with world.db.write() as s:
        f.job(s, world.project, world.ticket(t.id), stage='qa', status='failed',
              result={'failure_kind': 'infrastructure'})
    with world.db.read() as s:
        assert 'revision_retry' not in available(s, world.store, world.ticket(t.id))


def test_inherited_ui_controls_are_the_union_of_accepted_tickets(world, monkeypatch):
    """Real accept path (review/QA/UAT/integration) with synthetic receipts; no model."""
    from app.agents.ui_contract import UiContract
    from app.pipeline.runtime import PipelineRuntime
    row = {'testid': 'book-item', 'role': 'listitem', 'purpose': 'row', 'dynamic_text': True}
    borrow = {'testid': 'borrow-name-input', 'label': 'Nama peminjam', 'purpose': 'borrower', 'scope_testid': 'book-item'}
    contracts = iter([[row, borrow], [row, {**borrow, 'label': 'Nama anggota'},
                                       {'testid': 'late', 'role': 'button', 'name': 'Late', 'purpose': 'x'}]])
    original = type(world).target
    def target_with_contract(self, t, c, **override):
        controls = next(contracts)
        return original(self, t, c, ui_contract=UiContract.model_validate({'controls': controls}).model_dump(), **override)
    monkeypatch.setattr(type(world), 'target', target_with_contract)
    first, _ = world.accepted(world.approve(world.new()))
    # The third ticket does not depend on the second; the second changed the same page later.
    second, _ = world.accepted(world.approve(world.new({**SCOPE, 'title': 'Anggota'})))
    third = world.approve(world.new({**SCOPE, 'title': 'Kembalikan'}))
    rt = object.__new__(PipelineRuntime)
    rt.db, rt.store = world.db, world.store
    controls = {c['testid']: c for c in rt._inherited_ui_controls({'ticket_id': third.id})}
    assert set(controls) == {'book-item', 'borrow-name-input', 'late'}
    assert controls['borrow-name-input']['label'] == 'Nama anggota'  # latest acceptance wins
    assert controls['borrow-name-input']['scope_testid'] == 'book-item'
    own = {c['testid'] for c in rt._inherited_ui_controls({'ticket_id': second.id})}
    assert own == {'book-item', 'borrow-name-input'}  # a ticket never inherits from itself


def test_inherited_controls_skip_invalid_legacy_controls(world, monkeypatch):
    from app.pipeline.runtime import PipelineRuntime
    original = type(world).target
    def target_with_legacy(self, t, c, **override):
        return original(self, t, c, ui_contract={'controls': [
            {'testid': 'ok', 'role': 'button', 'name': 'OK', 'purpose': 'fine'},
            {'testid': 'legacy', 'role': 'not-a-role', 'purpose': 'old schema'}]}, **override)
    monkeypatch.setattr(type(world), 'target', target_with_legacy)
    world.accepted(world.approve(world.new()))
    other = world.approve(world.new({**SCOPE, 'title': 'Lain'}))
    rt = object.__new__(PipelineRuntime)
    rt.db, rt.store = world.db, world.store
    logs = []
    assert [c['testid'] for c in rt._inherited_ui_controls({'ticket_id': other.id}, log=logs.append)] == ['ok']
    assert 'legacy' in logs[0]
