"""Deterministic provider projection; Hermes' original transcript remains intact.

Only completed source/check tool exchanges are compacted. Scope, decisions,
feedback, user/system messages and pending exchanges are untouched. Sealed
blocks keep stable receipts; older observations remain available by reread.
"""
import copy
import hashlib
import json

SOURCE = {'read_file', 'read_files', 'write_file', 'edit_file', 'edit_file_batch', 'patch_file', 'inspect_diff'}
WRITES = {'write_file', 'edit_file', 'edit_file_batch', 'patch_file'}


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def decoded(value):
    try:
        return json.loads(value) if isinstance(value, str) else None
    except (ValueError, TypeError):
        return None


BLOCK_EXCHANGES = 8
ARCHIVED_EXCHANGES = 24
ACTIVE_SOURCE_CHARS = 64000
COMPACTABLE = SOURCE | {'run_command', 'run_checks', 'list_files', 'inspect_app'}


def abbreviated_arguments(args):
    result = copy.deepcopy(args)
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('content', 'old_text', 'new_text', 'patch') and isinstance(item, str):
                    value[key] = '[Archived executed argument: sha256=' + digest(item) + ', chars=' + str(len(item)) + ']'
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(result)
    return result


def receipt(name, result, text):
    summary = {'archived_tool_result': True, 'result_sha256': digest(text),
        'original_chars': len(text), 'tool': name,
        'note': 'Executed observation archived; reread a bounded path/range for current contents before editing.'}
    for key in ('path', 'digest', 'bytes', 'deleted', 'operation', 'offset', 'next_offset',
                'truncated', 'total_chars', 'exit_code', 'status', 'read_handle', 'unchanged_read'):
        if key in result:
            summary[key] = result[key]
    if name == 'read_files':
        summary['files'] = [{k: row[k] for k in ('path', 'digest', 'read_handle', 'offset',
            'next_offset', 'truncated', 'total_chars', 'unchanged_read') if k in row}
            for row in result.get('files', [])]
    if name in ('run_command', 'run_checks'):
        gate = result.get('repository_gate') or {}
        summary['repository_gate'] = {k: gate[k] for k in
            ('status', 'counts', 'failed_test_ids', 'executed_test_ids') if k in gate}
    if name == 'run_checks':
        summary['artifact_id'] = result.get('artifact_id')
        summary['phases'] = [{k: phase[k] for k in ('phase', 'status', 'exit_code',
            'repository_gate', 'cache_hit', 'error_excerpt', 'missing_testids') if k in phase}
            for phase in result.get('phases', []) if isinstance(phase, dict)]
    if result.get('error') or result.get('exit_code', 0) != 0:
        # Keep explicit failure facts; compression never turns a failure into success.
        summary['error'] = str(result.get('error', ''))[:2400]
        summary['failure_excerpt'] = (str(result.get('stderr', '')) + '\n' + str(result.get('stdout', '')))[:2400]
    return summary


def rollover_state(units, messages):
    """Deterministic state from retired observations; never copy source as code.

    Approved scope, decisions and user feedback are retained separately, verbatim.
    This checkpoint does not authorize edits or certify that old checks cover new edits.
    """
    files, checks = {}, None
    for message in messages:
        if message.get('role') != 'assistant':
            continue
        earlier = decoded(message.get('content'))
        state = earlier.get('checkpoint_state') if isinstance(earlier, dict) and earlier.get('archived_execution_history') is True else None
        if not isinstance(state, dict):
            continue
        observations = state.get('file_observations', [])
        for row in observations[:32] if isinstance(observations, list) else []:
            if isinstance(row, dict) and isinstance(row.get('path'), str):
                files[row['path']] = {k: row[k] for k in ('digest', 'deleted') if k in row}
        checks = state.get('last_checks') or checks
    for _, _, calls, rows in units:
        for call in calls:
            name = call['function']['name'].removeprefix('pipeline_')
            result = decoded(rows[call['id']].get('content')) or {}
            arguments = decoded(call['function'].get('arguments'))
            if not isinstance(arguments, dict):
                arguments = {}
            observed = result.get('files', []) if name == 'read_files' else [result]
            for item in observed if isinstance(observed, list) else []:
                if not isinstance(item, dict):
                    continue
                path = item.get('path') or arguments.get('path')
                if isinstance(path, str) and ('digest' in item or item.get('deleted')):
                    files.pop(path, None)
                    files[path] = {k: item[k] for k in ('digest', 'deleted') if k in item}
                    while len(files) > 32:
                        files.pop(next(iter(files)))
            if name == 'run_checks' or name == 'run_command' and arguments.get('phase') == 'test':
                checks = receipt(name, result, rows[call['id']].get('content') or '')
    file_rows, chars = [], 0
    for path, observed in reversed(list(files.items())):
        row = {'path': path, **observed}
        encoded = json.dumps(row)
        if chars + len(encoded) <= 6000:
            file_rows.append(row)
            chars += len(encoded)
    scope_prompt = next((m.get('content') for m in messages if m.get('role') == 'user'), '')
    return {'initial_prompt_sha256': digest(scope_prompt) if isinstance(scope_prompt, str) else None,
        'file_observations': list(reversed(file_rows)), 'last_checks': checks,
        'scope_and_feedback': 'Original user/system/decision messages remain in this context.',
        'note': 'File digests/checks describe earlier observations only. Reread before edits; '
                'checks must be rerun after changes. No budget, lease, approval or candidate resets.'}


