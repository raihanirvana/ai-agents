"""Consistent SQLite + registered artifacts + independent bare Git snapshots (POSIX).

The operator must stop API/worker/preview writers first. A SQLite writer lock covers
the snapshot, and live job/cleanup/preview ledgers are rejected. Only registered
artifacts and Git object/ref storage are copied: no target instructions are run,
and no executor home, credentials, hooks, config, worktree or original repo is copied.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import uuid

from sqlalchemy import delete, select

from app.persistence import ArtifactStore, Database, EventSpec, append_event, migrate, pinned_artifacts
from app.persistence.columns import utcnow
from app.persistence.models import Artifact, Job, LocalSession, Preview, Project, RuntimeCredential
from app.workspace.gitbroker import GitBroker


class RecoveryError(RuntimeError):
    pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def safe_path(root: Path, key: str) -> Path:
    p = PurePosixPath(key)
    if not key or p.is_absolute() or '..' in p.parts or '\\' in key or ':' in key:
        raise RecoveryError('unsafe inventory path')
    path = root
    for part in p.parts:
        path /= part
        if path.is_symlink():
            raise RecoveryError('symlink in snapshot')
    return path


def _copy_file(source: Path, dest: Path):
    if source.is_symlink() or not source.is_file():
        raise RecoveryError('snapshot input must be a regular file')
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(source, dest)
    dest.chmod(0o600)


def _copy_tree(source: Path, dest: Path):
    if source.is_symlink():
        raise RecoveryError('symlink in Git storage')
    for directory, dirs, files in os.walk(source):
        base = Path(directory)
        for name in dirs + files:
            if (base / name).is_symlink():
                raise RecoveryError('symlink in Git storage')
        for name in files:
            _copy_file(base / name, dest / (base / name).relative_to(source))


def _git_copy(source: Path, dest: Path, scratch: Path):
    broker = GitBroker(source, scratch)
    if (source / 'objects/info/alternates').exists():
        raise RecoveryError('managed Git must be independent (alternates found)')
    dest.mkdir(parents=True, mode=0o700)
    # Fresh safe bare metadata; discard hooks/config/worktree paths and reflogs.
    fresh = GitBroker(dest, scratch)
    fresh.run(['init', '--bare', str(dest)])
    for name in ('objects', 'refs'):
        if (source / name).exists():
            _copy_tree(source / name, dest / name)
    for name in ('HEAD', 'packed-refs', 'shallow'):
        if (source / name).exists():
            _copy_file(source / name, dest / name)
    fresh._bare('fsck', '--full', '--no-reflogs')
    if fresh.refs() != broker.refs():
        raise RecoveryError('Git refs changed during snapshot')
    return fresh.refs()


def _check_db(path: Path):
    if migrate.current_revision(path) != migrate.head_revision():
        raise RecoveryError('snapshot schema is not the current revision; upgrade before backup')
    if migrate.integrity_problems(path):
        raise RecoveryError('SQLite integrity/reference check failed')


def backup(*, db_path: Path, artifact_root: Path, workspace_root: Path, destination: Path,
           offline: bool = False) -> dict:
    if not offline:
        raise RecoveryError('stop API, worker and previews, then acknowledge --offline')
    db_path, artifact_root, workspace_root, destination = map(Path.resolve,
        (db_path, artifact_root, workspace_root, destination))
    if not db_path.is_file() or destination.exists():
        raise RecoveryError('source DB must exist and destination must be new')
    if any(destination.is_relative_to(p) for p in (artifact_root, workspace_root)):
        raise RecoveryError('backup destination must be outside runtime storage')
    stage = destination.with_name(destination.name + '.partial-' + uuid.uuid4().hex)
    stage.mkdir(parents=True, mode=0o700)
    try:
        # Backup API reads committed WAL pages, while the separate write lock
        # prevents DB state from moving during artifact/ref collection.
        with sqlite3.connect(db_path) as lock:
            lock.execute('BEGIN IMMEDIATE')
            rows = lock.execute("SELECT status, runtime_ref FROM jobs").fetchall()
            if any(status == 'running' or json.loads(ref).get('cleanup') for status, ref in rows):
                raise RecoveryError('live jobs or pending cleanup: stop/reconcile workers first')
            if lock.execute("SELECT 1 FROM previews WHERE status IN ('requested','starting','ready','stopping')").fetchone():
                raise RecoveryError('stop previews before backup')
            with sqlite3.connect(db_path) as reader, sqlite3.connect(stage / 'app.sqlite3') as target:
                reader.backup(target)
            _check_db(stage / 'app.sqlite3')
            db, store = Database(stage / 'app.sqlite3'), ArtifactStore(artifact_root)
            try:
                with db.read() as s:
                    artifacts = list(s.scalars(select(Artifact)))
                    projects = list(s.scalars(select(Project)))
                    pins = {k: [{'kind': p.owner_kind, 'id': p.owner_id} for p in v]
                            for k, v in pinned_artifacts(s).items()}
                unavailable, artifact_paths = [], {}
                for a in artifacts:
                    if a.storage != 'file':
                        continue
                    key = 'artifacts/' + a.path
                    artifact_paths[key] = a.id
                    try:
                        with db.read() as s:
                            raw = store.read_bytes(s, a.id)
                    except Exception as exc:
                        from app.persistence import ArtifactUnavailable
                        if not isinstance(exc, ArtifactUnavailable):
                            raise
                        unavailable.append(a.id)
                        continue
                    dest = safe_path(stage, key)
                    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    dest.write_bytes(raw)
                    dest.chmod(0o600)
                repos = {}
                for p in projects:
                    source = workspace_root / p.id / 'repo.git'
                    if not source.exists():
                        if p.workflow.get('accepted_tip'):
                            raise RecoveryError('project accepted tip has no managed repository')
                        continue
                    dest = safe_path(stage, f'workspaces/{p.id}/repo.git')
                    refs = _git_copy(source, dest, stage / '.git-home')
                    if p.workflow.get('accepted_tip') and refs.get('refs/heads/accepted') != p.workflow['accepted_tip']:
                        raise RecoveryError('Git/DB integration mismatch; reconcile integrator before backup')
                    repos[p.id] = refs
                    # Provenance of an onboarding import: without it a project whose baseline was blocked (never activated)
                    # cannot be onboarded again after a restore ("partial managed import has no provenance").
                    receipt = workspace_root / p.id / 'onboarding-import.json'
                    if receipt.is_file() and not receipt.is_symlink():
                        _copy_file(receipt, safe_path(stage, f'workspaces/{p.id}/onboarding-import.json'))
                for a in artifacts:
                    if a.storage == 'git' and a.availability == 'available':
                        GitBroker(stage / 'workspaces' / a.project_id / 'repo.git', stage / '.git-home')._bare(
                            'cat-file', '-e', a.checksum + '^{commit}')
            finally:
                db.dispose()
            shutil.rmtree(stage / '.git-home', ignore_errors=True)
            for suffix in ('-wal', '-shm'):
                (stage / ('app.sqlite3' + suffix)).unlink(missing_ok=True)
            for f in stage.rglob('*'):
                if f.is_file():
                    f.chmod(0o600)
            files = {f.relative_to(stage).as_posix(): {'sha256': digest(f), 'bytes': f.stat().st_size}
                     for f in sorted(stage.rglob('*')) if f.is_file()}
            inventory = {'version': 1, 'schema': migrate.head_revision(), 'created_at': utcnow().isoformat(),
                'files': files, 'repos': repos, 'artifact_paths': artifact_paths, 'unavailable': unavailable,
                'pins': pins, 'configuration': {'database': 'app.sqlite3', 'artifacts': 'artifacts', 'workspaces': 'workspaces'},
                'excluded': ['provider keys', 'executor homes', 'worktrees', 'hooks', 'source repositories']}
            (stage / 'inventory.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
            (stage / 'inventory.json').chmod(0o600)
            os.replace(stage, destination)
            lock.rollback()
            return inventory
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def restore(*, snapshot: Path, destination: Path, offline: bool = False,
            allow_unavailable: bool = False) -> dict:
    if not offline:
        raise RecoveryError('restore before starting writers; acknowledge --offline')
    snapshot, destination = Path(snapshot).resolve(), Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(snapshot):
        raise RecoveryError('restore destination must be new and outside the snapshot')
    inventory = json.loads(safe_path(snapshot, 'inventory.json').read_text(encoding='utf-8'))
    if inventory.get('version') != 1 or inventory.get('schema') != migrate.head_revision():
        raise RecoveryError('unsupported snapshot format/schema')
    if any(not isinstance(inventory.get(k), dict) for k in ('files', 'repos', 'artifact_paths')) or 'app.sqlite3' not in inventory['files']:
        raise RecoveryError('incomplete snapshot inventory')
    stage = destination.with_name(destination.name + '.partial-' + uuid.uuid4().hex)
    stage.mkdir(parents=True, mode=0o700)
    degraded = []
    try:
        for key, expected in inventory['files'].items():
            if key != 'app.sqlite3' and not key.startswith(('artifacts/', 'workspaces/')):
                raise RecoveryError('unexpected inventory storage')
            source, dest = safe_path(snapshot, key), safe_path(stage, key)
            valid = source.is_file() and source.stat().st_size == expected['bytes'] and digest(source) == expected['sha256']
            if not valid:
                if allow_unavailable and key in inventory['artifact_paths']:
                    degraded.append(inventory['artifact_paths'][key])
                    continue
                raise RecoveryError('missing/corrupt snapshot file: ' + key)
            _copy_file(source, dest)
        _check_db(stage / 'app.sqlite3')
        db, store = Database(stage / 'app.sqlite3'), ArtifactStore(stage / 'artifacts')
        try:
            with db.write() as s:
                for p in s.scalars(select(Project)):
                    if p.id in inventory['repos']:
                        broker = GitBroker(stage / 'workspaces' / p.id / 'repo.git', stage / '.git-home')
                        broker._bare('fsck', '--full', '--no-reflogs')
                        if broker.refs() != inventory['repos'][p.id] or (
                            p.workflow.get('accepted_tip') and broker.accepted_sha() != p.workflow['accepted_tip']):
                            raise RecoveryError('restored Git refs differ from inventory/DB')
                        # WorkspaceSupervisor.start_attempt expects this parent. No
                        # old run/credential/worktree is restored into it.
                        (broker.repo.parent / 'runs').mkdir(mode=0o700, exist_ok=True)
                    elif p.workflow.get('accepted_tip'):
                        raise RecoveryError('restored accepted project has no Git inventory')
                unavailable = []
                for a in s.scalars(select(Artifact)):
                    if a.storage == 'file':
                        if store.verify(s, a.id, actor='system:restore') != 'available':
                            unavailable.append(a.id)
                    elif a.availability == 'available':
                        GitBroker(stage / 'workspaces' / a.project_id / 'repo.git', stage / '.git-home')._bare(
                            'cat-file', '-e', a.checksum + '^{commit}')
                # Old capabilities and browser sessions never survive a relocation.
                s.execute(delete(RuntimeCredential))
                s.execute(delete(LocalSession))
                for j in s.scalars(select(Job)):
                    if j.status == 'running' or (j.runtime_ref or {}).get('cleanup'):
                        raise RecoveryError('snapshot contains live ownership')
                    j.lease_owner, j.lease_expires_at = None, None
                    if j.status != 'waiting_input':
                        j.lease_generation += 1
                    # waiting_input retains the immutable request generation; it has no
                    # running lease. The next claim bumps generation and creates new tools.
                    j.runtime_ref = {k: v for k, v in (j.runtime_ref or {}).items()
                                     if k not in ('processes', 'resources', 'cleanup')}
                    j.revision += 1
                for p in s.scalars(select(Project)):
                    append_event(s, p.id, EventSpec('project.restored', 'system:restore',
                        {'unavailable_artifact_ids': [a.id for a in s.scalars(select(Artifact).where(
                            Artifact.project_id == p.id, Artifact.availability == 'unavailable'))],
                         'new_capabilities_required': True}))
        finally:
            db.dispose()
        shutil.rmtree(stage / '.git-home', ignore_errors=True)
        # No linked worktree metadata was copied; fresh attempts get fresh paths.
        os.replace(stage, destination)
        return {'destination': str(destination), 'unavailable': unavailable, 'degraded_files': degraded,
                'approval_preserved': True, 'new_qa_claimed': False}
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
