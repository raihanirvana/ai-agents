"""Trusted browser runner. The candidate is never imported and cannot write this report."""
import base64
import csv
import hashlib
import io
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urljoin
from playwright.sync_api import sync_playwright, expect


MAX_DOWNLOAD_BYTES = 1024 * 1024


class BrowserContractError(RuntimeError):
    def __init__(self, message, diagnosis=None):
        super().__init__(message)
        self.diagnosis = diagnosis


def internal_url(base, path):
    destination = urljoin(base, path)
    source, target = urlsplit(base), urlsplit(destination)
    if ((target.scheme, target.netloc) != (source.scheme, source.netloc) or
            not path.startswith('/') or path.startswith('//') or '\\' in path or
            any(ord(c) < 32 for c in path)):
        raise BrowserContractError('navigation requires a same-origin path')
    return destination


def control_contract(loc, step):
    """Fixed runner-owned DOM reads, never an expression supplied by the model."""
    action = step['action']
    if action not in ('fill', 'select_option', 'check', 'uncheck', 'upload_file',
                       'assert_checked', 'assert_unchecked', 'assert_value', 'assert_values'):
        return
    # Let Playwright report absence/ambiguity normally. A missing control is not
    # automatically a test mistake, and must never authorize a suite correction.
    loc.wait_for(state='attached')
    if loc.count() != 1:
        return
    facts = loc.evaluate('(el) => ({tag: el.tagName.toLowerCase(), '
                         'type: el.getAttribute("type") || "text", '
                         'role: el.getAttribute("role"), editable: el.isContentEditable, '
                         'multiple: !!el.multiple})')
    tag, kind, role = facts['tag'], facts['type'].lower(), facts['role']
    diagnosis = {'action': action, 'selector': step['selector'], 'tag': tag, 'input_type': kind}
    if action == 'fill' and tag == 'select':
        value = step.get('value')
        options = loc.locator('option')
        matches = []
        if options.count() <= 200:
            for option in options.all():
                option_value = option.get_attribute('value')
                if option_value is None:
                    option_value = option.evaluate('(el) => el.value')
                if option_value == value and option.is_enabled():
                    matches.append(option_value)
        if len(matches) == 1 and loc.is_enabled() and loc.is_visible():
            diagnosis.update(matching_options=1, option_value=value, select_by='value')
        raise BrowserContractError('fill cannot operate on a select; use select_option', diagnosis)
    if action == 'fill' and not (facts['editable'] or tag == 'textarea' or
            (tag == 'input' and kind not in ('checkbox', 'radio', 'file', 'button', 'submit',
                                             'reset', 'image', 'hidden', 'range', 'color'))):
        raise BrowserContractError('fill requires a text-compatible input, textarea or contenteditable', diagnosis)
    if action == 'select_option' and tag != 'select':
        raise BrowserContractError('select_option requires a native select; custom menus use click', diagnosis)
    if action == 'select_option' and isinstance(step.get('value'), list) and len(step['value']) > 1 and not facts['multiple']:
        raise BrowserContractError('multiple selected options require select[multiple]', diagnosis)
    if action in ('check', 'uncheck', 'assert_checked', 'assert_unchecked') and not (
            (tag == 'input' and kind in ('checkbox', 'radio')) or role in ('checkbox', 'radio')):
        raise BrowserContractError('checked-state operations require a checkbox or radio', diagnosis)
    if action == 'uncheck' and (kind == 'radio' or role == 'radio'):
        raise BrowserContractError('uncheck cannot deselect a radio; select another radio instead', diagnosis)
    if action == 'upload_file' and not (tag == 'input' and kind == 'file'):
        raise BrowserContractError('upload_file requires an input[type=file]', diagnosis)
    if action == 'assert_value' and tag not in ('input', 'textarea', 'select'):
        raise BrowserContractError('assert_value requires an input, textarea or select', diagnosis)
    if action == 'assert_values' and not (tag == 'select' and facts['multiple']):
        raise BrowserContractError('assert_values requires a multiple select', diagnosis)


