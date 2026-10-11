"""Project regression gate (DEV-024): accepted journeys guard later tickets.

Real accept path (review/QA/UAT/integration) with synthetic receipts and a stub harness;
no browser, model or provider. Not QA/UAT evidence.
"""
from types import SimpleNamespace

import pytest

from app.persistence.models import Candidate, Verification
from app.pipeline.regression import regression_suite

from app.agents.ui_contract import UiContract
from app.pipeline.contracts import QaPlan
from app.pipeline.runtime import PipelineRuntime
from .conftest import SCOPE, world, db, db_path, store  # noqa: F401

BORROW = {'testid': 'borrow', 'role': 'button', 'name': 'Pinjam', 'purpose': 'borrow'}


def journeys(*ids):
    return QaPlan.model_validate({'kind': 'qa_plan', 'summary': 'accepted', 'tests': [
        {'id': test_id, 'uac': ['UAC-1'], 'purpose': 'feature', 'steps': [
            {'action': 'click', 'selector': 'testid=borrow'},
            {'action': 'assert_visible', 'selector': 'testid=borrow'}]} for test_id in ids]})


def accept(world, monkeypatch, suite, controls=(BORROW,), title='Menu'):
    original = type(world).target
    def target(self, t, c, **override):
        with self.db.write() as s:
            row = self.store.put_json(s, project_id=t.project_id, kind='report', name='suite.json',
                                      document=suite.model_dump())
        contract = UiContract.model_validate({'controls': list(controls)}).model_dump()
        return original(self, t, c, suite_artifact_id=row.id, runner_manifest_digest=suite.digest,
                        ui_contract=contract, **override)
    original_proof = type(world).proof
    def proof(self, t, c, target, **overrides):
        v, smoke = original_proof(self, t, c, target, **overrides)
        with self.db.write() as s:
            row = Verification(**{column.name: getattr(v, column.name) for column in Verification.__table__.columns
                                  if column.name not in ('id', 'created_at')})
            s.add(row)
            row.evidence_id += '-journeys'
            row.suite_digest = suite.digest
            row.expected_test_ids = [test.id for test in suite.tests]
            row.counts = dict(discovered=len(suite.tests), executed=len(suite.tests),
                              passed=len(suite.tests), failed=0, skipped=0)
            row.uac_coverage = {'UAC-1': row.expected_test_ids}
            row.results = {**row.results, 'executed_test_ids': row.expected_test_ids}
            s.flush()
        return row, smoke
    monkeypatch.setattr(type(world), 'proof', proof)
    monkeypatch.setattr(type(world), 'target', target)
    try:
        accepted, candidate = world.accepted(world.approve(world.new({**SCOPE, 'title': title})))
        with world.db.read() as s:
            return accepted, s.get(Candidate, candidate.id)
    finally:
        monkeypatch.setattr(type(world), 'target', original)
        monkeypatch.setattr(type(world), 'proof', original_proof)


class Harness:
    def __init__(self, candidate_fail=(), base_fail=(), broken=False):
        self.candidate_fail, self.base_fail, self.broken, self.runs = set(candidate_fail), set(base_fail), broken, []

    def run(self, ctx, site, target_digest, suite, node_image, *, expected_runner, diagnostics=True,
            legacy_test_ids=()):
        self.runs.append((site, [t.id for t in suite.tests]))
        if self.broken:
            return {'status': 'incomplete', 'infrastructure_failure': True, 'error': 'runner crashed'}
        failing = self.base_fail if site == 'base-site' else self.candidate_fail
        rows = [{'id': t.id, 'uac': t.uac, 'status': 'failed' if t.id in failing else 'passed',
                 **({'error': 'Pinjam button missing'} if t.id in failing else {})} for t in suite.tests]
        failed = sum(r['status'] == 'failed' for r in rows)
        counts = dict(discovered=len(rows), executed=len(rows), passed=len(rows)-failed, failed=failed, skipped=0)
        return {'status': 'failed' if failed else 'passed', 'runner': expected_runner,
                'infrastructure_failure': False, 'invocation_id': 'inv', 'counts': counts,
                'exit_code': 0 if not failed else 1,
                'commands': [{'argv': ['isolated-browser-runner', 'inv'], 'exit_code': 0 if not failed else 1}],
                'report': {'schema': 1, 'invocation_id': 'inv', 'target_digest': target_digest,
                           'suite_digest': suite.digest, 'smoke_passed': True, **counts, 'tests': rows}}


def gate(world, ticket, harness, contract_controls=(BORROW,), baseline=None):
    rt = object.__new__(PipelineRuntime)
    rt.db, rt.store = world.db, world.store
    rt.workspace = SimpleNamespace(harness=harness)
    logs = []
    target = {'runner': {}, 'node_image_id': 'node',
              'ui_contract': UiContract.model_validate({'controls': list(contract_controls)}).model_dump()}
    candidate = SimpleNamespace(target_digest='d' * 64, base_sha='b' * 40)
    result = rt._regression_gate(SimpleNamespace(log=logs.append), {'ticket_id': ticket.id}, candidate, target,
                                 'candidate-site', baseline or {'status': 'built', 'site': 'base-site', 'build_digest': 'b' * 64})
    return result, logs


