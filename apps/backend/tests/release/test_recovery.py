"""Cold restore of real release build/regression; ticket QA is a labelled domain contract fixture."""
import json
from datetime import timedelta

from sqlalchemy import select
from app.persistence import ArtifactStore, Database, cleanup_unpinned
from app.persistence.models import Approval, Artifact, Release
from app.recovery.offline import backup, restore
from app.workspace import WorkspaceSupervisor
from tests.release.conftest import TEST_JS, browser_test, page


def test_offline_restore_preserves_release_approval_git_and_exact_build_bundle(env, tmp_path):
    env.accept_ticket('Menu', {'index.html': page('<h1 id="menu">Menu</h1>'), 'test.cjs': TEST_JS},
               [browser_test('menu-visible', ['UAC-1'], '#menu', 'Menu')])
    assert env.run_job(env.request('freeze')).status == 'succeeded'
    r = env.release()
    env.approve_release(r)
    with env.db.read() as s:
        target = env.store.read_bytes(s, r.target_artifact_id)
        build = json.loads(env.store.read_bytes(s, r.build_artifact_id))
        bundle_id = build['bundle_artifact_id']
        bundle = env.store.read_bytes(s, bundle_id)
        approval = s.scalar(select(Approval).where(Approval.release_id == r.id))
    inv = backup(db_path=env.db.path, artifact_root=env.store.root, workspace_root=env.root,
                 destination=tmp_path/'snapshot', offline=True)
    assert bundle_id in inv['pins'] and r.target_artifact_id in inv['pins']
    report = restore(snapshot=tmp_path/'snapshot', destination=tmp_path/'relocated', offline=True)
    assert report['unavailable'] == []
    root = tmp_path/'relocated'
    with Database(root/'app.sqlite3') as db:
        store = ArtifactStore(root/'artifacts')
        with db.read() as s:
            restored = s.get(Release, r.id)
            assert restored.status == 'approved' and restored.target_digest == r.target_digest
            assert s.get(Approval, approval.id).target_digest == r.target_digest
            assert store.read_bytes(s, r.target_artifact_id) == target
            assert store.read_bytes(s, bundle_id) == bundle
            assert all(s.get(Artifact, aid).availability == 'available' for aid in r.evidence_artifact_ids)
        assert WorkspaceSupervisor(root/'workspaces').broker(env.pid).accepted_sha() == r.accepted_tip
        cleanup = cleanup_unpinned(db, store, project_id=env.pid, min_age=timedelta(0), dry_run=False)
        assert bundle_id in cleanup.kept_pinned
        with db.read() as s:
            assert store.read_bytes(s, bundle_id) == bundle
