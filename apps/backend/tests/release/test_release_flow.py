"""DEV-014 release flow with real Git, Docker build and the separate browser runner."""
import json

import pytest
from sqlalchemy import select

from app.domain.types import Conflict, Invalid
from app.persistence import Database
from app.persistence.models import Approval, Artifact, Job, Project, Release
from tests.release.conftest import TEST_JS, browser_test, page

MENU = page('<h1 id="menu">Menu</h1>')
CART = page('<h1 id="menu">Menu</h1>', '<h2 id="cart">Cart</h2>')


def two_tickets(env):
    a, _ = env.accept_ticket("Menu", {"index.html": MENU, "test.cjs": TEST_JS},
                             [browser_test("menu-visible", ["UAC-1"], "#menu", "Menu")], manual=["UAC-M"])
    b, _ = env.accept_ticket("Cart", {"index.html": CART}, [browser_test("cart-visible", ["UAC-1"], "#cart", "Cart")])
    return a, b


def doc(env, artifact_id):
    with env.db.read() as s:
        return json.loads(env.store.read_bytes(s, artifact_id))


def verified_draft(env):
    outcome = env.run_job(env.request("freeze"))
    assert outcome.status == "succeeded", outcome
    return env.release()


def test_freeze_verifies_one_combined_target_and_the_user_approves_exactly_that_target(env):
    a, b = two_tickets(env)
    release = verified_draft(env)
    assert release.status == "draft" and [e["ticket_id"] for e in release.scope_snapshot] == [a.id, b.id]
    with env.db.read() as s:
        tip = s.get(Project, env.pid).workflow["accepted_tip"]
    assert release.accepted_tip == tip == env.broker.accepted_sha()
    target = doc(env, release.target_artifact_id)
    assert target["accepted_tip"] == tip and target["scope_digest"] == env.world.w.scope_digest(release.scope_snapshot)
    assert {k for k in ("build_digest", "toolchain_digest", "config_digest", "fixture_digest", "migration_digest", "runner",
                        "suite_digest", "node_image_id") if target.get(k)} == {
        "build_digest", "toolchain_digest", "config_digest", "fixture_digest", "migration_digest", "runner", "suite_digest", "node_image_id"}
    suite = doc(env, target["suite_artifact_id"])
    assert sorted(t["id"] for t in suite["tests"]) == [f"t{a.number}-menu-visible", f"t{b.number}-cart-visible"]
    receipt = next(d for d in (doc(env, i) for i in release.evidence_artifact_ids)
                   if isinstance(d, dict) and d.get("kind") == "release_verification")
    assert receipt["status"] == "passed" and receipt["counts"]["passed"] == 2 and receipt["fake_provider"] is False
    assert receipt["repo_gate"]["status"] == "passed" and receipt["target_digest"] == release.target_digest
    assert set(receipt["uac_coverage"]) == {f"{a.id}:UAC-1", f"{b.id}:UAC-1"}
    # The manual UAC of an included ticket is the user's checklist for this release.
    assert env.world.w.release_checklist(release.scope_snapshot) == [f"{a.id}:UAC-M"]
    with pytest.raises(Invalid, match="checklist"):
        env.approve_release(release)
    approved = env.approve_release(release, [f"{a.id}:UAC-M"])
    assert approved.status == "approved"
    with env.db.read() as s:
        ap = s.scalar(select(Approval).where(Approval.type == "release"))
        assert ap.target_digest == release.target_digest and set(ap.evidence_artifact_ids) == set(release.evidence_artifact_ids)
    # Verification, approval and evidence survive a restart (a new database connection on the same file).
    reopened = Database(env.db.path if hasattr(env.db, "path") else env.db.engine.url.database)
    try:
        with reopened.read() as s:
            r = s.get(Release, release.id)
            assert r.status == "approved" and r.target_digest == release.target_digest
            assert s.scalar(select(Approval).where(Approval.type == "release")).release_id == release.id
            for i in r.evidence_artifact_ids:  # every pinned evidence file is still readable and matches its checksum
                env.store.read_bytes(s, i)
    finally:
        reopened.dispose()


