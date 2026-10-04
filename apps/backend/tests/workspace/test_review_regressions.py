"""R2 regression checks, including deterministic concurrent lifecycle operations."""
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.workspace import AuthorizationError, LimitExceeded, PathViolation, ResourceLimits, WorkspaceError
from app.workspace import fsutil
from app.workspace.dependencies import registry_tarballs
from app.workspace.errors import SandboxError
from app.workspace.sandbox import CommandResult
from .conftest import NoContainerSandbox, small_manifest, start


def test_fifo_read_does_not_block_host(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    # A separate process ensures a regression fails promptly rather than hanging pytest.
    code = "from pathlib import Path; from app.workspace.fsutil import read_file_beneath; read_file_beneath(Path(__import__('sys').argv[1]), 'pipe')"
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)],
                            env={**os.environ, "PYTHONPATH": str(__import__('pathlib').Path(__file__).parents[2])},
                            capture_output=True, timeout=3)
    assert result.returncode != 0
    assert b"PathViolation" in result.stderr


def test_scan_counts_directories_and_refuses_root_symlink(tmp_path):
    for i in range(4):
        (tmp_path / str(i)).mkdir()
    with pytest.raises(LimitExceeded):
        fsutil.scan_tree(tmp_path, limits=fsutil.TreeLimits(max_files=3))
    (tmp_path / "alias").symlink_to(tmp_path / "0")
    with pytest.raises(PathViolation, match="root"):
        fsutil.scan_tree(tmp_path / "alias")


def test_checkpoint_cannot_be_built(stub_sup, manifest):
    run = start(stub_sup, manifest)
    stub_sup.write_file(run.ref, run.credential, "work.txt", b"unfinished")
    record = stub_sup.checkpoint(run.ref, run.credential, "WIP")
    with pytest.raises(WorkspaceError, match="not a recorded commit"):
        stub_sup.build_target(run.ref, record["sha"])


def test_noop_submission_preserves_candidate_identity(stub_sup, manifest):
    run = start(stub_sup, manifest)
    stub_sup.write_file(run.ref, run.credential, "work.txt", b"candidate")
    first = stub_sup.submit_candidate(run.ref, run.credential, "ready")
    credential = stub_sup.renew_generation(run.ref, generation=2, lease_id="l2")
    assert stub_sup.submit_candidate(run.ref, credential, "same") == first


class RecordingSandbox(NoContainerSandbox):
    def __init__(self, block=False):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self.block = block

    def image_id(self, image):
        return "sha256:" + "a" * 64

    def run(self, **kw):
        self.calls.append(kw)
        self.entered.set()
        if self.block:
            deadline = time.monotonic() + 5
            while not self.release.is_set() and not kw["is_cancelled"]():
                if time.monotonic() > deadline:
                    raise AssertionError("test command was not revoked")
                time.sleep(0.01)
        cancelled = kw["is_cancelled"]()
        return CommandResult(tuple(kw["argv"]), None if cancelled else 0, False,
                             cancelled, False, 0, b"started\n", b"", False, kw["network"], kw["name"])


def test_cancel_archives_completed_command_evidence_and_uncommitted_source(stub_sup, manifest):
    box = RecordingSandbox(block=True)
    stub_sup.sandbox = box
    run = start(stub_sup, manifest)
    stub_sup.write_file(run.ref, run.credential, "uncommitted.txt", b"keep me")
    with ThreadPoolExecutor(2) as pool:
        command = pool.submit(stub_sup.run_command, run.ref, run.credential, ["sleep", "30"])
        assert box.entered.wait(3)
        archive = stub_sup.stop_run(run.ref, "cancelled")
        assert command.result(timeout=3).cancelled
    records = list((archive / "evidence").glob("cmd-*.json"))
    assert len(records) == 1 and json.loads(records[0].read_text())["cancelled"]
    assert (archive / "uncommitted-source" / "uncommitted.txt").read_bytes() == b"keep me"


