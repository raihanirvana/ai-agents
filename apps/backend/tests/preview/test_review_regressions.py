"""Review regressions: real persisted previews, Docker faults and a capturing unix upstream."""
import http.client
import socket
import subprocess
import threading

import pytest

from app.preview.proxy import UnixProxy
from app.preview.service import PreviewFailed
from tests.preview.helpers import free_port
from tests.preview.test_lifecycle import ask, row


@pytest.mark.parametrize("headers", [
    {"Host": "127.0.0.1:{port}"},
    {"Host": "localhost:{port}", "Cookie": "ai_team_session=sentinel"},
    {"Host": "localhost:{port}", "Authorization": "Bearer sentinel"},
    {"Host": "localhost:{port}", "X-CSRF-Token": "sentinel"},
    {"Host": "localhost:{port}", "Origin": "http://127.0.0.1:5173"},
])
def test_proxy_rejects_control_credentials_before_connecting_to_target(tmp_path, headers):
    port = free_port()
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(tmp_path / "capture.sock"))
    listener.listen()
    listener.settimeout(.2)
    proxy = UnixProxy(port, tmp_path / "capture.sock")
    proxy.start()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request("GET", "/", headers={k: v.format(port=port) for k, v in headers.items()})
        assert conn.getresponse().status == 403
        with pytest.raises(socket.timeout):
            listener.accept()
    finally:
        conn.close()
        proxy.stop()
        listener.close()


def test_proxy_does_not_forward_a_second_unguarded_request(tmp_path):
    port = free_port()
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(tmp_path / "capture.sock"))
    listener.listen()
    captured = []
    def target():
        upstream, _ = listener.accept()
        with upstream:
            captured.append(upstream.recv(65536))
            upstream.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
    worker = threading.Thread(target=target)
    worker.start()
    proxy = UnixProxy(port, tmp_path / "capture.sock")
    proxy.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
            client.sendall(f"GET / HTTP/1.1\r\nHost: localhost:{port}\r\n\r\n".encode() +
                          b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nCookie: sentinel\r\n\r\n")
            assert b"200 OK" in client.recv(4096)
        worker.join(timeout=3)
        assert len(captured) == 1 and b"sentinel" not in captured[0]
    finally:
        proxy.stop()
        listener.close()


def test_start_cleanup_failure_is_visible_and_retried(env, service, monkeypatch):
    svc = service()
    t, c, _, _ = env.to_uat(files={"app.js": "missing index"})
    pid = ask(env, t, c, free_port())
    with monkeypatch.context() as patch:
        patch.setattr(svc, "_teardown", lambda *a: (_ for _ in ()).throw(PreviewFailed("engine unavailable")))
        svc.run_once()
        assert row(env, pid).status == "stopping"
        assert "engine unavailable" in row(env, pid).error
    svc.run_once()
    assert row(env, pid).status == "stopped" and not (svc.base / pid).exists()


def test_switch_waits_until_previous_container_cleanup_is_proven(env, service, monkeypatch):
    svc, port = service(), free_port()
    a, ca, _, _ = env.to_uat()
    b, cb, _, _ = env.to_uat()
    first = ask(env, a, ca, port)
    svc.run_once()
    second = ask(env, b, cb, port)
    with monkeypatch.context() as patch:
        patch.setattr(svc, "_remove_container", lambda *a: (_ for _ in ()).throw(PreviewFailed("engine unavailable")))
        svc.run_once()
        assert row(env, first).status == "stopping"
        assert row(env, second).status == "requested"
    svc.run_once()
    assert row(env, first).status == "stopped" and row(env, second).status == "ready"


def test_recovery_cleanup_failure_moves_starting_to_retryable_state(env, service, monkeypatch):
    svc = service()
    t, c, _, _ = env.to_uat()
    pid = ask(env, t, c, free_port())
    from app.persistence.models import Preview
    with env.db.write() as s:
        s.get(Preview, pid).status = "starting"
    with monkeypatch.context() as patch:
        patch.setattr(svc, "_teardown", lambda *a: (_ for _ in ()).throw(PreviewFailed("engine unavailable")))
        svc.recover()
        assert row(env, pid).status == "stopping"
    svc.run_once()
    assert row(env, pid).status == "stopped"


def test_post_removal_inspect_transport_error_is_not_proof_of_absence(env, service, monkeypatch):
    svc = service()
    def docker(*args, **kwargs):
        if args[0] == "info":
            return subprocess.CompletedProcess(args, 0, b"28", b"")
        if "--format" in args:
            return subprocess.CompletedProcess(args, 0, b"preview-id", b"")
        return subprocess.CompletedProcess(args, 1, b"", b"connection reset by peer")
    monkeypatch.setattr(svc.sandbox, "_docker", docker)
    monkeypatch.setattr(svc.sandbox, "kill_and_remove", lambda *a: None)
    with pytest.raises(PreviewFailed, match="absence could not be verified"):
        svc._remove_container("preview-id", "container")
