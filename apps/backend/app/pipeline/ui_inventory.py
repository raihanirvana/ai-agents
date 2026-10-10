"""Static build vocabulary check, never evidence of a rendered UI or QA pass."""
from html.parser import HTMLParser
import re
from app.workspace.fsutil import scan_tree, read_file_beneath


class Attributes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.testids = set()

    def handle_starttag(self, tag, attrs):
        self.testids.update(value for key, value in attrs if key == 'data-testid' and value)


def check_build(site, contract):
    wanted = {c.testid for c in contract.controls}
    found, scanned, total, omitted = set(), [], 0, 0
    # The snapshot has already passed build limits. Bound parsing too and fail closed on omitted bytes.
    for entry in scan_tree(site):
        if entry.kind != 'file':
            continue
        path = entry.rel
        if not path.endswith(('.html', '.js', '.mjs', '.cjs')):
            continue
        if entry.size > 32 * 1024 * 1024 or total + entry.size > 64 * 1024 * 1024:
            omitted += 1
            continue
        raw = read_file_beneath(site, path, max_bytes=32 * 1024 * 1024)
        total += len(raw)
        if total > 64 * 1024 * 1024:
            return {'status': 'incomplete', 'missing_testids': sorted(wanted - found),
                    'reason': 'Static inventory exceeds 64 MiB', 'qa_pass': False}
        text = raw.decode('utf-8', errors='replace')
        if path.endswith('.html'):
            parser = Attributes()
            parser.feed(text)
            found.update(parser.testids)
        # React/Vite object keys and DOM setAttribute calls use literal IDs.
        # Regex is deliberately a presence check, not a JS parser or reachability proof.
        found.update(re.findall(r'''["']data-testid["']\s*:\s*["']([A-Za-z][A-Za-z0-9_.:-]{0,79})["']''', text))
        found.update(re.findall(r'''setAttribute\(\s*["']data-testid["']\s*,\s*["']([A-Za-z][A-Za-z0-9_.:-]{0,79})["']''', text))
        found.update(re.findall(r'''\.dataset\.testid\s*=\s*["']([A-Za-z][A-Za-z0-9_.:-]{0,79})["']''', text))
        # Vanilla applications may ship HTML templates in JavaScript string literals.
        found.update(re.findall(r'''data-testid\s*=\s*["']([A-Za-z][A-Za-z0-9_.:-]{0,79})["']''', text))
        scanned.append(path)
        if wanted <= found:
            break  # Complete literal-presence proof does not need every vendor bundle parsed.
    missing = sorted(wanted - found)
    return {'status': ('incomplete' if omitted else 'failed') if missing else 'passed', 'missing_testids': missing,
            'present_testids': sorted(wanted & found), 'scanned_files': len(scanned),
            'bytes_scanned': total, 'omitted_large_files': omitted, 'qa_pass': False,
            'limitation': 'Literal presence only; role, label, uniqueness, visibility and behaviour require browser execution.'}
