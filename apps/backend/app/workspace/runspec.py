"""Persistent run spec (immutable identity) and run state (supervisor-owned)."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import AuthorizationError, WorkspaceError

PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
RUN_ID = re.compile(r"^run-[0-9a-f]{12}$")
ROLES = ("po", "technical-lead", "developer", "qa")
SPEC_VERSION = 1


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ResourceLimits:
    memory_mb: int = 1024
    cpus: float = 1.0
    pids: int = 256
    command_timeout_s: int = 300
    tmpfs_mb: int = 512
    work_mb: int = 512
    max_log_bytes: int = 1024 * 1024
    max_commands: int = 200
    max_snapshot_bytes: int = 200 * 1024 * 1024
    max_snapshot_files: int = 20_000

    def validate(self) -> None:
        bounds = {
            "memory_mb": (64, 8192), "cpus": (0.1, 8), "pids": (16, 4096), "command_timeout_s": (1, 3600),
            "tmpfs_mb": (16, 4096), "work_mb": (16, 4096), "max_log_bytes": (1024, 16 * 1024 * 1024), "max_commands": (1, 5000),
            "max_snapshot_bytes": (1024, 2 * 1024**3), "max_snapshot_files": (1, 200_000),
        }
        for name, (low, high) in bounds.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not (low <= value <= high):
                raise WorkspaceError(f"limit {name} must be between {low} and {high}, got {value!r}")
            if name != "cpus" and not isinstance(value, int):
                raise WorkspaceError(f"limit {name} must be an integer")


@dataclass(frozen=True)
class RunRef:
    """Locator for a run; identity and permissions come from the persisted spec."""

    project_id: str
    run_id: str

    def __post_init__(self) -> None:
        if not PROJECT_ID.match(self.project_id) or not RUN_ID.match(self.run_id):
            raise AuthorizationError("malformed run reference")


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    project_id: str
    ticket_id: str
    scope_version: int
    role: str
    attempt: int
    generation: int
    lease_id: str
    base_sha: str
    attempt_ref: str
    manifest_digest: str
    limits: ResourceLimits
    allow_install_egress: bool = False
    provenance: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SPEC_VERSION

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_json(data: dict[str, Any]) -> "RunSpec":
        data = dict(data)
        data["limits"] = ResourceLimits(**data["limits"])
        return RunSpec(**data)


def hash_credential(credential: str) -> str:
    return hashlib.sha256(credential.encode()).hexdigest()


def new_credential() -> str:
    return secrets.token_urlsafe(32)


def atomic_write_json(path: Path, data: Any) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


class RunStore:
    """Reads and writes spec/state files of one run directory."""

    def __init__(self, run_dir: Path) -> None:
        self.dir = Path(run_dir)

    @property
    def spec_path(self) -> Path:
        return self.dir / "runspec.json"

    @property
    def state_path(self) -> Path:
        return self.dir / "state.json"

    def write_spec(self, spec: RunSpec) -> None:
        data = spec.to_json()
        with open(self.spec_path, "x") as fh:  # immutable: never overwritten
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")

    def spec(self) -> RunSpec:
        try:
            return RunSpec.from_json(json.loads(self.spec_path.read_text()))
        except FileNotFoundError as exc:
            raise AuthorizationError("unknown run") from exc

    def state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text())
        except FileNotFoundError as exc:
            raise AuthorizationError("unknown run") from exc

    def write_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = utcnow()
        atomic_write_json(self.state_path, state)

    @contextmanager
    def lock(self, name: str = "lock") -> Iterator[None]:
        """Per-run exclusive lock; serialises cancel against commit/authorize-then-act."""
        with open(self.dir / name, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
