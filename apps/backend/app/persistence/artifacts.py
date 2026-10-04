"""Local artifact files with checksums. The database keeps the reference, not the bytes.

Layout: <root>/<project_id>/<artifact_id>/<name>; every artifact owns its file, so
deleting one never affects another. A missing or corrupt file is marked unavailable
(with an event) and refused by require_available(); the database also rejects new
approvals/candidates/verifications/releases that reference an unavailable artifact.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from .changes import NotFound, PersistenceError
from .events import EventSpec, append_event
from .models import Artifact
from .columns import new_id, utcnow

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_CHUNK = 1024 * 1024
_HOOKED = "artifact_file_hooks"
_PENDING = "artifact_pending_files"


class ArtifactError(PersistenceError):
    pass


class ArtifactUnavailable(ArtifactError):
    def __init__(self, artifact_id: str, reason: str):
        super().__init__(f"artifact {artifact_id} is unavailable ({reason})")
        self.artifact_id, self.reason = artifact_id, reason


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class ArtifactStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._real_root = self.root.resolve()

    # -- paths -----------------------------------------------------------------
    def resolve(self, rel: str) -> Path:
        """Join a stored relative key under the root, refusing escapes and symlinks."""
        parts = rel.split("/")
        if not parts or any(not _SEGMENT.match(p) for p in parts):
            raise ArtifactError("invalid artifact path")
        current = self.root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise ArtifactError("artifact path contains a symlink")
        return current

    # -- writing ---------------------------------------------------------------
    def put_bytes(self, session: Session, *, project_id: str, kind: str, data: bytes, name: str,
                  run_id: str | None = None, meta: dict[str, Any] | None = None) -> Artifact:
        """Write the file, then add the row to the caller's transaction.

        If that transaction rolls back, the file is removed again; a crash in between
        can leave an orphan file, which has no row and is never served.
        """
        if not _SEGMENT.match(name):  # one file name, never a sub-path
            raise ArtifactError("invalid artifact name")
        session.connection()  # begin the transaction now: the file is owned by the innermost one
        artifact_id = new_id()
        rel = f"{project_id}/{artifact_id}/{name}"
        target = self.resolve(rel)
        target.parent.mkdir(parents=True, exist_ok=False)
        partial = target.with_name(target.name + ".part")
        try:
            fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(partial, target)
            os.chmod(target, 0o444)
        except BaseException:
            partial.unlink(missing_ok=True)
            self._remove_tree(target.parent)
            raise
        self._track(session, target)
        artifact = Artifact(id=artifact_id, project_id=project_id, kind=kind, storage="file", path=rel,
                            checksum=sha256_bytes(data), size_bytes=len(data), run_id=run_id,
                            meta=dict(meta or {}))
        session.add(artifact)
        session.flush()
        return artifact

    def put_json(self, session: Session, *, project_id: str, kind: str, document: Any, name: str,
                 run_id: str | None = None, meta: dict[str, Any] | None = None) -> Artifact:
        """Store a manifest as canonical JSON, so equal documents have equal checksums."""
        return self.put_bytes(session, project_id=project_id, kind=kind, data=canonical_json(document),
                              name=name, run_id=run_id, meta=meta)

    @staticmethod
    def put_git_commit(session: Session, *, project_id: str, sha: str, meta: dict[str, Any] | None = None) -> Artifact:
        """Reference a managed-Git commit so it can be pinned. Git, not this store, holds the bytes."""
        existing = session.scalar(select(Artifact).where(
            Artifact.project_id == project_id, Artifact.storage == "git", Artifact.checksum == sha))
        if existing is not None:
            return existing
        artifact = Artifact(project_id=project_id, kind="git_commit", storage="git", path=None,
                            checksum=sha, size_bytes=None, meta=dict(meta or {}))
        session.add(artifact)
        session.flush()
        return artifact

    # -- reading and verification -------------------------------------------------
    def read_bytes(self, session: Session, artifact_id: str) -> bytes:
        """Bytes that match the recorded checksum, or ArtifactUnavailable. Never serves bad data."""
        artifact = self._get(session, artifact_id)
        if artifact.storage != "file":
            raise ArtifactError("only file artifacts have bytes in the artifact store")
        if artifact.availability != "available":
            raise ArtifactUnavailable(artifact_id, artifact.unavailable_reason or "unavailable")
        try:
            data = self.resolve(artifact.path).read_bytes()
        except (OSError, ArtifactError) as exc:
            raise ArtifactUnavailable(artifact_id, "missing" if isinstance(exc, FileNotFoundError) else "unreadable")
        if len(data) != artifact.size_bytes or sha256_bytes(data) != artifact.checksum:
            raise ArtifactUnavailable(artifact_id, "corrupt")
        return data

    def verify(self, session: Session, artifact_id: str, *, actor: str = "system:artifact-verify") -> str:
        """Re-hash the file and record the result. Returns the resulting availability.

        Problems mark the artifact unavailable (and append an event). A file that again
        matches its checksum becomes available again, except one removed by cleanup.
        """
        artifact = self._get(session, artifact_id)
        if artifact.storage != "file":
            return artifact.availability  # Git objects are verified by the integration layer.
        problem = self._problem(artifact)
        artifact.verified_at = utcnow()
        if problem and artifact.availability == "available":
            artifact.availability, artifact.unavailable_reason = "unavailable", problem
            append_event(session, artifact.project_id, EventSpec(
                "artifact.unavailable", actor, {"reason": problem, "kind": artifact.kind},
                entity_type="artifact", entity_id=artifact.id))
        elif not problem and artifact.availability == "unavailable" and artifact.unavailable_reason != "cleaned":
            artifact.availability, artifact.unavailable_reason = "available", None
            append_event(session, artifact.project_id, EventSpec(
                "artifact.restored", actor, {"kind": artifact.kind}, entity_type="artifact", entity_id=artifact.id))
        session.flush()
        return artifact.availability

    def verify_project(self, session: Session, project_id: str, *, actor: str = "system:artifact-verify") -> dict[str, str]:
        """Verify every file artifact of a project; returns {artifact_id: availability}."""
        ids = session.scalars(select(Artifact.id).where(
            Artifact.project_id == project_id, Artifact.storage == "file")).all()
        return {artifact_id: self.verify(session, artifact_id, actor=actor) for artifact_id in ids}

    def _problem(self, artifact: Artifact) -> str | None:
        try:
            path = self.resolve(artifact.path)
            if not path.exists():
                return "missing"
            if not path.is_file() or path.stat().st_size != artifact.size_bytes:
                return "size_mismatch" if path.is_file() else "unreadable"
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(_CHUNK), b""):
                    digest.update(chunk)
        except (OSError, ArtifactError):
            return "unreadable"
        return None if digest.hexdigest() == artifact.checksum else "corrupt"

    # -- guards --------------------------------------------------------------------
    @staticmethod
    def require_available(session: Session, artifact_ids: Iterable[str]) -> None:
        """Raise unless every artifact exists and is available. Call before opening a preview
        or recording evidence-based approvals; the database enforces the same on insert."""
        for artifact_id in dict.fromkeys(artifact_ids):
            artifact = session.get(Artifact, artifact_id)
            if artifact is None:
                raise NotFound("artifact", artifact_id)
            if artifact.availability != "available":
                raise ArtifactUnavailable(artifact_id, artifact.unavailable_reason or "unavailable")

    # -- internals -------------------------------------------------------------------
    @staticmethod
    def _get(session: Session, artifact_id: str) -> Artifact:
        artifact = session.get(Artifact, artifact_id)
        if artifact is None:
            raise NotFound("artifact", artifact_id)
        return artifact

    @staticmethod
    def delete_path(path: Path) -> None:
        """Remove one stored file (they are read-only) and its now-empty artifact directory."""
        try:
            path.chmod(0o600)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass  # not empty or already gone

    def _remove_tree(self, directory: Path) -> None:
        for child in list(directory.glob("*")):
            self.delete_path(child)

    def _track(self, session: Session, target: Path) -> None:
        """Bind the file to the transaction chain (savepoints up to the outer one) that created it.

        A savepoint rollback removes only files created inside it; a released savepoint's files stay
        until the outer transaction ends, so an outer rollback still removes them; outer commit keeps all.
        """
        chain, transaction = [], session.get_nested_transaction() or session.get_transaction()
        while transaction is not None:  # captured now: SQLAlchemy clears links once a transaction ends
            chain.append(transaction)
            transaction = transaction.parent
        session.info.setdefault(_PENDING, []).append((target, chain))
        if session.info.get(_HOOKED):
            return
        session.info[_HOOKED] = True

        @event.listens_for(session, "after_soft_rollback")
        def _rolled_back(sess, previous):
            # A failed flush reports its own sub-transaction; the rollback really applies to the
            # nearest savepoint or root transaction above it.
            real = previous
            while real.parent is not None and not real.nested:
                real = real.parent
            kept = []
            for path, owners in sess.info.get(_PENDING, []):
                if any(owner is real for owner in owners):
                    self.delete_path(path)
                else:
                    kept.append((path, owners))
            sess.info[_PENDING] = kept

        @event.listens_for(session, "after_commit")
        def _committed(sess):
            # Releasing a savepoint also fires after_commit: its files stay pending until the outer
            # transaction commits or rolls back.
            if not sess.in_nested_transaction():
                sess.info.pop(_PENDING, None)
