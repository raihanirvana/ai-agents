"""Real SQLite WAL and Git, contract messages; no model/QA claim."""
import json
import sys
from datetime import timedelta

import pytest

if sys.platform == 'win32':
    pytest.skip('managed Git recovery requires POSIX/WSL', allow_module_level=True)

from sqlalchemy import select
from app.domain import Actor, Workflow
from app.persistence import ArtifactStore, Database, append_message, cleanup_unpinned, migrate, record_usage
from app.persistence.columns import utcnow
from app.persistence.models import Artifact, Job, Message, RuntimeCredential, LocalSession
from app.recovery.offline import backup, restore, RecoveryError, digest
from app.workspace import WorkspaceSupervisor
from app.workers import JobQueue
from app.workers.queue import StaleLease

LIMITS = {'model_calls': 3, 'tool_calls': 8, 'active_s': 60}


@pytest.fixture
def state(tmp_path):
    root = tmp_path / 'original'
    root.mkdir()
    migrate.upgrade(root / 'app.sqlite3')
    db, store = Database(root / 'app.sqlite3'), ArtifactStore(root / 'artifacts')
    w = Workflow(db, store)
    user = Actor('user:recovery-test', 'user', 'restore-project')
    p = w.create_project(user, name='Recovery contract fixture', mode='new', brief='Persist context')
    sup = WorkspaceSupervisor(root / 'workspaces')
    sha = sup.create_project(p.id)
    w.initialize_base(Actor('fixture', 'integrator', p.id), p.revision, sha)
    queue = JobQueue(db)
    with db.write() as s:
        a = store.put_bytes(s, project_id=p.id, kind='context', name='context.txt', data=b'preserved context')
        append_message(s, project_id=p.id, thread_id='chat', sender=user.id, body='WAL context', attachment_ids=[a.id])
        store.put_git_commit(s, project_id=p.id, sha=sha)
        s.add(LocalSession(token_hash='old-local-token', user_id=user.id, expires_at=utcnow()+timedelta(hours=1)))
    def save(dest=None, **kw):
        return backup(db_path=db.path, artifact_root=store.root, workspace_root=sup.root,
            destination=dest or tmp_path/'snapshot', offline=True, **kw)
    yield root, db, store, w, queue, p.id, a.id, save
    db.dispose()


def test_wal_refs_context_and_waiting_input_relocate_without_old_credentials(state, tmp_path):
    root, db, store, w, q, pid, aid, save = state
    j = q.enqueue(project_id=pid, lane='interactive', stage='chat', role='po', runtime='structured',
        idempotency_key='wait', limits=LIMITS)
    lease = q.claim('worker:old', 'interactive', capacity=1, runtimes=('structured',))
    with db.write() as s:
        record_usage(s, j.id, {'model_calls': 1, 'cost_usd': .001})
    request = q.request_input(lease, question='Fixture clarification?', checkpoint={'artifact': aid}, request_key='q')
    with db.write() as s:
        s.add(RuntimeCredential(token_hash='old-runtime-token', job_id=j.id, owner=lease.owner,
            generation=lease.generation, expires_at=utcnow()+timedelta(hours=1)))
    before = digest(store.resolve(next(a.path for a in _artifacts(db) if a.id == aid)))
    inv = save()
    assert aid in inv['pins'] and not any('runs/' in k or 'hermes' in k for k in inv['files'])
    report = restore(snapshot=tmp_path/'snapshot', destination=tmp_path/'new-root', offline=True)
    assert report['unavailable'] == [] and not report['new_qa_claimed']
    with Database(tmp_path/'new-root/app.sqlite3') as new:
        nq = JobQueue(new)
        ns = ArtifactStore(tmp_path/'new-root/artifacts')
        with new.read() as s:
            assert s.scalar(select(Message).where(Message.body == 'WAL context'))
            assert not list(s.scalars(select(RuntimeCredential))) and not list(s.scalars(select(LocalSession)))
            assert ns.read_bytes(s, aid) == b'preserved context'
            assert s.get(Job, j.id).usage['cost_usd'] == .001
        with pytest.raises(StaleLease):
            nq.complete(lease, {'forged': True})
        _, resumed = nq.answer(request, body='Fixture answer', answer_key='once', user='user:recovery-test')
        assert resumed
        fresh = nq.claim('worker:new', 'interactive', capacity=1, runtimes=('structured',))
        assert fresh.generation > lease.generation
        # Reopen alone is insufficient: the cold layout must also start fresh work.
        from app.workspace.manifest import parse_manifest, reference_manifest_dict
        supervisor = WorkspaceSupervisor(tmp_path/'new-root/workspaces')
        started = supervisor.start_attempt(pid, ticket_id='restore-test', scope_version=1, role='developer',
            attempt=1, generation=fresh.generation, lease_id=fresh.owner,
            manifest=parse_manifest(reference_manifest_dict()))
        supervisor.stop_run(started.ref, 'fresh restored attempt verified')
        with new.read() as s:
            assert s.get(Job, j.id).usage['model_calls'] == 1
        report = cleanup_unpinned(new, ns, project_id=pid, min_age=timedelta(0), dry_run=False)
        assert aid in report.kept_pinned
    assert digest(store.resolve(next(a.path for a in _artifacts(db) if a.id == aid))) == before


