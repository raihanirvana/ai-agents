"""Real supervisor/CAS/filesystem, no Docker or provider required."""
import json
from types import SimpleNamespace
import pytest
from app.agents import Redactor
from app.pipeline.source_tools import SourceTools
from app.workspace import AuthorizationError, PathViolation
from app.workspace import fsutil
from .conftest import start


@pytest.fixture
def source(stub_sup, manifest):
    started = start(stub_sup, manifest)
    stub_sup.write_file(started.ref, started.credential, 'src/app.js', b'first\nsecond\nfirst\n')
    checkpoints = []
    tool = SourceTools(stub_sup, started, Redactor([]), after_write=lambda: checkpoints.append(1) or 'checkpoint')
    return tool, checkpoints


def call(tool, method, args):
    return getattr(tool, method)(None, None, args)


def test_handle_binds_path_digest_and_batch_writes_once(source):
    tool, checkpoints = source
    read = call(tool, 'read', {'path': 'src/app.js'})
    result = call(tool, 'edit_many', {'read_handle': read['read_handle'], 'edits': [
        {'old_text': 'first\nsecond', 'new_text': 'one\nsecond'},
        {'old_text': 'second\nfirst', 'new_text': 'two\nthree'}]})
    assert result['path'] == 'src/app.js'
    assert result['checkpoint_id'] == 'checkpoint' and len(checkpoints) == 1
    assert call(tool, 'read', {'path': 'src/app.js'})['content'] == 'one\ntwo\nthree\n'
    stale = call(tool, 'edit', {'read_handle': read['read_handle'], 'old_text': 'one', 'new_text': 'broken'})
    assert 'changed' in stale['error'] and stale['digest'] == result['digest']
    assert len(checkpoints) == 1


def test_second_edit_invalid_preserves_every_byte_and_reports_match_count(source):
    tool, checkpoints = source
    read = call(tool, 'read', {'path': 'src/app.js'})
    failed = call(tool, 'edit_many', {'read_handle': read['read_handle'], 'edits': [
        {'old_text': 'second', 'new_text': 'changed'}, {'old_text': 'first', 'new_text': 'bad'}]})
    assert failed['matches'] == 2 and failed['edit_index'] == 1
    assert failed['digest'] == read['digest'] and not checkpoints
    assert call(tool, 'read', {'path': 'src/app.js', 'refresh': True})['content'] == read['content']


@pytest.mark.parametrize('edits', [None, [], '', {}, [{'old_text': '', 'new_text': 'invalid'}]])
def test_invalid_batch_never_becomes_a_delete(source, edits):
    tool, checkpoints = source
    read = call(tool, 'read', {'path': 'src/app.js'})
    from app.workspace.errors import WorkspaceError
    with pytest.raises((ValueError, WorkspaceError)):
        call(tool, 'edit_many', {'read_handle': read['read_handle'], 'edits': edits})
    assert not checkpoints
    assert call(tool, 'read', {'path': 'src/app.js', 'refresh': True})['content'] == read['content']


def test_concurrent_edits_with_same_handle_have_only_one_winner(source):
    from concurrent.futures import ThreadPoolExecutor
    tool, checkpoints = source
    read = call(tool, 'read', {'path': 'src/app.js'})
    def replace(value):
        return call(tool, 'edit', {'read_handle': read['read_handle'], 'old_text': 'second', 'new_text': value})
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(replace, ['A', 'B']))
    assert sum('error' not in result for result in outcomes) == 1
    assert sum('error' in result for result in outcomes) == 1
    assert len(checkpoints) == 1
    assert call(tool, 'read', {'path': 'src/app.js'})['content'] in ('first\nA\nfirst\n', 'first\nB\nfirst\n')


def test_read_handle_supports_continuation_without_digest_copy(source):
    tool, _ = source
    read = call(tool, 'read', {'path': 'src/app.js', 'limit': 6})
    rest = call(tool, 'read', {'read_handle': read['read_handle'], 'offset': read['next_offset']})
    assert rest['content'] == 'second\nfirst\n'


