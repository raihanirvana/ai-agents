"""Disposable DEV-009 fixture: the real HTTP API + a real supervisor whose PO model is the LABELLED FakeProvider.

Nothing here proves a real model or provider. The browser tests script the fake PO replies through a tiny
control port (never exposed by the product) so the whole brief -> proposal -> revision -> approval flow runs
through the real API, database, scheduler and structured runtime.
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "backend"))
import uvicorn
from app.agents import ContextBuilder, FakeProvider, ModelClient, ModelRegistry, StructuredAgentRuntime, load_agents
from app.domain import Workflow
from app.http.application import create_app
from app.http.security import Settings
from app.persistence import ArtifactStore, Database, migrate
from app.workers import Supervisor, WorkerConfig

API, UI, CONTROL = 19850, 19851, 19852
CODE = "test-only-web-code-0123456789abcdef"
CONFIG = {"default": {"provider": "fake", "model": "fake-model", "timeout_s": 5, "max_output_tokens": 512}}

script: list = []
gate = threading.Event()
lock = threading.Lock()


def reply(request):
    """Pop the next scripted reply. The string "HOLD" blocks the model call until the gate is opened."""
    with lock:
        item = script.pop(0) if script else "{}"
    if item == "HOLD":
        gate.wait(20)
        with lock:
            item = script.pop(0) if script else "{}"
    return item


class Control(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        if self.path == "/script":
            with lock:
                script.extend(body["replies"])
            gate.clear()
        elif self.path == "/gate/open":
            gate.set()
        elif self.path == "/script/clear":
            with lock:
                script.clear()
            gate.set()
        elif self.path in ("/fixture/uat", "/fixture/scope"):
            # Domain contract fixture with real DB/files and SYNTHETIC trusted receipts.
            # This tests GUI -> API wiring; it is not evidence of actual QA execution.
            from tests.domain.conftest import World
            world = World(db, store)
            if self.path == "/fixture/scope":
                t = world.new()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"project_id": t.project_id, "ticket_id": t.id}).encode())
                return
            t, c, target, verification, evidence_ids = world.uat()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"project_id": t.project_id, "ticket_id": t.id,
                "candidate_id": c.id, "evidence_ids": evidence_ids}).encode())
            return
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    with TemporaryDirectory(prefix="dev009-browser-") as tmp:
        path = Path(tmp)
        migrate.upgrade(path / "app.sqlite3")
        db = Database(path / "app.sqlite3")
        store = ArtifactStore(path / "artifacts")
        app = create_app(db=db, store=store, login_code=CODE, runtime="structured:fake",
                         settings=Settings(port=API, origins=(f"http://127.0.0.1:{UI}",), poll_s=.05))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=API, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        while not server.started:
            time.sleep(.05)
        state = app.state.api
        workflow = Workflow(db, store)
        provider = FakeProvider(reply)
        client = ModelClient(ModelRegistry.from_dict(CONFIG), {"fake": provider}, state.redactor)
        runtime = StructuredAgentRuntime(db=db, workflow=workflow, threads=state.threads,
            builder=ContextBuilder(db, store, load_agents(), state.redactor), client=client, redactor=state.redactor, fake=True)
        supervisor = Supervisor(db, store, {"structured:fake": runtime}, queue=state.queue, workflow=workflow,
                                limiter=state.limiter, config=WorkerConfig(worker_id="worker:web-browser", heartbeat_s=.1, poll_s=.1))
        stop = threading.Event()
        threading.Thread(target=supervisor.run_forever, args=(stop,), daemon=True).start()
        control = ThreadingHTTPServer(("127.0.0.1", CONTROL), Control)
        try:
            control.serve_forever()
        finally:
            stop.set()
            gate.set()
            server.should_exit = True
            db.dispose()
