"""Review regressions: real Git, Docker build, and independent browser regression runner."""
import json

import pytest
from sqlalchemy import select

from app.persistence.models import Job, Project, Release
from app.workers.queue import Lease
from app.domain.types import Invalid
from tests.release.conftest import TEST_JS, browser_test, page
from tests.release.test_export_sync import blob, clone_of_user_repo, doc, drafted, run_git


def test_export_includes_the_explicit_onboarding_patch_and_bundle_needs_only_source_head(existing, tmp_path):
    env = existing
    # Reproduce the baseline commit produced by an explicitly selected onboarding patch.
    baseline = env.commit({'selected.txt': 'explicit user patch\n'})
    env.broker._bare('update-ref', 'refs/heads/accepted', baseline, env.base)
    with env.db.write() as s:
        p = s.get(Project, env.pid)
        p.workflow = {**p.workflow, 'accepted_tip': baseline, 'onboarding_detail': {
            **p.workflow['onboarding_detail'], 'baseline_sha': baseline, 'patch_applied': True}}
    env.accept_ticket('Menu', {'index.html': page('<h1 id="menu">Menu</h1>')},
                      [browser_test('menu-visible', ['UAC-1'], '#menu')])
    release = env.approve_release(drafted(env))
    before = env.fingerprint()
    assert env.run_job(env.request('export', release.id)).status == 'succeeded'
    result = env.release().export_result
    clone = clone_of_user_repo(env, tmp_path)
    (clone / 'release.patch').write_bytes(blob(env, result['patch_artifact_id']))
    run_git(['apply', '--index', 'release.patch'], clone)
    assert (clone / 'selected.txt').read_text() == 'explicit user patch\n'
    fresh = clone_of_user_repo(env, tmp_path, 'bundle-clone')
    (fresh / 'release.bundle').write_bytes(blob(env, result['bundle_artifact_id']))
    run_git(['fetch', 'release.bundle', f"{result['ref_in_bundle']}:{result['branch']}"], fresh)
    assert run_git(['rev-parse', result['branch']], fresh).decode().strip() == release.accepted_tip
    assert result['base_sha'] == env.source_sha and env.fingerprint() == before


def test_second_release_regresses_features_from_the_first_release_too(env):
    env.accept_ticket('Menu', {'index.html': page('<h1 id="menu">Menu</h1>'), 'test.cjs': TEST_JS},
                      [browser_test('menu-visible', ['UAC-1'], '#menu')])
    env.approve_release(drafted(env))
    # Fixture QA accepts the new feature while breaking the already released feature.
    env.accept_ticket('Cart', {'index.html': page('<h2 id="cart">Cart</h2>')},
                      [browser_test('cart-visible', ['UAC-1'], '#cart')])
    second = drafted(env)
    assert second.status == 'failed'
    receipt = doc(env, second.evidence_artifact_ids[0])
    assert receipt['counts']['failed'] == 1 and receipt['counts']['executed'] == 2


def test_sync_retry_after_candidate_creation_publishes_exactly_one_replacement(existing, monkeypatch):
    env = existing
    env.accept_ticket('Menu', {'index.html': page('<h1 id="menu">Menu</h1>')},
                      [browser_test('menu-visible', ['UAC-1'], '#menu')])
    old = drafted(env)
    new_head = env.drift({'notes.txt': 'source change\n'})
    before = env.fingerprint()
    job = env.request('sync', old.id)
    original = env.runtime.verify_commit
    monkeypatch.setattr(env.runtime, 'verify_commit', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('crash after sync commit')))
    with pytest.raises(RuntimeError, match='crash after sync commit'):
        env.run_job(job)
    with env.db.read() as s:
        row = s.get(Job, job.id)
        lease = Lease(row.id, row.lease_owner, row.lease_generation)
    retry = env.queue.fail(lease, 'crash', retryable=True)
    assert retry
    env.queue.finish_cleanup(job.id, lease.generation)
    monkeypatch.setattr(env.runtime, 'verify_commit', original)
    outcome = env.run_job(retry)
    assert outcome.status == 'succeeded', outcome
    with env.db.read() as s:
        releases = list(s.scalars(select(Release)))
    assert len(releases) == 2 and env.release().status == 'draft'
    assert doc(env, env.release().target_artifact_id)['export_base'] == new_head
    assert env.fingerprint() == before