def test_the_freeze_holds_the_integrator_and_a_ticket_accepted_meanwhile_joins_the_next_release(env):
    a, _ = env.accept_ticket("Menu", {"index.html": MENU, "test.cjs": TEST_JS}, [browser_test("menu-visible", ["UAC-1"], "#menu", "Menu")])
    job = env.request("freeze")  # queued: the freeze is already in force
    frozen_tip = env.broker.accepted_sha()
    w = env.world
    sha = env.commit({"index.html": CART})
    from tests.domain.conftest import SCOPE
    t = w.approve(w.new({**SCOPE, "title": "Cart"}))
    dev, ref = w.job(t, "developer")
    t = w.ticket(t.id)
    commit, receipt, base = w.commit_receipt(t, ref, sha=sha)
    c = w.w.submit_candidate(dev, t.id, t.revision, ref, commit_artifact_id=commit.id, commit_receipt_id=receipt.id, base_sha=base,
                             submission_key="cart-late")
    target = w.target(w.ticket(t.id), c, suite_artifact_id=env.suite([browser_test("cart-visible", ["UAC-1"], "#cart", "Cart")]))
    lead, ref = w.job(t, "technical-lead")
    w.w.approve_review(lead, t.id, w.ticket(t.id).revision, ref, c.id)
    _, qa = w.job(t, "qa")
    v, smoke = w.proof(w.ticket(t.id), c, target)
    w.w.open_uat(w.actor("verification"), t.id, w.ticket(t.id).revision, qa, c.id, v.id, smoke.id)
    t = w.ticket(t.id)
    w.w.accept_uat(w.user, t.id, t.revision, c.id, 1, target.id, target.checksum, v.id, [*v.evidence_artifact_ids, smoke.id])
    assert env.integrator().run_once() == []  # held: accepted after the freeze, integrated after it
    assert env.broker.accepted_sha() == frozen_tip and w.ticket(t.id).phase == "integrating"
    assert env.run_job(job).status == "succeeded"
    release = env.release()
    assert release.accepted_tip == frozen_tip and [e["ticket_id"] for e in release.scope_snapshot] == [a.id]
    assert env.integrator().run_once() == [(t.id, "updated")]  # the freeze is over
    assert env.broker.accepted_sha() == c.commit_sha
    # The frozen release is still approvable although the tip moved on, and the next release holds only the new ticket.
    assert env.approve_release(release).status == "approved"
    second = env.release_after_approval = verified_draft(env)
    assert [e["ticket_id"] for e in second.scope_snapshot] == [t.id] and second.accepted_tip == c.commit_sha


def test_a_failing_regression_leaves_a_failed_release_with_evidence_that_can_never_be_approved(env):
    env.accept_ticket("Menu", {"index.html": MENU, "test.cjs": TEST_JS},
                      [browser_test("menu-visible", ["UAC-1"], "#menu", "Menu")])
    env.accept_ticket("Cart", {"index.html": page('<h1 id="menu">Menu</h1>', '<p>no cart here</p>')},
                      [browser_test("cart-visible", ["UAC-1"], "#cart", "Cart")])  # a regression
    outcome = env.run_job(env.request("freeze"))
    assert outcome.status == "succeeded"  # the job finished; its verdict is the release status
    release = env.release()
    assert release.status == "failed"
    receipt = next(d for d in (doc(env, i) for i in release.evidence_artifact_ids) if isinstance(d, dict) and d.get("kind") == "release_verification")
    assert receipt["status"] == "failed" and receipt["counts"]["failed"] == 1
    with pytest.raises(Conflict):
        env.approve_release(release)
    env.request("freeze")  # a failed release does not block a new attempt


