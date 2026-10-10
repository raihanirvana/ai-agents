"""Bounded source index; source is parsed as data, never imported or executed."""
import ast
import hashlib
import json
import re
from pathlib import Path

MAX_CHARS = 6000
MAX_FILES = 48
MAX_SOURCE_BYTES = 32768
SOURCE_SUFFIXES = {'.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.py', '.html', '.css', '.json'}
SYMBOLS = re.compile(r'(?:\bexport\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+'
    r'|\b(?:async\s+)?function\s+|\bclass\s+|\b(?:const|let)\s+(?=[A-Za-z_$][\w$]*\s*=\s*\([^;\n]*\)\s*=>))'
    r'([A-Za-z_$][\w$]*)')
EXPORTS = re.compile(r'\bexport\s*\{([^}\n]{1,300})\}')


def repo_map(sup, started, paths):
    rows, unreadable = [], []
    candidates = [p for p in paths if Path(p).suffix in SOURCE_SUFFIXES and
                  Path(p).name not in ('package-lock.json', 'yarn.lock', 'pnpm-lock.yaml')]
    # Prefer entry points/production modules; tests remain visible when space permits.
    candidates.sort(key=lambda p: (bool(set(Path(p).parts) & {'tests', 'test', '__tests__'}) or
                                  '.test.' in p or '.spec.' in p, p))
    from app.workspace.errors import WorkspaceError
    for path in candidates[:MAX_FILES]:
        try:
            raw = sup.read_file(started.ref, started.credential, path)
        except (OSError, WorkspaceError):
            unreadable.append(path[:180])
            continue
        text = raw[:MAX_SOURCE_BYTES].decode(errors='replace')
        names = []
        if Path(path).suffix == '.py':
            try:
                names = [n.name for n in ast.parse(text).body if isinstance(n,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            except SyntaxError:
                pass
        elif Path(path).suffix in {'.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'}:
            names = SYMBOLS.findall(text)
            names += [name.strip() for group in EXPORTS.findall(text) for name in group.split(',')]
        row = {'path': path, 'sha256': hashlib.sha256(raw).hexdigest(),
               'bytes': len(raw), 'symbols': list(dict.fromkeys(names))[:12],
               'symbols_complete': False}
        if len(json.dumps(rows + [row], ensure_ascii=False)) > MAX_CHARS:
            break
        rows.append(row)
    return {'format': 'source-index-v1', 'authority': 'untrusted_navigation_hint',
            'files': rows, 'total_files': len(paths), 'indexed_files': len(rows),
            'omitted_files': len(paths)-len(rows), 'unreadable': unreadable[:8],
            'note': 'Symbols are heuristic navigation hints, not a complete parser. Read relevant current source '
                    'with read_file/read_files before edits; map digests are not read handles or approvals.'}
