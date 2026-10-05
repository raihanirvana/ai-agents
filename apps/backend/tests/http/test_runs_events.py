import json
from dataclasses import replace
import pytest
from sqlalchemy import select
from app.persistence import EventSpec, append_event, append_message
from app.persistence.models import Job, Message


def frames(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_sse_replay_cursor_snapshot_and_no_duplicate_effects(api):
    p = api.project()
    base = api.client.get(f"/projects/{p.id}/tickets").json()["cursor"]
    r = api.cmd(f"/projects/{p.id}/messages", {"expected_revision": p.revision, "body": "Hello"}, "hello")
    assert r.status_code == 200
    j = api.api.queue.claim("worker:test", "interactive", capacity=1, runtimes=("structured:fake",))
    # A persisted final message, committed before the terminal job event (same worker contract).
    with api.db.write() as s:
        m, _ = append_message(s, project_id=p.id, thread_id=f"chat:{p.id}", sender="agent:po", body="Final reply")
    api.api.queue.complete(j, {"message_id": m.id})
    path = f"/projects/{p.id}/events?follow=false"
    events = frames(api.client.get(path, headers={"Last-Event-ID": str(base)}).text)
    assert any(e["type"] == "job.succeeded" for e in events)
    ids = [e["cursor"] for e in events]
    assert ids == sorted(set(ids))
    assert frames(api.client.get(path, headers={"Last-Event-ID": str(ids[-1])}).text) == []
    assert frames(api.client.get(path, headers={"Last-Event-ID": str(base)}).text) == events
    assert api.client.get(f"/projects/{p.id}/messages").json()["messages"][-1]["body"] == "Final reply"
    for cursor in ["-1", "abc", "1.0", "99999999999999999999"]:
        assert api.client.get(path, headers={"Last-Event-ID": cursor}).status_code == 422
    assert frames(api.client.get(path + "&cursor=0", headers={"Last-Event-ID": str(ids[-1])}).text) == []
    assert "snapshot_required" in api.client.get(path + "&cursor=999999").text
    other = api.project()
    assert all(e["project_id"] == other.id for e in frames(api.client.get(f"/projects/{other.id}/events?follow=false").text))


def test_replay_window_and_more_than_one_page(api):
    p = api.project()
    with api.db.write() as s:
        for i in range(205): append_event(s, p.id, EventSpec("test", "system:test", {"i": i}))
    path = f"/projects/{p.id}/events?follow=false"
    assert len(frames(api.client.get(path).text)) == 206
    api.api.settings = replace(api.api.settings, replay_events=3)
    r = api.client.get(path)
    assert "snapshot_required" in r.text and frames(r.text)[0]["reason"] == "cursor_expired"
    snapshot = api.client.get(f"/projects/{p.id}/tickets").json()
    assert frames(api.client.get(path + f"&cursor={snapshot['cursor']}").text) == []


@pytest.mark.parametrize("change", ["generation", "scope", "request", "revision"])
def test_input_identity_and_resume_only_once(api, change):
    j = api.job()
    lease = api.claim(j)
    rid = api.api.queue.request_input(lease, question="Remove at zero?", checkpoint={}, request_key="q")
    current = api.client.get(f"/runs/{j.id}").json()["run"]
    body = {"request_id": rid, "generation": lease.generation, "scope_version": None,
            "expected_revision": current["revision"], "answer": "Remove row"}
    wrong = dict(body)
    wrong[{"scope": "scope_version", "request": "request_id", "revision": "expected_revision"}.get(change, change)] = (
        "unknown" if change == "request" else 99)
    assert api.cmd(f"/runs/{j.id}/input", wrong).status_code in (404, 409)
    first = api.cmd(f"/runs/{j.id}/input", body, "answer")
    assert first.status_code == 200 and first.json()["resumed"] is True, first.text
    assert api.cmd(f"/runs/{j.id}/input", body, "answer").json() == first.json()
    assert api.cmd(f"/runs/{j.id}/input", body, "second-answer").status_code == 409
    with api.db.read() as s:
        assert len(list(s.scalars(select(Message).where(Message.kind == "input_answer")))) == 1
    new = api.claim(j)
    assert new.generation > lease.generation


def test_cancelled_input_cannot_resume(api):
    j = api.job()
    lease = api.claim(j)
    rid = api.api.queue.request_input(lease, question="Question?", checkpoint={}, request_key="q")
    run = api.client.get(f"/runs/{j.id}").json()["run"]
    r = api.cmd(f"/runs/{j.id}/stop", {})
    assert r.status_code == 200 and r.json()["run"]["status"] == "cancelled"
    r = api.cmd(f"/runs/{j.id}/input", {"expected_revision": run["revision"], "request_id": rid,
        "scope_version": None, "generation": lease.generation, "answer": "Too late"})
    assert r.status_code == 409


def test_runtime_denials_charge_once_and_cap_is_committed(api):
    j = api.job()
    lease = api.claim(j)
    with api.runtime_client(lease) as c:
        body = {"name": "approve_scope", "args": {}}
        for i in range(3):
            r = c.post("/runtime/tools", json=body, headers={"Idempotency-Key": f"bad-{i}"})
            assert r.status_code == 403, r.text
            assert c.post("/runtime/tools", json=body, headers={"Idempotency-Key": f"bad-{i}"}).json() == r.json()
        r = c.post("/runtime/tools", json=body, headers={"Idempotency-Key": "over-cap"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "budget_exhausted", r.text
    run = api.client.get(f"/runs/{j.id}").json()["run"]
    assert run["status"] == "stopped" and run["usage"]["tool_calls"] == 3
    r = api.cmd(f"/runs/{j.id}/budget-authorizations", {"expected_revision": run["revision"], "additions": {"tool_calls": 2}}, "more")
    assert r.status_code == 200, r.text
    assert r.json()["run"]["usage"]["tool_calls"] == 3
    assert api.cmd(f"/runs/{j.id}/budget-authorizations", {"expected_revision": run["revision"], "additions": {"tool_calls": 2}}, "more").json() == r.json()


def test_tool_input_and_identity_spoofing(api):
    j = api.job()
    lease = api.claim(j)
    with api.runtime_client(lease) as c:
        r = c.post("/runtime/tools", json={"name": "read_brief", "args": {"role": "user"}}, headers={"Idempotency-Key": "spoof"})
        assert r.status_code == 422
        r = c.post("/runtime/tools", json={"name": "request_input", "args": {"question": "Question?"}}, headers={"Idempotency-Key": "ask"})
        assert r.status_code == 200 and r.json()["waiting_input"] is True, r.text
    run = api.client.get(f"/runs/{j.id}").json()
    assert run["run"]["status"] == "waiting_input" and run["input"]["body"] == "Question?"


def test_tool_exception_rolls_back_effects_but_keeps_admission(api, monkeypatch):
    from app.agents.tools import ToolFacade
    j = api.job()
    lease = api.claim(j)
    def fail(self, ctx, identity, args):
        with self.db.write() as s:
            append_message(s, project_id=j.project_id, thread_id="partial", sender="agent:po", body="must rollback")
        raise KeyError("missing argument")
    monkeypatch.setattr(ToolFacade, "_read_brief", fail)
    with api.runtime_client(lease) as c:
        r = c.post("/runtime/tools", json={"name": "read_brief"}, headers={"Idempotency-Key": "failed-tool"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "tool_error", r.text
        assert c.post("/runtime/tools", json={"name": "read_brief"}, headers={"Idempotency-Key": "failed-tool"}).json() == r.json()
    with api.db.read() as s:
        assert not list(s.scalars(select(Message).where(Message.thread_id == "partial")))
        assert s.get(Job, j.id).usage["tool_calls"] == 1


def test_nonblocking_user_escalation_is_visible_and_answerable(api):
    j = api.job()
    with api.db.write() as s:
        m, _ = append_message(s, project_id=j.project_id, thread_id="escalation", sender="agent:technical-lead",
            recipient="user", kind="input_request", body="Choose", meta={"intent": "user_escalation",
            "source_request_id": None, "scope_version": None, "generation": 1, "job_id": j.id})
    body = {"scope_version": None, "generation": 1, "answer": "A"}
    first = api.cmd(f"/projects/{j.project_id}/inputs/{m.id}", body, "nonblocking")
    assert first.status_code == 200 and first.json()["resumed"] is False
    assert api.cmd(f"/projects/{j.project_id}/inputs/{m.id}", body, "nonblocking").json() == first.json()
    assert api.cmd(f"/projects/{j.project_id}/inputs/{m.id}", body, "other").status_code == 409
