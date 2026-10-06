"""Supervisor-side preview lifecycle (POSIX + Docker).

Owns: the container, its unix-socket directory, the loopback proxy and the unpacked site copy of one preview.
Never owns jobs, approvals or artifacts. The preview serves the stored, digest-checked build bundle; nothing here
rebuilds, so a reopened preview is the same tested artifact (a rebuild is a new candidate with new QA/UAT).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import stat
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from app.domain.types import Conflict
from app.persistence import ArtifactUnavailable
from app.persistence.columns import utcnow
from app.persistence.models import Candidate, Preview, Project, Ticket
from app.pipeline.workspace import unpack_tree
from app.workspace import fsutil
from app.workspace.sandbox import DockerSandbox, LABEL_MANAGED, LABEL_ROLE, SandboxError
from . import requests as pr
from .proxy import ProxyError, UnixProxy, fetch_via_socket, SOCKET_RELAY, relay_command

SUITE_DIR = Path(__file__).resolve().parents[4] / "contracts" / "verification"
ROLE_PREVIEW = "preview"
LABEL_PREVIEW = "aiagent.preview"
LIVENESS_EVERY_S = 3.0


class PreviewFailed(RuntimeError):
    """A start that cannot complete; the message is stored on the row (kept short, no secrets)."""


class PreviewService:
    def __init__(self, db, store, root, *, sandbox: DockerSandbox | None = None, owner: str | None = None,
                 health_timeout_s: float = 20.0):
        self.db, self.store = db, store
        self.base = Path(root).resolve() / ".previews"
        # Unix socket paths are limited to ~104 bytes, so sockets live under a short, private temp directory.
        self.sockets = Path(tempfile.gettempdir()) / f"aiagent-preview-{os.getuid()}"
        if len(os.fsencode(self.sockets / ('0' * 16) / 'preview.sock')) > 100:
            # macOS TMPDIR can already consume most of the AF_UNIX path limit.
            # Keep other temporary files in their configured location.
            self.sockets = Path('/tmp') / f"aiagent-preview-{os.getuid()}"
        self.owner = owner or f"preview:{socket.gethostname()}"
        self.sandbox = sandbox or DockerSandbox(supervisor_id=self.owner)
        self.health_timeout_s = health_timeout_s
        self._docker_relay = sys.platform == 'darwin'
        self._proxies: dict[str, UnixProxy] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="preview")
        self._busy = threading.Lock()
        self._last_liveness = 0.0
        self._recovered = False

    # -- scheduling ----------------------------------------------------------------------------
    def tick(self) -> None:
        """Supervisor maintenance hook: never blocks the supervisor loop (Docker work runs on one worker thread)."""
        if not self._busy.acquire(blocking=False):
            return

        def work():
            try:
                self.run_once()
            finally:
                self._busy.release()

        try:
            self._pool.submit(work)
        except BaseException:
            self._busy.release()
            raise

    def run_once(self) -> None:
        if not self._recovered:
            self.recover()
        with self.db.read() as s:
            rows = list(s.scalars(select(Preview).where(Preview.status.in_(("stopping", "requested"))).order_by(Preview.created_at)))
        for row in (r for r in rows if r.status == "stopping"):  # a switch stops the old preview before the new one starts
            self._stop(row.id)
        with self.db.read() as s:
            cleanup_pending = any(p.status == "stopping" for p in pr.active(s))
        if not cleanup_pending:
            for row in (r for r in rows if r.status == "requested"):
                self._start(row.id)
        if time.monotonic() - self._last_liveness >= LIVENESS_EVERY_S:
            self._last_liveness = time.monotonic()
            self._check_ready()

    def shutdown(self) -> None:
        """Process exit: proxies die with it, so nothing may claim to be ready afterwards."""
        self._pool.shutdown(wait=True)
        with self.db.read() as s:
            ids = [p.id for p in pr.active(s) if p.status in ("starting", "ready", "stopping")]
        for preview_id in ids:
            self._stop(preview_id, reason="worker_stopped", force=True)

    def recover(self) -> None:
        """Start of a worker: previews that were starting/ready lost their proxy with the old process."""
        self._recovered = True
        with self.db.read() as s:
            rows = [p.id for p in pr.active(s) if p.status in ("starting", "ready", "stopping")]
        for preview_id in rows:
            self._stop(preview_id, reason="worker_restart", force=True)

    # -- row helpers ---------------------------------------------------------------------------
    def _update(self, preview_id: str, mutate, *, event: str | None = None, **payload) -> Preview | None:
        for _ in range(5):
            try:
                with self.db.write() as s:
                    row = s.get(Preview, preview_id)
                    if row is None or not mutate(row):
                        return None
                    if event:
                        pr._event(s, row, event, self.owner, **payload)
                    s.flush()
                    s.expunge(row)
                    return row
            except StaleDataError:
                continue
        return None

    # -- start ---------------------------------------------------------------------------------
    def _start(self, preview_id: str) -> None:
        container = f"aiagent-preview-{preview_id[:12]}-{int(time.time())}"

        def claim(row):
            if row.status != "requested":
                return False
            row.status, row.owner, row.container_name, row.error = "starting", self.owner, container, None
            return True

        row = self._update(preview_id, claim, event="starting")
        if row is None:
            return
        directory = self.base / preview_id
        try:
            self._launch(row, directory, container)
        except Exception as exc:  # any failure leaves no container, socket, proxy or site copy behind
            reason = self._reason(exc)
            def cleanup_pending(r):
                if r.status not in ("starting", "stopping"):
                    return False
                r.status, r.error, r.stop_reason = "stopping", reason, "start_failed"
                return True
            self._update(preview_id, cleanup_pending, event="stopping", error=reason)
            try:
                self._teardown(preview_id, container)
            except Exception as cleanup_exc:
                self._update(preview_id, lambda r: self._cleanup_error(r, cleanup_exc))
                return
            def failed(r):
                if r.status != "stopping":
                    return False
                r.status, r.error, r.stopped_at = "failed", reason, utcnow()
                return True
            self._update(preview_id, failed, event="failed", error=reason)

    @staticmethod
    def _cleanup_error(row, exc):
        row.status, row.error = "stopping", f"cleanup incomplete: {exc}"[:500]
        return True

    @staticmethod
    def _reason(exc: Exception) -> str:
        if isinstance(exc, ArtifactUnavailable):
            return f"artifact unavailable: {exc}"[:500]
        return f"{type(exc).__name__}: {exc}"[:500]

    def _launch(self, row: Preview, directory: Path, container: str) -> None:
        with self.db.read() as s:
            try:
                _, candidate, target, extra = pr.eligible_target(s, self.store, row.ticket_id, row.candidate_id)
            except Conflict as exc:
                raise PreviewFailed(f"no longer eligible: {exc}") from exc
            if candidate.target_digest != row.target_digest:
                raise PreviewFailed("candidate target changed since the request")
            files = json.loads(self.store.read_bytes(s, extra["bundle_artifact_id"]))
        site, run = directory / "site", self._socket_dir(row.id)
        if directory.exists():
            shutil.rmtree(directory)
        self._check_socket_root(create=True)
        shutil.rmtree(run, ignore_errors=True)
        run.mkdir(mode=0o777)
        run.chmod(0o777)  # the container user must be able to create its socket here; nothing else is mounted writable
        unpack_tree(files, site)
        if fsutil.sha256_tree(site, fsutil.scan_tree(site)) != target["build_digest"]:
            raise PreviewFailed("stored build bytes do not match the immutable target")
        index = site / "index.html"
        if not index.is_file():
            raise PreviewFailed("build has no index.html to serve")
        image = self.sandbox.image_id(row.details.get("node_image_id") or target["node_image_id"])
        sock = run / "preview.sock"
        if len(os.fsencode(sock)) > 100:
            raise PreviewFailed("socket path is too long; set TMPDIR to a shorter directory")
        labels = {LABEL_MANAGED: "1", LABEL_ROLE: ROLE_PREVIEW, LABEL_PREVIEW: row.id, "aiagent.supervisor": self.owner}
        args = ["create", "--name", container, "--user", "1000:1000", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--memory", "256m", "--memory-swap", "256m", "--cpus", "1",
                "--pids-limit", "128", "--ulimit", "nofile=1024:1024", "--ulimit", "core=0",
                "--tmpfs", "/tmp:rw,nosuid,size=32m", "--network", "none",
                "--log-driver", "json-file", "--log-opt", "max-size=1m", "--log-opt", "max-file=1",
                "-e", "PREVIEW_SOCKET=" + ('/tmp/preview.sock' if self._docker_relay else '/run/preview/preview.sock'),
                "--mount", f"type=bind,source={site},target=/site,readonly",
                "--mount", f"type=bind,source={SUITE_DIR / 'preview-server.cjs'},target=/server.cjs,readonly"]
        if not self._docker_relay:
            args += ["--mount", f"type=bind,source={run},target=/run/preview"]
        for key, value in sorted(labels.items()):
            args += ["--label", f"{key}={value}"]
        self.sandbox._docker(*args, image, "node", "/server.cjs")
        self.sandbox._docker("start", container)
        expected = hashlib.sha256(index.read_bytes()).hexdigest()
        deadline = time.monotonic() + self.health_timeout_s
        last = "no response"
        while time.monotonic() < deadline:
            try:
                if self._docker_relay:
                    response = self.sandbox._docker('exec', '-i', container, 'node', '-e', SOCKET_RELAY,
                        input=b'GET / HTTP/1.0\r\nHost: preview\r\n\r\n', timeout=3, check=False)
                    head, _, body = response.stdout.partition(b'\r\n\r\n')
                    status = int(head.split(b' ', 2)[1]) if response.returncode == 0 else 0
                else:
                    status, body = fetch_via_socket(sock, "/")
                if status == 200 and hashlib.sha256(body).hexdigest() == expected:
                    break
                last = f"HTTP {status}" if status != 200 else "served page differs from the tested build"
            except (OSError, ValueError, IndexError, SandboxError) as exc:
                last = type(exc).__name__
            time.sleep(0.2)
        else:
            raise PreviewFailed(f"smoke health check failed: {last}")
        proxy = UnixProxy(row.port, sock,
            relay=relay_command(self.sandbox.docker, container) if self._docker_relay else None)
        try:
            proxy.start()
        except ProxyError as exc:
            raise PreviewFailed(str(exc)) from exc
        self._proxies[row.id] = proxy

        def ready(r):
            if r.status != "starting":
                return False
            r.status, r.ready_at, r.error = "ready", utcnow(), None
            return True
        if self._update(row.id, ready, event="ready", url=pr.url(row.port)) is None:
            raise PreviewFailed("preview request changed while starting")  # e.g. stopped by the user meanwhile

    # -- stop ----------------------------------------------------------------------------------
    def _stop(self, preview_id: str, *, reason: str | None = None, force: bool = False) -> None:
        with self.db.read() as s:
            row = s.get(Preview, preview_id)
            if row is None:
                return
            container = row.container_name
            status = row.status
        if not force and status != "stopping":
            return
        try:
            self._teardown(preview_id, container)
        except Exception as exc:  # cleanup that cannot be proven stays visible and is retried on the next tick
            message = f"cleanup incomplete: {exc}"[:500]

            def note(r):
                r.status, r.error = "stopping", message
                r.stop_reason = r.stop_reason or reason or "stopped"
                return True
            self._update(preview_id, note)
            return

        def stopped(r):
            if r.status in ("stopped", "failed"):
                return False
            r.status, r.stopped_at, r.error = "stopped", utcnow(), None
            r.stop_reason = r.stop_reason or reason or "stopped"
            return True
        self._update(preview_id, stopped, event="stopped", reason=reason or "stopped")

    def _teardown(self, preview_id: str, container: str | None) -> None:
        proxy = self._proxies.pop(preview_id, None)
        if proxy is not None:
            proxy.stop()
        if container:
            self._remove_container(preview_id, container)
        shutil.rmtree(self.base / preview_id, ignore_errors=True)
        if self._check_socket_root():
            shutil.rmtree(self._socket_dir(preview_id), ignore_errors=True)

    def _check_socket_root(self, *, create=False) -> bool:
        if create:
            self.sockets.mkdir(mode=0o700, exist_ok=True)
        try:
            info = self.sockets.lstat()
        except FileNotFoundError:
            return False
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise PreviewFailed('socket directory must be a private directory owned by the supervisor user')
        return True

    def _socket_dir(self, preview_id: str) -> Path:
        return self.sockets / preview_id[:16]

    def _owner_label(self, name: str):
        """(label, absent): the container's preview label, or absent=True only on an explicit not-found answer.
        Any other inspect failure is not proof of absence, and a lost engine never counts as 'gone'."""
        probe = self.sandbox._docker("inspect", "--format", '{{index .Config.Labels "' + LABEL_PREVIEW + '"}}', name, check=False)
        if probe.returncode == 0:
            return probe.stdout.decode().strip(), False
        self.sandbox._docker("info", "--format", "{{.ServerVersion}}")
        error = probe.stderr.lower()
        if b"no such object" not in error and b"no such container" not in error:
            raise PreviewFailed("container absence could not be verified")
        return None, True

    def _remove_container(self, preview_id: str, name: str) -> None:
        """Remove only a container that is provably this preview's."""
        label, absent = self._owner_label(name)
        if absent:
            return
        if label != preview_id:
            raise PreviewFailed("container ownership mismatch; refusing to remove it")
        # Stop and the start path's own cleanup may remove the same container at once: `docker rm -f` then answers
        # "removal already in progress" while it is still visible. That is not a leak; wait for it to finish.
        deadline = time.monotonic() + 15
        while True:
            self.sandbox.kill_and_remove(name)
            after = self.sandbox._docker("inspect", name, check=False)
            if after.returncode != 0:  # only an explicit not-found answer is proof that it is gone
                self.sandbox._docker("info", "--format", "{{.ServerVersion}}")
                error = after.stderr.lower()
                if b"no such object" not in error and b"no such container" not in error:
                    raise PreviewFailed("container absence could not be verified after cleanup")
                return
            if time.monotonic() >= deadline:
                raise PreviewFailed("owned container remains after cleanup")
            time.sleep(0.2)

    # -- ready previews ------------------------------------------------------------------------
    def _check_ready(self) -> None:
        with self.db.read() as s:
            ready = [p for p in pr.active(s) if p.status == "ready"]
            verdicts = {}
            for p in ready:
                t, c = s.get(Ticket, p.ticket_id), s.get(Candidate, p.candidate_id)
                tip = s.get(Project, p.project_id).workflow.get("accepted_tip")
                if t.phase != "uat" or t.workflow.get("candidate_id") != c.id or c.status != "verified" \
                        or c.target_digest != p.target_digest or tip != c.base_sha:
                    verdicts[p.id] = ("superseded", None)
        def stopping(r):
            if r.status != "ready":
                return False
            r.status, r.stop_reason = "stopping", "superseded"
            return True

        def failed(r):
            if r.status != "ready":
                return False
            r.status, r.error, r.stopped_at = "failed", "preview process exited", utcnow()
            return True

        for p in ready:
            if p.id in verdicts:  # the candidate was superseded, left UAT or the base moved: this exact target may no longer be accepted
                self._update(p.id, stopping, event="stopping", reason="superseded")
                self._stop(p.id)
                continue
            proxy = self._proxies.get(p.id)
            state = self.sandbox._docker("inspect", "--format", "{{.State.Running}}", p.container_name or "-", check=False)
            if proxy is None or not proxy.alive or state.stdout.decode().strip() != "true":
                self._teardown(p.id, p.container_name)
                self._update(p.id, failed, event="failed", error="preview process exited")