class TranscriptProjection:
    """Bound source history; sealed blocks change only at fixed exchange boundaries.

    Original Hermes messages stay intact. User/system/decision and incomplete
    tool exchanges are always kept. Only completed observation/command groups
    can age into receipts and then a deterministic historical digest.
    """
    def __init__(self, log):
        self.log = log

    def __call__(self, body, *, rollover=False):
        messages = body.get('messages')
        if not isinstance(messages, list):
            return body
        original = json.dumps(messages, ensure_ascii=False)
        projected = copy.deepcopy(messages)
        units, idx = [], 0
        while idx < len(projected):
            message = projected[idx]
            calls = message.get('tool_calls') if message.get('role') == 'assistant' else None
            if not calls or any((c.get('function') or {}).get('name', '').removeprefix('pipeline_')
                                not in COMPACTABLE for c in calls):
                idx += 1
                continue
            end = idx + 1
            while end < len(projected) and projected[end].get('role') == 'tool':
                end += 1
            rows = {m.get('tool_call_id'): m for m in projected[idx+1:end]}
            ids = [c.get('id') for c in calls]
            if (len(rows) != len(calls) or len(set(ids)) != len(ids) or set(ids) != set(rows)
                    or any(not isinstance(decoded(rows[cid].get('content')), dict) for cid in ids)):
                idx = end
                continue  # Pending/malformed exchanges are never sliced or rewritten.
            units.append((idx, end, calls, rows))
            idx = end
        # Keep one open block, rather than moving a six-message boundary each call.
        sealed = max(0, (len(units)-1) // BLOCK_EXCHANGES * BLOCK_EXCHANGES)
        last_anchor = max((i for i, m in enumerate(projected) if m.get('role') in
                           ('system', 'developer', 'user')), default=-1)
        while sealed < len(units) and units[sealed][1] <= last_anchor:
            sealed += 1
        if rollover:
            sealed = len(units)  # A finished Hermes turn has no active read window.
        forgotten = max(0, sealed-ARCHIVED_EXCHANGES)
        removed, historical, compacted, active_chars = set(), [], 0, 0
        if rollover:
            for number, message in enumerate(projected):
                earlier = decoded(message.get('content')) if message.get('role') == 'assistant' else None
                if isinstance(earlier, dict) and earlier.get('archived_execution_history') is True and isinstance(earlier.get('checkpoint_state'), dict):
                    historical.append(digest(message['content']))
                    removed.add(number)
        full_reads = set()
        for number in range(len(units)-1, sealed-1, -1):
            for call in reversed(units[number][2]):
                name = call['function']['name'].removeprefix('pipeline_')
                row = units[number][3][call['id']]
                if name in ('read_file', 'read_files'):
                    size = len(row.get('content') or '')
                    if active_chars + size <= ACTIVE_SOURCE_CHARS:
                        full_reads.add(call['id'])
                        active_chars += size
        # Original completed groups provide an immutable digest; no rolling
        # latest-read/write analysis rewrites the middle of the sealed prefix.
        for number, (start, end, calls, rows) in enumerate(units):
            if number < forgotten:
                historical.append(digest(json.dumps(messages[start:end], ensure_ascii=False)))
                removed.update(range(start, end))
                continue
            for call in calls:
                function = call['function']
                name = function['name'].removeprefix('pipeline_')
                row = rows[call['id']]
                content = row.get('content') or ''
                result, args = decoded(content), decoded(function.get('arguments'))
                archive = number < sealed or (name in WRITES and not result.get('error'))
                if not archive and name in ('read_file', 'read_files'):
                    archive = call['id'] not in full_reads
                if archive:
                    row['content'] = json.dumps(receipt(name, result, content), ensure_ascii=False)
                    if name in WRITES and isinstance(args, dict):
                        function['arguments'] = json.dumps(abbreviated_arguments(args), ensure_ascii=False)
                    compacted += 1
            if number < sealed:
                text = projected[start].get('content')
                if isinstance(text, str) and len(text) > 600:
                    projected[start]['content'] = text[:600] + ' [Earlier reasoning archived: sha256=' + digest(text) + ']'
                projected[start].pop('reasoning_content', None)
        ledger = None
        if historical:
            ledger = {'role': 'assistant', 'content': json.dumps({
                'archived_execution_history': True, 'completed_exchanges': forgotten,
                'provider_segment': forgotten // BLOCK_EXCHANGES,
                'history_sha256': digest(json.dumps(historical)),
                'checkpoint_state': rollover_state(units[:forgotten], messages),
                'note': 'Older completed source/check observations retained in original runtime diagnostics. '
                        'This digest is not source, approval or QA evidence; reread source or inspect logs when needed.'})}
        output, stable_prefix = [], []
        active_start = units[sealed][0] if sealed < len(units) else len(projected)
        for index, message in enumerate(projected):
            if index in removed:
                if ledger is not None:
                    output.append(ledger)
                    if index < active_start:
                        stable_prefix.append(ledger)
                    ledger = None
                continue
            output.append(message)
            if index < active_start:
                stable_prefix.append(message)
        encoded = json.dumps(output, ensure_ascii=False)
        if len(encoded) >= len(original):
            return body
        self.log('context.projection ' + json.dumps({'original_chars': len(original),
            'projected_chars': len(encoded), 'compacted_exchanges': compacted,
            'active_read_chars': active_chars, 'archived_exchanges': forgotten,
            'sealed_exchanges': sealed, 'block_exchanges': BLOCK_EXCHANGES,
            'provider_segment': forgotten // BLOCK_EXCHANGES,
            'turn_rollover': rollover,
            'sealed_prefix_sha256': digest(json.dumps(stable_prefix, ensure_ascii=False)),
            'original_sha256': digest(original), 'projected_sha256': digest(encoded)}))
        return {**body, 'messages': output}
