"""Runtime adapter contract and the fenced context a runtime works through.

A runtime never touches the database directly. Every model/tool call is reserved through
RunContext first (lease + budget + provider limiter), so revoked or over-budget attempts
cannot make new calls. Process groups are registered so cancellation and crash recovery
can stop them. The real Hermes adapter is wired in DEV-010; tests use a labelled fake.
"""
from __future__ import annotations

import os
import signal
import socket
import sys
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .limiter import ProviderLimiter
from .queue import JobQueue, Lease, QuotaWait

if sys.platform == "darwin":
    import psutil
    ProcessInspectionError = psutil.Error
else:
    ProcessInspectionError = OSError


class WaitingForInput(Exception):
    """Raised inside the runtime after the question was persisted; the run ends cleanly."""


class Cancelled(Exception):
    """The supervisor revoked this run; stop at the next safe point."""


@dataclass
class Outcome:
    status: str  # succeeded | failed
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    retryable: bool = False


class Runtime(Protocol):
    name: str

    def run(self, ctx: "RunContext") -> Outcome: ...

    def stop(self, ctx: "RunContext") -> None: ...


class RunContext:
    def __init__(self, *, queue: JobQueue, limiter: ProviderLimiter, lease: Lease, job: dict[str, Any],
                 answer: str | None = None):
        self.queue, self.limiter, self.lease, self.job, self.answer = queue, limiter, lease, job, answer
        self.lane = job["lane"]
        self.cancelled = threading.Event()
        self.processes: list[tuple[int, str]] = []
        self._stoppers: list = []  # e.g. WorkspaceSupervisor.stop_run for this attempt's containers
        self._log: list[str] = []
        self._lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._stopped = False
        self._cleanup_ok = True

    @property
    def tag(self) -> str:
        """Ownership label put into the environment of every process this attempt starts."""
        return f"{self.lease.job_id}:{self.lease.generation}"

    def actor(self):
        """The domain Actor for this attempt (DEV-003 checks owner/role/job/generation/lease)."""
        from app.domain import Actor
        return Actor(self.lease.owner, self.job["runtime_ref"]["role"], self.job["project_id"],
                     job_id=self.lease.job_id, generation=self.lease.generation)

    def log(self, line: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {line}"
        self.queue.log_line(self.lease.job_id, self.lease.generation, line)
        with self._lock:
            self._log.append(line)

    def log_bytes(self) -> bytes:
        with self._lock:
            return ("\n".join(self._log) + "\n").encode()

    def _check(self) -> None:
        if self.cancelled.is_set():
            raise Cancelled()

    def model_call(self, call) -> Any:
        """Reserve (counted before the call), run call(max_output_tokens), finalize usage.

        call returns (result, usage); usage None or missing keys are recorded as unknown.
        """
        self._check()
        self.limiter.acquire(self.lane)
        unknown = {"output_tokens": None, "total_tokens": None, "cost_usd": None}
        try:
            self.queue.reserve(self.lease, "model")
            try:
                result, usage = call(self.job["limits"].get("output_tokens"))
            except BaseException:
                # The request was already counted; whatever it consumed is unknown, never zero.
                self.queue.finalize_usage(self.lease.job_id, self.lease.generation, unknown)
                raise
        finally:
            self.limiter.release(self.lane)
        usage = {**unknown, **(usage or {})}
        self.queue.finalize_usage(self.lease.job_id, self.lease.generation, usage)
        cap = self.job["limits"].get("output_tokens")
        if cap and usage.get("output_tokens", 0) is not None and usage["output_tokens"] > cap:
            self.queue.cancel(self.lease.job_id, reason="provider exceeded output token cap", actor="system:supervisor")
            raise Cancelled("provider exceeded output token cap")
        self.log(f"model call usage={usage}")
        return result

    def provider_quota(self, retry_after_s: float, reason: str) -> QuotaWait:
        return self.limiter.exhausted(retry_after_s, reason)

    def tool_call(self, name: str, call) -> Any:
        self._check()
        self.queue.reserve(self.lease, "tool")
        self.log(f"tool {name}")
        return call()

    def request_input(self, question: str, checkpoint: dict[str, Any], key: str) -> None:
        request_id = self.queue.request_input(self.lease, question=question, checkpoint=checkpoint, request_key=key)
        self.log(f"waiting for input {request_id}")
        raise WaitingForInput(request_id)

    def register_process(self, pgid: int) -> None:
        try:
            with self._stop_lock:
                if self._stopped:
                    raise Cancelled()
                self.queue.register_process(self.lease, pgid, self.tag)
                self.processes.append((pgid, self.tag))
        except BaseException:
            clean = reap_recorded_processes({"runtime_ref": {"processes": [{"pgid": pgid, "tag": self.tag}]}})
            with self._stop_lock:
                self._cleanup_ok = self._cleanup_ok and clean
            raise

    def spawn_process(self, command: list[str]) -> int:
        """Persist intent before launch, so a crash before registration leaves a discoverable tag."""
        self._check()
        self.queue.register_resource(self.lease, {"kind": "process_groups", "generation": self.lease.generation})
        proc = subprocess.Popen(command, env={**os.environ, "AIAGENTS_RUN": self.tag}, start_new_session=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.register_process(proc.pid)
        return proc.pid

    def add_stopper(self, stop, *, resource: dict | None = None) -> None:
        """Register cleanup with a durable descriptor. Opaque callbacks require human recovery.

        A resource creation intent must be registered before launch. Passing that descriptor
        here is idempotent; unknown callbacks are recorded so a crash cannot silently lose them.
        """
        with self._stop_lock:
            if self._stopped:
                try:
                    stop()
                except Exception:
                    self._cleanup_ok = False
                    raise
                raise Cancelled()
            try:
                self.queue.register_resource(self.lease, resource or {
                    "kind": "opaque", "generation": self.lease.generation})
            except BaseException:
                try:
                    stop()
                except Exception:
                    self._cleanup_ok = False
                    raise
                raise
            self._stoppers.append(stop)

    def stop_resources(self, grace_s: float = 3.0) -> bool:
        """Stop every process group of this attempt, then its other registered resources.

        Runs once per resource; safe to call again (finished runs, cancellation, shutdown).
        """
        with self._stop_lock:
            if self._stopped:
                return self._cleanup_ok
            self._stopped = True
            processes_clean = reap_recorded_processes({"runtime_ref": {"processes": [
                {"pgid": pgid, "tag": tag} for pgid, tag in self.processes]}})
            self._cleanup_ok = self._cleanup_ok and processes_clean
            for stop in self._stoppers:
                try:
                    stop()
                    self.log("stopped attempt resource")
                except Exception as exc:
                    self._cleanup_ok = False
                    self.log(f"resource stop failed: {exc!r}")
            self._stoppers = []
            return self._cleanup_ok

def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def kill_group(pgid: int, grace_s: float = 3.0) -> bool:
    if not hasattr(os, "killpg"):
        raise NotImplementedError("process-group supervision needs a POSIX host (use WSL on Windows)")
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if not group_members(pgid):
            return True
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + grace_s
    while group_members(pgid) and time.monotonic() < deadline:
        time.sleep(0.02)
    return not group_members(pgid)


def process_tag(pid: int) -> str | None:
    """Read only the attempt label; unavailable ownership remains unknown."""
    if sys.platform == "darwin":
        try:
            return psutil.Process(pid).environ().get("AIAGENTS_RUN")
        except psutil.Error:
            return None
    try:
        environ = open(f"/proc/{pid}/environ", "rb").read().split(b"\0")
    except OSError:
        return None
    for item in environ:
        if item.startswith(b"AIAGENTS_RUN="):
            return item.split(b"=", 1)[1].decode()
    return None


def reap_recorded_processes(snapshot: dict[str, Any]) -> bool:
    """Recovery reaper: stop process groups a dead attempt recorded, but only when the live
    process still carries that attempt's label (a reused PID belongs to someone else).
    Returns False when ownership cannot be verified on this host."""
    ref = snapshot["runtime_ref"]
    processes = list(ref.get("processes", []))
    intentions = [r for r in ref.get("resources", []) if r.get("kind") == "process_groups"]
    if not processes and not intentions:
        return True
    if (sys.platform != "darwin" and not os.path.isdir("/proc")) or not hasattr(os, "killpg"):
        return False
    for intent in intentions:
        tag = f"{snapshot['id']}:{intent['generation']}"
        groups = set()
        try:
            entries = _intent_pids() if sys.platform == "darwin" else os.listdir("/proc")
        except (OSError, ProcessInspectionError, subprocess.SubprocessError, ValueError):
            return False
        for entry in entries:
            entry = str(entry)
            if entry.isdigit() and process_tag(int(entry)) == tag:
                try:
                    groups.add(os.getpgid(int(entry)))
                except ProcessLookupError:
                    pass
        processes.extend({"pgid": pgid, "tag": tag} for pgid in groups)
    for proc in processes:
        try:
            members = group_members(proc["pgid"])
        except (OSError, ProcessInspectionError):
            return False
        if not members:
            continue
        # The leader may already be gone while its children live on: check every member.
        tags = {process_tag(pid) for pid in members}
        if tags == {proc["tag"]}:
            try:
                if not kill_group(proc["pgid"]):
                    return False
            except (OSError, ProcessInspectionError):
                return False
        elif proc["tag"] in tags or None in tags:
            return False  # mixed ownership: never kill blindly, leave it to a human
    return True


def group_members(pgid: int) -> list[int]:
    """Live non-zombie members; unreadable membership raises instead of proving absence."""
    if sys.platform == "darwin":
        members = []
        for pid in psutil.pids():
            try:
                if os.getpgid(pid) == pgid and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                    members.append(pid)
            except (ProcessLookupError, psutil.NoSuchProcess):
                continue
        return members
    members = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            stat = open(f"/proc/{entry}/stat", "rb").read().decode(errors="replace")
        except OSError:
            continue
        fields = stat.rsplit(")", 1)[-1].split()
        if len(fields) > 2 and fields[0] != "Z" and int(fields[2]) == pgid:
            members.append(int(entry))
    return members


def host_identity() -> dict:
    return {"hostname": socket.gethostname(), "pid": os.getpid(), "start": process_start(os.getpid())}


def process_start(pid: int) -> str | None:
    if sys.platform == "darwin":
        try:
            return str(psutil.Process(pid).create_time())
        except psutil.Error:
            return None
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[-1].split()[19]
    except OSError:
        return None


def owner_gone(snapshot: dict) -> bool:
    """Do not reuse the slot while a timed-out worker can still create resources."""
    host = snapshot["runtime_ref"].get("cleanup", {}).get("host")
    if not host:
        return True  # direct queue claims have no executing Python runtime
    if host.get("hostname") != socket.gethostname():
        return False
    if sys.platform == "darwin":
        try:
            process = psutil.Process(host["pid"])
            if process.status() == psutil.STATUS_ZOMBIE:
                return True
            # Old macOS records have no start identity: only disappearance proves death.
            return host.get("start") is not None and str(process.create_time()) != host["start"]
        except psutil.NoSuchProcess:
            return True
        except psutil.Error:
            return False
    if not os.path.isdir("/proc"):
        return False
    if host.get("start") is None:
        return False
    try:
        with open(f"/proc/{host['pid']}/stat") as fh:
            fields = fh.read().rsplit(")", 1)[-1].split()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return fields[0] == "Z" or fields[19] != host["start"]


def _intent_pids() -> list[int]:
    """Inspect same-user processes for launch intents on macOS, failing closed on denial."""
    # macOS can deny proc_pidinfo even for identifying root-owned login processes.
    # ps exposes PID/UID without reading their environments; only our UID can own a child.
    listing = subprocess.run(["/bin/ps", "-axo", "pid=,uid="], capture_output=True, text=True,
                             check=True, timeout=10)
    pids = []
    for line in listing.stdout.splitlines():
        pid, uid = map(int, line.split())
        if uid != os.getuid():
            continue
        try:
            process = psutil.Process(pid)
            if process.status() != psutil.STATUS_ZOMBIE:
                # A denied environment read cannot prove an unregistered child absent.
                process.environ()
                pids.append(pid)
        except psutil.NoSuchProcess:
            continue
    return pids
