"""Trusted browser runner. The candidate is never imported and cannot write this report."""
import base64
import hashlib
import json
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
            try:
                response = page.goto(url, wait_until='networkidle', timeout=15000)
                if not response or response.status != 200:
                    raise RuntimeError('pinned target health failed')
                smoke = True
                for step in test['steps']:
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
