import uuid
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient
from app.agents import Redactor
from app.http.application import create_app
from app.http.security import Settings
from tests.persistence.conftest import db, db_path, store  # noqa: F401

CODE = "test-only-local-code-0123456789"
ORIGIN = "http://127.0.0.1:5173"
SECRET = "sk-test-secret-0123456789"
SCOPE = {"title": "Coffee", "uac": [{"id": "UAC-1", "text": "Add coffee"}]}


class ApiEnv:
    def __init__(self, db, store):
        self.db, self.store = db, store
        self.app = create_app(db=db, store=store, settings=Settings(poll_s=.01),
                              login_code=CODE, redactor=Redactor([SECRET]), runtime="structured:fake")
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False)
        self.client.__enter__()
        self.client.headers["Origin"] = ORIGIN
        login = self.client.post("/auth/login", json={"code": CODE})
        assert login.status_code == 200, login.text
        self.client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        self.api = self.app.state.api

    def cmd(self, path, body, key=None, **kwargs):
        return self.client.post(path, json=body,
                                headers={"Idempotency-Key": key or uuid.uuid4().hex}, **kwargs)

    def project(self):
        r = self.cmd("/projects", {"name": "Coffee"})
        assert r.status_code == 200, r.text
        return SimpleNamespace(**r.json()["project"])

    def ticket(self, project=None):
        p = project or self.project()
        r = self.cmd(f"/projects/{p.id}/tickets", SCOPE)
        assert r.status_code == 200, r.text
        return SimpleNamespace(**r.json()["ticket"])

    def job(self, p=None, **kwargs):
        p = p or self.project()
        return self.api.queue.enqueue(project_id=p.id, role="po", stage="chat", lane="interactive",
            limits={"model_calls": 3, "tool_calls": 3, "active_s": 60, "output_tokens": 500},
            idempotency_key=uuid.uuid4().hex, runtime="structured:fake", **kwargs)

    def claim(self, job):
        lease = self.api.queue.claim("worker:test-api", "interactive", capacity=3,
                                     runtimes=("structured:fake",))
        assert lease and lease.job_id == job.id
        return lease

    def runtime_client(self, lease):
        token = self.api.auth.issue_runtime(lease)
        return TestClient(self.app, base_url="http://127.0.0.1:8000",
                          headers={"Authorization": f"Bearer {token}"}, raise_server_exceptions=False)


@pytest.fixture
def api(db, store):
    env = ApiEnv(db, store)
    yield env
    env.client.__exit__(None, None, None)
