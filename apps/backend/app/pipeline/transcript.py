"""Deterministic provider projection; Hermes' original transcript remains intact.

Only completed source/check tool exchanges are compacted. Scope, decisions,
feedback, user/system messages and pending/recent exchanges are untouched.
Older source observations outside a bounded working set are available by reread.
"""
import copy
import hashlib
import json

SOURCE = {'read_file', 'write_file', 'edit_file', 'patch_file', 'inspect_diff'}
WRITES = {'write_file', 'edit_file', 'patch_file'}


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def decoded(value):
    try:
        return json.loads(value) if isinstance(value, str) else None
    except (ValueError, TypeError):
        return None


class TranscriptProjection:
    def __init__(self, log):
        self.log = log

    def __call__(self, body):
        messages = body.get('messages')
        if not isinstance(messages, list):
            return body
        original = json.dumps(messages, ensure_ascii=False)
        projected = copy.deepcopy(messages)
        results = {m.get('tool_call_id'): (idx, m, decoded(m.get('content')))
                   for idx, m in enumerate(projected) if m.get('role') == 'tool' and m.get('tool_call_id')}
        exchanges, latest_write, latest_read, latest_command = [], {}, {}, {}
        for idx, message in enumerate(projected):
            if message.get('role') != 'assistant':
                continue
            for call in message.get('tool_calls') or []:
                f = call.get('function') or {}
                name = f.get('name', '').removeprefix('pipeline_')
                args = decoded(f.get('arguments'))
                found = results.get(call.get('id'))
                if not isinstance(args, dict) or not found or not isinstance(found[2], dict):
                    continue
                ri, rm, result = found
                if ri <= idx:
                    continue
                path = args.get('path')
                exchanges.append((idx, ri, call, name, args, rm, result))
                success = not result.get('error') and result.get('exit_code', 0) == 0
                if success and isinstance(path, str):
                    if name in WRITES:
                        latest_write[path] = idx
                    if name == 'read_file' and not result.get('unchanged_read'):
                        latest_read[(path, args.get('offset', 0))] = idx
                if name == 'run_command':
                    latest_command[args.get('phase')] = idx
        compacted = 0
        active_reads, active_chars = set(), 0
        for idx, ri, call, name, args, message, result in reversed(exchanges):
            if name != 'read_file' or result.get('error') or result.get('unchanged_read'):
                continue
            path = args.get('path')
            if idx < latest_write.get(path, -1) or idx < latest_read.get((path, args.get('offset', 0)), idx):
                continue
            size = len(message.get('content') or '')
            if active_chars + size <= 64000:
                active_reads.add(call.get('id'))
                active_chars += size
        for idx, ri, call, name, args, message, result in exchanges:
            # Pending calls, decision tools and recent results never lose detail.
            if ri >= len(projected) - 6 or result.get('error'):
                continue
            path = args.get('path')
            obsolete_read = (name == 'read_file' and
                (idx < latest_write.get(path, -1) or idx < latest_read.get((path, args.get('offset', 0)), idx)))
            obsolete_write = name in WRITES and idx < latest_write.get(path, idx)
            old_check = name == 'run_command' and idx < latest_command.get(args.get('phase'), idx)
            old_diff = name == 'inspect_diff'
            archived_read = name == 'read_file' and call.get('id') not in active_reads
            archived_write = name in WRITES
            if not (obsolete_read or obsolete_write or old_check or old_diff or archived_read or archived_write):
                continue
            text = message.get('content') or ''
            summary = {'archived_tool_result': True, 'result_sha256': digest(text),
                       'original_chars': len(text), 'tool': name,
                       'note': 'Executed observation archived. Reread path/range for current contents and digest before editing. '
                               'Original retained in runtime diagnostics.'}
            for key in ('path', 'digest', 'bytes', 'deleted', 'operation', 'offset', 'next_offset',
                        'truncated', 'total_chars', 'exit_code', 'status'):
                if key in result:
                    summary[key] = result[key]
            if old_check:
                gate = result.get('repository_gate') or {}
                summary['repository_gate'] = {k: gate[k] for k in
                    ('status', 'counts', 'failed_test_ids', 'executed_test_ids') if k in gate}
                if result.get('exit_code', 0) != 0:
                    lines = (str(result.get('stderr', '')) + '\n' + str(result.get('stdout', ''))).splitlines()
                    errors = [line for line in lines if any(word in line.lower() for word in
                              ('not ok', 'error', 'expected', 'actual', 'not found', 'failed', 'timeout'))]
                    summary['failure_excerpt'] = '\n'.join(errors)[:2400]
            message['content'] = json.dumps(summary, ensure_ascii=False)
            if archived_write:
                abbreviated = dict(args)
                for key in ('content', 'old_text', 'new_text'):
                    value = abbreviated.get(key)
                    if isinstance(value, str):
                        abbreviated[key] = '[Archived executed argument: sha256=' + digest(value) + ', chars=' + str(len(value)) + ']'
                call['function']['arguments'] = json.dumps(abbreviated, ensure_ascii=False)
            compacted += 1
        # Repeated read receipts are pure observations, with a freshly checked
        # digest and no new source content. Drop old completed pairs, keeping
        # recent/pending calls and every mutation/decision exchange intact.
        redundant = {call['id'] for idx, ri, call, name, args, message, result in exchanges
                     if name == 'read_file' and result.get('unchanged_read')
                     and not result.get('error') and ri < len(projected) - 6}
        filtered = []
        for message in projected:
            if message.get('role') == 'tool' and message.get('tool_call_id') in redundant:
                continue
            if message.get('role') == 'assistant' and message.get('tool_calls'):
                kept = [call for call in message['tool_calls'] if call.get('id') not in redundant]
                if not kept and not message.get('content'):
                    continue
                if kept:
                    message['tool_calls'] = kept
                else:
                    message.pop('tool_calls', None)
            filtered.append(message)
        projected = filtered
        encoded = json.dumps(projected, ensure_ascii=False)
        if len(encoded) >= len(original):
            return body
        self.log('context.projection ' + json.dumps({'original_chars': len(original),
            'projected_chars': len(encoded), 'compacted_exchanges': compacted,
            'active_read_chars': active_chars, 'redundant_read_exchanges_removed': len(redundant),
            'original_sha256': digest(original), 'projected_sha256': digest(encoded)}))
        return {**body, 'messages': projected}
