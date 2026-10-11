"""Static build vocabulary check, never evidence of a rendered UI or QA pass."""
from html.parser import HTMLParser
import html
import re
from app.workspace.fsutil import scan_tree, read_file_beneath


# CSS named colors shorter than #RRGGBB, which minifiers can emit instead of hex.
# Numeric CSS keyword values; this table is independent of any Node runtime.
SHORT_COLORS = {
    'aqua': '#00ffff', 'azure': '#f0ffff', 'beige': '#f5f5dc', 'bisque': '#ffe4c4',
    'black': '#000000', 'blue': '#0000ff', 'brown': '#a52a2a', 'coral': '#ff7f50',
    'cyan': '#00ffff', 'gold': '#ffd700', 'gray': '#808080', 'green': '#008000',
    'grey': '#808080', 'indigo': '#4b0082', 'ivory': '#fffff0', 'khaki': '#f0e68c',
    'lime': '#00ff00', 'linen': '#faf0e6', 'maroon': '#800000', 'navy': '#000080',
    'olive': '#808000', 'orange': '#ffa500', 'orchid': '#da70d6', 'peru': '#cd853f',
    'pink': '#ffc0cb', 'plum': '#dda0dd', 'purple': '#800080', 'red': '#ff0000',
    'salmon': '#fa8072', 'sienna': '#a0522d', 'silver': '#c0c0c0', 'snow': '#fffafa',
    'tan': '#d2b48c', 'teal': '#008080', 'tomato': '#ff6347', 'violet': '#ee82ee',
    'wheat': '#f5deb3', 'white': '#ffffff', 'yellow': '#ffff00',
}


def _color_values(text):
    """Normalize opaque hex and short named CSS values, preserving alpha distinctions."""
    values = set()
    for value in re.findall(r'(?<![\w])#(?:[a-f0-9]{8}|[a-f0-9]{6}|[a-f0-9]{4}|[a-f0-9]{3})(?![\w])', text):
        if len(value) in (4, 5):
            value = '#' + ''.join(c * 2 for c in value[1:])
        if len(value) == 9:
            if not value.endswith('ff'):
                continue
            value = value[:7]
        values.add(value)
    for groups in re.findall(r'''[:=]\s*(?:"([a-z]+)"|'([a-z]+)'|([a-z]+)(?=\s*(?:[;}!,)]|$)))''', text):
        name = next(g for g in groups if g)
        if name in SHORT_COLORS:
            values.add(SHORT_COLORS[name])
    return values


class Attributes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.testids = set()

    def handle_starttag(self, tag, attrs):
        self.testids.update(value for key, value in attrs if key == 'data-testid' and value)


def copy_fragments(text):
    """Literal pieces of one copy text around {placeholders}; each must ship verbatim."""
    from app.agents.ui_spec import PLACEHOLDER
    return [piece.strip() for piece in PLACEHOLDER.split(text)[::2] if piece.strip()]


def _variants(fragment):
    return {fragment, html.escape(fragment, quote=True), html.escape(fragment, quote=False)}


def _unescape_js(text):
    """Decode literal escape spellings without evaluating target JavaScript."""
    import json
    escapes = {'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t', 'v': '\v', '0': '\0'}

    def decoded(match):
        value = match[1]
        if '\\u' in value:  # UTF-16 surrogate pair, consumed in one pass.
            return json.loads('"\\' + value + '"')
        if value.startswith(('u', 'x')):
            code = int(value[2:-1] if value.startswith('u{') else value[1:], 16)
            return chr(code) if code <= 0x10ffff else match[0]
        return escapes.get(value, value)

    return re.sub(r'''\\(u[dD][89abAB][0-9a-fA-F]{2}\\u[dD][c-fC-F][0-9a-fA-F]{2}|x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|u\{[0-9a-fA-F]{1,6}\}|["'\\`/bfnrtv0])''',
                  decoded, text)


def check_build(site, contract, spec=None):
    """Literal presence of contract testids and, with a UI spec, its copy and color tokens."""
    wanted = {c.testid for c in contract.controls}
    copy = {} if spec is None else {key: copy_fragments(text) for key, text in spec.copy_map().items()}
    copy = {key: fragments for key, fragments in copy.items() if fragments}
    colors = {} if spec is None else {c.name: c.value.lower() for c in spec.tokens.colors}
    found, copy_found, colors_found = set(), set(), set()
    pending = {key: set(fragments) for key, fragments in copy.items()}
    scanned, total, omitted = [], 0, 0
    # The snapshot has already passed build limits. Bound parsing too and fail closed on omitted bytes.
    for entry in scan_tree(site):
        if entry.kind != 'file':
            continue
        path = entry.rel
        if not path.endswith(('.html', '.js', '.mjs', '.cjs', '.css')):
            continue
        if path.endswith('.css') and not colors:
            continue
        if entry.size > 32 * 1024 * 1024 or total + entry.size > 64 * 1024 * 1024:
            omitted += 1
            continue
        raw = read_file_beneath(site, path, max_bytes=32 * 1024 * 1024)
        total += len(raw)
        if total > 64 * 1024 * 1024:
            return {'status': 'incomplete', 'missing_testids': sorted(wanted - found),
                    'missing_copy_keys': sorted(set(copy) - copy_found),
                    'missing_color_tokens': sorted(set(colors) - colors_found),
                    'reason': 'Static inventory exceeds 64 MiB', 'qa_pass': False}
        text = raw.decode('utf-8', errors='replace')
        lowered = text.lower()
        emitted_colors = _color_values(lowered)
        colors_found.update(name for name, value in colors.items() if value in emitted_colors)
        if not path.endswith('.css'):
            literal_text = _unescape_js(text)
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
            for key, fragments in pending.items():
                fragments.difference_update({f for f in fragments
                                             if f in literal_text or any(v in text for v in _variants(f))})
                if not fragments:
                    copy_found.add(key)
        scanned.append(path)
        if wanted <= found and set(copy) <= copy_found and set(colors) <= colors_found:
            break  # Complete literal-presence proof does not need every vendor bundle parsed.
    missing = sorted(wanted - found)
    missing_copy = sorted(set(copy) - copy_found)
    missing_colors = sorted(set(colors) - colors_found)
    complete = not (missing or missing_copy or missing_colors)
    return {'status': 'passed' if complete else ('incomplete' if omitted else 'failed'),
            'missing_testids': missing, 'present_testids': sorted(wanted & found),
            'missing_copy_keys': missing_copy, 'missing_color_tokens': missing_colors,
            'scanned_files': len(scanned), 'bytes_scanned': total, 'omitted_large_files': omitted, 'qa_pass': False,
            'limitation': 'Literal presence only; role, label, uniqueness, visibility and behaviour require browser execution.'}
