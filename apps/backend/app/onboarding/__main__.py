"""Explicit local onboarding command; execution belongs to the persistent worker."""
import argparse
import json
import os
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('inspect', 'request'))
    parser.add_argument('--source', type=Path)
    parser.add_argument('--project')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--patch', type=Path)
    parser.add_argument('--source-sha')
    parser.add_argument('--db', type=Path)
    parser.add_argument('--artifacts', type=Path)
    args = parser.parse_args()
    if args.command == 'inspect':
        if os.name != 'posix' or not args.source:
            parser.error('inspect requires POSIX/WSL and --source')
        import tempfile
        from app.workspace.gitbroker import GitBroker
        from .source import inspect_source
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = inspect_source(GitBroker(Path(tmp) / 'unused.git', Path(tmp) / 'home'), args.source)
        from app.agents import Redactor
        print(json.dumps(Redactor().redact_value(result), indent=2))
        return 0
    if not args.project or not args.manifest or args.source:
        parser.error('request requires --project and --manifest; source comes from the project repo_ref')
    from app.config import DATABASE_PATH, ARTIFACT_DIR
    from app.domain import Actor
    from app.persistence import Database, ArtifactStore
    from app.persistence.models import Project
    from app.agents import Redactor
    from app.http.service import bind
    from .requests import request
    db, store = Database(args.db or DATABASE_PATH), ArtifactStore(args.artifacts or ARTIFACT_DIR)
    try:
        with db.write() as s:
            p = s.get(Project, args.project)
            if p is None:
                raise ValueError('project does not exist')
            p, job = request(s, bind(s, store, Redactor()), store, Actor('user:local-operator', 'user', p.id),
                p.revision, json.loads(args.manifest.read_text(encoding='utf-8')), uuid.uuid4().hex,
                patch=args.patch.read_text(encoding='utf-8') if args.patch else None, source_sha=args.source_sha)
        print('Onboarding queued: ' + job.id + '. Run worker --runtime onboarding or pipeline with the same DB/artifacts/workspace root.')
    finally:
        db.dispose()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
