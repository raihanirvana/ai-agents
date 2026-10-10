"""Trusted browser runner. The candidate is never imported and cannot write this report."""
import base64
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urljoin
from playwright.sync_api import sync_playwright, expect


MAX_DOWNLOAD_BYTES = 1024 * 1024
MAX_ARIA_BYTES = 16 * 1024


def locate(page, selector):
    """Canonical semantic selectors use the public locator APIs with exact matching.

    Historical CSS/Playwright selectors remain executable on pinned legacy suites.
    New plans are separately limited to the supervisor's UI contract vocabulary.
    """
    if not selector.startswith(('testid=', 'role=', 'label="', 'text="')):
        return page.locator(selector)  # Preserve historical CSS/engine parsing exactly.
    # Split only outside quoted JSON strings, so a label containing >> stays literal.
    parts, start, quoted, escaped = [], 0, False, False
    index = 0
    while index < len(selector):
        char = selector[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif selector[index:index + 4] == ' >> ':
            parts.append(selector[start:index])
            index += 3
            start = index + 1
        index += 1
    parts.append(selector[start:])
    current = page
    for part in parts:
        if re.fullmatch(r'testid=[A-Za-z][A-Za-z0-9_.:-]{0,79}', part):
            current = current.get_by_test_id(part[7:])
        elif part.startswith(('label="', 'text="', 'has_text="')):
            kind, value = part.split('=', 1)
            value = json.loads(value)
            if kind == 'has_text':
                current = current.filter(has_text=value)
            else:
                current = (current.get_by_label(value, exact=True) if kind == 'label'
                           else current.get_by_text(value, exact=True))
        elif (role := re.fullmatch(r'role=([a-z]+)(?:\[name=("(?:[^"\\]|\\.)*")\])?', part)):
            args = {'name': json.loads(role[2]), 'exact': True} if role[2] else {}
            current = current.get_by_role(role[1], **args)
        elif re.fullmatch(r'nth=(?:[0-9]|[1-9][0-9])', part):
            current = current.nth(int(part[4:]))
        else:
            current = current.locator(part)
    return current


def dom_observation(page):
    """Fixed bounded reads, not script from the target/model; observations are untrusted data."""
    snapshot = page.locator('body').aria_snapshot(timeout=1500, depth=12)
    raw = snapshot.encode('utf-8')
    text = raw[:MAX_ARIA_BYTES].decode('utf-8', errors='ignore')
    # These locator candidates are vocabulary evidence only, never expected values.
    candidates = []
    controls = page.locator('[data-testid], button, input, select, textarea, a, [role], label')
    total_controls, deadline, omitted = controls.count(), time.monotonic() + 1.5, False
    for index in range(min(total_controls, 120)):
        remaining = deadline - time.monotonic()
        if remaining < 0.05:
            omitted = True
            break
        control = controls.nth(index)
        try:
            facts = control.evaluate(r"""el => {
                const tag = el.tagName.toLowerCase();
                const type = (el.getAttribute('type') || 'text').toLowerCase();
                const roles = {button:'button', a:'link', textarea:'textbox', select:'combobox'};
                const role = el.getAttribute('role') || (tag === 'input' ?
                    ({checkbox:'checkbox', radio:'radio', number:'spinbutton', search:'searchbox',
                      button:'button', submit:'button', reset:'button'}[type] ||
                     (type === 'password' || type === 'hidden' ? null : 'textbox')) : roles[tag]);
                const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/).slice(0, 4)
                    .map(id => document.getElementById(id)?.textContent || '').join(' ').trim();
                const name = el.getAttribute('aria-label') || labelled ||
                    (role === 'button' || role === 'link' ? el.textContent || el.value || '' : '');
                return {testid: el.getAttribute('data-testid')?.slice(0, 81), id: el.id.slice(0, 101), tag,
                    role: role?.slice(0, 31),
                    name: name.trim().slice(0, 161),
                    labels: [...(el.labels || [])].map(x => x.textContent.trim().slice(0, 161)).slice(0, 3)};
            }""", timeout=min(500, remaining * 1000))
        except Exception:
            omitted = True
            continue
        values = []
        if facts['testid'] and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]{0,79}', facts['testid']):
            values.append('testid=' + facts['testid'])
        if facts['id'] and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,99}', facts['id']):
            values.append('#' + facts['id'])
        for label in facts['labels']:
            if label and len(label) <= 160:
                values.append('label=' + json.dumps(label, ensure_ascii=False))
        if facts['role'] and re.fullmatch(r'[a-z]{1,30}', facts['role']):
            role = 'role=' + facts['role']
            values.append(role)
            if facts['name'] and len(facts['name']) <= 160:
                values.append(role + '[name=' + json.dumps(facts['name'], ensure_ascii=False) + ']')
        for selector in values:
            candidates.append({'selector': selector, 'tag': facts['tag'], 'role': facts['role']})
    record = {'aria_snapshot': text, 'aria_snapshot_truncated': len(raw) > MAX_ARIA_BYTES,
              'locator_candidates': candidates[:240],
              'locator_candidates_truncated': omitted or len(candidates) > 240 or total_controls > 120}
    # Bound the whole JSON observation as well as raw aria bytes (escaping/unicode expand on transport).
    while len(json.dumps(record).encode()) > 32 * 1024:
        if record['locator_candidates']:
            record['locator_candidates'].pop()
            record['locator_candidates_truncated'] = True
        else:
            record['aria_snapshot'] = record['aria_snapshot'][:len(record['aria_snapshot']) // 2]
            record['aria_snapshot_truncated'] = True
    record['aria_snapshot_sha256'] = hashlib.sha256(record['aria_snapshot'].encode()).hexdigest()
    return record


class BrowserContractError(RuntimeError):
    def __init__(self, message, diagnosis=None):
        super().__init__(message)
        self.diagnosis = diagnosis


class CsvComparisonError(AssertionError):
    """A mismatch needs QA diagnosis; it alone cannot identify an application bug."""
    def __init__(self, actual, expected):
        super().__init__('Downloaded CSV rows do not match exactly')
        differences = []
        count = 0
        for row in range(max(len(actual), len(expected))):
            found = actual[row] if row < len(actual) else []
            wanted = expected[row] if row < len(expected) else []
            for column in range(max(len(found), len(wanted))):
                observed = found[column] if column < len(found) else None
                required = wanted[column] if column < len(wanted) else None
                if observed != required:
                    count += 1
                    if len(differences) < 8:
                        differences.append({'row': row, 'column': column,
                            'expected': required[:200] if required is not None else None,
                            'actual': observed[:200] if observed is not None else None,
                            'expected_truncated': required is not None and len(required) > 200,
                            'actual_truncated': observed is not None and len(observed) > 200})
        self.diagnosis = {'comparison': 'parsed_csv_cells', 'expected_rows': len(expected),
            'actual_rows': len(actual), 'different_cells': count,
            'differences': differences, 'differences_truncated': count > len(differences)}


def internal_url(base, path):
    destination = urljoin(base, path)
    source, target = urlsplit(base), urlsplit(destination)
    if ((target.scheme, target.netloc) != (source.scheme, source.netloc) or
            not path.startswith('/') or path.startswith('//') or '\\' in path or
            any(ord(c) < 32 for c in path)):
        raise BrowserContractError('navigation requires a same-origin path')
    return destination


def missing_fill_controls(page, step):
    """Observe alternatives only; absence is not a diagnosis or a passing test."""
    if step.get('action') != 'fill' or locate(page, step['selector']).count() != 0:
        return None
    controls = page.locator('input, textarea')
    if controls.count() > 80:
        return None
    candidates = []
    for control in controls.all():
        if not control.is_visible() or not control.is_enabled() or not control.is_editable():
            continue
        facts = control.evaluate('''el => ({tag: el.tagName.toLowerCase(),
            type: (el.getAttribute('type') || 'text').toLowerCase(), id: el.id,
            classes: [...el.classList], name: el.getAttribute('name') || '',
            label: [...(el.labels || [])].map(x => x.textContent).join(' '),
            placeholder: el.getAttribute('placeholder') || ''})''')
        if facts['tag'] == 'input' and facts['type'] not in ('text', 'search', 'email', 'tel', 'url', 'number'):
            continue
        selectors = ['#' + facts['id']] + ['.' + value for value in facts['classes'][:8]]
        for selector in selectors:
            if (not re.fullmatch(r'[.#][A-Za-z_][A-Za-z0-9_-]{0,99}', selector)
                    or page.locator(selector).count() != 1):
                continue
            candidates.append({'selector': selector, 'tag': facts['tag'], 'type': facts['type'],
                'label': facts['label'][:200], 'name': facts['name'][:100],
                'placeholder': facts['placeholder'][:200], 'matched_count': 1,
                'visible': True, 'enabled': True, 'editable': True})
            if len(candidates) >= 160:
                return None
    return {'contract': 'missing_fill_selector', 'selector': step['selector'],
            'matched_count': 0, 'candidates': candidates}


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
    if action == 'select_option' and (step.get('select_by') or 'value') == 'value':
        options = loc.locator('option')
        if options.count() <= 200 and loc.is_visible() and loc.is_enabled():
            observed = options.evaluate_all('(els) => els.map(el => ({value: el.value, '
                'label: el.label, enabled: !el.disabled && !(el.parentElement.tagName === "OPTGROUP" '
                '&& el.parentElement.disabled)}))')
            requested = step['value'] if isinstance(step['value'], list) else [step['value']]
            if any(not any(row['enabled'] and row['value'] == value for row in observed) for value in requested):
                bounded = [row for row in observed if len(row['value']) <= 1000 and len(row['label']) <= 1000]
                complete = len(bounded) == len(observed) and len(json.dumps(bounded).encode()) <= 64 * 1024
                diagnosis.update(contract='unavailable_option_value', select_by='value',
                    requested=step['value'], options=bounded if complete else [], options_complete=complete)
                raise BrowserContractError('Requested option value is absent. Compare original fixture inputs '
                    'with the observed option labels; never guess generated record IDs.', diagnosis)
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
            if rows != expected['csv_rows']:
                raise CsvComparisonError(rows, expected['csv_rows'])


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
    loc = locate(page, step['selector'])
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
    diagnostics_enabled = os.environ.get('AIAGENT_DIAGNOSTICS', 'on-failure') != 'none'
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-sandbox', '--disable-dev-shm-usage'])
        for test in plan['tests']:
            started = time.monotonic()
            context = browser.new_context(service_workers='block', accept_downloads=True)
            context.route('**/*', lambda r: r.continue_() if
                (urlsplit(r.request.url).scheme, urlsplit(r.request.url).netloc) ==
                (origin.scheme, origin.netloc) else r.abort())
            # Lightweight action trace; expensive DOM/screenshots are not
            # captured for every successful test. Baseline runs disable it.
            if diagnostics_enabled:
                context.tracing.start(screenshots=False, snapshots=False)
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
                if diagnostics_enabled:
                    try:
                        record['dom'] = dom_observation(page)
                    except Exception:
                        record.setdefault('diagnostic_errors', []).append('DOM snapshot unavailable')
                try:
                    missing = missing_fill_controls(page, step) if step else None
                    if missing is not None:
                        record['selector_diagnosis'] = missing
                except Exception:
                    pass  # Missing/ambiguous facts never authorize correction.
                if isinstance(exc, BrowserContractError):
                    record['failure_kind'] = 'action_contract'
                    if exc.diagnosis is not None:
                        record['action_diagnosis'] = exc.diagnosis
                if isinstance(exc, CsvComparisonError):
                    record['failure_kind'] = 'expectation_diagnosis'
                    record['expectation_diagnosis'] = exc.diagnosis
                if 'strict mode violation' in str(exc):
                    record['failure_kind'] = 'selector_contract'
                # A narrow, observed contract defect: one visible alert and
                # additional hidden matches. No model-supplied JavaScript is run
                # and no failed assertion is accepted as a pass.
                if (step and step['action'] == 'assert_visible' and
                        'strict mode violation' in str(exc)):
                    try:
                        loc = locate(page, step['selector'])
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
                if diagnostics_enabled and record['status'] != 'passed':
                    if record.get('dom', {}).get('aria_snapshot'):
                        diagnostics.append(('aria_snapshot', record['dom']['aria_snapshot'].encode('utf-8')))
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
                elif diagnostics_enabled:
                    try:
                        context.tracing.stop()  # Discard; no zip/screenshot/base64 for passes.
                    except Exception:
                        record.setdefault('diagnostic_errors', []).append('trace disposal unavailable')
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
