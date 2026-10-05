from app.pipeline.gates import node_gate
from pathlib import Path
import pytest

PASS = b'TAP version 13\n# Subtest: total\nok 1 - total\n# tests 1\n# pass 1\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n'


def test_empty_repo_script_cannot_satisfy_a_required_gate():
    assert node_gate(b'', b'', 0)['status'] == 'incomplete'
    assert node_gate(PASS, b'', 0)['status'] == 'passed'


def test_repo_skipped_or_inconsistent_counts_are_incomplete_even_exit_zero():
    assert node_gate(PASS.replace(b'# skipped 0', b'# skipped 1'), b'', 0)['status'] == 'incomplete'
    assert node_gate(PASS.replace(b'# tests 1', b'# tests 0'), b'', 0)['status'] == 'incomplete'
    assert node_gate(PASS, b'', 1)['status'] == 'incomplete'


def test_failure_identity_changes_even_when_total_failure_count_is_the_same():
    failed = PASS.replace(b'ok 1', b'not ok 1').replace(b'# pass 1', b'# pass 0').replace(b'# fail 0', b'# fail 1')
    old = node_gate(failed + b'  error: old bug\n', b'', 1)
    new = node_gate(failed + b'  error: new bug\n', b'', 1)
    assert old['status'] == new['status'] == 'failed'
    assert old['test_id'] == 'node:total' and old['signature'] != new['signature']


def suite_output():
    # Recorded npm test output, Node 22.20.0, from the demo candidate rejected as 3 tests / 1 result.
    return (Path(__file__).parent / 'fixtures/node-tap-suite-passed.txt').read_bytes()


def test_real_node_suite_counts_each_child_test_without_counting_the_suite():
    gate = node_gate(suite_output(), b'', 0)
    assert gate['status'] == 'passed'
    assert gate['counts'] == {'discovered': 3, 'executed': 3, 'passed': 3, 'failed': 0, 'skipped': 0}
    assert gate['executed_test_ids'] == [
        'node:Shopping List Persistence > should save and restore items to localStorage',
        'node:Shopping List Persistence > should toggle item bought state',
        'node:Shopping List Persistence > should delete items',
    ]


def test_failed_child_is_reported_once_with_the_full_suite_path():
    output = suite_output().replace(b'    ok 2 -', b'    not ok 2 -').replace(
        b'\nok 1 - Shopping', b'\nnot ok 1 - Shopping').replace(b'# pass 3', b'# pass 2').replace(b'# fail 0', b'# fail 1')
    gate = node_gate(output, b'', 1)
    assert gate['status'] == 'failed'
    assert gate['counts']['executed'] == 3
    assert gate['failed_test_ids'] == ['node:Shopping List Persistence > should toggle item bought state']
    assert [failure['test_id'] for failure in gate['failures']] == gate['failed_test_ids']


@pytest.mark.parametrize('old,new', [
    (b'    ok 1 - should save and restore items to localStorage',
     b'    ok 1 - should save and restore items to localStorage # SKIP disabled'),
    (b'\nok 1 - Shopping List Persistence', b'\nok 1 - Shopping List Persistence # TODO later'),
    (b'# tests 3', b'# tests 3\n# tests 3'),
    (b'# suites 1', b'# suites 0'),
    (b'# pass 3', b'# pass 2'),
    (b'should toggle item bought state', b'should delete items'),
])
def test_nested_skipped_todo_duplicate_and_inconsistent_evidence_stays_incomplete(old, new):
    assert node_gate(suite_output().replace(old, new), b'', 0)['status'] == 'incomplete'


def test_nested_success_with_nonzero_exit_is_not_passed():
    assert node_gate(suite_output(), b'', 1)['status'] == 'incomplete'


def test_nested_suite_hook_failure_cannot_be_hidden_by_passing_tests():
    output = suite_output().replace(b'\nok 1 - Shopping', b'\nnot ok 1 - Shopping')
    assert node_gate(output, b'', 1)['status'] == 'incomplete'


def test_same_child_names_in_different_suites_have_distinct_ids():
    body = suite_output().split(b'TAP version 13\n')[1].split(b'\n1..1\n# tests')[0]
    output = b'TAP version 13\n' + body + b'\n' + body.replace(b'Shopping List Persistence', b'Other suite') + (
        b'\n1..2\n# tests 6\n# suites 2\n# pass 6\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n')
    gate = node_gate(output, b'', 0)
    assert gate['status'] == 'passed'
    assert len(set(gate['executed_test_ids'])) == gate['counts']['executed'] == 6


def test_multiple_suite_levels_preserve_the_full_test_identity():
    body = suite_output().split(b'TAP version 13\n')[1].split(b'\n1..1\n# tests')[0]
    indented = b'\n'.join(b'    ' + line for line in body.splitlines())
    output = b'TAP version 13\n# Subtest: Outer\n' + indented + (
        b'\n    1..1\nok 1 - Outer\n  ---\n  type: \'suite\'\n  ...\n'
        b'1..1\n# tests 3\n# suites 2\n# pass 3\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n')
    gate = node_gate(output, b'', 0)
    assert gate['status'] == 'passed'
    assert all(name.startswith('node:Outer > Shopping List Persistence > ') for name in gate['executed_test_ids'])


def test_echo_success_remains_incomplete():
    assert node_gate(b'Tests passing\n', b'', 0)['status'] == 'incomplete'