def test_no_accepted_ticket_means_no_gate(world):
    t = world.approve(world.new(SCOPE))
    assert gate(world, t, Harness())[0] is None


def test_accepted_journeys_run_on_every_later_candidate(world, monkeypatch):
    first, _ = accept(world, monkeypatch, journeys('borrow-book', 'return-book'))
    later = world.approve(world.new({**SCOPE, 'title': 'Laporan'}))
    harness = Harness()
    result, _ = gate(world, later, harness)
    assert result['status'] == 'passed'
    assert result['test_ids'] == [f'reg-t{first.number}-borrow-book', f'reg-t{first.number}-return-book']
    assert result['entries'][0]['ticket_id'] == first.id and harness.runs[0][0] == 'candidate-site'


def test_candidate_regression_blocks_with_owner_and_error(world, monkeypatch):
    first, _ = accept(world, monkeypatch, journeys('borrow-book'))
    later = world.approve(world.new({**SCOPE, 'title': 'Laporan'}))
    broken = f'reg-t{first.number}-borrow-book'
    result, _ = gate(world, later, Harness(candidate_fail={broken}))
    assert result['status'] == 'failed' and result['failed'] == [broken]
    assert result['owners'][broken] == {'ticket_id': first.id, 'number': first.number, 'test_id': 'borrow-book'}
    assert result['errors'][broken] == 'Pinjam button missing'


def test_changed_controls_and_base_failures_do_not_grant_automatic_waivers(world, monkeypatch):
    first, _ = accept(world, monkeypatch, journeys('borrow-book', 'return-book'))
    later = world.approve(world.new({**SCOPE, 'title': 'Laporan'}))
    stale, renamed = f'reg-t{first.number}-borrow-book', f'reg-t{first.number}-return-book'
    # Also failing on the accepted base: not this candidate's regression.
    result, logs = gate(world, later, Harness(candidate_fail={stale}, base_fail={stale}))
    assert result['status'] == 'failed' and result['base_failures'] == [stale] and logs
    # This ticket intentionally renamed the accepted control the journey uses (UAC-driven wording).
    result, _ = gate(world, later, Harness(candidate_fail={renamed}), contract_controls=[{**BORROW, 'name': 'Pinjamkan'}])
    assert result['status'] == 'failed' and result['changed_control_test_ids'] == [renamed]


def test_incomplete_regression_is_infrastructure_not_a_pass(world, monkeypatch):
    accept(world, monkeypatch, journeys('borrow-book'))
    later = world.approve(world.new({**SCOPE, 'title': 'Laporan'}))
    result, _ = gate(world, later, Harness(broken=True))
    assert result['status'] == 'incomplete' and 'runner crashed' in result['error']


def test_all_accepted_journeys_are_kept_past_sixty():
    entries = [{'ticket_id': f'ticket-{n}', 'number': n, 'legacy': True,
                'tests': journeys(*(f'journey-{i}' for i in range(24))).tests} for n in range(3)]
    suite, legacy, owners = regression_suite(entries)
    assert len(suite.tests) == len(legacy) == len(owners) == 72
    assert suite.tests[-1].id == 'reg-t2-journey-23'


@pytest.mark.parametrize('damage', ['target_missing', 'suite_missing', 'suite_identity', 'evidence_missing'])
def test_unavailable_or_mismatched_accepted_sources_cannot_silently_remove_gate(world, monkeypatch, damage):
    import json
    from app.persistence.models import Artifact
    first, candidate = accept(world, monkeypatch, journeys('borrow-book'))
    later = world.approve(world.new({**SCOPE, 'title': 'Laporan'}))
    with world.db.write() as s:
        current = s.get(Candidate, candidate.id)
        target = json.loads(world.store.read_bytes(s, current.target_artifact_id))
        if damage == 'suite_identity':
            read = world.store.read_bytes
            def wrong_suite(session, artifact_id):
                if artifact_id == target['suite_artifact_id']:
                    return json.dumps(journeys('other-journey').model_dump()).encode()
                return read(session, artifact_id)
            monkeypatch.setattr(world.store, 'read_bytes', wrong_suite)
        else:
            artifact_id = (current.target_artifact_id if damage == 'target_missing' else
                           target['suite_artifact_id'] if damage == 'suite_missing' else
                           s.get(Verification, current.preview['verification_id']).evidence_artifact_ids[0])
            artifact = s.get(Artifact, artifact_id)
            artifact.availability, artifact.unavailable_reason = 'unavailable', 'missing'
    harness = Harness()
    result, _ = gate(world, later, harness)
    assert result['status'] == 'incomplete' and result['error'] and not harness.runs


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'wrong_digest', 'skipped', 'contradiction', 'smoke', 'commands', 'infrastructure', 'invocation'])
def test_incomplete_or_contradictory_runner_report_cannot_pass(world, monkeypatch, damage):
    accept(world, monkeypatch, journeys('borrow-book'))
    later = world.approve(world.new({**SCOPE, 'title': 'Laporan'}))
    harness = Harness()
    run = harness.run
    def corrupt(*args, **kwargs):
        proof = run(*args, **kwargs)
        if damage == 'missing':
            proof['report']['tests'] = []
        elif damage == 'duplicate':
            proof['report']['tests'] *= 2
        elif damage == 'wrong_digest':
            proof['report']['suite_digest'] = '0' * 64
        elif damage == 'skipped':
            proof['report']['skipped'] = 1
        elif damage == 'smoke':
            proof['report']['smoke_passed'] = 1
        elif damage == 'commands':
            proof['commands'] = []
        elif damage == 'infrastructure':
            proof.pop('infrastructure_failure')
        elif damage == 'invocation':
            proof['invocation_id'] = proof['report']['invocation_id'] = ''
        else:
            proof['status'] = 'failed'
        return proof
    harness.run = corrupt
    result, _ = gate(world, later, harness)
    assert result['status'] == 'incomplete'