def download_assertions(download, expected):
    if download is None:
        raise BrowserContractError('assert_download needs a preceding download in this test')
    if expected.get('filename') is not None:
        assert download['filename'] == expected['filename'], 'Downloaded filename does not match'
    if any(expected.get(key) is not None for key in ('text', 'contains_text', 'csv_rows')):
        try:
            text = download['data'].decode('utf-8')
        except UnicodeDecodeError as exc:
            raise AssertionError('Downloaded content is not UTF-8') from exc
        if expected.get('text') is not None:
            assert text == expected['text'], 'Downloaded text does not match'
        if expected.get('contains_text') is not None:
            assert expected['contains_text'] in text, 'Expected text is absent from download'
        if expected.get('csv_rows') is not None:
            try:
                rows = list(csv.reader(io.StringIO(text.removeprefix('\ufeff'), newline=''), strict=True))
            except csv.Error as exc:
                raise AssertionError('Downloaded CSV is malformed') from exc
            assert rows == expected['csv_rows'], 'Downloaded CSV rows do not match exactly'


def run_step(page, step, base_url, last_download):
    action, value = step['action'], step.get('value')
    if action in ('reload', 'navigate'):
        destination = internal_url(base_url, value) if action == 'navigate' else None
        response = (page.reload(wait_until='networkidle', timeout=15000) if action == 'reload' else
                    page.goto(destination, wait_until='networkidle', timeout=15000))
        if action == 'navigate' and response is None and page.url == destination:
            return last_download  # Same-document/hash navigation has no HTTP response.
        if not response or response.status != 200:
            raise RuntimeError('pinned target navigation health failed')
        return last_download
    if action == 'assert_url':
        expect(page).to_have_url(internal_url(base_url, value))
        return last_download
    if action == 'assert_download':
        download_assertions(last_download, step['download'])
        return last_download
    loc = page.locator(step['selector'])
    control_contract(loc, step)
    if action == 'click': loc.click()
    elif action == 'click_dialog':
        wanted, observed = step['dialog'], []
        def handle(dialog):
            observed.append({'type': dialog.type, 'message': dialog.message})
            if wanted['accept']:
                if wanted['type'] == 'prompt':
                    dialog.accept(wanted.get('prompt_text') or '')
                else:
                    dialog.accept()
            else:
                dialog.dismiss()
        page.on('dialog', handle)
        try:
            loc.click()
            assert len(observed) == 1, 'Expected one native dialog from this click'
            assert observed[0] == {k: wanted[k] for k in ('type', 'message')}, 'Native dialog type/message differs'
        finally:
            page.remove_listener('dialog', handle)
    elif action == 'double_click': loc.dblclick()
    elif action == 'hover': loc.hover()
    elif action == 'focus': loc.focus()
    elif action == 'fill': loc.fill(value)
    elif action == 'press': loc.press(value)
    elif action == 'select_option': loc.select_option(**{step.get('select_by') or 'value': value})
    elif action == 'check': loc.check()
    elif action == 'uncheck': loc.uncheck()
    elif action == 'upload_file':
        fixture = step['file']
        loc.set_input_files({'name': fixture['name'], 'mimeType': fixture['mime_type'],
                             'buffer': fixture['content'].encode('utf-8')})
    elif action == 'download':
        with page.expect_download(timeout=5000) as event:
            loc.click()
        downloaded = event.value
        assert downloaded.failure() is None, 'Browser download failed'
        path = downloaded.path()
        with Path(path).open('rb') as handle:
            data = handle.read(MAX_DOWNLOAD_BYTES + 1)
        assert len(data) <= MAX_DOWNLOAD_BYTES, 'Downloaded file exceeds runner limit (1 MiB)'
        last_download = {'filename': downloaded.suggested_filename, 'data': data}
    elif action == 'assert_text': expect(loc).to_have_text(value)
    elif action == 'assert_contains_text': expect(loc).to_contain_text(value)
    elif action == 'assert_count': expect(loc).to_have_count(value)
    elif action == 'assert_visible': expect(loc).to_be_visible()
    elif action == 'assert_hidden': expect(loc).to_be_hidden()
    elif action == 'assert_value': expect(loc).to_have_value(value)
    elif action == 'assert_values': expect(loc).to_have_values(value)
    elif action == 'assert_checked': expect(loc).to_be_checked()
    elif action == 'assert_unchecked': expect(loc).not_to_be_checked()
    elif action == 'assert_enabled': expect(loc).to_be_enabled()
    elif action == 'assert_disabled': expect(loc).to_be_disabled()
    elif action == 'assert_attribute': expect(loc).to_have_attribute(step['attribute'], value)
    else: raise BrowserContractError('unsupported DSL action: ' + str(action))
    return last_download