def _artifacts(db):
    with db.read() as s:
        return list(s.scalars(select(Artifact)))


def test_live_ownership_blocks_snapshot_and_partial_destination_is_not_published(state, tmp_path):
    _, db, _, _, q, pid, _, save = state
    q.enqueue(project_id=pid, lane='interactive', stage='chat', role='po', runtime='structured',
        idempotency_key='running', limits=LIMITS)
    q.claim('worker:live', 'interactive', capacity=1, runtimes=('structured',))
    with pytest.raises(RecoveryError, match='live jobs'):
        save()
    assert not (tmp_path/'snapshot').exists() and not list(tmp_path.glob('*.partial-*'))


def test_corrupt_artifact_can_restore_degraded_but_cannot_be_served(state, tmp_path):
    _, _, _, _, _, _, aid, save = state
    inv = save()
    key = next(k for k, v in inv['artifact_paths'].items() if v == aid)
    (tmp_path/'snapshot'/key).write_bytes(b'corruption')
    with pytest.raises(RecoveryError, match='corrupt snapshot'):
        restore(snapshot=tmp_path/'snapshot', destination=tmp_path/'strict', offline=True)
    assert not (tmp_path/'strict').exists()
    report = restore(snapshot=tmp_path/'snapshot', destination=tmp_path/'degraded', offline=True, allow_unavailable=True)
    assert aid in report['unavailable']
    from app.persistence import ArtifactUnavailable
    with Database(tmp_path/'degraded/app.sqlite3') as db:
        with db.read() as s:
            with pytest.raises(ArtifactUnavailable):
                ArtifactStore(tmp_path/'degraded/artifacts').read_bytes(s, aid)


def test_database_corruption_never_becomes_a_degraded_success(state, tmp_path):
    *_, save = state
    save()
    (tmp_path/'snapshot/app.sqlite3').write_bytes(b'bad DB')
    with pytest.raises(RecoveryError):
        restore(snapshot=tmp_path/'snapshot', destination=tmp_path/'new-root', offline=True, allow_unavailable=True)
    assert not (tmp_path/'new-root').exists()


def test_symlink_or_traversal_inventory_cannot_read_outside_snapshot(state, tmp_path):
    *_, save = state
    save()
    p = tmp_path/'snapshot/inventory.json'
    inv = json.loads(p.read_text())
    inv['files']['artifacts/../../outside'] = {'sha256': '0'*64, 'bytes': 1}
    p.write_text(json.dumps(inv))
    with pytest.raises(RecoveryError, match='unsafe inventory'):
        restore(snapshot=tmp_path/'snapshot', destination=tmp_path/'new-root', offline=True)


def test_ref_inventory_mismatch_fails_restore_without_overwriting_existing_root(state, tmp_path):
    *_, pid, aid, save = state
    save()
    p = tmp_path/'snapshot/inventory.json'
    inv = json.loads(p.read_text())
    inv['repos'][pid]['refs/heads/accepted'] = '0'*40
    p.write_text(json.dumps(inv))
    with pytest.raises(RecoveryError, match='refs differ'):
        restore(snapshot=tmp_path/'snapshot', destination=tmp_path/'new-root', offline=True)
    old = tmp_path/'existing'
    old.mkdir()
    (old/'keep').write_text('user data')
    with pytest.raises(RecoveryError, match='destination must be new'):
        restore(snapshot=tmp_path/'snapshot', destination=old, offline=True)
    assert (old/'keep').read_text() == 'user data'


def test_a_blocked_onboarding_import_keeps_its_provenance_across_backup_and_restore(tmp_path):
    """DEV-015 review: the import receipt lived outside DB/artifacts/refs, so after a restore the project could never be
    onboarded again ('partial managed import has no provenance')."""
    from app.onboarding.source import import_source
    from tests.onboarding.test_source import source_repo
    root = tmp_path / 'o'
    root.mkdir()
    migrate.upgrade(root / 'app.sqlite3')
    db, store = Database(root / 'app.sqlite3'), ArtifactStore(root / 'artifacts')
    try:
        w = Workflow(db, store)
        p = w.create_project(Actor('user:x', 'user', 'onboarding-project'), name='n', mode='existing', brief='', repo_ref='/src')
        sup = WorkspaceSupervisor(root / 'workspaces')
        source, _ = source_repo(tmp_path)
        saved = import_source(sup, p.id, source, 'a' * 32)  # imported, then the sandbox baseline was blocked: no accepted tip
        inventory = backup(db_path=db.path, artifact_root=store.root, workspace_root=sup.root, destination=tmp_path / 'snap', offline=True)
        assert f'workspaces/{p.id}/onboarding-import.json' in inventory['files']
    finally:
        db.dispose()
    restore(snapshot=tmp_path / 'snap', destination=tmp_path / 'restored', offline=True)
    again = import_source(WorkspaceSupervisor(tmp_path / 'restored' / 'workspaces'), p.id, source, 'b' * 32)
    assert again['baseline_sha'] == saved['baseline_sha'] and again['source_sha'] == saved['source_sha']
