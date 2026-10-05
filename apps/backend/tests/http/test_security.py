from datetime import timedelta
import pytest
from starlette.testclient import TestClient
from sqlalchemy import select
from app.http.security import Settings, local_login_code, token_hash
from app.persistence.columns import utcnow
from app.persistence.models import LocalSession, RuntimeCredential
from .conftest import CODE, ORIGIN, SECRET


@pytest.mark.parametrize("host", ["localhost:8000", "evil.test:8000", "127.0.0.1:8001", "127.0.0.1.evil:8000"])
@pytest.mark.parametrize("method", ["get", "head", "options"])
def test_host_on_every_method(api, host, method):
    r = getattr(api.client, method)("/health", headers={"Host": host})
    assert r.status_code == 403


@pytest.mark.parametrize("origin", ["http://localhost:5173", "null", "http://127.0.0.1:9999", "http://127.0.0.1:5173.evil"])
def test_preview_and_foreign_origins_rejected_even_with_valid_cookie(api, origin):
    r = api.cmd("/projects", {"name": "Bad"}, key="bad",)
    assert r.status_code == 200
    r = api.client.post("/projects", json={"name": "Attack"}, headers={"Origin": origin,
        "Idempotency-Key": "attack"})
    assert r.status_code == 403 and "access-control-allow-origin" not in r.headers
    assert len(api.client.get("/projects").json()["projects"]) == 1


def test_cookie_session_csrf_logout_and_restart(api):
    r = api.client.post("/auth/login", json={"code": CODE})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "domain=" not in cookie
    raw = api.client.cookies.get("ai_team_session")
    with api.db.read() as s:
        assert s.get(LocalSession, token_hash(raw)) is not None
        assert raw not in str(list(s.scalars(select(LocalSession))))
    with TestClient(api.app, base_url="http://127.0.0.1:8000") as restarted:
        restarted.cookies.set("ai_team_session", raw)
        assert restarted.get("/auth/session").status_code == 200
        assert restarted.post("/projects", json={"name": "x"}, headers={"Origin": ORIGIN}).status_code == 403
        assert restarted.post("/projects", json={"name": "x"}).status_code == 403
    api.client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert api.client.post("/auth/logout").status_code == 200
    api.client.cookies.set("ai_team_session", raw)
    assert api.client.get("/auth/session").status_code == 401


def test_authentication_and_expiry(api):
    with TestClient(api.app, base_url="http://127.0.0.1:8000") as c:
        assert c.get("/projects").status_code == 401
        assert c.post("/auth/login", json={"code": "wrong-code-1234567"}, headers={"Origin": ORIGIN}).status_code == 401
        assert c.post("/auth/login", json={"code": "é" * 20}, headers={"Origin": ORIGIN}).status_code == 401
    with api.db.write() as s:
        row = s.get(LocalSession, token_hash(api.client.cookies.get("ai_team_session")))
        row.expires_at = utcnow() - timedelta(seconds=1)
    assert api.client.get("/projects").status_code == 401


def test_cors_preflight_headers_and_duplicate_header_guard(api):
    r = api.client.options("/projects", headers={"Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "X-CSRF-Token,Idempotency-Key,Content-Type"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert r.headers["access-control-allow-credentials"] == "true"
    assert api.client.get("/health", headers=[("Host", "127.0.0.1:8000"), ("Host", "evil")]).status_code == 403
    assert api.client.get("/health", headers=[("Origin", ORIGIN), ("Origin", ORIGIN)]).status_code == 400


def test_runtime_is_distinct_scoped_and_fenced(api):
    j = api.job()
    lease = api.claim(j)
    with api.runtime_client(lease) as c:
        assert c.get("/projects").status_code == 401
        assert c.post("/projects", json={"name": "x"}, headers={"Origin": ORIGIN,
            "Idempotency-Key": "runtime-user"}).status_code == 401
        assert api.cmd("/runtime/tools", {"name": "read_brief"}).status_code == 401
        r = c.post("/runtime/tools", json={"name": "read_brief"}, headers={"Idempotency-Key": "read"})
        assert r.status_code == 200, r.text
        with api.db.read() as s:
            rows = list(s.scalars(select(RuntimeCredential)))
            assert rows[0].job_id == j.id and rows[0].generation == lease.generation
        api.api.queue.cancel(j.id, reason="test", actor="user:local")
        assert c.post("/runtime/tools", json={"name": "read_brief"}, headers={"Idempotency-Key": "read"}).status_code == 409


def test_validation_and_limits_do_not_echo_secrets(api, tmp_path):
    r = api.cmd("/projects", {"name": "x", SECRET: SECRET})
    assert r.status_code == 422 and SECRET not in r.text
    assert api.client.post("/projects", content=b"x" * (1024*1024+1)).status_code == 413
    assert api.client.get("/openapi.json").status_code == 200
    path = tmp_path / "auth" / "code"
    first = local_login_code(path)
    assert len(first) >= 32 and local_login_code(path) == first
    path.write_text("")
    with pytest.raises(ValueError): local_login_code(path)
    with pytest.raises(ValueError): Settings(origins=("http://localhost:5173",))
