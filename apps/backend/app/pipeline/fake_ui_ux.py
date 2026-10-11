"""LABELLED FAKE UI/UX driver: deterministic UiSpec replies for foundation tests and keyless demos.

It is never a design and never evidence of UI/UX quality. Plug it into FakeProvider as the
reply callable; requests for other roles are delegated to `fallback`.
"""
import json
import re

FAKE_SPEC = {
    'kind': 'ui_spec', 'revision': 1,
    'summary': 'FAKE UI/UX spec: deterministic placeholder from the labelled fake driver, not a design.',
    'tokens': {
        'direction': 'FAKE: neutral placeholder tokens for foundation tests.',
        'colors': [{'name': 'background', 'value': '#F6F7F8'}, {'name': 'text', 'value': '#1F2933'},
                   {'name': 'accent', 'value': '#2B6CB0'}, {'name': 'danger', 'value': '#B83232'}],
        'fonts': [{'role': 'body', 'family': 'Inter', 'fallback': 'sans-serif'}],
        'spacing_px': [4, 8, 16, 24],
        'radius': [{'name': 'control', 'px': 6}],
    },
    'screens': [{'id': 'main', 'purpose': 'FAKE main screen', 'components': [
        {'testid': 'page-title', 'role': 'heading', 'name_key': 'page.title', 'text_key': 'page.title',
         'purpose': 'page title'},
        {'testid': 'item-input', 'label_key': 'item.label', 'purpose': 'new item text'},
        {'testid': 'add-item', 'role': 'button', 'name_key': 'item.add', 'purpose': 'add an item'},
        {'testid': 'item-row', 'role': 'listitem', 'repeated': True, 'purpose': 'one item'},
        {'testid': 'form-error', 'role': 'alert', 'purpose': 'form error'},
    ]}],
    'copy': [
        {'key': 'page.title', 'text': 'Daftar item'},
        {'key': 'item.label', 'text': 'Nama item'},
        {'key': 'item.add', 'text': 'Tambah item'},
        {'key': 'item.added', 'text': 'Item "{name}" ditambahkan'},
        {'key': 'item.required', 'text': 'Isi nama item terlebih dahulu'},
        {'key': 'list.empty', 'text': 'Belum ada item. Tambahkan item pertama.'},
    ],
    'states': [{'screen': 'main', 'kind': 'empty', 'copy_key': 'list.empty'}],
    'feedback': [
        {'action_testid': 'add-item', 'outcome': 'success', 'kind': 'toast', 'copy_key': 'item.added'},
        {'action_testid': 'add-item', 'outcome': 'error', 'kind': 'inline', 'copy_key': 'item.required',
         'target_testid': 'form-error'},
    ],
}


def _task(prompt):
    """Read the actual Task JSON, rather than keys quoted in conversation/repo data."""
    headers = list(re.finditer(r'^## Task\n', prompt, re.MULTILINE))
    if not headers:
        return {}
    try:
        task, _ = json.JSONDecoder().raw_decode(prompt, headers[-1].end())
    except ValueError:
        return {}
    return task if isinstance(task, dict) else {}


class FakeUiUxDriver:
    """FakeProvider reply callable: ui_spec tasks get a valid labelled FAKE spec (or additive amendment)."""

    def __init__(self, fallback=None):
        self.fallback = fallback
        self.requests = 0

    def is_ui_spec_task(self, request):
        return _task(request.user).get('name') == 'ui_spec'

    def __call__(self, request):
        if not self.is_ui_spec_task(request):
            if self.fallback is None:
                return '{}'
            return self.fallback(request) if callable(self.fallback) else self.fallback.pop(0)
        self.requests += 1
        previous = _task(request.user).get('previous_ui_spec')
        if isinstance(previous, dict) and previous.get('kind') == 'ui_spec':
            amended = json.loads(json.dumps(previous))
            amended['revision'] = previous['revision'] + 1
            key = f"fake.amendment.{amended['revision']}"
            amended['copy'].append({'key': key, 'text': f"FAKE tambahan revisi {amended['revision']}"})
            amended['states'].append({'screen': amended['screens'][0]['id'], 'kind': 'loading', 'copy_key': key})
            return amended
        return json.loads(json.dumps(FAKE_SPEC))
