"""Offline backup/restore and explicit failed-job retry (POSIX/WSL)."""
import argparse
import json
import os
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('backup')
    b.add_argument('--db', type=Path, required=True)
    b.add_argument('--artifacts', type=Path, required=True)
    b.add_argument('--workspaces', type=Path, required=True)
    b.add_argument('--destination', type=Path, required=True)
    b.add_argument('--offline', action='store_true', help='API/worker/preview writers have been stopped')
    r = sub.add_parser('restore')
    r.add_argument('--snapshot', type=Path, required=True)
    r.add_argument('--destination', type=Path, required=True)
    r.add_argument('--offline', action='store_true')
    r.add_argument('--allow-unavailable', action='store_true', help='mark lost artifact bytes unavailable; DB/Git remain strict')
    t = sub.add_parser('retry-job', help='one explicit operator retry after the failure cause is fixed; caps stay unchanged')
    t.add_argument('--db', type=Path, required=True)
    t.add_argument('--job', required=True)
    t.add_argument('--authorization-id', required=True, help='stable unique ID for this local operator decision')
    args = p.parse_args()
    if os.name != 'posix':
        p.exit(2, 'Managed Git recovery requires POSIX; use WSL on Windows.\n')
    from .offline import backup, restore, RecoveryError
    try:
        if args.command == 'backup':
            result = backup(db_path=args.db, artifact_root=args.artifacts, workspace_root=args.workspaces,
                            destination=args.destination, offline=args.offline)
            print(json.dumps({'files': len(result['files']), 'unavailable': result['unavailable']}))
        elif args.command == 'restore':
            print(json.dumps(restore(snapshot=args.snapshot, destination=args.destination, offline=args.offline,
                                     allow_unavailable=args.allow_unavailable)))
        else:
            from app.persistence import Database, ArtifactStore
            from app.domain import Workflow
            from app.workers import JobQueue
            from app.workers.queue import QueueError
            with Database(args.db) as db:
                from app.config import ARTIFACT_DIR
                workflow = Workflow(db, ArtifactStore(ARTIFACT_DIR))
                queue = JobQueue(db, startable=workflow.startable)
                try:
                    job_id = queue.retry_failed(args.job, user='user:local-operator', authorization_id=args.authorization_id)
                except QueueError as exc:
                    p.exit(2, str(exc) + '\n')
                print(json.dumps({'job_id': job_id, 'caps_unchanged': True}))
    except (RecoveryError, OSError, ValueError) as exc:
        p.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
