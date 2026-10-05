"""Per-failure baseline matching: changed same-count failure must never be waived."""
import pytest
from pathlib import Path
from types import SimpleNamespace

pytest.importorskip('fcntl')  # the pipeline runtime imports the POSIX workspace package
from app.pipeline.gates import node_gate
from app.pipeline.runtime import PipelineRuntime
from app.domain import Actor
from tests.domain.conftest import world, db, db_path, store  # noqa: F401


def tap(names, failed, diagnostic='old bug'):
    lines = ['TAP version 13']
    for i, name in enumerate(names, 1):
        lines.append(f'# Subtest: {name}')
        lines.append(f'{"not ok" if name in failed else "ok"} {i} - {name}')
        if name in failed:
            lines += ['  ---', '  duration_ms: 4', f'  error: {diagnostic}', '  ...']
    lines += [f'# tests {len(names)}', f'# pass {len(names)-len(failed)}', f'# fail {len(failed)}',
              '# cancelled 0', '# skipped 0', '# todo 0']
    return ('\n'.join(lines) + '\n').encode()


def test_baseline_waiver_per_test_allows_new_green_tests_but_rejects_new_same_count_failures(world):
    t = world.approve(world.new())
    baseline = node_gate(tap(['old', 'other'], {'old', 'other'}), b'', 1)
    runtime = PipelineRuntime.__new__(PipelineRuntime)
    runtime.db, runtime.workflow = world.db, world.w
    identity = {'ticket_id': t.id, 'scope_version': t.current_version, 'project_id': t.project_id}
    with world.db.read() as s:
        from app.persistence.models import Project
        base = s.get(Project, t.project_id).workflow['accepted_tip']
    candidate = SimpleNamespace(base_sha=base)
    assert runtime.gate_admission(identity, candidate, {'gate': {**baseline, 'environment_digest': 'd'*64}})['status'] == 'failed'
    for failure in baseline['failures']:
        with world.db.write() as s:
            fp = world.store.put_json(s, project_id=t.project_id, kind='report', name='baseline-failure.json',
                document={'kind': 'baseline_failure', 'ticket_id': t.id, 'scope_version': 1, 'base_sha': base,
                    'category': 'baseline', 'uac_ids': [], 'infrastructure_failure': False,
                    'environment': {'fixture': 'contract'}, 'environment_digest': 'd'*64, **failure},
                meta={'producer': 'verification'})
        world.w.waive_baseline(world.user, t.id, world.ticket(t.id).revision, fp.id, 'known pre-existing failure')
    def admission(names, failed, diagnostic='old bug', **extra):
        gate = {**node_gate(tap(names, failed, diagnostic), b'', 1), 'environment_digest': 'd'*64, **extra}
        return runtime.gate_admission(identity, candidate, {'gate': gate})
    assert admission(['green-new', 'old', 'other'], {'old', 'other'})['status'] == 'waived'
    assert admission(['old', 'replacement'], {'old', 'replacement'})['status'] == 'failed'
    assert admission(['old', 'other'], {'old', 'other'}, 'changed error')['status'] == 'failed'
    assert admission(['old', 'other'], {'old', 'other'}, infrastructure_failure=True)['status'] == 'failed'
    assert admission(['old', 'other'], {'old', 'other'}, environment_digest='e'*64)['status'] == 'failed'


FIXTURES = Path(__file__).parent / 'fixtures'


def real_gate(name, replace=None):
    """Output of the real `node --test --test-reporter=tap` (node 22.20.0) for three failing tests."""
    text = (FIXTURES / name).read_text()
    for old, new in (replace or {}).items():
        assert old in text
        text = text.replace(old, new)
    return node_gate(text.encode(), b'', 1)


def failures(gate):
    return {f['test_id']: f['signature'] for f in gate['failures']}


def test_real_node_tap_failure_identity_survives_a_test_added_above_but_not_a_changed_failure():
    """DEV-013 review: node prints each test's file:line:column. A feature test added above an old failing test moved
    those lines, changed every fingerprint and silently voided all baseline waivers."""
    before = real_gate('node-tap-before.txt')
    shifted = real_gate('node-tap-green-test-inserted-above.txt')
    assert before['status'] == shifted['status'] == 'failed'
    assert set(failures(before)) == {'node:old failure', 'node:other failure', 'node:async failure'}
    assert failures(before) == failures(shifted)  # same failures, new green test, moved line numbers
    changed = failures(real_gate('node-tap-before.txt', {'2 !== 3': '2 !== 4'}))
    assert changed['node:old failure'] != failures(before)['node:old failure']  # a different failure is a new failure
    assert changed['node:other failure'] == failures(before)['node:other failure']
    other_error = failures(real_gate('node-tap-before.txt', {"error: 'boom x'": "error: 'boom y'"}))
    assert other_error['node:other failure'] != failures(before)['node:other failure']
