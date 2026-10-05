"""DEV-014 export and combined synchronisation against a real user repository (read-only) and real Git/Docker."""
import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from app.persistence.models import Job, Message, Project, Release
from tests.release.conftest import browser_test, page

HOME = '<h1 id="home">Home</h1>'
MENU = '<h1 id="menu">Menu</h1>'
CART = '<h2 id="cart">Cart</h2>'


def doc(env, artifact_id):
    with env.db.read() as s:
        return json.loads(env.store.read_bytes(s, artifact_id))


def report(env, ids, kind):
    """The JSON report of this kind among evidence that also holds screenshots, traces, logs and patches."""
    for artifact_id in ids:
        try:
            value = doc(env, artifact_id)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("kind") == kind:
            return value
    raise AssertionError("no " + kind)


def blob(env, artifact_id):
    with env.db.read() as s:
        return env.store.read_bytes(s, artifact_id)


def two_accepted(env):
    a, _ = env.accept_ticket("Menu", {"index.html": page(HOME, MENU)}, [browser_test("menu-visible", ["UAC-1"], "#menu", "Menu")],
                             manual=["UAC-M"])
    b, _ = env.accept_ticket("Cart", {"index.html": page(HOME, MENU, CART)}, [browser_test("cart-visible", ["UAC-1"], "#cart", "Cart")])
    return a, b


def drafted(env):
    assert env.run_job(env.request("freeze")).status == "succeeded"
    return env.release()


def messages(env, text):
    with env.db.read() as s:
        return [m.body for m in s.scalars(select(Message).where(Message.thread_id.like("release:%"))) if text in m.body]


def clone_of_user_repo(env, tmp_path, name="clone"):
    target = tmp_path / name
    run_git(["-c", "protocol.file.allow=always", "clone", "--quiet", "--no-hardlinks", str(env.source), str(target)], tmp_path)
    run_git(["config", "user.email", "user@example.com"], target)
    run_git(["config", "user.name", "User"], target)
    return target


def run_git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True,
                          env={"PATH": "/usr/bin:/bin", "HOME": str(cwd), "GIT_CONFIG_NOSYSTEM": "1"}).stdout


def test_export_produces_a_patch_and_a_bundle_that_reproduce_the_release_and_nothing_is_pushed(existing, tmp_path):
    env = existing
    a, b = two_accepted(env)
    release = drafted(env)
    approved = env.approve_release(release, [f"{a.id}:UAC-M"])
    before = env.fingerprint()
    assert env.run_job(env.request("export", approved.id)).status == "succeeded"
    exported = env.release()
    assert exported.status == "exported" and exported.deployment_result is None
    result = exported.export_result
    assert result["pushed"] is False and result["deployed"] is False and result["tip"] == release.accepted_tip
    assert result["base_sha"] == env.base and result["branch"].startswith("release/")
    patch, bundle = blob(env, result["patch_artifact_id"]), blob(env, result["bundle_artifact_id"])
    # The patch applies to the user's repository and reproduces the released tree.
    clone = clone_of_user_repo(env, tmp_path)
    (clone / "release.patch").write_bytes(patch)
    run_git(["apply", "--index", "release.patch"], clone)
    assert (clone / "index.html").read_bytes() == env.broker.file_at(release.accepted_tip, "index.html")
    # The bundle carries exactly the new commits, on top of the user's own commit.
    fresh = clone_of_user_repo(env, tmp_path, "fetch")
    (fresh / "release.bundle").write_bytes(bundle)
    run_git(["fetch", "--quiet", "release.bundle", f"{result['ref_in_bundle']}:{result['branch']}"], fresh)
    assert run_git(["rev-parse", result["branch"]], fresh).decode().strip() == release.accepted_tip
    assert env.fingerprint() == before  # the user's repository was only read
    assert not (env.source / ".git" / "refs" / "heads" / "release").exists()
    assert any("Nothing was pushed" in m for m in messages(env, "exported"))


def test_export_needs_an_approved_release_and_a_repeat_changes_nothing(existing):
    env = existing
    a, b = two_accepted(env)
    release = drafted(env)
    with pytest.raises(Exception, match="approved"):
        env.request("export", release.id)
    approved = env.approve_release(release, [f"{a.id}:UAC-M"])
    assert env.run_job(env.request("export", approved.id)).status == "succeeded"
    with pytest.raises(Exception, match="approved"):
        env.request("export", approved.id)  # already exported: not offered again


def test_export_is_refused_when_the_source_repository_moved_and_the_release_stays_approved(existing):
    env = existing
    a, b = two_accepted(env)
    approved = env.approve_release(drafted(env), [f"{a.id}:UAC-M"])
    env.drift({"index.html": page(HOME, footer="v2")})
    outcome = env.run_job(env.request("export", approved.id))
    assert outcome.status == "failed" and "export is refused" in outcome.error
    assert env.release().status == "approved" and env.release().export_result is None
    assert messages(env, "Synchronise the release")


