"""AC: artifacts keep a reference and checksum; missing or corrupt files become unavailable."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from sqlalchemy import select

from app.persistence import (
    ArtifactError, ArtifactUnavailable, NotFound, canonical_json, read_events, sha256_bytes,
)
from app.persistence.models import Artifact

from . import factories as f


def stored(db, store, artifact_id) -> tuple[Artifact, Path]:
    with db.read() as s:
        row = s.get(Artifact, artifact_id)
        return row, store.resolve(row.path)


def overwrite(path: Path, data: bytes) -> None:
    os.chmod(path, 0o600)  # stored files are read-only
    path.write_bytes(data)


def test_put_records_reference_checksum_and_readonly_file(db, store, world):
    with db.write() as s:
        art = store.put_bytes(s, project_id=world.project.id, kind="log", data=b"build output", name="build.log",
                              run_id="run-1", meta={"phase": "build"})
    row, path = stored(db, store, art.id)
    assert (row.checksum, row.size_bytes, row.kind, row.run_id) == (sha256_bytes(b"build output"), 12, "log", "run-1")
    assert row.path == f"{world.project.id}/{art.id}/build.log"
    assert path.read_bytes() == b"build output"
    assert stat.S_IMODE(path.stat().st_mode) & 0o222 == 0  # mode bits, since root ignores access checks
    with db.read() as s:
        assert store.read_bytes(s, art.id) == b"build output"


def test_manifest_json_is_canonical_so_equal_documents_share_a_checksum(db, store, world):
    with db.write() as s:
        a = store.put_json(s, project_id=world.project.id, kind="target_manifest", document={"b": 1, "a": [1, 2]}, name="a.json")
        b = store.put_json(s, project_id=world.project.id, kind="target_manifest", document={"a": [1, 2], "b": 1}, name="b.json")
    assert a.checksum == b.checksum == sha256_bytes(canonical_json({"a": [1, 2], "b": 1}))
    assert a.path != b.path  # every artifact owns its file


@pytest.mark.parametrize("name", ["../escape.txt", "a/b.txt", "..", ".hidden", "", "x" * 200, "back\\slash"])
def test_unsafe_names_are_refused(db, store, world, name):
    with pytest.raises(ArtifactError):
        with db.write() as s:
            store.put_bytes(s, project_id=world.project.id, kind="log", data=b"x", name=name)
    assert not any(Path(store.root).rglob("*.txt"))


def test_unsafe_project_ids_cannot_escape_the_root(db, store):
    with pytest.raises(ArtifactError):
        store.resolve("../outside/a/b.txt")
    with pytest.raises(ArtifactError):
        store.resolve("/etc/passwd")


def test_rolled_back_transaction_removes_the_file(db, store, world):
    with pytest.raises(RuntimeError):
        with db.write() as s:
            art = store.put_bytes(s, project_id=world.project.id, kind="log", data=b"x", name="x.log")
            path = store.resolve(art.path)
            assert path.exists()
            raise RuntimeError
    assert not path.exists() and not path.parent.exists()
    with db.read() as s:
        assert s.scalars(select(Artifact)).all() == []


def test_missing_file_is_marked_unavailable_with_an_event(db, store, world):
    art = db_artifact(db, store, world)
    _, path = stored(db, store, art.id)
    os.chmod(path, 0o600)
    path.unlink()
    with db.write() as s:
        assert store.verify(s, art.id) == "unavailable"
    row, _ = stored(db, store, art.id)
    assert (row.availability, row.unavailable_reason) == ("unavailable", "missing")
    with db.read() as s:
        [event] = [e for e in read_events(s) if e.type == "artifact.unavailable"]
        assert event.payload["reason"] == "missing" and event.entity_id == art.id


def db_artifact(db, store, world, data=b"payload", name="evidence.txt", **kw):
    with db.write() as s:
        return f.artifact(s, store, world.project, data=data, name=name, **kw)


def test_corrupt_and_truncated_files_are_detected(db, store, world):
    same_size = db_artifact(db, store, world, data=b"payload", name="a.txt")
    longer = db_artifact(db, store, world, data=b"payload", name="b.txt")
    overwrite(stored(db, store, same_size.id)[1], b"PAYLOAD")  # same size, different bytes
    overwrite(stored(db, store, longer.id)[1], b"payload and more")
    with db.write() as s:
        assert store.verify_project(s, world.project.id) == {same_size.id: "unavailable", longer.id: "unavailable"}
    assert stored(db, store, same_size.id)[0].unavailable_reason == "corrupt"
    assert stored(db, store, longer.id)[0].unavailable_reason == "size_mismatch"


def test_intact_file_stays_available(db, store, world):
    art = db_artifact(db, store, world)
    with db.write() as s:
        assert store.verify(s, art.id) == "available"
    assert stored(db, store, art.id)[0].verified_at is not None
    with db.read() as s:
        assert [e for e in read_events(s) if e.type.startswith("artifact.")] == []


def test_read_refuses_bad_bytes_without_trusting_the_flag(db, store, world):
    art = db_artifact(db, store, world)
    overwrite(stored(db, store, art.id)[1], b"tampered")
    with db.read() as s:  # the row still says available; the checksum catches it
        with pytest.raises(ArtifactUnavailable) as bad:
            store.read_bytes(s, art.id)
    assert bad.value.reason == "corrupt"
    with db.write() as s:
        store.verify(s, art.id)
    with db.read() as s:
        with pytest.raises(ArtifactUnavailable, match="corrupt|size_mismatch"):
            store.read_bytes(s, art.id)
        with pytest.raises(ArtifactUnavailable):
            store.require_available(s, [art.id])
        with pytest.raises(NotFound):
            store.require_available(s, ["no-such-artifact"])


def test_restored_file_becomes_available_again(db, store, world):
    art = db_artifact(db, store, world)
    _, path = stored(db, store, art.id)
    overwrite(path, b"damaged")
    with db.write() as s:
        store.verify(s, art.id)
    overwrite(path, b"payload")  # e.g. restored from a local backup
    with db.write() as s:
        assert store.verify(s, art.id) == "available"
    assert stored(db, store, art.id)[0].unavailable_reason is None
    with db.read() as s:
        assert [e.type for e in read_events(s)] == ["artifact.unavailable", "artifact.restored"]


def test_unavailable_evidence_cannot_back_a_new_approval(db, store, world, rejected):
    def prepare(s):
        cand, target, evidence = f.verified_candidate(s, store, world.project, world.ticket)
        return cand, target, evidence

    with db.write() as s:
        cand, target, evidence = prepare(s)
    overwrite(stored(db, store, evidence.id)[1], b"corrupted evidence")
    with db.write() as s:
        store.verify(s, evidence.id)  # the integrity check marks it unavailable
    rejected(db, lambda s: f.uat_approval(s, world.project, world.ticket, cand, target, [evidence.id]),
             "missing, unavailable or belongs")


def test_symlinked_artifact_directory_is_refused(db, store, world, tmp_path):
    art = db_artifact(db, store, world)
    row, path = stored(db, store, art.id)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "evidence.txt").write_bytes(b"payload")
    directory = path.parent
    os.chmod(path, 0o600)
    path.unlink()
    directory.rmdir()
    try:
        directory.symlink_to(elsewhere, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create symlinks")
    with db.write() as s:
        assert store.verify(s, art.id) == "unavailable"
    with db.read() as s:  # read the row directly: resolve() refuses the symlinked path by design
        assert s.get(Artifact, art.id).unavailable_reason == "unreadable"
    with pytest.raises(ArtifactError):
        store.resolve(row.path)


def test_git_commit_artifact_is_a_pinnable_reference(db, store, world):
    with db.write() as s:
        one = store.put_git_commit(s, project_id=world.project.id, sha=f.SHA_A, meta={"ref": "refs/attempts/1"})
        again = store.put_git_commit(s, project_id=world.project.id, sha=f.SHA_A)
    assert one.id == again.id and one.path is None and one.size_bytes is None
    with db.write() as s:
        assert store.verify(s, one.id) == "available"  # Git objects are verified by the integration layer
    with db.read() as s:
        with pytest.raises(ArtifactError):
            store.read_bytes(s, one.id)


def put(store, s, world, name, data=b"x"):
    return store.put_bytes(s, project_id=world.project.id, kind="log", data=data, name=name)


def row_state(db, store, artifact):
    with db.read() as s:
        row = s.get(Artifact, artifact.id)
        return (row.availability, store.resolve(artifact.path).exists()) if row else (None, None)


def test_savepoint_rollback_keeps_the_outer_transactions_files(db, store, world):
    """Regression (review R002-02): the outer artifact must survive an inner rollback and commit."""
    with db.write() as s:
        outer = put(store, s, world, "outer.log", b"outer")
        with pytest.raises(RuntimeError):
            with s.begin_nested():
                inner = put(store, s, world, "inner.log", b"inner")
                raise RuntimeError
        assert not store.resolve(inner.path).exists()  # only the savepoint's own file is dropped
        assert store.resolve(outer.path).exists()
    assert row_state(db, store, outer) == ("available", True)
    with db.read() as s:
        assert store.read_bytes(s, outer.id) == b"outer"  # row and bytes agree
        assert s.get(Artifact, inner.id) is None
    with db.write() as s:
        assert store.verify(s, outer.id) == "available"


def test_released_savepoint_files_follow_the_outer_transaction(db, store, world):
    with db.write() as s:
        with s.begin_nested():
            inner = put(store, s, world, "inner.log")
        assert store.resolve(inner.path).exists()  # released, not yet committed
    assert row_state(db, store, inner) == ("available", True)

    with pytest.raises(RuntimeError):
        with db.write() as s:
            with s.begin_nested():
                doomed = put(store, s, world, "doomed.log")
            doomed_path = store.resolve(doomed.path)
            assert doomed_path.exists()
            raise RuntimeError  # outer rollback after the savepoint was released
    assert not doomed_path.exists()
    with db.read() as s:
        assert s.get(Artifact, doomed.id) is None


def test_nested_savepoints_each_drop_only_their_own_files(db, store, world):
    with db.write() as s:
        a = put(store, s, world, "a.log")
        with s.begin_nested():
            b = put(store, s, world, "b.log")
            with pytest.raises(RuntimeError):
                with s.begin_nested():
                    c = put(store, s, world, "c.log")
                    raise RuntimeError
            assert store.resolve(b.path).exists() and not store.resolve(c.path).exists()
        d = put(store, s, world, "d.log")
    for kept in (a, b, d):
        assert row_state(db, store, kept) == ("available", True)
    with db.read() as s:
        assert s.get(Artifact, c.id) is None


def test_failed_flush_inside_a_savepoint_removes_the_file(db, store, world):
    with db.write() as s:
        keep = put(store, s, world, "keep.log")
        with pytest.raises(Exception):
            with s.begin_nested():
                store.put_bytes(s, project_id="no-such-project", kind="log", data=b"x", name="bad.log")
    assert row_state(db, store, keep) == ("available", True)
    assert not list(store.root.glob("no-such-project/**/*.log"))
