"""Bounded source reads and unambiguous create/replace/edit operations."""
import hashlib
import threading
import secrets
import json
from app.workspace.errors import WorkspaceError, EditConflict


def page(text, args, *, digest=None):
    offset, limit = args.get('offset', 0), args.get('limit', 4000)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 6000:
        raise ValueError('offset must be nonnegative and limit 1..6000 characters')
    digest = digest or hashlib.sha256(text.encode()).hexdigest()
    expected = args.get('expected_digest', '')
    if offset and not expected:
        raise ValueError('continuation requires expected_digest from previous page')
    if expected and expected != digest:
        raise WorkspaceError('source changed; restart read at offset 0')
    if offset > len(text):
        raise ValueError('offset exceeds text length')
    end = min(offset + limit, len(text))
    return {'content': text[offset:end], 'digest': digest, 'total_chars': len(text),
            'offset': offset, 'truncated': end < len(text),
            'next_offset': end if end < len(text) else None}


def trim_page(row):
    content = row.get('content', '')
    if len(content) <= 1:
        raise ValueError('source metadata exceeds response limit; use individual shorter paths')
    row['content'] = content[:len(content) // 2]
    row['next_offset'] = row['offset'] + len(row['content'])
    row['truncated'] = True


def bounded_page(result):
    # Relay serializes with JSON's default ASCII escaping; astral characters
    # take twelve bytes there even though they count as one Python character.
    while len(json.dumps(result).encode()) > 58000:
        trim_page(result)
    return result


PAGE_FIELDS = {'offset': {'type': 'integer', 'minimum': 0, 'default': 0},
               'limit': {'type': 'integer', 'minimum': 1, 'maximum': 6000, 'default': 4000},
               'expected_digest': {'type': 'string', 'default': '',
                                   'description': 'Digest from first page; required for continuation.'}}
DIGEST_FIELD = {'type': 'string', 'description': 'Current read_file SHA-256. Empty only to create a missing file.'}


class SourceTools:
    def __init__(self, sup, started, redactor, *, after_write=None):
        self.sup, self.started, self.redactor = sup, started, redactor
        self.after_write = after_write
        self.mutation_lock = threading.RLock()
        self.read_pages = {}
        self.handles = {}

    def new_handle(self, path, digest):
        with self.mutation_lock:
            handle = secrets.token_urlsafe(18)
            self.handles[handle] = (path, digest)
            if len(self.handles) > 256:
                self.handles.pop(next(iter(self.handles)))
            return handle

    def binding(self, args):
        """Handles bind observations, never grant permission or skip supervisor CAS."""
        handle = args.get('read_handle', '')
        if not isinstance(handle, str):
            raise ValueError('read_handle must be a string')
        if handle:
            with self.mutation_lock:
                binding = self.handles.get(handle)
            if binding is None:
                raise ValueError('unknown/expired read_handle; read_file again in this attempt')
            path, digest = binding
            if args.get('path') and args['path'] != path or args.get('expected_digest') and args['expected_digest'] != digest:
                raise ValueError('path/digest conflicts with read_handle')
            return path, digest
        if not isinstance(args.get('path'), str) or not args['path'] or not isinstance(args.get('expected_digest'), str):
            raise ValueError('provide read_handle OR path and expected_digest from read_file')
        return args['path'], args['expected_digest']

    def read(self, c, i, a):
        if set(a) - {'path', 'refresh', 'read_handle', *PAGE_FIELDS}:
            raise ValueError('read_file takes path and optional paging fields')
        if a.get('read_handle'):
            path, digest = self.binding(a)
            a = {**a, 'path': path, 'expected_digest': digest}
        if not isinstance(a.get('path'), str) or not a['path']:
            raise ValueError('read_file requires path or read_handle')
        if type(a.get('refresh', False)) is not bool:
            raise ValueError('refresh must be boolean')
        if a['path'] == '.':
            text = '\n'.join(self.sup.list_files(self.started.ref, self.started.credential))
            return bounded_page({**page(text, a), 'format': 'source-file-list'})
        data = self.sup.read_file(self.started.ref, self.started.credential, a['path'])
        result = {**page(self.redactor.redact(data.decode('utf-8')), a,
                        digest=hashlib.sha256(data).hexdigest()), 'path': a['path']}
        bounded_page(result)
        key = (a['path'], result['digest'], result['offset'], a.get('limit', 4000))
        with self.mutation_lock:
            result['read_handle'] = self.new_handle(a['path'], result['digest'])
            duplicate = key in self.read_pages
            # Bound only receipt metadata. No source bytes or credentials are cached.
            self.read_pages[key] = None
            if len(self.read_pages) > 128:
                self.read_pages.pop(next(iter(self.read_pages)))
        if hasattr(c, 'log'):
            c.log(f'source.read path={self.redactor.redact(a["path"])} offset={result["offset"]} '
                  f'unchanged={duplicate} refresh={a.get("refresh", False)}')
        if duplicate and not a.get('refresh', False):
            result.pop('content')
            result.update(unchanged_read=True,
                next='This exact page was already read and the source digest is still current. '
                     'Reuse its content and proceed to edits/checks. If the earlier page is unavailable '
                     'in active context, request refresh=true once. Do not repeat unchanged reads.')
        return result

    def read_many(self, c, i, a):
        requests = a.get('files')
        if set(a) != {'files'} or not isinstance(requests, list) or not 1 <= len(requests) <= 6:
            raise ValueError('read_files requires 1..6 file requests')
        if any(not isinstance(r, dict) or type(r.get('limit', 4000)) is not int or
               not 1 <= r.get('limit', 4000) <= 6000 for r in requests):
            raise ValueError('each request limit must be 1..6000')
        if sum(r.get('limit', 4000) for r in requests) > 24000:
            raise ValueError('combined read_files limit must be <=24000 characters')
        result = {'files': [self.read(c, i, request) for request in requests]}
        # UTF-8 and JSON escaping can exceed the relay's 64 KiB even when
        # character limits fit. Preserve paging and digest, never drop a result.
        while len(json.dumps(result).encode()) > 60000:
            row = max(result['files'], key=lambda r: len(r.get('content', '')))
            trim_page(row)
            with self.mutation_lock:
                for key in list(self.read_pages):
                    if key[:3] == (row.get('path'), row['digest'], row['offset']):
                        del self.read_pages[key]
        return result

    def write(self, c, i, a):
        if set(a) != {'path', 'content', 'expected_digest'} or (a['content'] is not None and not isinstance(a['content'], str)):
            raise ValueError('write_file takes path, full content (null deletes), expected_digest')
        with self.mutation_lock:
            result = self.sup.change_file(self.started.ref, self.started.credential, **a)
            if self.after_write:
                result['checkpoint_id'] = self.after_write()
            if result.get('digest'):
                result['read_handle'] = self.new_handle(result['path'], result['digest'])
            return result

    def edit(self, c, i, a):
        if set(a) - {'path', 'expected_digest', 'read_handle', 'old_text', 'new_text'} or not all(
                isinstance(a.get(k), str) for k in ('old_text', 'new_text')):
            raise ValueError('edit_file takes old_text/new_text and read_handle OR path/expected_digest')
        return self.mutate(c, i, a, old_text=a['old_text'], new_text=a['new_text'])

    def edit_many(self, c, i, a):
        if (set(a) - {'path', 'expected_digest', 'read_handle', 'edits'} or
                not isinstance(a.get('edits'), list) or not 1 <= len(a['edits']) <= 10):
            raise ValueError('edit_file_batch takes edits and read_handle OR path/expected_digest')
        return self.mutate(c, i, a, edits=a['edits'])

    def mutate(self, c, i, a, **changes):
        with self.mutation_lock:
            path, digest = self.binding(a)
            try:
                result = self.sup.change_file(self.started.ref, self.started.credential,
                                             path, expected_digest=digest, **changes)
            except EditConflict as exc:
                return self.redactor.redact_value({'error': str(exc), **exc.details})
            if self.after_write:
                result['checkpoint_id'] = self.after_write()
            result['read_handle'] = self.new_handle(path, result['digest'])
            return result

    def diff(self, c, i, a):
        if set(a) - {'path', *PAGE_FIELDS}:
            raise ValueError('inspect_diff takes optional path and paging fields')
        path = a.get('path') or None
        text = self.sup.inspect_diff(self.started.ref, self.started.credential, stat_only=path is None, path=path)
        return bounded_page({**page(self.redactor.redact(text), a),
                'format': 'file-diff' if path else 'diff-stat',
                'next': 'Pass path to inspect a file diff; follow next_offset with expected_digest.'})

    @property
    def handlers(self):
        return {'read_file': self.read, 'read_files': self.read_many, 'write_file': self.write,
                'edit_file': self.edit, 'edit_file_batch': self.edit_many, 'inspect_diff': self.diff}

    @property
    def parameters(self):
        binding = {'path': {'type': 'string', 'default': ''},
                   'expected_digest': {**DIGEST_FIELD, 'default': ''},
                   'read_handle': {'type': 'string', 'default': '',
                       'description': 'Opaque read_file handle binds path and digest for this attempt. CAS still applies.'}}
        read = {'path': {'type': 'string', 'default': ''}, **PAGE_FIELDS,
                    'read_handle': binding['read_handle'],
                    'refresh': {'type': 'boolean', 'default': False,
                        'description': 'Force page content only if the prior unchanged page is unavailable in active context.'}}
        edits = {'type': 'array', 'minItems': 1, 'maxItems': 10,
                 'description': 'Sequential replacements in ONE file. All validate before one write/checkpoint; failure writes nothing.',
                 'items': {'type': 'object', 'additionalProperties': False, 'required': ['old_text', 'new_text'],
                           'properties': {'old_text': {'type': 'string', 'minLength': 1}, 'new_text': {'type': 'string'}}}}
        return {'read_file': read,
                'read_files': {'files': {'type': 'array', 'minItems': 1, 'maxItems': 6,
                    'description': 'Read several bounded pages in one call; combined limits <=24000 characters.',
                    'items': {'type': 'object', 'additionalProperties': False, 'properties': read}}},
                'write_file': {'path': {'type': 'string'}, 'content': {'type': ['string', 'null'],
                    'description': 'Entire new file contents. For a small change use edit_file. Null deletes.'},
                    'expected_digest': DIGEST_FIELD},
                'edit_file': {**binding, 'old_text': {'type': 'string', 'minLength': 1,
                    'description': 'Exact text matching once; include context to make the match unique.'},
                    'new_text': {'type': 'string'}},
                'edit_file_batch': {**binding, 'edits': edits},
                'inspect_diff': {'path': {'type': 'string', 'default': '',
                    'description': 'Empty returns stat; relative file path returns its diff.'}, **PAGE_FIELDS}}
