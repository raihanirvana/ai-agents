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
    # Fingerprint each failed test's diagnostics, independent of unrelated pass counts/test ordinals.
    # Whole-output signature is retained for legacy persisted receipts.
    per_failure = []
    blocks = list(re.finditer(r'(?m)^(not )?ok \d+ - (.+?)(?: #(SKIP|TODO).*)?$', text))
    for i, block in enumerate(blocks):
        if not block.group(1):
            continue
        end = blocks[i + 1].start() if i + 1 < len(blocks) else len(text)
        diagnostic = text[block.end():end]
        diagnostic = re.split(r'(?m)^(?:# (?:Subtest:|tests |suites |pass |fail |cancelled |skipped |todo |duration_ms )|1\.\.\d+)',
                              diagnostic)[0]  # the TAP plan line (1..N) and summary are not part of this failure
        diagnostic = re.sub(r'(?m)^\s*duration_ms:.*$', '', diagnostic).strip()
        # Node prints the test's own position (`location:`) and stack frames as file:line:column. Editing code or
        # adding a test above this one moves them without changing the failure, which would void every waiver.
        diagnostic = re.sub(r'(\.[cm]?[jt]sx?):\d+(?::\d+)?', r'\1:L:C', diagnostic)
        # Node's own scheduler frames (node:internal/...) differ with the test's position in the file and the
        # preceding tests, not with the failure; only the project's frames identify it.
        diagnostic = re.sub(r'(?m)^[ \t]*[^\n]*\(node:[^)\n]*\)[ \t]*\n?', '', diagnostic)
        test_id = 'node:' + block.group(2)
        per_failure.append({'test_id': test_id, 'signature': digest_of({
            'test_id': test_id, 'diagnostic': diagnostic, 'stderr': stderr.decode(errors='replace')})})
    return {'status': status, 'counts': counts, 'executed_test_ids': ids, 'failed_test_ids': failures,
        'test_id': failures[0] if len(failures) == 1 else 'repo:test', 'signature': signature,
        'failures': per_failure}
