"""Deterministic dependency/gate projection; the full pinned diff stays evidence."""
import hashlib
import json
import re
from app.workspace.dependencies import registry_tarballs
from app.workspace.errors import SandboxError


def selector_contract(suite):
    """Keep exact selectors/actions and step indices without schema/default duplication."""
    return {'suite_digest': suite.digest, 'tests': [
        {'id': test.id, 'purpose': test.purpose, 'uac': test.uac,
         'steps': [{'step_index': index, **step.model_dump(exclude_none=True)}
                   for index, step in enumerate(test.steps)]}
        for test in suite.tests]}


def package_facts(package):
    if package is None:
        return None
    if not isinstance(package, dict):
        raise ValueError('invalid dependency package facts')
    canonical = json.dumps(package, sort_keys=True, separators=(',', ':')).encode()
    return {'name': package.get('name'), 'version': package.get('version'), 'sha256': hashlib.sha256(canonical).hexdigest(),
            'dependencies': package.get('dependencies', {}),
            'devDependencies': package.get('devDependencies', {}),
            'optionalDependencies': package.get('optionalDependencies', {}),
            'peerDependencies': package.get('peerDependencies', {}),
            'hasInstallScript': package.get('hasInstallScript', False),
            'dev': package.get('dev', False), 'optional': package.get('optional', False)}


def review_diff(broker, base, head, diff):
    chunks = re.split(r'(?=^diff --git )', diff, flags=re.MULTILINE)
    lock_chunks = [c for c in chunks if c.startswith('diff --git a/package-lock.json b/package-lock.json\n')]
    if not lock_chunks:
        return {'diff': diff, 'dependency_changes': None}
    states = []
    for sha in (base, head):
        raw = broker.read_committed_file(sha, 'package-lock.json')
        try:
            lock = json.loads(raw) if raw is not None else None
            if raw is not None and not isinstance(lock, dict):
                raise ValueError('lock must be an object')
            if lock is not None:
                registry_tarballs(lock)  # Eligibility for summary, not a new runner policy.
                if any(not isinstance(p, dict) for p in lock['packages'].values()):
                    raise ValueError('invalid package facts')
        except (ValueError, SandboxError):
            # Custom/existing offline runners may use other lock formats. Keep
            # their full diff; the installer remains the authority for egress.
            return {'diff': diff, 'dependency_changes': None}
        states.append(lock)
    before, after = states
    bp, ap = (before or {}).get('packages', {}), (after or {}).get('packages', {})
    changes = []
    for path in sorted(bp.keys() | ap.keys()):
        if bp.get(path) != ap.get(path):
            changes.append({'path': path, 'before': package_facts(bp.get(path)),
                            'after': package_facts(ap.get(path))})
    projected = {'diff': ''.join(c for c in chunks if c not in lock_chunks),
            'dependency_changes': {'path': 'package-lock.json', 'lockfile_version': (after or {}).get('lockfileVersion'),
                'registry': 'https://registry.npmjs.org', 'integrity_policy': 'sha512 verified offline installer',
                'removed': after is None, 'before_package_count': len(bp), 'after_package_count': len(ap),
                'changes': changes, 'omitted_lock_diff_sha256': hashlib.sha256(''.join(lock_chunks).encode()).hexdigest(),
                'note': 'Canonical package hashes cover every field, including resolved/integrity/engines. '
                        'Source/test diffs stay verbatim. Full lock diff is retained in pinned evidence.'}}

    if len(json.dumps(projected)) >= len(json.dumps({'diff': diff, 'dependency_changes': None})):
        return {'diff': diff, 'dependency_changes': None}
    return projected


def dependency_manifest_summary(broker, head):
    """Report declared/root equality separately from extra lockfile entries."""
    documents = []
    for path in ('package.json', 'package-lock.json'):
        raw = broker.read_committed_file(head, path)
        if raw is None:
            return {'status': 'unavailable', 'missing': path}
        try:
            documents.append(json.loads(raw))
        except (ValueError, TypeError):
            return {'status': 'unavailable', 'invalid': path}
    package, lock = documents
    if not isinstance(package, dict) or not isinstance(lock, dict) or not isinstance(lock.get('packages'), dict):
        return {'status': 'unavailable', 'reason': 'unsupported package/lock structure'}
    root = lock['packages'].get('')
    if not isinstance(root, dict):
        return {'status': 'unavailable', 'reason': 'lock root missing'}
    sections = ('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies')
    declared = {k: package.get(k, {}) for k in sections}
    locked = {k: root.get(k, {}) for k in sections}
    if any(not isinstance(section, dict) or any(not isinstance(v, str) for v in section.values())
           for section in (*declared.values(), *locked.values())):
        return {'status': 'unavailable', 'reason': 'invalid dependency declarations'}
    return {'status': 'inspected', 'source_sha': head, 'declared': declared, 'lock_root': locked,
            'root_matches': declared == locked, 'lock_package_count': len(lock['packages']) - 1,
            'note': 'Root equality checks declared dependencies only. Extra lock entries alone do not '
                    'prove npm ci incompatibility. Consult the pinned candidate install/build command evidence.'}


def gate_summary(gates):
    command_fields = ('label', 'argv', 'exit_code', 'timed_out', 'cancelled', 'oom_killed', 'truncated',
                      'image_id', 'manifest_digest', 'run_id', 'generation', 'seq',
                      'stdout_sha256', 'stderr_sha256', 'stdout_file_artifact_id', 'stderr_file_artifact_id')

    def command_summary(command):
        return {k: command[k] for k in command_fields if k in command}

    def summarize(value):
        if not isinstance(value, dict):
            return value
        wanted = ('status', 'test_id', 'counts', 'executed_test_ids', 'failed_test_ids',
                  'missing_baseline_tests', 'failures', 'signature', 'environment_digest',
                  'infrastructure_failure', 'exit_code', 'schema')
        result = {k: value[k] for k in wanted if k in value}
        command = value.get('command')
        if isinstance(command, dict):
            result['command'] = command_summary(command)
        return result
    result = {k: summarize(v) for k, v in gates.items() if k in ('gate', 'build', 'baseline', 'status', 'build_error')}
    result['commands'] = [command_summary(command) for command in gates.get('commands', [])
                          if isinstance(command, dict)]
    return result


def gate_failure_summary(gates):
    """Keep actionable failures ahead of command details and large passing ID lists."""
    gate = gate_summary(gates).get('gate') or {}
    fields = ('missing_baseline_tests', 'failed_test_ids', 'failures', 'status',
              'counts', 'exit_code', 'infrastructure_failure', 'signature', 'command')
    return {key: gate[key] for key in fields if key in gate}
