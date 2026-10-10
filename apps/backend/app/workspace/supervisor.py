"""Trusted workspace supervisor (DEV-005 standalone harness).

This is an experiment/foundation harness, not the product scheduler: run specs,
generations and leases are supplied by the caller and persisted here. DEV-010
rewires authorization and usage to the product database and jobs.

Layout under `root` (gitignored runtime data):
  <project>/repo.git                    bare repo, supervisor-only (accepted ref + attempt refs)
  <project>/runs/<run_id>/              0700; runspec.json, state.json, manifest.json
      worktree/                         Git worktree (supervisor-owned)
      sandbox/src/                      source snapshot mounted in the container (no .git)
      verify/<build_id>/src/            clean export of a candidate used for build/smoke
      builds/<build_id>/artifact/       copied build output
      evidence/                         command records, logs, target manifests
  <project>/archive/<run_id>/           evidence archived on stop, with checksums
"""

from __future__ import annotations

import hmac
import fcntl
import json
import os
import math
import re
import secrets
import shutil
import time
import uuid
import tempfile
import stat
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any

from . import fsutil
from .errors import AuthorizationError, SandboxError, WorkspaceError
from .gitbroker import GitBroker, is_sha
from .manifest import RunnerManifest, digest_of, parse_manifest
from .runspec import (
    PROJECT_ID, ROLES, ResourceLimits, RunRef, RunSpec, RunStore, atomic_write_json,
    hash_credential, utcnow,
)
from .sandbox import CommandResult, DockerSandbox, sha256_bytes

ROLE_OPERATIONS: dict[str, frozenset[str]] = {
    "developer": frozenset({"read_file", "write_file", "delete_file", "list_files", "run_command",
                            "run_phase", "inspect_diff", "submit_candidate", "checkpoint"}),
    "qa": frozenset({"read_file", "list_files", "run_phase", "inspect_diff"}),
    "technical-lead": frozenset({"read_file", "list_files", "inspect_diff"}),
    "po": frozenset(),
}
_AGENT_AUTHOR = {"developer": ("AI Developer", "developer@localhost")}


@dataclass(frozen=True)
class StartedRun:
    ref: RunRef
    credential: str
    spec: RunSpec


def _make_credential(run_id: str, generation: int) -> str:
    return f"{run_id}.{generation}.{secrets.token_urlsafe(32)}"


def serialized_operation(method):
    """Keep target execution, file tools and snapshots mutually exclusive per run."""
    @wraps(method)
    def wrapped(self, ref, *args, **kwargs):
        with self._store(ref).lock("operation.lock"):
            return method(self, ref, *args, **kwargs)
    return wrapped


