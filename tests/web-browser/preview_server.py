"""Disposable DEV-011 fixture: the real HTTP API + the real PreviewService (Docker, unix sockets, loopback proxy).

Tickets are put into UAT through the real domain transitions with contract fixtures for QA evidence (a preview only
needs a stored, digest-pinned build and a passed verification; nothing here is model output or real QA). The previewed
page is TARGET code: it probes the control plane from the preview origin so the browser tests can prove that it
cannot reach it. Linux/macOS with Docker only (WSL on Windows).
"""
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "backend"))
import uvicorn
from sqlalchemy import select
from app.http.application import create_app
from app.http.security import Settings
from app.persistence import ArtifactStore, Database, migrate
from app.persistence.models import Artifact, Candidate, Preview, Ticket
from app.preview.service import PreviewService
from tests.preview.helpers import PreviewEnv

API, UI, CONTROL, PREVIEW = 19860, 19861, 19862, 19863
CODE = "test-only-preview-code-0123456789abcdef"
PROBE = """(async () => {
  const out = {};
  const attempts = [['get', {credentials: 'include'}],
    ['post', {method: 'POST', credentials: 'include', body: '{}',
      headers: {'Content-Type': 'application/json', 'Idempotency-Key': 'probe-' + Date.now(), 'X-CSRF-Token': 'guessed'}}]];
  for (const [name, init] of attempts) {
    try { out[name] = 'status:' + (await fetch('http://127.0.0.1:%d/projects', init)).status; }
    catch (error) { out[name] = 'blocked'; }
  }
  document.getElementById('probe').textContent = JSON.stringify(out);
})();""" % API
SITE = {"index.html": '<!doctype html><title>Coffee preview</title><main id="app">Latte 4.00</main><pre id="probe">pending</pre>'
                      '<script src="/probe.js"></script>', "probe.js": PROBE}
SECOND_SITE = {"index.html": '<!doctype html><title>Second preview</title><main id="app">Mocha 5.00</main>'}
captured, state = [], {"ready": False}


class Capture:
    """Records every API request's origin/status and whether a credential arrived (booleans only)."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])

        async def record(message):
            if message["type"] == "http.response.start":
                captured.append({"method": scope["method"], "path": scope["path"], "status": message["status"],
                                 "origin": headers.get(b"origin", b"").decode(), "cookie": bool(headers.get(b"cookie")),
                                 "authorization": bool(headers.get(b"authorization"))})
            await send(message)
        await self.app(scope, receive, record)


def docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, timeout=30).stdout.decode()


def make_control(db, store):
    class Control(BaseHTTPRequestHandler):
        def _json(self, value, status=200):
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/ready":
                return self._json({"ready": True}, 200 if state["ready"] else 503)
            if path == "/captured":
                return self._json(captured)
            if path == "/containers":
                names = docker("ps", "-a", "--filter", "label=aiagent.container-role=preview",
                               "--filter", "label=aiagent.supervisor=preview:browser-test", "--format", "{{.Names}}").split()
                return self._json(names)
            if path == "/previews":
                with db.read() as s:
                    return self._json([{"id": p.id, "status": p.status, "reason": p.stop_reason, "error": p.error}
                                       for p in s.scalars(select(Preview).order_by(Preview.created_at))])
            self._json({}, 404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            if self.path == "/break-bundle":  # the stored build bundle goes missing from disk
                with db.read() as s:
                    ticket = s.scalar(select(Ticket).where(Ticket.title == body["title"]))
                    candidate = s.get(Candidate, ticket.workflow["candidate_id"])
                    build = json.loads(store.read_bytes(s, candidate.build_artifact_id))
                    path = store.resolve(s.get(Artifact, build["bundle_artifact_id"]).path)
                path.chmod(0o600)
                path.unlink()
                return self._json({"removed": True})
            self._json({}, 404)

        def log_message(self, *args):
            pass
    return Control


if __name__ == "__main__":
    with TemporaryDirectory(prefix="dev011-browser-") as tmp:
        path = Path(tmp)
        migrate.upgrade(path / "app.sqlite3")
        db, store = Database(path / "app.sqlite3"), ArtifactStore(path / "artifacts")
        env = PreviewEnv(db, store, path)
        for title, files in (("Preview A", SITE), ("Preview B", SECOND_SITE), ("Preview C", SITE), ("Preview D", SITE), ("Preview E", SITE)):
            ticket = env.world.approve(env.world.new({"title": title, "description": "", "dependencies": [],
                                                        "uac": [{"id": "UAC-1", "text": "Latte is shown"}]}))
            env.to_uat(ticket, files=files)
        app = create_app(db=db, store=store, login_code=CODE, runtime="structured:fake",
                         settings=Settings(port=API, origins=(f"http://127.0.0.1:{UI}",), poll_s=.05, preview_port=PREVIEW))
        app.add_middleware(Capture)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=API, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        while not server.started:
            time.sleep(.05)
        service = PreviewService(db, store, path / "root", owner="preview:browser-test")
        stop = threading.Event()

        def loop():
            while not stop.is_set():
                service.run_once()
                stop.wait(.2)
        threading.Thread(target=loop, daemon=True).start()
        state["ready"] = True
        control = ThreadingHTTPServer(("127.0.0.1", CONTROL), make_control(db, store))
        try:
            control.serve_forever()
        finally:
            stop.set()
            service.shutdown()
            server.should_exit = True
            db.dispose()
