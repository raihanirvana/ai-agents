"""Bounded source reads and unambiguous create/replace/edit operations."""
import hashlib
import threading
from app.workspace.errors import WorkspaceError


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

    def read(self, c, i, a):
        if set(a) - {'path', 'refresh', *PAGE_FIELDS} or 'path' not in a:
            raise ValueError('read_file takes path and optional paging fields')
        if type(a.get('refresh', False)) is not bool:
            raise ValueError('refresh must be boolean')
        if a['path'] == '.':
            text = '\n'.join(self.sup.list_files(self.started.ref, self.started.credential))
            return {**page(text, a), 'format': 'source-file-list'}
        data = self.sup.read_file(self.started.ref, self.started.credential, a['path'])
        result = {**page(self.redactor.redact(data.decode('utf-8')), a,
                        digest=hashlib.sha256(data).hexdigest()), 'path': a['path']}
        key = (a['path'], result['digest'], result['offset'], a.get('limit', 4000))
        with self.mutation_lock:
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

    def write(self, c, i, a):
        if set(a) != {'path', 'content', 'expected_digest'} or (a['content'] is not None and not isinstance(a['content'], str)):
            raise ValueError('write_file takes path, full content (null deletes), expected_digest')
        with self.mutation_lock:
            result = self.sup.change_file(self.started.ref, self.started.credential, **a)
            if self.after_write:
                result['checkpoint_id'] = self.after_write()
            return result

    def edit(self, c, i, a):
        if set(a) != {'path', 'old_text', 'new_text', 'expected_digest'} or not all(isinstance(a[k], str) for k in a):
            raise ValueError('edit_file takes path, old_text, new_text, expected_digest strings')
        with self.mutation_lock:
            result = self.sup.change_file(self.started.ref, self.started.credential, **a)
            if self.after_write:
                result['checkpoint_id'] = self.after_write()
            return result

    def diff(self, c, i, a):
        if set(a) - {'path', *PAGE_FIELDS}:
            raise ValueError('inspect_diff takes optional path and paging fields')
        path = a.get('path') or None
        text = self.sup.inspect_diff(self.started.ref, self.started.credential, stat_only=path is None, path=path)
        return {**page(self.redactor.redact(text), a),
                'format': 'file-diff' if path else 'diff-stat',
                'next': 'Pass path to inspect a file diff; follow next_offset with expected_digest.'}

    @property
    def handlers(self):
        return {'read_file': self.read, 'write_file': self.write, 'edit_file': self.edit, 'inspect_diff': self.diff}

    @property
    def parameters(self):
        return {'read_file': {'path': {'type': 'string'}, **PAGE_FIELDS,
                    'refresh': {'type': 'boolean', 'default': False,
                        'description': 'Force page content only if the prior unchanged page is unavailable in active context.'}},
                'write_file': {'path': {'type': 'string'}, 'content': {'type': ['string', 'null'],
                    'description': 'Entire new file contents. For a small change use edit_file. Null deletes.'},
                    'expected_digest': DIGEST_FIELD},
                'edit_file': {'path': {'type': 'string'}, 'old_text': {'type': 'string', 'minLength': 1,
                    'description': 'Exact text matching once; include context to make the match unique.'},
                    'new_text': {'type': 'string'}, 'expected_digest': DIGEST_FIELD},
                'inspect_diff': {'path': {'type': 'string', 'default': '',
                    'description': 'Empty returns stat; relative file path returns its diff.'}, **PAGE_FIELDS}}
