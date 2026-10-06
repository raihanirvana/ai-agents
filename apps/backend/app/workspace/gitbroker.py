"""Trusted Git broker.

Runs on the supervisor host only. Git is invoked with a scrubbed environment and
config: no system/global config, hooks disabled, no fsmonitor/external diff/
textconv, protocols disabled. Only attempt refs are written here; the accepted
ref is created once at project init and is otherwise the integrator's (DEV-012).
"""

from __future__ import annotations

import os
import fcntl
import re
import shutil
import subprocess
from pathlib import Path
from contextlib import contextmanager

from .errors import GitBrokerError

ACCEPTED_REF = "refs/heads/accepted"
ATTEMPT_PREFIX = "refs/heads/attempts/"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
ZERO_SHA = "0" * 40
_SHA = re.compile(r"^[0-9a-f]{40}$")
_ATTEMPT_REF = re.compile(r"^refs/heads/attempts/run-[0-9a-f]{12}$")
_SUPERVISOR = ("AI Dev Team Supervisor", "supervisor@localhost")

_CONFIG = (
    ("core.hooksPath", os.devnull),
    ("core.fsmonitor", "false"),
    ("core.attributesFile", os.devnull),
    ("core.untrackedCache", "false"),
    ("core.autocrlf", "false"),
    ("core.symlinks", "true"),
    ("protocol.allow", "never"),
    ("commit.gpgsign", "false"),
    ("tag.gpgsign", "false"),
    ("gc.auto", "0"),
    ("transfer.fsckObjects", "true"),
    ("advice.detachedHead", "false"),
)


def is_sha(value: str) -> bool:
    return isinstance(value, str) and _SHA.match(value) is not None


def is_attempt_ref(ref: str) -> bool:
    return isinstance(ref, str) and _ATTEMPT_REF.match(ref) is not None


