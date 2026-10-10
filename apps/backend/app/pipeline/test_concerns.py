"""Qualify advisory concerns against an exact suite and immutable shipped source."""
from .contracts import TestConcern
from app.workspace.fsutil import validate_relpath
from app.workspace.errors import WorkspaceError


def qualify_concerns(concerns, suite, broker, sha):
    tests = {test.id: test for test in suite.tests}
    accepted, source, ignored, seen = [], {}, [], set()
    for data in concerns[:16]:
        try:
            concern = TestConcern.model_validate(data)
            test = tests.get(concern.test_id)
            if test is None or concern.step_index >= len(test.steps):
                raise ValueError('test/step absent from pinned suite')
            step = test.steps[concern.step_index]
            if step.action != 'fill' or step.selector != concern.selector:
                raise ValueError('concern does not match a fill in the pinned suite')
            path = validate_relpath(concern.source_path)
            if (path.suffix not in ('.html', '.css', '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx') or
                    set(path.parts).intersection(('test', 'tests', '__tests__', 'node_modules', 'home')) or
                    path.stem.endswith(('.test', '.spec'))):
                raise ValueError('concern must cite shipped UI source')
            raw = broker.read_committed_file(sha, str(path), max_bytes=256 * 1024)
            text = raw.decode('utf-8') if raw is not None else ''
            if concern.source_excerpt not in text:
                raise ValueError('excerpt absent from immutable candidate source')
            key = (concern.test_id, concern.step_index)
            if key in seen:
                continue
            seen.add(key)
            # Bounded data for independent QA selection, never executable instructions.
            start = max(0, text.index(concern.source_excerpt) - 2400)
            if str(path) not in source and sum(map(len, source.values())) + min(len(text), 6000) <= 24000:
                source[str(path)] = text[start:start + 6000]
            if str(path) not in source or concern.source_excerpt not in source[str(path)]:
                raise ValueError('source context exceeds concern working set')
            accepted.append(concern.model_dump())
            if len(accepted) == 8:
                break
        except (ValueError, WorkspaceError, UnicodeError) as exc:
            ignored.append(str(exc)[:300])
    return accepted, source, ignored


def pinned_concerns(messages, candidate, suite):
    """Messages from other scopes/candidates/suites cannot influence this run."""
    result = []
    for message in messages:
        meta = message.meta
        if (meta.get('intent') in ('candidate_handoff', 'technical_review') and
                meta.get('candidate_id') == candidate.id and
                meta.get('scope_version') == candidate.scope_version and
                meta.get('commit_sha') == candidate.commit_sha and
                meta.get('suite_digest') == suite.digest):
            result.extend(meta.get('test_concerns', [])[:8])
        if len(result) >= 16:
            break
    return result[:16]
