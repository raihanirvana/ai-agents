"""Actual Docker preview lifecycle: build bundle -> container without network -> loopback proxy. QA fixtures are contract
fixtures (see helpers.py); the containers, sockets, proxy, ports and cleanup are real."""
import http.client
import json
import socket
import subprocess

import pytest
from sqlalchemy import select

from app.domain.types import Conflict
from app.persistence.models import Artifact, Candidate, Job, Preview
from app.preview import requests as pr
from tests.preview.helpers import INDEX, NODE_IMAGE, SITE, free_port


def ask(env, t, c, port):
    with env.db.write() as s:
        return pr.request_preview(s, env.store, ticket_id=t.id, candidate_id=c.id, user_id="user:local", port=port).id


def row(env, preview_id) -> Preview:
    with env.db.read() as s:
        return s.get(Preview, preview_id)


def get(port, path="/", host="localhost", method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, headers={"Host": f"{host}:{port}"})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def containers(svc):
    return svc.sandbox._docker("ps", "-a", "--filter", "label=aiagent.container-role=preview", "--format", "{{.Names}}",
                               "--filter", f"label=aiagent.supervisor={svc.owner}").stdout.decode().split()


def inspect(svc, name, fmt):
    return json.loads(svc.sandbox._docker("inspect", "--format", fmt, name).stdout)


