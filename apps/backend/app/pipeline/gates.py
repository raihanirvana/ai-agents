"""Minimum Node TAP repository gate. Its output is a gate, never authoritative browser acceptance."""
import re
from .contracts import digest_of


RESULT = re.compile(r'(?m)^([ ]*)(not )?ok \d+ - (.+?)(?:[ \t]+#[ \t]*(SKIP|TODO)\b.*)?$', re.IGNORECASE)
SUBTEST = re.compile(r'(?m)^([ ]*)# Subtest: (.+)$')


def _tap_results(text):
    """Node emits children before their parent result; suite results are not test executions."""
    blocks = list(RESULT.finditer(text))
    markers = iter(SUBTEST.finditer(text))
    marker = next(markers, None)
    parents, results = [], []
    for index, block in enumerate(blocks):
        indent = len(block.group(1))
        while marker is not None and marker.start() < block.start():
            level = len(marker.group(1))
            parents = [(depth, name) for depth, name in parents if depth < level]
            parents.append((level, marker.group(2)))
            marker = next(markers, None)
        names = [name for depth, name in parents if depth < indent] + [block.group(3)]
        parents = [(depth, name) for depth, name in parents if depth < indent]
        end = blocks[index + 1].start() if index + 1 < len(blocks) else len(text)
        diagnostic = text[block.end():end]
        diagnostic = re.split(r'(?m)^\s*(?:# (?:Subtest:|tests |suites |pass |fail |cancelled |skipped |todo |duration_ms )|1\.\.\d+)',
                              diagnostic)[0]
        kind = re.search(r"(?m)^\s*type: ['\"]?(test|suite)['\"]?\s*$", diagnostic)
        results.append({'id': 'node:' + ' > '.join(names), 'failed': bool(block.group(2)),
            'directive': block.group(4), 'suite': kind is not None and kind.group(1) == 'suite',
            'diagnostic': re.sub(r'(?m)^ {' + str(indent) + '}', '', diagnostic) if indent else diagnostic})
    return results


def node_gate(stdout, stderr, exit_code):
    text = stdout.decode(errors='replace')
    results = _tap_results(text)
    tests = [result for result in results if not result['suite']]
    ids = [result['id'] for result in tests]
    summary_rows = re.findall(r'(?m)^# (tests|suites|pass|fail|cancelled|skipped|todo) (\d+)\s*$', text)
    summaries = {key: int(value) for key, value in summary_rows}
    counts = {'discovered': summaries.get('tests', 0), 'executed': len(tests),
        'passed': summaries.get('pass', 0), 'failed': summaries.get('fail', 0), 'skipped': summaries.get('skipped', 0)}
    complete = (bool(ids) and len(set(ids)) == len(ids) and counts['discovered'] == counts['executed'] and
        counts['passed'] + counts['failed'] == counts['executed'] and counts['skipped'] == 0 and
        summaries.get('cancelled') == summaries.get('todo') == 0 and
        len(summary_rows) == len(summaries) and not any(result['directive'] for result in results) and
        counts['passed'] == sum(not result['failed'] for result in tests) and
        ('suites' not in summaries or summaries['suites'] == sum(result['suite'] for result in results)))
    failures = [result['id'] for result in tests if result['failed']]
    # A failing suite must have a failed test behind it; a broken suite hook is incomplete evidence.
    if any(result['suite'] and result['failed'] and
           not any(test_id.startswith(result['id'] + ' > ') for test_id in failures)
           for result in results):
        complete = False
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
    for result in tests:
        if not result['failed']:
            continue
        diagnostic = result['diagnostic']
        diagnostic = re.sub(r'(?m)^\s*duration_ms:.*$', '', diagnostic).strip()
        # Node prints the test's own position (`location:`) and stack frames as file:line:column. Editing code or
        # adding a test above this one moves them without changing the failure, which would void every waiver.
        diagnostic = re.sub(r'(\.[cm]?[jt]sx?):\d+(?::\d+)?', r'\1:L:C', diagnostic)
        # Node's own scheduler frames (node:internal/...) differ with the test's position in the file and the
        # preceding tests, not with the failure; only the project's frames identify it.
        diagnostic = re.sub(r'(?m)^[ \t]*[^\n]*\(node:[^)\n]*\)[ \t]*\n?', '', diagnostic)
        test_id = result['id']
        per_failure.append({'test_id': test_id, 'signature': digest_of({
            'test_id': test_id, 'diagnostic': diagnostic, 'stderr': stderr.decode(errors='replace')})})
    return {'status': status, 'counts': counts, 'executed_test_ids': ids, 'failed_test_ids': failures,
        'test_id': failures[0] if len(failures) == 1 else 'repo:test', 'signature': signature,
        'failures': per_failure}