class WorkspaceSupervisor:
    def __init__(self, root: Path, *, sandbox: DockerSandbox | None = None) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        id_file = self.root / ".supervisor-id"
        lock_fd = os.open(self.root / '.supervisor-id.lock',
                          os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(lock_fd).st_mode) or os.fstat(lock_fd).st_uid != os.getuid():
                raise WorkspaceError('supervisor ID lock is not a trusted regular file')
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                fd = os.open(id_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            except FileNotFoundError:
                fd, temporary = tempfile.mkstemp(dir=self.root, prefix='.supervisor-id-')
                try:
                    with os.fdopen(fd, 'w') as output:
                        output.write(uuid.uuid4().hex + '\n')
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, id_file)
                    parent = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(parent)
                    finally:
                        os.close(parent)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
                fd = os.open(id_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            with os.fdopen(fd, 'r') as identity:
                info = os.fstat(identity.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    raise WorkspaceError('supervisor ID is not a trusted regular file')
                self.supervisor_id = identity.read(64).strip()
                if info.st_size > 64 or not re.fullmatch(r'[0-9a-f]{32}', self.supervisor_id):
                    raise WorkspaceError('supervisor ID is invalid; do not replace an existing ownership identity')
        finally:
            os.close(lock_fd)
        self.sandbox = sandbox or DockerSandbox(supervisor_id=self.supervisor_id, dependency_cache=self.root / ".dependency-cache")
        self._scratch_home = self.root / ".git-home"

    # -- paths ---------------------------------------------------------------
    def _project_dir(self, project_id: str) -> Path:
        if not PROJECT_ID.match(project_id):
            raise WorkspaceError("invalid project id")
        return self.root / project_id

    def broker(self, project_id: str) -> GitBroker:
        return GitBroker(self._project_dir(project_id) / "repo.git", self._scratch_home)

    def run_dir(self, ref: RunRef) -> Path:
        path = self._project_dir(ref.project_id) / "runs" / ref.run_id
        if not path.is_dir():
            raise AuthorizationError("unknown run")
        return path

    def _store(self, ref: RunRef) -> RunStore:
        return RunStore(self.run_dir(ref))

    def src_dir(self, ref: RunRef) -> Path:
        return self.run_dir(ref) / "sandbox" / "src"

    # -- project -------------------------------------------------------------
    def create_project(self, project_id: str) -> str:
        pdir = self._project_dir(project_id)
        if pdir.exists():
            raise WorkspaceError("project already exists")
        pdir.mkdir(parents=True, mode=0o700)
        (pdir / "runs").mkdir(mode=0o700)
        return self.broker(project_id).init_project()

    # -- attempts ------------------------------------------------------------
    def start_attempt(self, project_id: str, *, ticket_id: str, scope_version: int, role: str, attempt: int,
                      generation: int, lease_id: str, manifest: RunnerManifest, limits: ResourceLimits | None = None,
                      allow_install_egress: bool = False, provenance: dict[str, Any] | None = None,
                      run_id: str | None = None) -> StartedRun:
        if role not in ROLES:
            raise WorkspaceError(f"unknown role {role!r}")
        limits = limits or ResourceLimits()
        limits.validate()
        for name, value in (("attempt", attempt), ("generation", generation), ("scope_version", scope_version)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise WorkspaceError(f"{name} must be a positive integer")
        if not ticket_id or not lease_id:
            raise WorkspaceError("ticket_id and lease_id are required")
        broker = self.broker(project_id)
        base_sha = broker.accepted_sha()
        run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        ref = RunRef(project_id, run_id)
        run_dir = self._project_dir(project_id) / "runs" / run_id
        run_dir.mkdir(mode=0o700)
        spec = RunSpec(
            run_id=run_id, project_id=project_id, ticket_id=ticket_id, scope_version=scope_version, role=role,
            attempt=attempt, generation=generation, lease_id=lease_id, base_sha=base_sha,
            attempt_ref=f"refs/heads/attempts/{run_id}", manifest_digest=manifest.digest, limits=limits,
            allow_install_egress=allow_install_egress,
            provenance={"created_by": "dev005-standalone-harness", "created_at": utcnow(),
                        "supervisor_id": self.supervisor_id, **(provenance or {})},
        )
        credential = _make_credential(run_id, generation)
        store = RunStore(run_dir)
        store.write_spec(spec)
        atomic_write_json(run_dir / "manifest.json", manifest.to_dict())
        store.write_state({"status": "active", "generation": generation, "lease_id": lease_id,
                           "credential_sha256": hash_credential(credential), "command_count": 0})
        (run_dir / "evidence").mkdir(mode=0o700)
        (run_dir / "sandbox").mkdir(mode=0o700)
        try:
            broker.create_worktree(spec.attempt_ref, run_dir / "worktree", base_sha)
            src = run_dir / "sandbox" / "src"
            src.mkdir(mode=0o777)
            os.chmod(src, 0o777)
            self._materialise_snapshot(run_dir / "worktree", src, manifest, limits)
        except BaseException:
            self._cleanup_dirs(ref, spec)
            raise
        return StartedRun(ref, credential, spec)

    def _materialise_snapshot(self, worktree: Path, src: Path, manifest: RunnerManifest, limits: ResourceLimits) -> None:
        entries = fsutil.scan_tree(worktree, skip_top=[".git"], exclude=manifest.exclude_from_sync,
                                   limits=fsutil.TreeLimits(limits.max_snapshot_files, limits.max_snapshot_bytes))
        fsutil.copy_entries(worktree, entries, src, sandbox_visible=True)

    def renew_generation(self, ref: RunRef, *, generation: int, lease_id: str) -> str:
        """New lease generation: rotates credential and stops containers of the old one."""
        store = self._store(ref)
        with store.lock():
            state = store.state()
            if state["status"] != "active":
                raise AuthorizationError(f"run is {state['status']}")
            if isinstance(generation, bool) or not isinstance(generation, int) or generation <= state["generation"]:
                raise WorkspaceError("generation must increase")
            if not isinstance(lease_id, str) or not lease_id:
                raise WorkspaceError("lease_id is required")
            credential = _make_credential(ref.run_id, generation)
            state.update(generation=generation, lease_id=lease_id, credential_sha256=hash_credential(credential))
            store.write_state(state)
        # Revocation happens before waiting, so an in-flight command can observe it.
        with store.lock("operation.lock"):
            self._kill_run_containers(ref, older_than=generation)
        return credential

    # -- authorisation ---------------------------------------------------------
    def authorize(self, ref: RunRef, credential: str, operation: str) -> tuple[RunSpec, RunnerManifest, RunStore]:
        """Identity comes from persisted state; model-supplied arguments never choose it."""
        store = self._store(ref)
        spec, state = store.spec(), store.state()
        if state["status"] != "active":
            raise AuthorizationError(f"run is {state['status']}")
        parts = credential.split(".") if isinstance(credential, str) else []
        if len(parts) != 3 or parts[0] != ref.run_id or not parts[1].isdigit():
            raise AuthorizationError("credential does not belong to this run")
        if int(parts[1]) != state["generation"]:
            raise AuthorizationError("stale generation")
        expected = state.get("credential_sha256")
        if not expected or not hmac.compare_digest(hash_credential(credential), expected):
            raise AuthorizationError("invalid credential")
        if spec.run_id != ref.run_id or spec.project_id != ref.project_id:
            raise AuthorizationError("run identity mismatch")
        if operation not in ROLE_OPERATIONS.get(spec.role, frozenset()):
            raise AuthorizationError(f"role {spec.role!r} may not {operation}")
        manifest = parse_manifest(json.loads((store.dir / "manifest.json").read_text()))
        if manifest.digest != spec.manifest_digest:
            raise AuthorizationError("manifest changed since the run was created")
        return spec, manifest, store

    def _require_active(self, ref: RunRef) -> tuple[RunSpec, RunnerManifest, RunStore]:
        store = self._store(ref)
        spec, state = store.spec(), store.state()
        if state["status"] != "active":
            raise AuthorizationError(f"run is {state['status']}")
        manifest = parse_manifest(json.loads((store.dir / "manifest.json").read_text()))
        if manifest.digest != spec.manifest_digest:
            raise AuthorizationError("manifest changed since the run was created")
        return spec, manifest, store

    # -- command execution --------------------------------------------------
    def _labels(self, spec: RunSpec, generation: int) -> dict[str, str]:
        return self.sandbox.labels(project_id=spec.project_id, run_id=spec.run_id, attempt=spec.attempt, generation=generation)

    def _reserve_command(self, store: RunStore, spec: RunSpec, generation: int) -> tuple[int, int]:
        with store.lock():
            state = store.state()
            if state["status"] != "active":
                raise AuthorizationError(f"run is {state['status']}")
            if state["generation"] != generation:
                raise AuthorizationError("stale generation")
            if state["command_count"] >= spec.limits.max_commands:
                raise WorkspaceError("command cap for this run is exhausted")
            state["command_count"] += 1
            store.write_state(state)
            return state["command_count"], state["generation"]

    def _execute(self, ref: RunRef, spec: RunSpec, manifest: RunnerManifest, store: RunStore, *, source: Path,
                 argv: list[str], network: str, timeout_s: float, label: str, secrets_to_redact: list[str]) -> CommandResult:
        generation = int(secrets_to_redact[0].split(".")[1])
        seq, generation = self._reserve_command(store, spec, generation)
        running = [c for c in self.sandbox.owned(run_id=ref.run_id, include_stopped=False)]
        if running:
            raise SandboxError("another sandbox container of this run is still running")
        name = self.sandbox.container_name(spec.project_id, spec.run_id, generation)
        timeout_s = min(timeout_s, spec.limits.command_timeout_s)
        image_id = self.sandbox.image_id(manifest.image)
        result = self.sandbox.run(
            name=name, image=image_id, source=source, argv=argv, network=network, limits=spec.limits,
            env=manifest.env, labels=self._labels(spec, generation), timeout_s=timeout_s, secrets=secrets_to_redact,
            is_cancelled=lambda: self._revoked(store, generation),
            install_phase=label == 'phase-install',
        )
        self._record_command(store.dir / "evidence", seq, label, spec, generation, manifest, result, image_id=image_id)
        return result

    def _record_command(self, evidence: Path, seq: int, label: str, spec: RunSpec, generation: int,
                        manifest: RunnerManifest, result: CommandResult, *, image_id: str) -> dict[str, Any]:
        base = f"cmd-{seq:04d}-{label}"
        (evidence / f"{base}.stdout.log").write_bytes(result.stdout)
        (evidence / f"{base}.stderr.log").write_bytes(result.stderr)
        if result.cancelled:
            (evidence / f"final-{result.container}.log").write_bytes(result.stdout + result.stderr)
        record = {
            "seq": seq, "label": label, "run_id": spec.run_id, "generation": generation,
            "argv": [re.sub(r"run-[0-9a-f]{12}\.\d+\.[A-Za-z0-9_-]+", "[REDACTED]", a)
                     for a in result.argv], "network": result.network, "exit_code": result.exit_code,
            "timed_out": result.timed_out, "cancelled": result.cancelled, "oom_killed": result.oom_killed,
            "duration_s": result.duration_s, "peak_memory_bytes": result.peak_memory_bytes,
            "seed_inventory_digest": result.seed_inventory_digest, "memory_observation": result.memory_observation, "truncated": result.truncated, "image": manifest.image,
            "image_id": image_id, "manifest_digest": manifest.digest,
            "container_network": "none",
            "dependency_acquisition": ('verified-install-snapshot' if result.cache_key else
                                       'verified-npm-tarballs' if result.network == "egress" else "none"),
            "execution_kind": "cache_reuse" if result.cache_key else "executed",
            "cache_key": result.cache_key, "cache_origin": result.cache_origin,
            "limits": {"memory_mb": spec.limits.memory_mb, "cpus": spec.limits.cpus, "pids": spec.limits.pids,
                       "work_mb": spec.limits.work_mb, "tmpfs_mb": spec.limits.tmpfs_mb,
                       "max_snapshot_bytes": spec.limits.max_snapshot_bytes,
                       "max_snapshot_files": spec.limits.max_snapshot_files},
            "env_names": sorted(manifest.env), "stdout_file": f"{base}.stdout.log", "stderr_file": f"{base}.stderr.log",
            "stdout_sha256": sha256_bytes(result.stdout), "stderr_sha256": sha256_bytes(result.stderr),
            "recorded_at": utcnow(),
        }
        atomic_write_json(evidence / f"{base}.json", record)
        return record

    @serialized_operation
    def run_command(self, ref: RunRef, credential: str, argv: list[str], *, timeout_s: float | None = None) -> CommandResult:
        """Agent-chosen command, always inside the sandbox with no network."""
        spec, manifest, store = self.authorize(ref, credential, "run_command")
        if not (isinstance(argv, list) and argv and all(isinstance(a, str) and "\0" not in a for a in argv)):
            raise WorkspaceError("argv must be a non-empty list of strings")
        if timeout_s is not None and (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
                                      or not math.isfinite(timeout_s) or timeout_s <= 0):
            raise WorkspaceError("timeout_s must be finite and positive")
        return self._execute(ref, spec, manifest, store, source=self.src_dir(ref), argv=argv, network="none",
                             timeout_s=timeout_s or spec.limits.command_timeout_s, label="run_command",
                             secrets_to_redact=[credential])

    @serialized_operation
    def run_phase(self, ref: RunRef, credential: str, phase: str) -> CommandResult:
        spec, manifest, store = self.authorize(ref, credential, "run_phase")
        if phase not in manifest.commands or phase == "start":
            raise WorkspaceError("phase must be install, build or test (use start_target for start)")
        cmd = manifest.commands[phase]
        if cmd.network == "egress" and not spec.allow_install_egress:
            raise AuthorizationError("this run is not allowed install egress")
        return self._execute(ref, spec, manifest, store, source=self.src_dir(ref), argv=list(cmd.argv),
                             network=cmd.network, timeout_s=cmd.timeout_s, label=f"phase-{phase}",
                             secrets_to_redact=[credential])

    # -- file tools (sandbox snapshot only) ----------------------------------
    @serialized_operation
    def read_file(self, ref: RunRef, credential: str, path: str) -> bytes:
        with self._store(ref).lock():
            self.authorize(ref, credential, "read_file")
            return fsutil.read_file_beneath(self.src_dir(ref), path)

    @serialized_operation
    def write_file(self, ref: RunRef, credential: str, path: str, data: bytes) -> None:
        with self._store(ref).lock():
            spec, manifest, _ = self.authorize(ref, credential, "write_file")
            top = path.split("/", 1)[0]
            if top in manifest.exclude_from_sync:
                raise WorkspaceError(f"{top} is managed by the runner, not by file tools")
            if len(data) > min(50 * 1024 * 1024, spec.limits.max_snapshot_bytes):
                raise WorkspaceError("file exceeds snapshot byte limit")
            fsutil.write_file_beneath(self.src_dir(ref), path, data)

    @serialized_operation
    def change_file(self, ref: RunRef, credential: str, path: str, *, expected_digest: str,
                    content: str | None = None, old_text: str | None = None,
                    new_text: str | None = None, edits: list[dict] | None = None) -> dict:
        """Compare-and-swap under the same operation/state locks as target commands.

        Empty expected_digest means create only; edits require a current SHA-256
        and exactly one matching occurrence. No Git/symlink paths are trusted.
        """
        with self._store(ref).lock():
            from .errors import EditConflict
            spec, manifest, _ = self.authorize(ref, credential, 'write_file')
            if path.split('/', 1)[0] in manifest.exclude_from_sync:
                raise WorkspaceError('path is managed by the runner')
            if (not isinstance(expected_digest, str) or
                    (expected_digest and (len(expected_digest) != 64 or
                     any(c not in '0123456789abcdef' for c in expected_digest)))):
                raise WorkspaceError('expected_digest must be a SHA-256 or empty for create only')
            try:
                current = fsutil.read_file_beneath(self.src_dir(ref), path,
                                                 max_bytes=spec.limits.max_snapshot_bytes)
            except FileNotFoundError:
                current = None
            actual = sha256_bytes(current) if current is not None else ''
            if not hmac.compare_digest(actual, expected_digest):
                raise EditConflict('file changed or exists; refresh read_file before retrying',
                                   path=path, digest=actual)
            if edits is not None and (content is not None or old_text is not None or new_text is not None):
                raise WorkspaceError('edits cannot be combined with content/old_text/new_text')
            if old_text is not None or edits is not None:
                changes = edits if edits is not None else [{'old_text': old_text, 'new_text': new_text}]
                if (not isinstance(changes, list) or not 1 <= len(changes) <= 10 or
                        any(not isinstance(e, dict) or set(e) != {'old_text', 'new_text'} or
                            not isinstance(e['old_text'], str) or not e['old_text'] or
                            not isinstance(e['new_text'], str) for e in changes) or
                        sum(len(e['old_text']) + len(e['new_text']) for e in changes) > 64000):
                    raise WorkspaceError('edits requires 1..10 exact replacements, at most 64000 characters')
                if current is None or not expected_digest:
                    raise WorkspaceError('edit requires an existing file and its current digest')
                text = current.decode('utf-8')
                for index, edit in enumerate(changes):
                    matches = text.count(edit['old_text'])
                    if matches != 1:
                        original = current.decode('utf-8')
                        offset = max(0, original.find(edit['old_text'][:80]) - 100)
                        raise EditConflict('old_text must match exactly once; add unique surrounding context',
                            path=path, digest=actual, edit_index=index, matches=matches,
                            read_offset=offset, current_excerpt=original[offset:offset + 400],
                            next='No changes were written. Refresh the relevant page and retry the whole batch.')
                    text = text.replace(edit['old_text'], edit['new_text'], 1)
                    if len(text.encode('utf-8')) > min(50 * 1024 * 1024, spec.limits.max_snapshot_bytes):
                        raise WorkspaceError('file exceeds snapshot byte limit')
                content = text
            if content is None:
                if current is None:
                    raise WorkspaceError('cannot delete a missing file')
                self.authorize(ref, credential, 'delete_file')
                fsutil.remove_beneath(self.src_dir(ref), path)
                return {'path': path, 'deleted': True, 'digest': None}
            data = content.encode('utf-8')
            if len(data) > min(50 * 1024 * 1024, spec.limits.max_snapshot_bytes):
                raise WorkspaceError('file exceeds snapshot byte limit')
            fsutil.atomic_write_file_beneath(self.src_dir(ref), path, data)
            return {'path': path, 'digest': sha256_bytes(data), 'bytes': len(data),
                    'operation': 'edit' if old_text is not None or edits is not None else 'write'}

    @serialized_operation
    def delete_file(self, ref: RunRef, credential: str, path: str) -> None:
        with self._store(ref).lock():
            self.authorize(ref, credential, "delete_file")
            fsutil.remove_beneath(self.src_dir(ref), path)

    @serialized_operation
    def list_files(self, ref: RunRef, credential: str) -> list[str]:
        with self._store(ref).lock():
            spec, manifest, _ = self.authorize(ref, credential, "list_files")
            entries = fsutil.scan_tree(self.src_dir(ref), exclude=manifest.exclude_from_sync,
                                       limits=fsutil.TreeLimits(spec.limits.max_snapshot_files, spec.limits.max_snapshot_bytes))
            return [e.rel for e in entries if e.kind != "dir"]

    # -- git via broker ------------------------------------------------------
    @serialized_operation
    def checkpoint_files(self, ref: RunRef, credential: str) -> dict[str, bytes]:
        """Fenced source snapshot for durable resume; no Git ref or sandbox command."""
        with self._store(ref).lock():
            spec, manifest, _ = self.authorize(ref, credential, 'checkpoint')
            entries = fsutil.scan_tree(self.src_dir(ref), exclude=manifest.exclude_from_sync,
                limits=fsutil.TreeLimits(spec.limits.max_snapshot_files, min(spec.limits.max_snapshot_bytes, 64 * 1024 * 1024)))
            if any(e.kind == 'symlink' for e in entries):
                raise WorkspaceError('source checkpoint symlinks are unsupported')
            return {e.rel: fsutil.read_file_beneath(self.src_dir(ref), e.rel,
                    max_bytes=64 * 1024 * 1024) for e in entries if e.kind == 'file'}

    def _sync_to_worktree(self, ref: RunRef, spec: RunSpec, manifest: RunnerManifest) -> Path:
        """Freeze the sandbox, validate its tree, and mirror it into the supervisor worktree."""
        self._kill_run_containers(ref)
        run_dir = self.run_dir(ref)
        worktree = run_dir / "worktree"
        entries = fsutil.scan_tree(
            self.src_dir(ref), exclude=manifest.exclude_from_sync,
            limits=fsutil.TreeLimits(spec.limits.max_snapshot_files, spec.limits.max_snapshot_bytes))
        fsutil.clear_dir(worktree, keep=[".git"])
        fsutil.copy_entries(self.src_dir(ref), entries, worktree, sandbox_visible=False)
        return worktree

    @serialized_operation
    def inspect_diff(self, ref: RunRef, credential: str, *, stat_only: bool = False,
                     path: str | None = None) -> str:
        spec, manifest, store = self.authorize(ref, credential, "inspect_diff")
        with store.lock():
            self.authorize(ref, credential, "inspect_diff")
            worktree = self._sync_to_worktree(ref, spec, manifest)
            return self.broker(ref.project_id).diff_cached(worktree, stat_only=stat_only, path=path)

    @staticmethod
    def _assert_active(store: RunStore) -> None:
        if store.state()["status"] != "active":
            raise AuthorizationError(f"run is {store.state()['status']}")

    def submit_candidate(self, ref: RunRef, credential: str, message: str) -> dict[str, Any]:
        return self._commit(ref, credential, message, "submit_candidate", "candidate")

    def checkpoint(self, ref: RunRef, credential: str, message: str) -> dict[str, Any]:
        """WIP commit on the attempt ref; never treated as accepted or as a QA candidate."""
        return self._commit(ref, credential, message, "checkpoint", "checkpoint")

    @serialized_operation
    def _commit(self, ref: RunRef, credential: str, message: str, operation: str, kind: str) -> dict[str, Any]:
        spec, manifest, store = self.authorize(ref, credential, operation)
        if not isinstance(message, str) or not message.strip() or len(message) > 2000 or "\0" in message:
            raise WorkspaceError("commit message must be 1..2000 characters")
        broker = self.broker(ref.project_id)
        with store.lock():  # cancel waits for this; commit after cancel is rejected below
            self.authorize(ref, credential, operation)
            worktree = self._sync_to_worktree(ref, spec, manifest)
            head = broker.head(worktree)
            self._assert_active(store)
            sha, changed = broker.commit_worktree(worktree, spec.attempt_ref, head, message.strip(),
                                                  author=_AGENT_AUTHOR.get(spec.role, ("AI Agent", "agent@localhost")))
            state = store.state()
            record = {
                "kind": kind, "accepted": False, "sha": sha, "changed": changed, "base_sha": spec.base_sha,
                "parent_sha": head, "attempt_ref": spec.attempt_ref, "run_id": spec.run_id,
                "generation": state["generation"], "ticket_id": spec.ticket_id, "scope_version": spec.scope_version,
                "project_id": spec.project_id, "manifest_digest": spec.manifest_digest, "created_at": utcnow(),
            }
            cdir = store.dir / "candidates"
            cdir.mkdir(exist_ok=True, mode=0o700)
            record_path = cdir / f"{kind}-{sha}.json"
            if record_path.exists():
                return json.loads(record_path.read_text())
            atomic_write_json(record_path, record)
            return record

    # -- verification target ----------------------------------------------------
    @serialized_operation
    def build_target(self, ref: RunRef, candidate_sha: str, *, run_tests: bool = False) -> dict[str, Any]:
        return self._build_target(ref, candidate_sha, run_tests=run_tests)

    @serialized_operation
    def build_baseline(self, ref: RunRef, sha: str | None = None) -> dict[str, Any]:
        """Trusted build of this attempt's accepted base (onboarding, release freeze), or of a commit the release
        machinery pinned under refs/releases/. Never a candidate submission."""
        spec, _, _ = self._require_active(ref)
        return self._build_target(ref, sha or spec.base_sha, run_tests=False, baseline=True)

    def _build_target(self, ref: RunRef, candidate_sha: str, *, run_tests: bool = False,
                      baseline: bool = False) -> dict[str, Any]:
        """Clean build of an immutable candidate and write its verification target manifest.

        Every call is a new build record: the same SHA rebuilt yields a new target.
        """
        spec, manifest, store = self._require_active(ref)
        generation = store.state()["generation"]
        broker = self.broker(ref.project_id)
        known = ((candidate_sha == spec.base_sha == broker.accepted_sha() or candidate_sha in broker.release_shas())
                 if baseline else self._known_candidate(store, candidate_sha))
        if not is_sha(candidate_sha) or not known:
            raise WorkspaceError("candidate is not a recorded commit of this attempt")
        image_id = self.sandbox.image_id(manifest.image)
        build_id = f"build-{uuid.uuid4().hex[:12]}"
        vdir = store.dir / "verify" / build_id
        src = vdir / "src"
        src.mkdir(parents=True, mode=0o777)
        os.chmod(src, 0o777)
        broker.export_commit(candidate_sha, src)
        self._chmod_for_sandbox(src)
        records: list[dict[str, Any]] = []
        failed: str | None = None
        for phase in (["install", "build"] + (["test"] if run_tests else [])):
            cmd = manifest.commands[phase]
            if cmd.network == "egress" and not spec.allow_install_egress:
                raise AuthorizationError("this run is not allowed install egress")
            seq, generation = self._reserve_command(store, spec, generation)
            result = self.sandbox.run(
                name=self.sandbox.container_name(spec.project_id, spec.run_id, generation), image=image_id,
                source=src, argv=list(cmd.argv), network=cmd.network, limits=spec.limits, env=manifest.env,
                labels=self._labels(spec, generation), timeout_s=min(cmd.timeout_s, spec.limits.command_timeout_s),
                is_cancelled=lambda: self._revoked(store, generation), install_phase=phase == 'install')
            records.append(self._record_command(store.dir / "evidence", seq, f"{build_id}-{phase}", spec, generation, manifest, result, image_id=image_id))
            if result.exit_code != 0:
                failed = phase
                break
        if failed:
            raise WorkspaceError(f"build {build_id} failed in phase {failed}; see evidence")
        out = src / manifest.build_output
        entries = fsutil.scan_tree(out, limits=fsutil.TreeLimits(spec.limits.max_snapshot_files, spec.limits.max_snapshot_bytes))
        if any(entry.kind == 'symlink' for entry in entries):
            raise WorkspaceError('build output symlinks are unsupported; see build command evidence')
        build_digest = fsutil.sha256_tree(out, entries)
        artifact_dir = store.dir / "builds" / build_id / "artifact"
        artifact_dir.mkdir(parents=True, mode=0o755)
        fsutil.copy_entries(out, entries, artifact_dir, sandbox_visible=False)
        dependency_blobs = {f: broker.file_at(candidate_sha, f) for f in manifest.dependency_files}
        dependency_digest = digest_of({f: None if blob is None else sha256_bytes(blob) for f, blob in dependency_blobs.items()})
        target = {
            "schema": 1, "build_id": build_id, "project_id": spec.project_id, "run_id": spec.run_id,
            "ticket_id": spec.ticket_id, "candidate_sha": candidate_sha, "base_sha": spec.base_sha,
            "scope_version": spec.scope_version, "generation": generation,
            "attempt_ref": spec.attempt_ref, "build_digest": build_digest,
            "runner_manifest_revision": manifest.revision, "runner_manifest_digest": manifest.digest,
            "effective_config_digest": manifest.effective_config_digest,
            "toolchain": {"declared": manifest.toolchain, "image": manifest.image,
                          "image_id": image_id},
            "dependency_digest": dependency_digest, "fixture": manifest.fixture, "migrations": manifest.migrations,
            "evidence": [f"{r['label']}:{r['stdout_sha256']}" for r in records],
            "created_at": utcnow(),
        }
        target_id = digest_of(target)
        target["target_id"] = target_id
        path = store.dir / "evidence" / f"target-{target_id}.json"
        with store.lock():
            if self._revoked(store, generation):
                raise AuthorizationError("build finished after its generation was revoked")
            with open(path, "x") as fh:  # write-once
                fh.write(json.dumps(target, indent=2, sort_keys=True) + "\n")
            (store.dir / "evidence" / f"target-{target_id}.sha256").write_text(sha256_bytes(path.read_bytes()) + "\n")
            os.chmod(path, 0o444)
        return target

    @staticmethod
    def _known_candidate(store: RunStore, sha: str) -> bool:
        cdir = store.dir / "candidates"
        path = cdir / f"candidate-{sha}.json"
        if not path.is_file():
            return False
        record = json.loads(path.read_text())
        return record.get("kind") == "candidate" and record.get("sha") == sha

    @staticmethod
    def _chmod_for_sandbox(root: Path) -> None:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            os.chmod(dirpath, 0o777)
            for name in filenames:
                p = os.path.join(dirpath, name)
                if not os.path.islink(p):
                    os.chmod(p, 0o777 if os.stat(p).st_mode & 0o111 else 0o666)

    @serialized_operation
    def smoke_target(self, ref: RunRef, build_id: str, *, wait_s: float = 30) -> dict[str, Any]:
        """Start the built target (no network, no published port) and probe health from inside."""
        spec, manifest, store = self._require_active(ref)
        if not re.fullmatch(r"build-[0-9a-f]{12}", build_id):
            raise WorkspaceError("invalid build id")
        src = store.dir / "verify" / build_id / "src"
        if not src.is_dir():
            raise WorkspaceError("build is unavailable")
        if isinstance(wait_s, bool) or not isinstance(wait_s, (int, float)) or not math.isfinite(wait_s) or wait_s <= 0:
            raise WorkspaceError("wait_s must be finite and positive")
        targets = [json.loads(p.read_text()) for p in (store.dir / "evidence").glob("target-*.json")]
        target = next((t for t in targets if t.get("build_id") == build_id), None)
        if target is None:
            raise WorkspaceError("build has no completed verification target")
        if digest_of({k: v for k, v in target.items() if k != "target_id"}) != target.get("target_id"):
            raise WorkspaceError("verification target digest mismatch")
        artifact = store.dir / "builds" / build_id / "artifact"
        entries = fsutil.scan_tree(artifact)
        if fsutil.sha256_tree(artifact, entries) != target["build_digest"]:
            raise WorkspaceError("build artifact digest mismatch")
        generation = store.state()["generation"]
        _, generation = self._reserve_command(store, spec, generation)
        name = self.sandbox.container_name(spec.project_id, spec.run_id, generation)
        self.sandbox.start_detached(name=name, image=target["toolchain"]["image_id"], source=src,
                                    argv=list(manifest.commands["start"].argv), limits=spec.limits,
                                    env=manifest.env, labels=self._labels(spec, generation),
                                    artifact=artifact, build_output=manifest.build_output)
        url = f"http://127.0.0.1:{manifest.port}{manifest.health_path}"
        healthy, body = False, b""
        probe_error = None
        deadline = time.monotonic() + min(wait_s, spec.limits.command_timeout_s, manifest.commands["start"].timeout_s)
        try:
            while time.monotonic() < deadline and not self._revoked(store, generation):
                # Bound response bytes inside the container before capture_output on host.
                probe_script = (
                    "setTimeout(()=>process.exit(1),2000);"
                    "require('node:http').get(process.argv[1],r=>{"
                    "if(r.statusCode<200||r.statusCode>=300)process.exit(1);let n=0;"
                    "r.on('data',b=>{const p=b.subarray(0,512-n);process.stdout.write(p);"
                    "n+=p.length;if(n>=512)process.exit(0)});"
                    "r.on('end',()=>process.exit(0));"
                    "}).on('error',()=>process.exit(1));"
                )
                try:
                    probe = self.sandbox.exec_probe(name, ["node", "-e", probe_script, url],
                                                   timeout=max(0.1, min(3, deadline-time.monotonic())))
                except SandboxError as exc:
                    # A failed probe (including the last, short Docker timeout)
                    # must still produce health evidence and container logs.
                    probe_error = str(exc)[:400]
                else:
                    if probe.returncode == 0:
                        healthy, body = True, probe.stdout[:512]
                        break
                time.sleep(max(0, min(0.5, deadline - time.monotonic())))
            out, err = self.sandbox.logs(name, spec.limits.max_log_bytes)
        finally:
            self.sandbox.kill_and_remove(name)
        healthy = healthy and not self._revoked(store, generation)
        result = {"build_id": build_id, "target_id": target["target_id"], "healthy": healthy, "url_inside_container": url,
                  "probe_error": probe_error if not healthy else None,
                  "body_prefix": body.decode(errors="replace"), "stdout_tail": out[-2000:].decode(errors="replace"),
                  "stderr_tail": err[-2000:].decode(errors="replace")}
        atomic_write_json(store.dir / "evidence" / f"smoke-{build_id}.json", result)
        return result

    # -- stop / recovery -----------------------------------------------------
    @staticmethod
    def _revoked(store: RunStore, generation: int) -> bool:
        state = store.state()
        return state["status"] != "active" or state["generation"] != generation

    def _kill_run_containers(self, ref: RunRef, *, older_than: int | None = None) -> list[str]:
        """Remove only containers whose labels prove they belong to this supervisor and run."""
        removed = []
        for row in self.sandbox.owned(project_id=ref.project_id, run_id=ref.run_id):
            if row.get("aiagent.run") == ref.run_id and row.get("aiagent.project") == ref.project_id:
                if older_than is not None and int(row.get("aiagent.generation", "0")) >= older_than:
                    continue
                self.sandbox.kill_and_remove(row["name"])
                removed.append(row["name"])
        return removed

    def stop_run(self, ref: RunRef, reason: str = "stopped") -> Path:
        """Revoke access, stop everything the run owns, archive evidence, then clean up."""
        store = self._store(ref)
        spec = store.spec()
        with store.lock():
            state = store.state()
            if state["status"] == "active":
                state.update(status="cancelled" if reason == "cancelled" else "stopped",
                             credential_sha256=None, stop_reason=reason, revoked_at=utcnow())
                store.write_state(state)
        # In-flight commands observe revoked state, record their result, and release
        # the operation lock before the archive is sealed or source is removed.
        with store.lock("operation.lock"):
            evidence = store.dir / "evidence"
            for row in self.sandbox.owned(project_id=ref.project_id, run_id=ref.run_id):
                if row.get("aiagent.run") != ref.run_id:
                    continue
                out, err = self.sandbox.logs(row["name"], spec.limits.max_log_bytes)
                (evidence / f"final-{row['name']}.log").write_bytes(out + err)
            self._kill_run_containers(ref)
            archive = self._archive(ref, spec, store)
            self._cleanup_dirs(ref, spec)
            return archive

    def _archive(self, ref: RunRef, spec: RunSpec, store: RunStore) -> Path:
        dest = self._project_dir(ref.project_id) / "archive" / ref.run_id
        if (dest / "ARCHIVE-MANIFEST.json").is_file():
            return dest
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, mode=0o700)
        files: dict[str, str] = {}
        broker = self.broker(ref.project_id)
        try:
            head = broker.resolve(spec.attempt_ref)
            if head != spec.base_sha:
                patch = broker.diff_commits(spec.base_sha, head)
                (dest / "attempt.patch").write_text(patch)
        except WorkspaceError:
            (dest / "attempt.patch.unavailable").write_text("attempt ref could not be resolved\n")
        for name in ("runspec.json", "state.json", "manifest.json"):
            if (store.dir / name).exists():
                shutil.copyfile(store.dir / name, dest / name)
        source = self.src_dir(ref)
        if source.is_dir():
            manifest = parse_manifest(json.loads((store.dir / "manifest.json").read_text()))
            try:
                entries = fsutil.scan_tree(source, exclude=manifest.exclude_from_sync,
                    limits=fsutil.TreeLimits(spec.limits.max_snapshot_files, spec.limits.max_snapshot_bytes))
                snapshot = dest / "uncommitted-source"
                snapshot.mkdir()
                fsutil.copy_entries(source, entries, snapshot, sandbox_visible=False)
            except (WorkspaceError, OSError) as exc:
                (dest / "uncommitted-source.unavailable").write_text(str(exc) + "\n")
        for sub in ("evidence", "candidates"):
            if (store.dir / sub).is_dir():
                shutil.copytree(store.dir / sub, dest / sub, symlinks=False)
        for path in sorted(p for p in dest.rglob("*") if p.is_file()):
            files[str(path.relative_to(dest))] = sha256_bytes(path.read_bytes())
        atomic_write_json(dest / "ARCHIVE-MANIFEST.json", {"run_id": ref.run_id, "archived_at": utcnow(), "files": files})
        return dest

    def _cleanup_dirs(self, ref: RunRef, spec: RunSpec) -> None:
        run_dir = (self._project_dir(ref.project_id) / "runs" / ref.run_id).resolve()
        if run_dir.parent.parent.parent != self.root or run_dir.name != ref.run_id or spec.run_id != ref.run_id:
            raise WorkspaceError("refusing cleanup outside the run's own directory")
        self.broker(ref.project_id).remove_worktree(run_dir / "worktree")
        for sub in ("sandbox", "verify", "baseline", "baseline-cache"):
            shutil.rmtree(run_dir / sub, ignore_errors=True)

    def reap_orphans(self, project_id: str) -> list[str]:
        """After a crash: remove labelled containers whose run is no longer active."""
        removed = []
        for row in self.sandbox.owned(project_id=project_id):
            run_id = row.get("aiagent.run", "")
            try:
                ref = RunRef(project_id, run_id)
                state = self._store(ref).state()
                active = state["status"] == "active" and str(state["generation"]) == row.get("aiagent.generation")
            except WorkspaceError:
                active = False
            if not active:
                self.sandbox.kill_and_remove(row["name"])
                removed.append(row["name"])
        return removed
