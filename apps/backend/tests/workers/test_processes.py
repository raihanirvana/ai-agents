"""Real process groups and a real DEV-005 workspace run (POSIX; run under WSL on Windows)."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from app.adapters.runtime.fake import FakeRuntime
from app.workers.runtime import group_members

pytestmark = pytest.mark.skipif(not hasattr(os, "killpg") or
                                (sys.platform != "darwin" and not os.path.isdir("/proc")),
                                reason="process-group supervision needs Linux or macOS")


def gone(pgid, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not group_members(pgid):
            return True
        time.sleep(0.05)
    return False


def spawn_group(tag, seconds=60):
    """A tagged process group with a child, like an attempt's runtime would start."""
    code = ("import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; "
            f"time.sleep({seconds})']); time.sleep({seconds})")
    proc = subprocess.Popen([sys.executable, "-c", code], env={**os.environ, "AIAGENTS_RUN": tag},
                            start_new_session=True)
    deadline = time.monotonic() + 5
    while len(group_members(proc.pid)) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    return proc


def kill_quietly(pgid):
    if not group_members(pgid):
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_cancel_stops_the_whole_process_group(env):
    sup = env.supervisor()
    job = env.enqueue([{"spawn": 60}, {"sleep": 60}, {"finish": {}}])
    env.run_until(sup, lambda: env.job(job.id).runtime_ref.get("processes"))
    pgid = env.job(job.id).runtime_ref["processes"][0]["pgid"]
    deadline = time.monotonic() + 5
    while len(group_members(pgid)) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(group_members(pgid)) >= 2  # child and grandchild are alive
    try:
        env.queue.cancel(job.id, reason="user cancelled", actor="user:local")  # revoke first
        env.run_until(sup, lambda: sup.running() == 0, 10)
        assert gone(pgid)
        assert env.job(job.id).status == "cancelled"
        assert env.job(job.id).result["evidence_artifact_ids"]
    finally:
        kill_quietly(pgid)


def test_a_finished_run_leaves_no_lingering_children(env):
    sup = env.supervisor()
    job = env.enqueue([{"spawn": 60}, {"finish": {"done": True}}])
    env.run_until(sup, lambda: env.job(job.id).status == "succeeded")
    pgid = env.job(job.id).runtime_ref["processes"][0]["pgid"]
    try:
        assert sup.wait_idle(10) and gone(pgid)
    finally:
        kill_quietly(pgid)


def test_recovery_after_a_dead_worker_stops_its_verified_processes_then_retries(clocked, clock):
    job = clocked.enqueue()
    lease = clocked.queue.claim("worker:dead", "execution", capacity=1, runtimes=("fake",))
    proc = spawn_group(f"{lease.job_id}:{lease.generation}")
    clocked.queue.register_process(lease, proc.pid, f"{lease.job_id}:{lease.generation}")
    try:
        clock.advance(31)  # the dead worker never heartbeats again
        sup = clocked.supervisor(worker_id="worker:new")
        assert sup.recover() == [job.id]
        assert gone(proc.pid)  # its child and grandchild were stopped before any retry
        old = clocked.job(job.id)
        assert old.status == "failed" and old.result["reason"] == "lease_expired"
        assert clocked.job(old.result["retry_job_id"]).status == "queued"
    finally:
        kill_quietly(proc.pid)


def test_recovery_never_kills_a_process_that_is_not_the_attempts(clocked, clock):
    job = clocked.enqueue()
    lease = clocked.queue.claim("worker:dead", "execution", capacity=1, runtimes=("fake",))
    foreign = spawn_group("someone-else:1")  # e.g. a reused PID or a preview process
    clocked.queue.register_process(lease, foreign.pid, f"{lease.job_id}:{lease.generation}")
    try:
        clock.advance(31)
        clocked.supervisor(worker_id="worker:new").recover()
        assert len(group_members(foreign.pid)) >= 2  # untouched
        assert clocked.job(job.id).status == "failed"
    finally:
        kill_quietly(foreign.pid)


def test_cancel_revokes_and_archives_the_attempts_workspace_run(env, tmp_path):
    from app.workspace import WorkspaceSupervisor
    from app.workspace.errors import AuthorizationError
    from tests.workspace.conftest import NoContainerSandbox, small_manifest

    workspace = WorkspaceSupervisor(tmp_path / "ws", sandbox=NoContainerSandbox())
    workspace.create_project("demo")
    runtime = FakeRuntime(workspace=workspace, manifest=small_manifest())
    sup = env.supervisor(runtime=runtime)
    job = env.enqueue([{"workspace": "demo"}, {"sleep": 60}, {"finish": {}}])
    env.run_until(sup, lambda: job.id in runtime.workspace_runs)
    run = runtime.workspace_runs[job.id]
    assert workspace.list_files(run.ref, run.credential) is not None  # credential works while running
    env.queue.cancel(job.id, reason="user cancelled", actor="user:local")
    env.run_until(sup, lambda: sup.running() == 0, 10)
    with pytest.raises(AuthorizationError):
        workspace.list_files(run.ref, run.credential)  # the workspace credential was revoked
    archive = workspace._project_dir("demo") / "archive" / run.ref.run_id
    assert (archive / "ARCHIVE-MANIFEST.json").is_file()  # evidence kept before cleanup