def test_repository_tests_that_fail_block_the_release(env):
    env.accept_ticket("Menu", {"index.html": MENU, "test.cjs": "const t=require('node:test');t('broken',()=>{throw new Error('red')});\n"},
                      [browser_test("menu-visible", ["UAC-1"], "#menu", "Menu")])
    assert env.run_job(env.request("freeze")).status == "succeeded"
    release = env.release()
    assert release.status == "failed"
    receipt = next(d for d in (doc(env, i) for i in release.evidence_artifact_ids) if isinstance(d, dict) and d.get("kind") == "release_verification")
    assert receipt["repo_gate"]["status"] == "failed" and "repository tests" in receipt["reason"]


def test_a_rebuild_or_changed_configuration_is_a_new_target_and_the_old_approval_does_not_transfer(env):
    env.accept_ticket("Menu", {"index.html": MENU, "test.cjs": TEST_JS}, [browser_test("menu-visible", ["UAC-1"], "#menu", "Menu")])
    first = verified_draft(env)
    approved = env.approve_release(first)
    assert approved.status == "approved"
    # Another ticket joins; the same accepted tip is not re-verified silently: a new freeze builds a new target.
    env.accept_ticket("Cart", {"index.html": CART}, [browser_test("cart-visible", ["UAC-1"], "#cart", "Cart")])
    second = verified_draft(env)
    assert second.target_digest != first.target_digest and second.build_artifact_id != first.build_artifact_id
    assert second.status == "draft"  # it needs its own approval; the first one pins the first target only
    with pytest.raises(Conflict):
        env.world.w.approve_release(env.world.user, second.id, second.revision, first.target_artifact_id, first.target_digest,
                                    list(first.evidence_artifact_ids), [])
    # Changing the effective configuration changes the target identity even for the same commit.
    other = json.loads(json.dumps(env.manifest_dict))
    other["env"] = {"CI": "1", "RELEASE_FLAG": "on"}
    env.world.w.discard_release(env.world.user, second.id, second.revision)
    with env.db.write() as s:
        p = s.get(Project, env.pid)
        p.workflow = {**p.workflow, "pipeline": {"manifest": other}}
    third = verified_draft(env)
    t2, t3 = doc(env, second.target_artifact_id), doc(env, third.target_artifact_id)
    assert t3["commit_sha"] == t2["commit_sha"] and t3["config_digest"] != t2["config_digest"]
    assert third.target_digest != second.target_digest


def test_a_freeze_requires_runner_configuration_accepted_work_and_no_parallel_release_job(env):
    with pytest.raises(Invalid, match="no accepted"):
        env.request("freeze")
    env.accept_ticket("Menu", {"index.html": MENU, "test.cjs": TEST_JS}, [browser_test("menu-visible", ["UAC-1"], "#menu", "Menu")])
    job = env.request("freeze")
    with pytest.raises(Conflict, match="already running"):
        env.request("freeze")
    with env.db.read() as s:
        assert len(list(s.scalars(select(Job).where(Job.stage == "release")))) == 1
    assert env.run_job(job).status == "succeeded"


def test_a_new_project_exports_its_whole_history_as_a_patch_and_a_bundle(env, tmp_path):
    a, _ = env.accept_ticket("Menu", {"index.html": MENU, "test.cjs": TEST_JS}, [browser_test("menu-visible", ["UAC-1"], "#menu", "Menu")])
    release = verified_draft(env)
    approved = env.approve_release(release)
    assert env.run_job(env.request("export", approved.id)).status == "succeeded"
    exported = env.release()
    result = exported.export_result
    assert exported.status == "exported" and result["pushed"] is False and result["deployed"] is False
    assert result["base_sha"] == env.base  # the root commit of the managed repository
    with env.db.read() as s:
        patch, bundle = env.store.read_bytes(s, result["patch_artifact_id"]), env.store.read_bytes(s, result["bundle_artifact_id"])
    import subprocess
    git = lambda args, cwd: subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True,
                                           env={"PATH": "/usr/bin:/bin", "HOME": str(cwd), "GIT_CONFIG_NOSYSTEM": "1"}).stdout
    empty = tmp_path / "empty"
    empty.mkdir()
    git(["init", "--quiet"], empty)
    (empty / "release.patch").write_bytes(patch)
    git(["apply", "release.patch"], empty)
    assert (empty / "index.html").read_text() == MENU
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    git(["init", "--quiet"], fresh)
    (fresh / "b.bundle").write_bytes(bundle)
    git(["fetch", "--quiet", "b.bundle", f"{result['ref_in_bundle']}:{result['branch']}"], fresh)
    assert git(["rev-parse", result["branch"]], fresh).decode().strip() == release.accepted_tip


