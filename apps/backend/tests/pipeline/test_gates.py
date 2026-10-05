from app.pipeline.gates import node_gate

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