class GitBroker:
    def __init__(self, repo_dir: Path, scratch_home: Path) -> None:
        self.repo = Path(repo_dir)
        self.home = Path(scratch_home)
        self.home.mkdir(parents=True, exist_ok=True)
        git = shutil.which("git")
        if git is None:
            raise GitBrokerError("git executable not found")
        self.git = git

    def _env(self, author: tuple[str, str] = _SUPERVISOR) -> dict[str, str]:
        env = {
            "PATH": f"{os.path.dirname(self.git)}:/usr/bin:/bin",
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home),
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CEILING_DIRECTORIES": str(self.repo.parent),
            "GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1],
            "GIT_COMMITTER_NAME": _SUPERVISOR[0], "GIT_COMMITTER_EMAIL": _SUPERVISOR[1],
            "GIT_CONFIG_COUNT": str(len(_CONFIG)),
        }
        for i, (key, value) in enumerate(_CONFIG):
            env[f"GIT_CONFIG_KEY_{i}"] = key
            env[f"GIT_CONFIG_VALUE_{i}"] = value
        return env

    def run(self, args: list[str], *, cwd: Path | None = None, input: bytes | None = None,
            extra_env: dict[str, str] | None = None, check: bool = True) -> bytes:
        env = self._env()
        if extra_env:
            env.update(extra_env)
        proc = subprocess.run([self.git, *args], cwd=cwd, env=env, input=input,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if check and proc.returncode != 0:
            raise GitBrokerError(f"git {args[0]} failed ({proc.returncode}): {proc.stderr.decode(errors='replace').strip()}")
        return proc.stdout

    def _bare(self, *args: str, **kw) -> bytes:
        return self.run(["--git-dir", str(self.repo), *args], **kw)

    # -- project -------------------------------------------------------------
    def init_project(self, *, resume: bool = False) -> str:
        """Create the bare repo with the supervisor's initial empty commit on accepted."""
        if self.repo.is_symlink():
            raise GitBrokerError('initialization refuses a symlink repository')
        if self.repo.exists() and not resume:
            raise GitBrokerError("repository already exists")
        if self.repo.exists():
            if self._bare('rev-parse', '--is-bare-repository').decode().strip() != 'true':
                raise GitBrokerError('initialization requires a managed bare repository')
            refs = self.refs()
            if refs:
                if set(refs) != {ACCEPTED_REF}:
                    raise GitBrokerError('initialization cannot replace existing refs')
                sha = self.accepted_sha()
                tree = self._bare('rev-parse', sha + '^{tree}').decode().strip()
                history = self._bare('rev-list', '--count', sha).decode().strip()
                if tree != EMPTY_TREE or history != '1':
                    raise GitBrokerError('initialization cannot accept existing application code')
                return sha
        self.repo.parent.mkdir(parents=True, exist_ok=True)
        self.run(["init", "--bare", "--initial-branch=accepted", str(self.repo)])
        tree = self._bare("hash-object", "-t", "tree", "-w", "--stdin", input=b"").decode().strip()
        if tree != EMPTY_TREE:
            raise GitBrokerError("unexpected empty tree id")
        sha = self._bare("commit-tree", tree, "-m", "Initial empty commit (supervisor technical base)").decode().strip()
        self._bare("update-ref", ACCEPTED_REF, sha, ZERO_SHA)
        return sha

    def resolve(self, ref: str) -> str:
        out = self._bare("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).decode().strip()
        if not is_sha(out):
            raise GitBrokerError(f"cannot resolve {ref}")
        return out

    def accepted_sha(self) -> str:
        return self.resolve(ACCEPTED_REF)

    def refs(self) -> dict[str, str]:
        out = self._bare("for-each-ref", "--format=%(refname) %(objectname)").decode()
        return dict(line.split(" ", 1) for line in out.splitlines() if line)

    def release_shas(self) -> set[str]:
        """Commits the release machinery pinned under refs/releases/ (never accepted or attempt refs)."""
        return {sha for ref, sha in self.refs().items() if ref.startswith("refs/releases/")}

    # -- attempts ------------------------------------------------------------
    def create_worktree(self, attempt_ref: str, worktree: Path, base_sha: str) -> None:
        if not is_attempt_ref(attempt_ref) or not is_sha(base_sha):
            raise GitBrokerError("invalid attempt ref or base sha")
        branch = attempt_ref.removeprefix("refs/heads/")
        with self._ref_lock():
            self._bare("worktree", "add", "-b", branch, str(worktree), base_sha)

    @contextmanager
    def _ref_lock(self):
        with open(self.repo / "supervisor-commit.lock", "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def remove_worktree(self, worktree: Path) -> None:
        self._bare("worktree", "remove", "--force", str(worktree), check=False)
        self._bare("worktree", "prune", check=False)
        if Path(worktree).exists():
            shutil.rmtree(worktree, ignore_errors=True)

    def head(self, worktree: Path) -> str:
        return self.run(["rev-parse", "--verify", "HEAD"], cwd=worktree).decode().strip()

    def commit_worktree(self, worktree: Path, attempt_ref: str, expected_head: str,
                        message: str, *, author: tuple[str, str]) -> tuple[str, bool]:
        """Commit the worktree onto attempt_ref only. Returns (sha, changed)."""
        # Different runs share refs. Serialize writers before comparing the ref set.
        with self._ref_lock():
            return self._commit_worktree(worktree, attempt_ref, expected_head, message, author=author)

    def _commit_worktree(self, worktree: Path, attempt_ref: str, expected_head: str,
                         message: str, *, author: tuple[str, str]) -> tuple[str, bool]:
        if not is_attempt_ref(attempt_ref):
            raise GitBrokerError("broker only writes attempt refs")
        branch = self.run(["symbolic-ref", "-q", "HEAD"], cwd=worktree).decode().strip()
        if branch != attempt_ref:
            raise GitBrokerError("worktree HEAD is not the attempt ref")
        if self.head(worktree) != expected_head:
            raise GitBrokerError("attempt ref moved unexpectedly")
        before = self.refs()
        self.run(["add", "-A", "--", "."], cwd=worktree)
        status = subprocess.run([self.git, "diff", "--cached", "--quiet", "--no-ext-diff"], cwd=worktree,
                                env=self._env(), capture_output=True).returncode
        if status == 0:
            return expected_head, False
        self.run(["commit", "--no-verify", "--no-gpg-sign", "-q", "-m", message], cwd=worktree,
                 extra_env={"GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1]})
        after = self.refs()
        changed_refs = {r for r in set(before) | set(after) if before.get(r) != after.get(r)}
        if changed_refs != {attempt_ref}:
            raise GitBrokerError(f"unexpected ref changes during commit: {sorted(changed_refs)}")
        return self.head(worktree), True

    def diff_cached(self, worktree: Path, *, stat_only: bool = False, max_bytes: int = 1024 * 1024,
                    path: str | None = None) -> str:
        self.run(["add", "-A", "--", "."], cwd=worktree)
        args = ["diff", "--cached", "--no-ext-diff", "--no-textconv", "--no-color"]
        if stat_only:
            args.append("--stat")
        if path is not None:
            from .fsutil import validate_relpath
            validate_relpath(path)
        data = self.run([*args, 'HEAD', '--', *([path] if path is not None else [])], cwd=worktree)
        if len(data) > max_bytes:
            raise GitBrokerError('diff exceeds transport bound; request stat or an individual file')
        return data.decode(errors='replace')

    def diff_commits(self, base: str, head: str, *, max_bytes: int = 4 * 1024 * 1024) -> str:
        if not (is_sha(base) and is_sha(head)):
            raise GitBrokerError("invalid sha")
        data = self._bare('diff', '--no-ext-diff', '--no-textconv', '--no-color', '--binary', base, head)
        if len(data) > max_bytes:
            raise GitBrokerError('commit diff exceeds byte bound; cannot silently truncate review evidence')
        return data.decode(errors='replace')

    def read_committed_file(self, sha: str, path: str, *, max_bytes: int = 50 * 1024 * 1024) -> bytes | None:
        from .fsutil import validate_relpath
        if not is_sha(sha):
            raise GitBrokerError('invalid sha')
        validate_relpath(path)
        entry = self._bare('ls-tree', sha, '--', path)
        if not entry:
            return None
        if entry.split(None, 1)[0] not in (b'100644', b'100755'):
            raise GitBrokerError('committed file must be a regular blob')
        data = self._bare('cat-file', 'blob', sha + ':' + path)
        if len(data) > max_bytes:
            raise GitBrokerError('committed file exceeds byte bound')
        return data

    def export_commit(self, sha: str, dest: Path) -> None:
        """Materialise a commit into an empty directory using a throwaway index."""
        if not is_sha(sha):
            raise GitBrokerError("invalid sha")
        dest.mkdir(parents=True, exist_ok=True)
        index = dest.parent / f".index-{os.getpid()}-{sha[:8]}"
        try:
            env = {"GIT_INDEX_FILE": str(index)}
            self._bare("read-tree", sha, extra_env=env)
            self._bare("--work-tree", str(dest), "checkout-index", "--all", "--force", extra_env=env)
        finally:
            if index.exists():
                index.unlink()

    def file_at(self, sha: str, path: str) -> bytes | None:
        if not is_sha(sha):
            raise GitBrokerError("invalid sha")
        proc = subprocess.run([self.git, "--git-dir", str(self.repo), "cat-file", "blob", f"{sha}:{path}"],
                              env=self._env(), capture_output=True, timeout=60)
        return proc.stdout if proc.returncode == 0 else None
