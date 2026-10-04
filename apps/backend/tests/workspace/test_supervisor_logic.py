"""Authorization, snapshot-sync and lifecycle logic. No containers are started."""
import json
import os
import subprocess

import pytest

from app.workspace import (
    AuthorizationError, LimitExceeded, PathViolation, ResourceLimits, WorkspaceError,
)
from app.workspace.gitbroker import ACCEPTED_REF

from .conftest import start


def refs(sup):
    return sup.broker("demo").refs()


def test_attempt_gets_own_worktree_recorded_base_and_source_without_git(stub_sup, manifest):
    base = stub_sup.broker("demo").accepted_sha()
    a, b = start(stub_sup, manifest), start(stub_sup, manifest, ticket="T-2")
    assert a.spec.base_sha == b.spec.base_sha == base
    assert a.spec.attempt_ref != b.spec.attempt_ref
    wa, wb = stub_sup.run_dir(a.ref) / "worktree", stub_sup.run_dir(b.ref) / "worktree"
    assert wa != wb and (wa / ".git").is_file() and (wb / ".git").is_file()
    src = stub_sup.src_dir(a.ref)
    assert src.is_dir() and not (src / ".git").exists()
    assert oct(stub_sup.run_dir(a.ref).stat().st_mode & 0o777) == "0o700"
    spec = json.loads((stub_sup.run_dir(a.ref) / "runspec.json").read_text())
    assert spec["provenance"]["created_by"] == "dev005-standalone-harness"
    assert spec["limits"]["memory_mb"] > 0 and spec["generation"] == 1


def test_credential_must_match_run_and_unknown_runs_fail(stub_sup, manifest):
    a, b = start(stub_sup, manifest), start(stub_sup, manifest, ticket="T-2")
    with pytest.raises(AuthorizationError):
        stub_sup.read_file(a.ref, b.credential, "x")
    with pytest.raises(AuthorizationError):
        stub_sup.read_file(a.ref, "garbage", "x")
    with pytest.raises(AuthorizationError):
        stub_sup.read_file(a.ref, a.credential.rsplit(".", 1)[0] + ".forged", "x")
    with pytest.raises(AuthorizationError):
        stub_sup.run_dir(type(a.ref)("demo", "run-" + "0" * 12))


def test_role_permissions_are_enforced_by_backend(stub_sup, manifest):
    dev = start(stub_sup, manifest)
    qa = start(stub_sup, manifest, role="qa", ticket="T-2")
    po = start(stub_sup, manifest, role="po", ticket="T-3")
    stub_sup.write_file(dev.ref, dev.credential, "a.txt", b"x")
    with pytest.raises(AuthorizationError, match="may not write_file"):
        stub_sup.write_file(qa.ref, qa.credential, "a.txt", b"x")
    with pytest.raises(AuthorizationError, match="may not submit_candidate"):
        stub_sup.submit_candidate(qa.ref, qa.credential, "m")
    with pytest.raises(AuthorizationError):
        stub_sup.read_file(po.ref, po.credential, "a.txt")
    with pytest.raises(AuthorizationError, match="may not run_command"):
        stub_sup.run_command(qa.ref, qa.credential, ["true"])


def test_submit_candidate_commits_on_attempt_ref_only(stub_sup, manifest):
    base = stub_sup.broker("demo").accepted_sha()
    other = start(stub_sup, manifest, ticket="T-2")
    run = start(stub_sup, manifest)
    stub_sup.write_file(run.ref, run.credential, "src/cart.js", b"export const x = 1;\n")
    record = stub_sup.submit_candidate(run.ref, run.credential, "add cart")
    assert record["changed"] and record["accepted"] is False and record["kind"] == "candidate"
    assert record["base_sha"] == base and record["parent_sha"] == base
    assert record["scope_version"] == 1 and record["attempt_ref"] == run.spec.attempt_ref
    now = refs(stub_sup)
    assert now[ACCEPTED_REF] == base
    assert now[other.spec.attempt_ref] == base
    assert now[run.spec.attempt_ref] == record["sha"]
    assert stub_sup.broker("demo").file_at(record["sha"], "src/cart.js") == b"export const x = 1;\n"