def test_renew_cancels_inflight_generation_and_rejects_old_commands(stub_sup, manifest):
    box = RecordingSandbox(block=True)
    stub_sup.sandbox = box
    run = start(stub_sup, manifest)
    with ThreadPoolExecutor(2) as pool:
        command = pool.submit(stub_sup.run_command, run.ref, run.credential, ["sleep", "30"])
        assert box.entered.wait(3)
        new = stub_sup.renew_generation(run.ref, generation=2, lease_id="l2")
        assert command.result(timeout=3).cancelled
    with pytest.raises(AuthorizationError, match="stale"):
        stub_sup.run_command(run.ref, run.credential, ["true"])
    box.block = False
    assert stub_sup.run_command(run.ref, new, ["true"]).exit_code == 0


def test_phase_obeys_run_timeout_and_records_actual_image(stub_sup, manifest):
    box = RecordingSandbox()
    stub_sup.sandbox = box
    run = start(stub_sup, manifest, limits=ResourceLimits(command_timeout_s=2))
    stub_sup.run_phase(run.ref, run.credential, "build")
    assert box.calls[0]["timeout_s"] == 2
    record = json.loads(next((stub_sup.run_dir(run.ref) / "evidence").glob("cmd-*.json")).read_text())
    assert record["image_id"] == box.calls[0]["image"]


def test_generation_rotated_between_authorization_and_reservation_is_rejected(stub_sup, manifest, monkeypatch):
    stub_sup.sandbox = RecordingSandbox()
    run = start(stub_sup, manifest)
    original = stub_sup._reserve_command
    def rotate(store, spec, generation):
        # Model the interleaving after authorize, before reservation's state lock.
        with store.lock():
            state = store.state()
            state["generation"] += 1
            store.write_state(state)
        return original(store, spec, generation)
    monkeypatch.setattr(stub_sup, "_reserve_command", rotate)
    with pytest.raises(AuthorizationError, match="stale"):
        stub_sup.run_command(run.ref, run.credential, ["true"])
    assert stub_sup.sandbox.calls == []


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/a.tgz", "https://host.docker.internal/a.tgz",
                                  "https://registry.npmjs.org@127.0.0.1/a.tgz", "git+https://example.com/repo"])
def test_dependency_downloader_rejects_host_and_non_registry_urls(url):
    with pytest.raises(SandboxError, match="registry"):
        registry_tarballs({"lockfileVersion": 3, "packages": {"node_modules/x": {"resolved": url}}})


def test_dependency_fetch_retries_timeout_and_verifies_integrity(tmp_path, monkeypatch):
    import base64
    import hashlib
    import io
    from app.workspace.dependencies import fetch_tarballs
    body = b"tarball bytes"
    lock = {"lockfileVersion": 3, "packages": {"node_modules/x": {
        "resolved": "https://registry.npmjs.org/x/-/x-1.tgz",
        "integrity": "sha512-" + base64.b64encode(hashlib.sha512(body).digest()).decode()}}}
    source, dest = tmp_path / "source", tmp_path / "dest"
    source.mkdir(); dest.mkdir()
    (source / "package-lock.json").write_text(json.dumps(lock))
    class Opener:
        calls = 0
        def open(self, url, timeout):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("transient read timeout")
            return io.BytesIO(body)
    opener = Opener()
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: opener)
    fetch_tarballs(source, dest, ResourceLimits(), deadline=time.monotonic() + 10, is_cancelled=lambda: False)
    assert opener.calls == 2
    assert (dest / "00000.tgz").read_bytes() == body
    (source / "package-lock.json").write_text(json.dumps(lock).replace(lock["packages"]["node_modules/x"]["integrity"],
        "sha512-" + base64.b64encode(b"x" * 64).decode()))
    with pytest.raises(SandboxError, match="integrity mismatch"):
        fetch_tarballs(source, dest, ResourceLimits(), deadline=time.monotonic() + 10, is_cancelled=lambda: False)


def test_two_attempts_can_commit_concurrently(stub_sup, manifest):
    runs = [start(stub_sup, manifest, ticket=f"T-{i}") for i in range(2)]
    for i, run in enumerate(runs):
        stub_sup.write_file(run.ref, run.credential, f"file-{i}.txt", b"ok")
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(stub_sup.submit_candidate, run.ref, run.credential, "ready") for run in runs]
        records = [f.result(timeout=10) for f in futures]
    assert len({r["sha"] for r in records}) == 2
    assert stub_sup.broker("demo").accepted_sha() == runs[0].spec.base_sha
