"""Minimum Node TAP repository gate. Its output is a gate, never authoritative browser acceptance."""
import re
from .contracts import digest_of


def node_gate(stdout, stderr, exit_code):
    text = stdout.decode(errors='replace')
    tests = re.findall(r'(?m)^(not )?ok \d+ - (.+?)(?: #(SKIP|TODO).*)?$', text)
    ids = ['node:' + name for _, name, _ in tests]
    summaries = {key: int(value) for key, value in re.findall(r'(?m)^# (tests|pass|fail|cancelled|skipped|todo) (\d+)\s*$', text)}
    counts = {'discovered': summaries.get('tests', 0), 'executed': len(tests),
        'passed': summaries.get('pass', 0), 'failed': summaries.get('fail', 0), 'skipped': summaries.get('skipped', 0)}
    complete = (bool(ids) and len(set(ids)) == len(ids) and counts['discovered'] == counts['executed'] and
        counts['passed'] + counts['failed'] == counts['executed'] and counts['skipped'] == 0 and
        summaries.get('cancelled') == summaries.get('todo') == 0)
    failures = ['node:' + name for failed, name, _ in tests if failed]
    status = 'incomplete'
    if complete and len(failures) == counts['failed']:
        if counts['failed'] == 0 and exit_code == 0:
            status = 'passed'
        elif counts['failed'] > 0 and type(exit_code) is int and exit_code > 0:
            status = 'failed'
    # Preserve exact diagnostics; discard only TAP timing values. Changed failures with the same count differ.
    stable = re.sub(r'(?m)^\s*(?:duration_ms:.*|# duration_ms .*)$', '', text)
    signature = digest_of({'stdout': stable, 'stderr': stderr.decode(errors='replace')})
    return {'status': status, 'counts': counts, 'executed_test_ids': ids, 'failed_test_ids': failures,
        'test_id': failures[0] if len(failures) == 1 else 'repo:test', 'signature': signature}