def test_checkpoint_is_not_a_candidate_or_accepted(stub_sup, manifest):
    run = start(stub_sup, manifest)
    stub_sup.write_file(run.ref, run.credential, "wip.txt", b"half done")
    record = stub_sup.checkpoint(run.ref, run.credential, "wip")
    assert record["kind"] == "checkpoint" and record["accepted"] is False
    assert refs(stub_sup)[ACCEPTED_REF] == run.spec.base_sha


def test_managed_dirs_and_traversal_are_refused_by_file_tools(stub_sup, manifest, tmp_path):
    run = start(stub_sup, manifest)
    for bad in ("../escape.txt", "/abs.txt", ".git/config", "a/.git/hooks/pre-commit"):
        with pytest.raises(PathViolation):
            stub_sup.write_file(run.ref, run.credential, bad, b"x")
    with pytest.raises(WorkspaceError, match="managed by the runner"):
        stub_sup.write_file(run.ref, run.credential, "node_modules/x.js", b"x")
    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.parametrize("plant", ["symlink_abs", "symlink_chain", "fifo", "git_dir", "git_hook", "huge"])
def test_hostile_sandbox_content_is_rejected_and_nothing_moves(stub_sup, manifest, plant):
    limits = ResourceLimits(max_snapshot_bytes=1024)
    run = start(stub_sup, manifest, limits=limits)
    src = stub_sup.src_dir(run.ref)
    (src / "ok.txt").write_text("fine")
    if plant == "symlink_abs":
        os.symlink("/etc/passwd", src / "evil")
    elif plant == "symlink_chain":
        (src / "a").mkdir()
        os.symlink("..", src / "a" / "b")
        os.symlink("a/b/..", src / "s")
    elif plant == "fifo":
        os.mkfifo(src / "pipe")
    elif plant == "git_dir":
        (src / ".git").mkdir()
    elif plant == "git_hook":
        (src / "sub" / ".git" / "hooks").mkdir(parents=True)
        (src / "sub" / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\ntouch /tmp/pwned\n")
    elif plant == "huge":
        (src / "big.bin").write_bytes(b"0" * 4096)
    before = refs(stub_sup)
    worktree_listing = sorted(p.name for p in (stub_sup.run_dir(run.ref) / "worktree").iterdir())
    with pytest.raises((PathViolation, LimitExceeded)):
        stub_sup.submit_candidate(run.ref, run.credential, "evil")
    assert refs(stub_sup) == before
    assert sorted(p.name for p in (stub_sup.run_dir(run.ref) / "worktree").iterdir()) == worktree_listing
    assert not os.path.exists("/tmp/pwned")


def test_node_modules_and_dist_stay_out_of_commits(stub_sup, manifest):
    run = start(stub_sup, manifest)
    src = stub_sup.src_dir(run.ref)
    (src / "node_modules").mkdir()
    os.symlink("/etc/passwd", src / "node_modules" / "weird")  # excluded: never scanned or committed
    (src / "dist").mkdir()
    (src / "dist" / "bundle.js").write_text("x")
    stub_sup.write_file(run.ref, run.credential, "keep.txt", b"k")
    record = stub_sup.submit_candidate(run.ref, run.credential, "m")
    diff = stub_sup.broker("demo").diff_commits(record["base_sha"], record["sha"])
    assert "keep.txt" in diff and "node_modules" not in diff and "bundle.js" not in diff


def test_inspect_diff_shows_pending_changes_without_committing(stub_sup, manifest):
    run = start(stub_sup, manifest)
    stub_sup.write_file(run.ref, run.credential, "n.txt", b"new")
    assert "n.txt" in stub_sup.inspect_diff(run.ref, run.credential)
    assert refs(stub_sup)[run.spec.attempt_ref] == run.spec.base_sha


def test_cancelled_attempt_cannot_submit_or_use_tools(stub_sup, manifest):
    run = start(stub_sup, manifest)
    stub_sup.write_file(run.ref, run.credential, "late.txt", b"x")
    stub_sup.stop_run(run.ref, "cancelled")
    for call in (
        lambda: stub_sup.submit_candidate(run.ref, run.credential, "late"),
        lambda: stub_sup.write_file(run.ref, run.credential, "b.txt", b"x"),
        lambda: stub_sup.read_file(run.ref, run.credential, "late.txt"),
    ):
        with pytest.raises(AuthorizationError, match="cancelled"):
            call()
    assert refs(stub_sup)[run.spec.attempt_ref] == run.spec.base_sha
    assert json.loads((stub_sup.run_dir(run.ref) / "state.json").read_text())["credential_sha256"] is None


def test_stale_generation_credential_is_rejected_after_renewal(stub_sup, manifest):
    run = start(stub_sup, manifest)
    new_credential = stub_sup.renew_generation(run.ref, generation=2, lease_id="lease-2")
    state = json.loads((stub_sup.run_dir(run.ref) / "state.json").read_text())
    assert (state["generation"], state["lease_id"]) == (2, "lease-2")
    assert json.loads((stub_sup.run_dir(run.ref) / "runspec.json").read_text())["generation"] == 1  # spec stays immutable
    with pytest.raises(AuthorizationError, match="stale generation"):
        stub_sup.write_file(run.ref, run.credential, "old.txt", b"x")
    with pytest.raises(AuthorizationError, match="stale generation"):
        stub_sup.submit_candidate(run.ref, run.credential, "old")
    stub_sup.write_file(run.ref, new_credential, "new.txt", b"x")
    with pytest.raises(WorkspaceError, match="must increase"):
        stub_sup.renew_generation(run.ref, generation=2, lease_id="lease-3")
    assert stub_sup.submit_candidate(run.ref, new_credential, "ok")["generation"] == 2


def test_stop_archives_evidence_before_cleanup_and_is_idempotent(stub_sup, manifest):
    run = start(stub_sup, manifest)
    stub_sup.write_file(run.ref, run.credential, "f.txt", b"x")
    record = stub_sup.submit_candidate(run.ref, run.credential, "m")
    archive = stub_sup.stop_run(run.ref, "stopped")
    names = {p.name for p in archive.iterdir()}
    assert {"runspec.json", "state.json", "manifest.json", "candidates", "attempt.patch", "ARCHIVE-MANIFEST.json"} <= names
    inventory = json.loads((archive / "ARCHIVE-MANIFEST.json").read_text())["files"]
    import hashlib
    for rel, digest in inventory.items():
        assert hashlib.sha256((archive / rel).read_bytes()).hexdigest() == digest
    assert "f.txt" in (archive / "attempt.patch").read_text()
    run_dir = stub_sup.run_dir(run.ref)
    assert not (run_dir / "sandbox").exists() and not (run_dir / "worktree").exists()
    worktrees = subprocess.run(["git", "--git-dir", str(stub_sup.broker("demo").repo), "worktree", "list"],
                               capture_output=True, text=True).stdout
    assert str(run_dir / "worktree") not in worktrees
    assert stub_sup.broker("demo").resolve(run.spec.attempt_ref) == record["sha"]  # evidence ref kept
    assert stub_sup.stop_run(run.ref, "stopped") == archive


def test_stopping_one_run_leaves_other_runs_intact(stub_sup, manifest):
    a, b = start(stub_sup, manifest), start(stub_sup, manifest, ticket="T-2")
    stub_sup.stop_run(a.ref)
    stub_sup.write_file(b.ref, b.credential, "still.txt", b"alive")
    assert (stub_sup.src_dir(b.ref) / "still.txt").read_text() == "alive"


def test_start_attempt_validates_inputs(stub_sup, manifest):
    with pytest.raises(WorkspaceError, match="unknown role"):
        start(stub_sup, manifest, role="admin")
    with pytest.raises(WorkspaceError, match="limit memory_mb"):
        start(stub_sup, manifest, limits=ResourceLimits(memory_mb=10**9))
    with pytest.raises(WorkspaceError, match="limit pids"):
        start(stub_sup, manifest, limits=ResourceLimits(pids=0))
    with pytest.raises(WorkspaceError, match="invalid project id"):
        stub_sup.start_attempt("../evil", ticket_id="T", scope_version=1, role="developer", attempt=1,
                               generation=1, lease_id="l", manifest=manifest)


def test_tampered_manifest_after_start_is_rejected(stub_sup, manifest):
    run = start(stub_sup, manifest)
    path = stub_sup.run_dir(run.ref) / "manifest.json"
    data = json.loads(path.read_text())
    data["env"]["EXTRA"] = "1"
    path.write_text(json.dumps(data))
    with pytest.raises(AuthorizationError, match="manifest changed"):
        stub_sup.read_file(run.ref, run.credential, "x")
