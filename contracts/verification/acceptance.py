"""Trusted browser runner. The candidate is never imported and cannot write this report."""
import base64
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect


def main():
    invocation, target, digest, url = sys.argv[1:]
    plan = json.loads(Path('/plan.json').read_text())
    records, artifacts, smoke, artifact_bytes = [], [], False, 0
    origin = urlsplit(url)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-sandbox', '--disable-dev-shm-usage'])
        for test in plan['tests']:
            started = time.monotonic()
            context = browser.new_context(service_workers='block')
            context.route('**/*', lambda r: r.continue_() if
                (urlsplit(r.request.url).scheme, urlsplit(r.request.url).netloc) ==
                (origin.scheme, origin.netloc) else r.abort())
            context.tracing.start(screenshots=True, snapshots=True)
            page = context.new_page()
            page.set_default_timeout(3000)
            record = {'id': test['id'], 'uac': test['uac'], 'status': 'failed'}
            step_index, step = None, None
            try:
                response = page.goto(url, wait_until='networkidle', timeout=15000)
                if not response or response.status != 200:
                    raise RuntimeError('pinned target health failed')
                smoke = True
                for step_index, step in enumerate(test['steps']):
                    if step['action'] == 'reload':
                        response = page.reload(wait_until='networkidle', timeout=15000)
                        if not response or response.status != 200:
                            raise RuntimeError('pinned target reload health failed')
                        continue
                    loc, action, value = page.locator(step['selector']), step['action'], step.get('value')
                    if action == 'click': loc.click()
                    elif action == 'fill': loc.fill(value)
                    elif action == 'press': loc.press(value)
                    elif action == 'assert_text': expect(loc).to_have_text(value)
                    elif action == 'assert_count': expect(loc).to_have_count(value)
                    elif action == 'assert_visible': expect(loc).to_be_visible()
                    elif action == 'assert_value': expect(loc).to_have_value(value)
                    else: raise RuntimeError('unsupported assertion')
                record['status'] = 'passed'
            except Exception as exc:
                record['error'] = str(exc)[:3000]
                record['failure_kind'] = 'assertion_or_application'
                record['failed_step'] = step_index
                if 'strict mode violation' in str(exc):
                    record['failure_kind'] = 'selector_contract'
                # A narrow, observed contract defect: one visible alert and
                # additional hidden matches. No application JavaScript is run
                # to diagnose it, and no assertion is accepted as a pass.
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
                screenshot = page.screenshot()
                trace = Path('/tmp') / (test['id'].replace(':', '_') + '.zip')
                context.tracing.stop(path=str(trace))
                for kind, raw in [('screenshot', screenshot), ('trace', trace.read_bytes())]:
                    if len(raw) <= 1024 * 1024 and artifact_bytes + len(raw) <= 4 * 1024 * 1024:
                        artifact_bytes += len(raw)
                        artifacts.append({'test_id': test['id'], 'kind': kind,
                            'sha256': hashlib.sha256(raw).hexdigest(), 'data': base64.b64encode(raw).decode()})
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
