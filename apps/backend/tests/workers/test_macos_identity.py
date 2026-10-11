"""macOS owner recovery must handle legacy records and PID reuse without blind cleanup."""
import os
import socket
import sys

import pytest

from app.workers import runtime

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS process identity")


def snapshot(pid, start=None, hostname=None):
    return {"runtime_ref": {"cleanup": {"host": {
        "pid": pid, "start": start, "hostname": hostname or socket.gethostname()}}}}


def test_legacy_owner_still_alive_does_not_release_slot():
    assert not runtime.owner_gone(snapshot(os.getpid()))


def test_current_owner_identity_does_not_release_slot():
    assert not runtime.owner_gone(snapshot(os.getpid(), runtime.process_start(os.getpid())))


def test_reused_pid_is_not_the_original_owner():
    assert runtime.owner_gone(snapshot(os.getpid(), "different-start"))


def test_other_host_cannot_be_reconciled_locally():
    assert not runtime.owner_gone(snapshot(os.getpid(), "different-start", "another-host"))


def test_legacy_missing_owner_can_be_recovered(monkeypatch):
    def missing(pid):
        raise runtime.psutil.NoSuchProcess(pid)
    monkeypatch.setattr(runtime.psutil, "Process", missing)
    assert runtime.owner_gone(snapshot(12345))


def test_denied_intent_inspection_blocks_cleanup(monkeypatch):
    def denied():
        raise runtime.psutil.AccessDenied(12345)
    monkeypatch.setattr(runtime, "_intent_pids", denied)
    assert not runtime.reap_recorded_processes({"id": "job", "runtime_ref": {
        "resources": [{"kind": "process_groups", "generation": 1}]}})

@pytest.mark.parametrize('after_denial', ['zombie', 'gone', 'running', 'denied'])
def test_intent_environment_denial_rechecks_whether_process_can_still_execute(monkeypatch, after_denial):
    """macOS environ can deny a process that became a zombie after status inspection."""
    from types import SimpleNamespace
    pid = 12345
    class Process:
        calls = 0
        def status(self):
            self.calls += 1
            if self.calls == 1:
                return runtime.psutil.STATUS_RUNNING
            if after_denial == 'gone':
                raise runtime.psutil.NoSuchProcess(pid)
            if after_denial == 'denied':
                raise runtime.psutil.AccessDenied(pid)
            return runtime.psutil.STATUS_ZOMBIE if after_denial == 'zombie' else runtime.psutil.STATUS_RUNNING
        def environ(self):
            raise runtime.psutil.AccessDenied(pid)
    process = Process()
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=f'{pid} {os.getuid()}\n'))
    monkeypatch.setattr(runtime.psutil, 'Process', lambda _: process)
    if after_denial in ('zombie', 'gone'):
        assert runtime._intent_pids() == []
    else:
        with pytest.raises(runtime.psutil.AccessDenied):
            runtime._intent_pids()