def test_source_drift_during_bundle_creation_prevents_export_publication(existing, monkeypatch):
    env = existing
    env.accept_ticket('Menu', {'index.html': page('<h1 id="menu">Menu</h1>')},
                      [browser_test('menu-visible', ['UAC-1'], '#menu')])
    release = env.approve_release(drafted(env))
    from app.workspace.gitbroker import GitBroker
    original = GitBroker._bare
    changed = []
    def move_source(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if args[:2] == ('bundle', 'create') and not changed:
            changed.append(env.drift({'notes.txt': 'source moved during export\n'}))
        return result
    monkeypatch.setattr(GitBroker, '_bare', move_source)
    outcome = env.run_job(env.request('export', release.id))
    assert outcome.status == 'failed' and 'HEAD changed during export' in outcome.error
    assert env.release().status == 'approved' and env.release().export_result is None


def test_sync_approval_requires_explicit_technical_review_of_its_pinned_diffs(existing):
    env = existing
    env.accept_ticket('Menu', {'index.html': page('<h1 id="menu">Menu</h1>')},
                      [browser_test('menu-visible', ['UAC-1'], '#menu')])
    old = drafted(env)
    env.drift({'notes.txt': 'source changed\n'})
    assert env.run_job(env.request('sync', old.id)).status == 'succeeded'
    new = env.release()
    with pytest.raises(Invalid, match='technical review'):
        env.world.w.approve_release(env.world.user, new.id, new.revision, new.target_artifact_id,
                                   new.target_digest, new.evidence_artifact_ids, [])
    diffs = doc(env, new.target_artifact_id)['technical_review_evidence_ids']
    for incorrect in (diffs[:1], [diffs[0], diffs[0]], ['another', diffs[1]]):
        with pytest.raises(Invalid, match='technical review'):
            env.world.w.approve_release(env.world.user, new.id, new.revision, new.target_artifact_id,
                                       new.target_digest, new.evidence_artifact_ids, [], incorrect)
    approved = env.approve_release(new)
    from app.persistence.models import Approval
    from app.release.requests import public
    with env.db.read() as s:
        ap = s.scalar(select(Approval).where(Approval.release_id == new.id))
        assert ap.details['technical_review']['diff_artifact_ids'] == diffs
        assert ap.details['technical_review']['target_digest'] == new.target_digest
        assert public(approved, s)['technical_review_evidence_ids'] == diffs


def test_sync_includes_the_affected_manual_uac_of_an_already_released_feature(existing):
    env = existing
    home, menu, cart = '<h1 id="home">Home</h1>', '<h1 id="menu">Menu</h1>', '<h2 id="cart">Cart</h2>'
    first, _ = env.accept_ticket('Menu', {'index.html': page(home, menu)},
                                [browser_test('menu-visible', ['UAC-1'], '#menu')], manual=['UAC-OLD'])
    env.approve_release(drafted(env), [f'{first.id}:UAC-OLD'])
    second, _ = env.accept_ticket('Cart', {'index.html': page(home, menu, cart)},
                                 [browser_test('cart-visible', ['UAC-1'], '#cart')])
    old = drafted(env)
    assert [e['ticket_id'] for e in old.scope_snapshot] == [second.id]
    env.drift({'index.html': page(home, footer='v2')})
    assert env.run_job(env.request('sync', old.id)).status == 'succeeded'
    new = env.release()
    assert {e['ticket_id'] for e in new.scope_snapshot} == {first.id, second.id}
    assert env.world.w.release_checklist(new.scope_snapshot) == [f'{first.id}:UAC-OLD']
    assert doc(env, new.evidence_artifact_ids[0])['counts']['executed'] == 2


def test_discard_during_sync_prevents_a_replacement_from_being_published(existing, monkeypatch):
    env = existing
    env.accept_ticket('Menu', {'index.html': page('<h1 id="menu">Menu</h1>')},
                      [browser_test('menu-visible', ['UAC-1'], '#menu')])
    old = drafted(env)
    env.drift({'notes.txt': 'source changed\n'})
    publish = env.runtime._publish
    def discard_then_publish(*args, **kwargs):
        env.world.w.discard_release(env.world.user, old.id, old.revision)
        return publish(*args, **kwargs)
    monkeypatch.setattr(env.runtime, '_publish', discard_then_publish)
    outcome = env.run_job(env.request('sync', old.id))
    assert outcome.status == 'failed' and 'no longer' in outcome.error
    with env.db.read() as s:
        releases = list(s.scalars(select(Release)))
    assert len(releases) == 1 and releases[0].status == 'failed'