def refuses(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    except OSError:
        return True
    return False


def test_preview_serves_the_tested_build_from_a_container_without_any_network_and_stop_removes_everything(env, service):
    svc, port = service(), free_port()
    t, c, target, bundle = env.to_uat()
    with env.db.read() as s:
        jobs_before = len(list(s.scalars(select(Job))))
    pid = ask(env, t, c, port)
    svc.run_once()
    p = row(env, pid)
    assert p.status == "ready" and p.port == port and p.owner == svc.owner and p.error is None, p.error
    assert pr.public(p)["url"] == f"http://localhost:{port}/"

    status, headers, body = get(port)
    assert (status, body) == (200, INDEX.encode()) and headers["Cache-Control"] == "no-store"
    assert get(port, "/assets/app.js")[2] == SITE["assets/app.js"].encode()
    assert get(port, "/%2e%2e/%2e%2e/etc/passwd")[0] == 404 and get(port, "/missing")[0] == 404
    assert get(port, "/", method="POST")[0] == 403 and get(port, "/", method="HEAD")[0] == 200

    name = p.container_name
    assert containers(svc) == [name]
    assert inspect(svc, name, "{{json .HostConfig.NetworkMode}}") == "none"
    assert inspect(svc, name, "{{json .HostConfig.PortBindings}}") in ({}, None)
    assert inspect(svc, name, "{{json .HostConfig.ReadonlyRootfs}}") is True
    assert inspect(svc, name, "{{json .HostConfig.CapDrop}}") == ["ALL"]
    mounts = {m["Destination"]: m["RW"] for m in inspect(svc, name, "{{json .Mounts}}")}
    assert mounts == {"/site": False, "/run/preview": True, "/server.cjs": False}
    env_vars = inspect(svc, name, "{{json .Config.Env}}")
    assert not [v for v in env_vars if any(k in v.upper() for k in ("KEY", "SECRET", "TOKEN", "PASSWORD", "DATABASE"))]
    # The target can reach nothing: no interface but loopback, and the host gateway/control plane are unreachable.
    interfaces = svc.sandbox._docker("exec", name, "sh", "-c", "cut -d: -f1 /proc/net/dev | tail -n +3 | tr -d ' '").stdout.decode().split()
    assert interfaces == ["lo"]
    for address in ("172.17.0.1:8000", "host.docker.internal:8000", "127.0.0.1:8000"):
        reach = svc.sandbox._docker("exec", name, "wget", "-T", "2", "-q", "-O-", f"http://{address}/health", check=False)
        assert reach.returncode != 0
    with env.db.read() as s:
        assert len(list(s.scalars(select(Job)))) == jobs_before  # ownership is separate from job execution

    with env.db.write() as s:
        pr.request_stop(s, pid, "user:local")
    svc.run_once()
    stopped = row(env, pid)
    assert stopped.status == "stopped" and stopped.stop_reason == "user_stop" and stopped.stopped_at
    assert containers(svc) == [] and refuses(port) and not (svc.base / pid).exists()
    with env.db.read() as s:  # the tested artifacts are untouched by stop/cleanup
        assert all(s.get(Artifact, a).availability == "available" for a in (bundle.id, target.id))


def test_switching_stops_the_previous_preview_before_the_next_starts_and_reopen_serves_the_same_artifact(env, service):
    svc, port = service(), free_port()
    a, ca, ta, _ = env.to_uat()
    b, cb, tb, _ = env.to_uat(files={"index.html": "<!doctype html><title>Second</title>second"})
    first = ask(env, a, ca, port)
    svc.run_once()
    old_container = row(env, first).container_name
    second = ask(env, b, cb, port)
    assert row(env, first).status == "stopping" and row(env, first).stop_reason == "switched"
    svc.run_once()  # the old preview is stopped first, then the new one takes the same port
    assert row(env, first).status == "stopped" and old_container not in containers(svc)
    assert row(env, second).status == "ready" and get(port)[2].startswith(b"<!doctype html><title>Second")
    ready = row(env, second)
    with env.db.write() as s:
        pr.request_stop(s, second, "user:local")
    svc.run_once()
    assert refuses(port) and containers(svc) == []
    # Reopen: a new row for the same pinned target, same configuration, same bytes.
    again = ask(env, b, cb, port)
    svc.run_once()
    reopened = row(env, again)
    assert again != second and reopened.status == "ready" and reopened.target_digest == tb.checksum
    assert reopened.details == ready.details and get(port)[2].startswith(b"<!doctype html><title>Second")
    # Switching while the first is already running: the supervisor stops it first, then starts the other on its port.
    with env.db.write() as s:
        pr.request_stop(s, again, "user:local")
    svc.run_once()
    third = ask(env, a, ca, port)
    svc.run_once()
    fourth = ask(env, b, cb, port)
    assert row(env, third).status == "stopping"
    svc.run_once()
    assert row(env, third).status == "stopped" and row(env, fourth).status == "ready", (row(env, third).error, row(env, fourth).error)
    assert len(containers(svc)) == 1 and old_container not in containers(svc)


def test_a_start_that_cannot_complete_fails_visibly_and_leaves_no_container_socket_or_site_copy(env, service):
    svc = service()
    t, c, _, _ = env.to_uat(files={"app.js": "no index"})
    pid = ask(env, t, c, free_port())
    svc.run_once()
    failed = row(env, pid)
    assert failed.status == "failed" and "index.html" in failed.error and failed.stopped_at
    assert containers(svc) == [] and not (svc.base / pid).exists()

    t2, c2, _, _ = env.to_uat()
    with socket.socket() as taken:  # the preview port is already in use
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        pid2 = ask(env, t2, c2, taken.getsockname()[1])
        svc.run_once()
    busy = row(env, pid2)
    assert busy.status == "failed" and "cannot listen" in busy.error
    assert containers(svc) == [] and not (svc.base / pid2).exists()


def test_an_artifact_that_disappears_after_the_request_fails_the_start_as_unavailable(env, service):
    svc = service()
    t, c, _, bundle = env.to_uat()
    pid = ask(env, t, c, free_port())
    with env.db.read() as s:
        path = env.store.resolve(s.get(Artifact, bundle.id).path)
    path.chmod(0o600)
    path.unlink()
    svc.run_once()
    failed = row(env, pid)
    assert failed.status == "failed" and failed.error.startswith("artifact unavailable") and containers(svc) == []


def test_bytes_that_no_longer_match_the_pinned_build_digest_are_never_served(env, service):
    svc = service()
    t, c, _, _ = env.to_uat(bundle_files={"index.html": "<!doctype html>tampered"})  # stored bytes differ from the pinned digest
    pid = ask(env, t, c, free_port())
    svc.run_once()
    assert row(env, pid).status == "failed" and "do not match" in row(env, pid).error and containers(svc) == []


def test_feedback_that_supersedes_the_candidate_closes_its_preview_and_a_rebuild_of_the_same_sha_is_a_new_target(env, service):
    svc, port = service(), free_port()
    t, c, old_target, _ = env.to_uat()
    old = ask(env, t, c, port)
    svc.run_once()
    assert row(env, old).status == "ready"
    w = env.world
    w.w.request_changes(w.user, t.id, w.ticket(t.id).revision, c.id, "Harga salah")
    svc._last_liveness = 0
    svc.run_once()
    closed = row(env, old)
    assert closed.status == "stopped" and closed.stop_reason == "superseded" and refuses(port) and containers(svc) == []
    with env.db.write() as s, pytest.raises(Conflict):
        pr.request_preview(s, env.store, ticket_id=t.id, candidate_id=c.id, user_id="user:local", port=port)

    # Same commit, different effective configuration: a new candidate, target digest and verification, so a new UAT.
    t2, c2, new_target, _ = env.to_uat(w.ticket(t.id), config_digest="e" * 64)
    assert c2.commit_sha == c.commit_sha and c2.id != c.id
    assert new_target.checksum != old_target.checksum
    fresh = ask(env, t2, c2, port)
    svc.run_once()
    reopened = row(env, fresh)
    assert reopened.status == "ready" and reopened.details["config_digest"] == "e" * 64
    assert reopened.details["config_digest"] != row(env, old).details["config_digest"]
    with env.db.read() as s:
        assert s.get(Candidate, c.id).status == "superseded"


def test_uat_acceptance_does_not_depend_on_the_preview_process(env, service):
    svc, port = service(), free_port()
    t, c, target, _ = env.to_uat()
    pid = ask(env, t, c, port)
    svc.run_once()
    with env.db.write() as s:
        pr.request_stop(s, pid, "user:local")
    svc.run_once()
    assert containers(svc) == []
    w = env.world
    with env.db.read() as s:
        cand = s.get(Candidate, c.id)
        evidence_ids, verification_id = list(cand.evidence_artifact_ids), cand.preview["verification_id"]
    ticket = w.ticket(t.id)
    w.w.accept_uat(w.user, t.id, ticket.revision, c.id, ticket.current_version, target.id, target.checksum,
                   verification_id, evidence_ids)
    assert w.ticket(t.id).phase == "integrating"


def test_a_restarted_worker_does_not_trust_previews_whose_proxy_died_with_the_old_process(env, service):
    first, port = service(), free_port()
    t, c, _, _ = env.to_uat()
    pid = ask(env, t, c, port)
    first.run_once()
    assert row(env, pid).status == "ready"
    first._proxies.pop(pid).stop()  # the process that owned the proxy is gone; the container is left behind
    second = service()
    second.run_once()
    gone = row(env, pid)
    assert gone.status == "stopped" and gone.stop_reason == "worker_restart"
    assert containers(first) == [] and refuses(port)


def test_cleanup_refuses_a_container_that_is_not_this_previews_and_keeps_the_row_visible(env, service):
    svc = service()
    t, c, _, _ = env.to_uat()
    pid = ask(env, t, c, free_port())
    svc.run_once()
    name = row(env, pid).container_name
    svc.sandbox._docker("rename", name, name + "-real")
    svc.sandbox._docker("run", "-d", "--name", name, "--network", "none", "--label", "aiagent.preview=someone-else",
                        "--label", "aiagent.container-role=decoy", NODE_IMAGE, "sleep", "60")
    try:
        svc._stop(pid, force=True)
        kept = row(env, pid)
        assert kept.status == "stopping" and "ownership mismatch" in kept.error
        assert svc.sandbox._docker("inspect", "--format", "{{.State.Running}}", name, check=False).returncode == 0
    finally:
        svc.sandbox._docker("rm", "-f", name, check=False)
        svc.sandbox._docker("rename", name + "-real", name, check=False)
        svc._proxies.pop(pid).stop() if pid in svc._proxies else None
        svc.sandbox._docker("rm", "-f", name, check=False)


def test_an_independent_ticket_keeps_being_worked_while_a_preview_is_running_in_uat(env, service):
    from app.persistence.models import Project
    from app.pipeline.scheduler import PipelineScheduler
    from app.workers import JobQueue
    svc, port = service(), free_port()
    t, c, _, _ = env.to_uat()
    pid = ask(env, t, c, port)
    svc.run_once()
    assert row(env, pid).status == "ready"
    w = env.world
    with env.db.write() as s:
        project = s.get(Project, w.project.id)
        project.workflow = {**project.workflow, "pipeline": {"manifest": {}}}
        for job in s.scalars(select(Job)):  # the fixture's finished attempts for the UAT ticket are still marked running
            job.status = "succeeded"
    independent = w.approve(w.new())
    queue = JobQueue(env.db, startable=w.w.startable)
    dispatched = PipelineScheduler(env.db, queue, w.w).tick()
    with env.db.read() as s:
        jobs = {j.ticket_id: j for j in s.scalars(select(Job).where(Job.id.in_(dispatched)))}
    assert set(jobs) == {independent.id} and jobs[independent.id].stage == "technical_plan"
    lease = queue.claim("worker:test", "execution", capacity=1, runtimes=("pipeline",))
    assert lease is not None and lease.job_id == jobs[independent.id].id  # the single execution slot is free
    assert row(env, pid).status == "ready" and get(port)[0] == 200  # and the preview kept running meanwhile
