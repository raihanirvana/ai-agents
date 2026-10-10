import copy
import json
from app.pipeline.transcript import TranscriptProjection


def exchange(number, name, args, result):
    return [{'role': 'assistant', 'tool_calls': [{'id': str(number), 'type': 'function',
             'function': {'name': 'pipeline_' + name, 'arguments': json.dumps(args)}}]},
            {'role': 'tool', 'tool_call_id': str(number), 'content': json.dumps(result)}]


def test_handle_edits_obsolete_the_correct_file_and_archive_batch_arguments():
    body = {'messages': exchange(1, 'read_file', {'path': 'app.js'},
        {'path': 'app.js', 'digest': 'old', 'read_handle': 'h1', 'offset': 0, 'content': 'Old source ' * 1000}) +
        exchange(2, 'edit_file_batch', {'read_handle': 'h1', 'edits': [
            {'old_text': 'Old source ' * 1000, 'new_text': 'New source ' * 1000}]},
            {'path': 'app.js', 'digest': 'new', 'bytes': 11000}) +
        [{'role': 'user', 'content': 'Keep this decision'}] * 7}
    original = copy.deepcopy(body)
    result = TranscriptProjection(lambda _: None)(body)
    assert body == original
    content = json.dumps(result)
    assert 'Old source ' * 100 not in content and 'New source ' * 100 not in content
    read = json.loads(result['messages'][1]['content'])
    assert read['read_handle'] == 'h1' and read['path'] == 'app.js'
    args = json.loads(result['messages'][2]['tool_calls'][0]['function']['arguments'])
    assert args['read_handle'] == 'h1' and 'Archived executed argument' in args['edits'][0]['new_text']
    assert result['messages'][-1]['content'] == 'Keep this decision'


def test_batch_reads_share_the_working_set_and_keep_handles_when_archived():
    messages = []
    for number in range(5):
        messages += exchange(number, 'read_files', {'files': [{'path': f'{number}.js'}]},
            {'files': [{'path': f'{number}.js', 'offset': 0, 'digest': str(number),
                        'read_handle': f'h{number}', 'content': str(number) * 22000}]})
    messages += [{'role': 'user', 'content': 'Continue'}] * 7
    result = TranscriptProjection(lambda _: None)({'messages': messages})
    results = [json.loads(m['content']) for m in result['messages'] if m['role'] == 'tool']
    retained = sum(len(json.dumps(row)) for row in results if not row.get('archived_tool_result'))
    assert retained <= 64000
    assert results[0]['files'][0]['read_handle'] == 'h0'
    assert 'content' not in results[0]['files'][0]
