import os
import stat
import subprocess

import pytest

from app.workspace import GitBrokerError
from app.workspace.gitbroker import ACCEPTED_REF, EMPTY_TREE, GitBroker


@pytest.fixture
def broker(tmp_path):
    b = GitBroker(tmp_path / "repo.git", tmp_path / "home")
    b.init_project()
    return b


def attempt_ref(n=1):
    return f"refs/heads/attempts/run-{n:012x}"


def test_init_creates_empty_base_on_accepted(broker):
    sha = broker.accepted_sha()
    tree = broker.run(["--git-dir", str(broker.repo), "rev-parse", f"{sha}^{{tree}}"]).decode().strip()
    assert tree == EMPTY_TREE
    assert list(broker.refs()) == [ACCEPTED_REF]


def test_commit_moves_only_the_attempt_ref(broker, tmp_path):
    base = broker.accepted_sha()
    ref2 = attempt_ref(2)
    wt1, wt2 = tmp_path / "w1", tmp_path / "w2"
    broker.create_worktree(attempt_ref(1), wt1, base)
    broker.create_worktree(ref2, wt2, base)
    (wt1 / "a.txt").write_text("one")
    sha, changed = broker.commit_worktree(wt1, attempt_ref(1), base, "add a", author=("Dev", "d@x"))
    assert changed and sha != base
    refs = broker.refs()
    assert refs[ACCEPTED_REF] == base
    assert refs[ref2] == base
    assert refs[attempt_ref(1)] == sha


def test_empty_commit_is_a_noop(broker, tmp_path):
    base = broker.accepted_sha()
    broker.create_worktree(attempt_ref(1), tmp_path / "w", base)
    sha, changed = broker.commit_worktree(tmp_path / "w", attempt_ref(1), base, "nothing", author=("D", "d@x"))
    assert (sha, changed) == (base, False)


def test_broker_refuses_to_write_accepted_or_foreign_refs(broker, tmp_path):
    base = broker.accepted_sha()
    broker.create_worktree(attempt_ref(1), tmp_path / "w", base)
    (tmp_path / "w" / "f").write_text("x")
    with pytest.raises(GitBrokerError, match="attempt refs"):
        broker.commit_worktree(tmp_path / "w", ACCEPTED_REF, base, "m", author=("D", "d@x"))
    with pytest.raises(GitBrokerError, match="not the attempt ref"):
        broker.commit_worktree(tmp_path / "w", attempt_ref(9), base, "m", author=("D", "d@x"))
    with pytest.raises(GitBrokerError):
        broker.create_worktree("refs/heads/accepted", tmp_path / "w2", base)
    assert broker.accepted_sha() == base


def test_commit_refuses_when_attempt_ref_moved(broker, tmp_path):
    base = broker.accepted_sha()
    broker.create_worktree(attempt_ref(1), tmp_path / "w", base)
    (tmp_path / "w" / "f").write_text("x")
    with pytest.raises(GitBrokerError, match="moved unexpectedly"):
        broker.commit_worktree(tmp_path / "w", attempt_ref(1), "1" * 40, "m", author=("D", "d@x"))


def test_repo_hooks_and_filters_are_never_executed(broker, tmp_path):
    canary = tmp_path / "canary"
    hooks = broker.repo / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in ("pre-commit", "commit-msg", "post-commit", "post-checkout", "reference-transaction"):
        hook = hooks / name
        hook.write_text(f"#!/bin/sh\necho {name} >> {canary}\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    # Even config planted in the shared repo config must not turn on filters for undefined drivers.
    base = broker.accepted_sha()
    broker.create_worktree(attempt_ref(1), tmp_path / "w", base)
    (tmp_path / "w" / ".gitattributes").write_text("* filter=evil diff=evil\n")
    (tmp_path / "w" / "f").write_text("x")
    broker.commit_worktree(tmp_path / "w", attempt_ref(1), base, "m", author=("D", "d@x"))
    assert not canary.exists()


def test_user_global_config_does_not_leak_into_broker(broker, tmp_path, monkeypatch):
    evil_cfg = tmp_path / "gitconfig"
    canary = tmp_path / "global-hook-ran"
    hookdir = tmp_path / "ghooks"
    hookdir.mkdir()
    hook = hookdir / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {canary}\n")
    hook.chmod(0o755)
    evil_cfg.write_text(f"[core]\n\thooksPath = {hookdir}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(evil_cfg))
    monkeypatch.setenv("HOME", str(tmp_path))
    base = broker.accepted_sha()
    broker.create_worktree(attempt_ref(1), tmp_path / "w", base)
    (tmp_path / "w" / "f").write_text("x")
    broker.commit_worktree(tmp_path / "w", attempt_ref(1), base, "m", author=("D", "d@x"))
    assert not canary.exists()


def test_export_commit_and_file_at(broker, tmp_path):
    base = broker.accepted_sha()
    broker.create_worktree(attempt_ref(1), tmp_path / "w", base)
    (tmp_path / "w" / "pkg.json").write_text("{}")
    sha, _ = broker.commit_worktree(tmp_path / "w", attempt_ref(1), base, "m", author=("D", "d@x"))
    out = tmp_path / "export"
    broker.export_commit(sha, out)
    assert (out / "pkg.json").read_text() == "{}"
    assert not (out / ".git").exists()
    assert broker.file_at(sha, "pkg.json") == b"{}"
    assert broker.file_at(sha, "missing") is None
    assert "pkg.json" in broker.diff_commits(base, sha)


def test_worktree_removal_prunes_metadata(broker, tmp_path):
    base = broker.accepted_sha()
    broker.create_worktree(attempt_ref(1), tmp_path / "w", base)
    broker.remove_worktree(tmp_path / "w")
    assert not (tmp_path / "w").exists()
    listing = subprocess.run(["git", "--git-dir", str(broker.repo), "worktree", "list"], capture_output=True, text=True).stdout
    assert str(tmp_path / "w") not in listing