@pytest.mark.parametrize('args', [
    {'read_handle': 'unknown'}, {'read_handle': []},
    {'path': 'other.js', 'read_handle': 'valid'}, {'expected_digest': 'bad', 'read_handle': 'valid'}])
def test_invalid_handle_and_conflicting_arguments_are_refused(source, args):
    tool, checkpoints = source
    handle = call(tool, 'read', {'path': 'src/app.js'})['read_handle']
    args = {k: handle if v == 'valid' else v for k, v in args.items()}
    with pytest.raises(ValueError):
        call(tool, 'edit', {**args, 'old_text': 'second', 'new_text': 'changed'})
    assert not checkpoints


def test_handles_are_attempt_local_and_do_not_authorize_revoked_credentials(source):
    tool, _ = source
    handle = call(tool, 'read', {'path': 'src/app.js'})['read_handle']
    other = SourceTools(tool.sup, tool.started, tool.redactor)
    with pytest.raises(ValueError, match='unknown/expired'):
        call(other, 'edit', {'read_handle': handle, 'old_text': 'second', 'new_text': 'changed'})
    tool.started = SimpleNamespace(ref=tool.started.ref, credential='revoked')
    with pytest.raises(AuthorizationError):
        call(tool, 'edit', {'read_handle': handle, 'old_text': 'second', 'new_text': 'changed'})


def test_read_many_is_bounded_and_returns_per_file_handles(source):
    tool, _ = source
    read = call(tool, 'read_many', {'files': [{'path': 'src/app.js'}, {'path': '.'}]})
    assert read['files'][0]['read_handle']
    assert 'src/app.js' in read['files'][1]['content']
    with pytest.raises(ValueError, match='24000'):
        call(tool, 'read_many', {'files': [{'path': 'src/app.js', 'limit': 6000}] * 5})
    with pytest.raises(ValueError):
        call(tool, 'read_many', {'files': []})


@pytest.mark.parametrize('character', ['\x01', '😀'])
def test_json_byte_bound_keeps_continuation_and_content_receipts_correct(source, character):
    tool, _ = source
    for n in range(4):
        tool.sup.write_file(tool.started.ref, tool.started.credential, f'{n}.txt', (character * 6000).encode())
    result = call(tool, 'read_many', {'files': [{'path': f'{n}.txt', 'limit': 6000} for n in range(4)]})
    assert len(json.dumps(result).encode()) <= 60000
    row = next(r for r in result['files'] if r['truncated'])
    content, cursor = row['content'], row
    while cursor['next_offset'] is not None:
        cursor = call(tool, 'read', {'read_handle': row['read_handle'], 'offset': cursor['next_offset'], 'limit': 6000})
        assert len(json.dumps(cursor).encode()) < 65536
        content += cursor['content']
    assert content == character * 6000
    assert 'content' in call(tool, 'read', {'path': row['path'], 'limit': 6000})


@pytest.mark.parametrize('path', ['../escape', '.git/config', 'src/.git/config'])
def test_batch_edits_cannot_escape_snapshot(source, path):
    tool, _ = source
    with pytest.raises(PathViolation):
        call(tool, 'edit_many', {'path': path, 'expected_digest': 'a' * 64,
                                'edits': [{'old_text': 'x', 'new_text': 'y'}]})


def test_failed_atomic_write_preserves_old_file_and_removes_temporary_file(source, monkeypatch):
    tool, _ = source
    read = call(tool, 'read', {'path': 'src/app.js'})
    def failed_write(*_):
        raise OSError('disk full fixture')
    monkeypatch.setattr(fsutil.os, 'write', failed_write)
    with pytest.raises(OSError, match='disk full'):
        call(tool, 'edit', {'read_handle': read['read_handle'], 'old_text': 'second', 'new_text': 'changed'})
    assert tool.sup.read_file(tool.started.ref, tool.started.credential, 'src/app.js').decode() == read['content']
    assert not list(tool.sup.src_dir(tool.started.ref).rglob('.source-edit-*'))