def test_regression_evidence_pins_full_runner_report_suite_and_sources(world, monkeypatch):
    first, candidate = accept(world, monkeypatch, journeys('borrow-book'))
    later = world.approve(world.new({**SCOPE, 'title': 'Laporan'}))
    result, _ = gate(world, later, Harness())
    assert result['execution']['report']['tests'][0]['id'] == result['test_ids'][0]
    assert result['suite']['tests'][0]['id'] == result['test_ids'][0]
    assert result['suite_digest'] == result['execution']['report']['suite_digest']
    entry = result['entries'][0]
    assert entry['target_artifact_id'] == candidate.target_artifact_id
    assert entry['suite_artifact_id'] and entry['verification_id'] and entry['evidence_artifact_ids']


@pytest.mark.parametrize('missing_waiver_evidence', [False, True])
def test_only_a_valid_persisted_user_decision_excludes_accepted_journeys(world, missing_waiver_evidence):
    from app.persistence.models import Artifact
    from .test_qa_manual_resolution import inconclusive, decide
    first, candidate, verification, args = inconclusive(world)
    first = decide(world, first, args)
    with world.db.read() as s:
        ids = s.get(Candidate, candidate.id).evidence_artifact_ids
    operation = world.w.accept_uat(world.user, first.id, first.revision, candidate.id, 1,
                                  args['target_artifact_id'], args['target_digest'], verification.id, ids, ['UAC-1'])
    world.w.finish_integration(world.actor('integrator'), first.id, world.ticket(first.id).revision,
                               candidate.id, operation['operation_id'], candidate.commit_sha)
    if missing_waiver_evidence:
        with world.db.write() as s:
            row = s.get(Artifact, args['diagnosis_artifact_id'])
            row.availability, row.unavailable_reason = 'unavailable', 'missing'
    later = world.approve(world.new({**SCOPE, 'title': 'Laporan'}))
    result, _ = gate(world, later, Harness())
    if missing_waiver_evidence:
        assert result['status'] == 'incomplete'
    else:
        assert result['status'] == 'waived' and result['test_ids'] == []
        assert result['entries'][0]['excluded_test_ids'] == ['feature-add']
        assert result['entries'][0]['waiver_id'] and result['entries'][0]['waiver_evidence_artifact_ids']


def test_base_diagnosis_pins_build_bytes_even_when_sha_is_unchanged(world, monkeypatch, tmp_path):
    from app.pipeline.contracts import digest_of
    first, _ = accept(world, monkeypatch, journeys('borrow-book'))
    later = world.approve(world.new({**SCOPE, 'title': 'Laporan'}))
    baseline_site = tmp_path / 'baseline'
    baseline_site.mkdir()
    page = baseline_site / 'index.html'
    page.write_text('<button>Pinjam</button>')
    test_id = f'reg-t{first.number}-borrow-book'
    baseline = {'status': 'built', 'site': baseline_site}
    before, _ = gate(world, later, Harness(candidate_fail={test_id}), baseline=baseline)
    page.write_text('<button>Pinjamkan</button>')
    after, _ = gate(world, later, Harness(candidate_fail={test_id}), baseline=baseline)
    first_target, second_target = before['base_execution']['target'], after['base_execution']['target']
    assert first_target['base_sha'] == second_target['base_sha']
    assert first_target['build_digest'] != second_target['build_digest']
    assert before['base_execution']['execution']['report']['target_digest'] == digest_of(first_target)
    assert after['base_execution']['execution']['report']['target_digest'] == digest_of(second_target)
    assert before['status'] == after['status'] == 'failed'