def test_dead_worker_recovery_revokes_workspace_keeps_logs_and_preserves_other_run(clocked, clock, db_path, tmp_path):
    """SIGKILL a real supervisor, leaving its children and workspace behind."""
    from app.workspace import WorkspaceSupervisor
    from app.workspace.errors import AuthorizationError
    from app.workspace.runspec import RunRef, RunStore
    from tests.workspace.conftest import NoContainerSandbox, small_manifest

    root = tmp_path / "workspace"
    workspace = WorkspaceSupervisor(root, sandbox=NoContainerSandbox())
    workspace.create_project("demo")
    other = workspace.start_attempt("demo", ticket_id="other", scope_version=1, role="developer", attempt=1,
        generation=1, lease_id="other:1", manifest=small_manifest(), provenance={"job_id": "unrelated"})
    job = clocked.enqueue([{"workspace": "demo"}, {"spawn": 60}, {"sleep": 60}])
    code = """
import sys, threading
from pathlib import Path
from app.persistence import Database, ArtifactStore
from app.workers import JobQueue, Supervisor, ProviderLimiter, WorkerConfig
from app.adapters.runtime.fake import FakeRuntime
from app.workspace import WorkspaceSupervisor
from tests.workspace.conftest import NoContainerSandbox, small_manifest
db = Database(Path(sys.argv[1]))
ws = WorkspaceSupervisor(Path(sys.argv[3]), sandbox=NoContainerSandbox())
sup = Supervisor(db, ArtifactStore(Path(sys.argv[2])), {'fake': FakeRuntime(ws, small_manifest())},
    queue=JobQueue(db), limiter=ProviderLimiter(), config=WorkerConfig(heartbeat_s=0.05, poll_s=0.02))
sup.run_forever(threading.Event())
"""
    worker = subprocess.Popen([sys.executable, "-c", code, str(db_path), str(clocked.store.root), str(root)])
    pgid = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            recorded = clocked.job(job.id).runtime_ref.get("processes")
            if recorded:
                pgid = recorded[0]["pgid"]
                break
            assert worker.poll() is None
            time.sleep(0.02)
        assert pgid is not None
        worker.kill()
        # Keep the killed worker as a zombie until recovery: it cannot execute even
        # though its PID/start time remain visible in /proc until the parent reaps it.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if sys.platform == "darwin":
                import psutil
                state = "Z" if psutil.Process(worker.pid).status() == psutil.STATUS_ZOMBIE else "R"
            else:
                state = open(f"/proc/{worker.pid}/stat").read().rsplit(")", 1)[-1].split()[0]
            if state == "Z":
                break
            time.sleep(0.02)
        assert state == "Z"
        assert group_members(pgid)  # target children survived the worker's death
        owned = [RunStore(path).spec() for path in (root / "demo" / "runs").glob("run-*")
                 if RunStore(path).spec().provenance.get("job_id") == job.id]
        [spec] = owned
        clock.advance(61)
        runtime = FakeRuntime(workspace, small_manifest())
        sup = clocked.supervisor(worker_id="replacement", runtime=runtime)
        assert sup.recover() == [job.id]
        worker.wait(5)
        assert gone(pgid)
        state = RunStore(workspace.run_dir(RunRef("demo", spec.run_id))).state()
        assert state["credential_sha256"] is None
        assert (root / "demo" / "archive" / spec.run_id / "ARCHIVE-MANIFEST.json").is_file()
        assert workspace.list_files(other.ref, other.credential) is not None  # another run/preview stays live
        old = clocked.job(job.id)
        assert old.result["retry_job_id"]
        [log_id] = old.result["evidence_artifact_ids"]
        with clocked.db.read() as s:
            log = clocked.store.read_bytes(s, log_id)
        assert b"FAKE runtime start" in log and b"spawned process group" in log
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(5)
        if pgid:
            kill_quietly(pgid)
        for path in (root / "demo" / "runs").glob("run-*"):
            workspace.stop_run(RunRef("demo", path.name), "test_cleanup")


def test_recovery_finds_process_launched_before_registration(clocked, clock):
    job = clocked.enqueue()
    lease = clocked.queue.claim("dead", "execution", capacity=1, runtimes=("fake",))
    clocked.queue.register_resource(lease, {"kind": "process_groups", "generation": lease.generation})
    proc = spawn_group(f"{job.id}:{lease.generation}")
    try:
        # Simulate death after Popen, before register_process could write the PGID.
        clock.advance(31)
        clocked.supervisor().recover()
        assert gone(proc.pid)
        assert clocked.job(job.id).result["retry_job_id"]
    finally:
        kill_quietly(proc.pid)