def test_sync_onto_an_unrelated_source_change_makes_one_replacement_candidate_with_its_own_verification(existing, tmp_path):
    env = existing
    a, b = two_accepted(env)
    first = drafted(env)  # still a draft: the replacement supersedes it
    new_head = env.drift({"notes.txt": "the user's own notes\n"})
    before = env.fingerprint()
    outcome = env.run_job(env.request("sync", first.id))
    assert outcome.status == "succeeded", outcome
    with env.db.read() as s:
        old, new = s.get(Release, first.id), env.release()
        project = s.get(Project, env.pid)
    assert old.status == "failed" and new.status == "draft" and new.id != old.id
    assert new.accepted_tip != old.accepted_tip and new.accepted_tip in project.workflow["release_tips"]
    assert project.workflow["accepted_tip"] == old.accepted_tip  # the managed accepted ref is not re-based by a sync
    target = doc(env, new.target_artifact_id)
    assert target["export_base"] == new_head and target["sync"]["replaces"] == old.id and target["sync"]["affected_tickets"] == []
    assert target["scope_digest"] == env.world.w.scope_digest(new.scope_snapshot)
    assert [e["ticket_id"] for e in new.scope_snapshot] == [a.id, b.id] and all(e["affected_by_sync"] is False for e in new.scope_snapshot)
    # The user's repository was only read; the replacement has its own target and receipt.
    assert env.fingerprint() == before and new.target_digest != old.target_digest
    receipt = report(env, new.evidence_artifact_ids, "release_verification")
    assert receipt["status"] == "passed" and receipt["accepted_tip"] == new.accepted_tip and receipt["counts"]["passed"] == 2
    assert env.approve_release(new).status == "approved"  # no affected ticket: nothing left on the checklist


def test_sync_with_overlapping_changes_marks_affected_tickets_and_only_their_manual_uac_form_the_checklist(existing, tmp_path):
    env = existing
    a, b = two_accepted(env)
    old = env.approve_release(drafted(env), [f"{a.id}:UAC-M"])
    new_head = env.drift({"index.html": page(HOME, footer="v2")})  # the user edited the file the tickets also changed
    assert env.run_job(env.request("sync", old.id)).status == "succeeded"
    new = env.release()
    assert env.release("approved").id == old.id and new.status == "draft"  # approved history is kept, not rewritten
    entries = {e["ticket_id"]: e for e in new.scope_snapshot}
    assert entries[a.id]["affected_by_sync"] and entries[b.id]["affected_by_sync"] and entries[a.id]["overlap_files"] == ["index.html"]
    assert env.world.w.release_checklist(new.scope_snapshot) == [f"{a.id}:UAC-M"]
    sync_receipt = report(env, new.evidence_artifact_ids, "release_sync")
    assert sync_receipt["affected_tickets"] == [a.id, b.id] and "index.html" in sync_receipt["drift_files"]
    assert b"footer>v2" in blob(env, sync_receipt["source_drift_patch_artifact_id"])
    assert b"cart" in blob(env, sync_receipt["combined_patch_artifact_id"])
    from app.domain import Invalid
    with pytest.raises(Invalid, match="checklist"):
        env.approve_release(new)
    approved = env.approve_release(new, [f"{a.id}:UAC-M"])
    assert approved.status == "approved"
    # Exporting the replacement targets the NEW source HEAD: the patch applies there and keeps the user's edit.
    assert env.run_job(env.request("export", approved.id)).status == "succeeded"
    result = env.release().export_result
    assert result["base_sha"] == new_head and result["tip"] == new.accepted_tip
    clone = clone_of_user_repo(env, tmp_path)
    (clone / "release.patch").write_bytes(blob(env, result["patch_artifact_id"]))
    run_git(["apply", "--index", "release.patch"], clone)
    released = (clone / "index.html").read_text()
    assert "footer>v2" in released and 'id="cart"' in released and 'id="menu"' in released
    assert released.encode() == env.broker.file_at(new.accepted_tip, "index.html")


def test_sync_that_conflicts_with_the_source_change_is_blocked_with_the_reason_and_creates_nothing(existing):
    env = existing
    a, b = two_accepted(env)
    old = drafted(env)
    env.drift({"index.html": page('<h1 id="home">Home (renamed by the user)</h1>')})  # the line the release hunks sit next to
    before = env.fingerprint()
    outcome = env.run_job(env.request("sync", old.id))
    assert outcome.status == "failed" and "conflict" in outcome.error and "new ticket" in outcome.error
    with env.db.read() as s:
        rows = list(s.scalars(select(Release)))
    assert [r.id for r in rows] == [old.id] and rows[0].status == "draft"
    assert not [r for r in env.broker.refs() if r.endswith("-sync")]
    assert env.fingerprint() == before
    assert messages(env, "cannot be re-applied")


def test_sync_is_offered_only_for_unexported_releases_and_when_the_source_actually_moved(existing):
    env = existing
    a, b = two_accepted(env)
    release = drafted(env)
    outcome = env.run_job(env.request("sync", release.id))
    assert outcome.status == "failed" and "nothing to synchronise" in outcome.error
    approved = env.approve_release(release, [f"{a.id}:UAC-M"])
    assert env.run_job(env.request("export", approved.id)).status == "succeeded"
    with pytest.raises(Exception, match="not exported"):
        env.request("sync", approved.id)