def test_a_crash_before_the_draft_is_published_leaves_no_release_and_the_retry_publishes_exactly_one(env, monkeypatch):
    env.accept_ticket("Menu", {"index.html": MENU, "test.cjs": TEST_JS}, [browser_test("menu-visible", ["UAC-1"], "#menu", "Menu")])
    job = env.request("freeze")
    real = type(env.runtime)._publish

    def crash(self, *args, **kwargs):
        raise RuntimeError("worker died after verification, before publication")
    monkeypatch.setattr(type(env.runtime), "_publish", crash)
    with pytest.raises(RuntimeError, match="worker died"):
        env.run_job(job)
    assert env.release() is None  # the evidence artifacts exist, but nothing was published or approvable
    with env.db.read() as s:
        row = s.get(Job, job.id)
        lease = type("L", (), {"job_id": row.id, "owner": row.lease_owner, "generation": row.lease_generation})()
    from app.workers.queue import Lease
    retry_id = env.queue.fail(Lease(lease.job_id, lease.owner, lease.generation), "worker died", retryable=True)
    assert retry_id
    env.queue.finish_cleanup(job.id, lease.generation)
    monkeypatch.setattr(type(env.runtime), "_publish", real)
    assert env.run_job(retry_id).status == "succeeded"
    with env.db.read() as s:
        assert len(list(s.scalars(select(Release)))) == 1
    assert env.release().status == "draft"

@pytest.mark.parametrize('exact_name, expected_status', [('Save now', 'succeeded'), ('Save', 'failed')])
def test_mixed_release_preserves_legacy_matching_without_downgrading_exact_contract(env, exact_name, expected_status):
    html = page('<button data-testid="save">Save now</button>')
    old, _ = env.accept_ticket('Legacy', {'index.html': html, 'test.cjs': TEST_JS},
                              [browser_test('legacy', ['UAC-1'], 'role=button[name*="save"i]')])
    new, _ = env.accept_ticket('Contract', {'contract.txt': 'Exact locator contract'},
                              [browser_test('exact', ['UAC-1'], f'role=button[name="{exact_name}"]')],
                              ui_contract={'revision': 1, 'controls': [
                                  {'testid': 'save', 'role': 'button', 'name': exact_name, 'purpose': 'Save'}]})
    outcome = env.run_job(env.request('freeze'))
    assert outcome.status == 'succeeded', outcome
    release = env.release()
    with env.db.read() as s:
        acceptance_id = next(i for i in release.evidence_artifact_ids
                             if (s.get(Artifact, i).path or '').endswith('/release-acceptance.json'))
    acceptance = doc(env, acceptance_id)
    assert release.status == ('draft' if expected_status == 'succeeded' else 'failed'), json.dumps(acceptance.get('report'))
    target = doc(env, release.target_artifact_id)
    assert target['legacy_test_ids'] == [f't{old.number}-legacy']
    receipt = next(d for d in (doc(env, i) for i in release.evidence_artifact_ids)
                   if isinstance(d, dict) and d.get('kind') == 'release_verification')
    assert receipt['counts']['passed'] == (2 if expected_status == 'succeeded' else 1), receipt
    assert f't{new.number}-exact' not in target['legacy_test_ids']
