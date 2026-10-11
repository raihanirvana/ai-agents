from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select, func
from app.persistence.models import ApiCommand, Ticket, TicketVersion, Approval, Job, Message
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
    initial_jobs = count(api, Job)  # project creation now enqueues runner setup
    original = JobQueue.enqueue
    monkeypatch.setattr(JobQueue, "enqueue", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError(SECRET)))
    r = api.cmd(path, body, "chat")
    assert r.status_code == 500 and SECRET not in r.text
    assert count(api, Message) == 0 and count(api, Job) == initial_jobs
    monkeypatch.setattr(JobQueue, "enqueue", original)
    first = api.cmd(path, body, "chat")
    assert first.status_code == 200, first.text
    assert SECRET not in first.text
    assert api.cmd(path, body, "chat").json() == first.json()
    assert count(api, Message) == 1 and count(api, Job) == initial_jobs + 1
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


@pytest.mark.parametrize('marker', [False, True])
def test_ui_change_survives_scope_edit_and_optional_field_replay(api, marker):
    project = api.project()
    path = f'/projects/{project.id}/tickets'
    body = {**SCOPE, 'ui_change': marker}
    first = api.cmd(path, body, 'marked-scope')
    assert first.status_code == 200, first.text
    assert api.cmd(path, body, 'marked-scope').json() == first.json()
    ticket = first.json()['ticket']
    edit_path = f"/tickets/{ticket['id']}/scope-versions"
    edit = {'expected_revision': ticket['revision'], 'document': {**SCOPE, 'title': 'Coffee v2'}}
    revised = api.cmd(edit_path, edit, 'marked-edit')
    assert revised.status_code == 200, revised.text
    # Null and omission normalize to the same historical command payload.
    assert api.cmd(edit_path, {**edit, 'document': {**edit['document'], 'ui_change': None}},
                   'marked-edit').json() == revised.json()
    with api.db.read() as s:
        versions = list(s.scalars(select(TicketVersion).where(TicketVersion.ticket_id == ticket['id'])))
        assert len(versions) == 2 and all(version.scope['ui_change'] is marker for version in versions)


@pytest.mark.parametrize('value', ['false', 0])
def test_http_scope_rejects_non_boolean_ui_change(api, value):
    project = api.project()
    result = api.cmd(f'/projects/{project.id}/tickets', {**SCOPE, 'ui_change': value})
    assert result.status_code == 422 and count(api, Ticket) == 0


def test_unmarked_scope_creation_replays_with_null_marker(api):
    project = api.project()
    path = f'/projects/{project.id}/tickets'
    first = api.cmd(path, SCOPE, 'legacy-scope')
    assert first.status_code == 200, first.text
    assert api.cmd(path, {**SCOPE, 'ui_change': None}, 'legacy-scope').json() == first.json()
    assert count(api, Ticket) == 1
