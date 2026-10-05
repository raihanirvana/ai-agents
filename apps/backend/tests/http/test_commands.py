from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select, func
from app.persistence.models import ApiCommand, Ticket, Approval, Job, Message
from app.workers import JobQueue
from .conftest import SCOPE, SECRET


def count(api, model):
    with api.db.read() as s: return s.scalar(select(func.count()).select_from(model))


def test_idempotency_expected_revision_and_atomic_batch(api):
    p = api.project()
    t = api.ticket(p)
    path = f"/tickets/{t.id}/priority"
    body = {"expected_revision": t.revision, "priority": 5}
    first = api.cmd(path, body, "priority")
    assert first.status_code == 200, first.text
    assert api.cmd(path, body, "priority").json() == first.json()
    assert api.cmd(path, {**body, "priority": 6}, "priority").status_code == 409
    assert api.cmd(path, body, "stale").json()["error"]["code"] == "revision_conflict"
    assert api.cmd(path, {**body, "expected_revision": True}).status_code == 422
    t = first.json()["ticket"]
    t2 = api.ticket(p)
    r = api.cmd(f"/projects/{p.id}/scope-approvals", {"items": [
        {"ticket_id": t["id"], "scope_version": 1, "expected_revision": t["revision"]},
        {"ticket_id": t2.id, "scope_version": 1, "expected_revision": 100}]}, "batch")
    assert r.status_code == 409 and count(api, Approval) == 0
    assert api.client.get(f"/tickets/{t['id']}").json()["ticket"]["phase"] == "scope_review"
    r = api.cmd(f"/projects/{p.id}/scope-approvals", {"items": [
        {"ticket_id": t["id"], "scope_version": 1, "expected_revision": t["revision"]},
        {"ticket_id": t2.id, "scope_version": 1, "expected_revision": t2.revision}]}, "batch")
    assert r.status_code == 200, r.text
    assert count(api, Approval) == 2


def test_concurrent_retry_and_competing_revisions(api):
    p = api.project()
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: api.cmd(f"/projects/{p.id}/tickets", SCOPE, "same-ticket"), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json() and count(api, Ticket) == 1
    t = responses[0].json()["ticket"]
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda i: api.cmd(f"/tickets/{t['id']}/priority",
            {"expected_revision": t["revision"], "priority": i}, f"writer-{i}"), [1, 2]))
    assert sorted(r.status_code for r in responses) == [200, 409]


def test_chat_is_atomic_labelled_redacted_and_retryable(api, monkeypatch):
    p = api.project()
    path, body = f"/projects/{p.id}/messages", {"expected_revision": p.revision, "body": f"Build {SECRET}"}
    original = JobQueue.enqueue
    monkeypatch.setattr(JobQueue, "enqueue", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError(SECRET)))
    r = api.cmd(path, body, "chat")
    assert r.status_code == 500 and SECRET not in r.text
    assert count(api, Message) == 0 and count(api, Job) == 0
    monkeypatch.setattr(JobQueue, "enqueue", original)
    first = api.cmd(path, body, "chat")
    assert first.status_code == 200, first.text
    assert SECRET not in first.text
    assert api.cmd(path, body, "chat").json() == first.json()
    assert count(api, Message) == 1 and count(api, Job) == 1
    j = api.client.get(f"/runs/{first.json()['job_id']}").json()["run"]
    assert j["runtime"] == "structured:fake" and j["fake"] is True
    with api.db.read() as s:
        assert SECRET not in str([c.response for c in s.scalars(select(ApiCommand))])
    assert api.client.get(f"/projects/{p.id}/messages").json()["messages"][0]["body"] == first.json()["message"]["body"]


def test_no_arbitrary_identity_or_status_commands(api):
    t = api.ticket()
    assert api.cmd(f"/tickets/{t.id}/status", {"phase": "accepted"}).status_code == 404
    assert api.cmd(f"/tickets/{t.id}/priority", {"expected_revision": t.revision,
        "priority": 1, "role": "user"}).status_code == 422
    assert api.client.post("/projects", json={"name": "x"}).json()["error"]["code"] == "idempotency_required"
