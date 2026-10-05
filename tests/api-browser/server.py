"""Disposable real HTTP servers for DEV-008; uses no local config or provider key."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "backend"))
import uvicorn
from app.http.application import create_app
from app.http.security import Settings
from app.persistence import ArtifactStore, Database, migrate

API, CONTROL, PREVIEW = 19841, 19842, 19843
CODE = "test-only-browser-code-0123456789"


class Page(BaseHTTPRequestHandler):
    api_requests = []
    def do_GET(self):
        if self.path == "/client.js":
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript")
            self.end_headers()
            self.wfile.write((ROOT / "data" / "dev008" / "client.js").read_bytes())
            return
        capture = self.path.startswith("/capture") or self.path.startswith("/api-capture")
        self.send_response(200)
        self.send_header("Content-Type", "application/json" if capture else "text/html")
        self.end_headers()
        if capture:
            # Store booleans only, never credential values in reports.
            data = self.api_requests if self.path.startswith("/api-capture") else {"host": self.headers.get("Host"), "cookie_present": bool(self.headers.get("Cookie")),
                    "authorization_present": bool(self.headers.get("Authorization")),
                    "csrf_present": bool(self.headers.get("X-CSRF-Token"))}
            self.wfile.write(json.dumps(data).encode())
        else:
            self.wfile.write(b"<!doctype html><title>DEV-008 browser fixture</title><p>Local fixture</p>")

    def log_message(self, *args): pass


class CaptureApi:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http": return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        async def record(message):
            if message["type"] == "http.response.start":
                Page.api_requests.append({"method": scope["method"], "path": scope["path"],
                    "origin": headers.get(b"origin", b"").decode(), "status": message["status"],
                    "last_event_id": headers.get(b"last-event-id", b"").decode()})
            await send(message)
        await self.app(scope, receive, record)


if __name__ == "__main__":
    with TemporaryDirectory(prefix="dev008-browser-") as tmp:
        path = Path(tmp)
        migrate.upgrade(path / "app.sqlite3")
        db = Database(path / "app.sqlite3")
        app = create_app(db=db, store=ArtifactStore(path / "artifacts"), login_code=CODE,
            settings=Settings(port=API, origins=(f"http://127.0.0.1:{CONTROL}",), poll_s=.05, replay_events=3), runtime="structured:fake")
        app.add_middleware(CaptureApi)
        servers = [ThreadingHTTPServer(("127.0.0.1", port), Page) for port in (CONTROL, PREVIEW)]
        for server in servers: threading.Thread(target=server.serve_forever, daemon=True).start()
        try: uvicorn.run(app, host="127.0.0.1", port=API, log_level="warning")
        finally:
            for server in servers: server.shutdown(); server.server_close()
            db.dispose()