def main():
    invocation, target, digest, url = sys.argv[1:]
    plan = json.loads(Path('/plan.json').read_text())
    records, artifacts, smoke, artifact_bytes = [], [], False, 0
    origin = urlsplit(url)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-sandbox', '--disable-dev-shm-usage'])
        for test in plan['tests']:
            started = time.monotonic()
            context = browser.new_context(service_workers='block', accept_downloads=True)
            context.route('**/*', lambda r: r.continue_() if
                (urlsplit(r.request.url).scheme, urlsplit(r.request.url).netloc) ==
                (origin.scheme, origin.netloc) else r.abort())
            context.tracing.start(screenshots=True, snapshots=True)
            page = context.new_page()
            page.set_default_timeout(3000)
            record = {'id': test['id'], 'uac': test['uac'], 'status': 'failed'}
            step_index, step, last_download = None, None, None
            try:
                response = page.goto(url, wait_until='networkidle', timeout=15000)
                if not response or response.status != 200:
                    raise RuntimeError('pinned target health failed')
                smoke = True
                for step_index, step in enumerate(test['steps']):
                    last_download = run_step(page, step, url, last_download)
                    if step['action'] == 'download':
                        record.setdefault('downloads', []).append({'step': step_index,
                            'filename': last_download['filename'][:200], 'size_bytes': len(last_download['data']),
                            'sha256': hashlib.sha256(last_download['data']).hexdigest()})
                record['status'] = 'passed'
            except Exception as exc:
                record['error'] = str(exc)[:3000]
                record['failure_kind'] = 'assertion_or_application'
                record['failed_step'] = step_index
                if isinstance(exc, BrowserContractError):
                    record['failure_kind'] = 'action_contract'
                    if exc.diagnosis is not None:
                        record['action_diagnosis'] = exc.diagnosis
                if 'strict mode violation' in str(exc):
                    record['failure_kind'] = 'selector_contract'
                # A narrow, observed contract defect: one visible alert and
                # additional hidden matches. No model-supplied JavaScript is run
                # and no failed assertion is accepted as a pass.
                if (step and step['action'] == 'assert_visible' and
                        'strict mode violation' in str(exc)):
                    try:
                        loc = page.locator(step['selector'])
                        count = loc.count()
                        visible = [loc.nth(i) for i in range(count) if loc.nth(i).is_visible()] if count <= 100 else []
                        if count > 1 and len(visible) == 1:
                            item_id = visible[0].get_attribute('id')
                            if (item_id and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,99}', item_id)
                                    and visible[0].get_attribute('role') == 'alert'
                                    and page.locator('#' + item_id).count() == 1):
                                record['selector_diagnosis'] = {
                                    'selector': step['selector'], 'matched_count': count,
                                    'visible_count': 1, 'unique_visible_alert': '#' + item_id}
                    except Exception:
                        pass  # Unavailable diagnosis never authorizes a repair.
            finally:
                # Binary diagnostics travel on runner stdout; there is no shared writable mount.
                trace = Path('/tmp') / (test['id'].replace(':', '_') + '.zip')
                diagnostics = []
                try:
                    diagnostics.append(('screenshot', page.screenshot(timeout=3000)))
                except Exception:
                    record.setdefault('diagnostic_errors', []).append('screenshot unavailable')
                try:
                    context.tracing.stop(path=str(trace))
                    with trace.open('rb') as handle:
                        raw = handle.read(1024 * 1024 + 1)
                    diagnostics.append(('trace', raw))
                except Exception:
                    record.setdefault('diagnostic_errors', []).append('trace unavailable')
                for kind, raw in diagnostics:
                    if len(raw) <= 1024 * 1024 and artifact_bytes + len(raw) <= 4 * 1024 * 1024:
                        artifact_bytes += len(raw)
                        artifacts.append({'test_id': test['id'], 'kind': kind,
                            'sha256': hashlib.sha256(raw).hexdigest(), 'data': base64.b64encode(raw).decode()})
                trace.unlink(missing_ok=True)
                context.close()
            record['duration_s'] = round(time.monotonic() - started, 3)
            records.append(record)
        browser.close()
    passed = sum(t['status'] == 'passed' for t in records)
    report = {'schema': 1, 'invocation_id': invocation, 'target_digest': target, 'suite_digest': digest,
        'tests': records, 'discovered': len(records), 'executed': len(records), 'passed': passed,
        'failed': len(records) - passed, 'skipped': 0, 'smoke_passed': smoke, 'artifacts': artifacts}
    print('PIPELINE_REPORT ' + json.dumps(report), flush=True)
    return 0 if records and passed == len(records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
