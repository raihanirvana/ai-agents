"""Regressions for the DEV-008 review (docs/reviews/DEV-008-review.md). Real SQLite, fake provider, no network."""
from sqlalchemy import event


def run_state(api, run_id):
    return api.client.get(f"/runs/{run_id}").json()["run"]


# --- F1: the emergency stop must not depend on a revision that heartbeats advance -------------------------------------
def test_stop_is_not_blocked_by_a_heartbeat_that_advanced_the_run_revision(api):
    j = api.job()
    lease = api.claim(j)
    seen = run_state(api, j.id)["revision"]
    api.api.queue.heartbeat(lease, 0.5)  # the supervisor does this every few seconds
    assert run_state(api, j.id)["revision"] > seen
    r = api.cmd(f"/runs/{j.id}/stop", {})
    assert r.status_code == 200, r.text
    assert run_state(api, j.id)["status"] == "cancelled"
    assert r.json()["cleanup"] == "supervisor_pending"


def test_stop_retry_returns_the_original_receipt_and_a_stale_field_is_not_accepted(api):
    j = api.job()
    api.claim(j)
    first = api.cmd(f"/runs/{j.id}/stop", {}, key="stop-1")
    assert api.cmd(f"/runs/{j.id}/stop", {}, key="stop-1").json() == first.json()
    assert api.cmd(f"/runs/{j.id}/stop", {"expected_revision": 1}, key="stop-2").status_code == 422  # strict contract


# --- F2: stopping a run that is not active is not a success --------------------------------------------------------------
def test_stopping_a_finished_or_already_stopped_run_is_a_conflict(api):
    j = api.job()
    lease = api.claim(j)
    api.api.queue.complete(lease, {"done": True})
    r = api.cmd(f"/runs/{j.id}/stop", {})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict" and "succeeded" in r.json()["error"]["message"]
    k = api.job()
    api.claim(k)
    assert api.cmd(f"/runs/{k.id}/stop", {}, key="a").status_code == 200
    again = api.cmd(f"/runs/{k.id}/stop", {}, key="b")  # a different logical command on an already cancelled run
    assert again.status_code == 409 and "cancelled" in again.json()["error"]["message"]


# --- F3: runtime log lines are not conversation ----------------------------------------------------------------------------
def test_runtime_logs_stay_out_of_chat_and_ticket_detail_but_remain_on_the_logs_endpoint(api):
    p = api.project()
    t = api.ticket(p)
    j = api.job(p, ticket_id=t.id)
    lease = api.claim(j)
    for n in range(150):  # more lines than the chat window: they must not push real messages out of it
        api.api.queue.log_line(j.id, lease.generation, f"runtime line {n}")
    api.cmd(f"/projects/{p.id}/messages", {"expected_revision": p.revision, "body": "Please add oat milk",
                                           "task": "note"})
    chat = api.client.get(f"/projects/{p.id}/messages").json()["messages"]
    assert [m["body"] for m in chat] == ["Please add oat milk"]
    assert not any((m["metadata"] or {}).get("runtime_log") for m in chat)
    detail = api.client.get(f"/tickets/{t.id}").json()
    assert not any((m["metadata"] or {}).get("runtime_log") for m in detail["messages"])
    logs = api.client.get(f"/runs/{j.id}/logs?generation={lease.generation}").json()["messages"]
    assert len(logs) == 150  # still served, in full, by the dedicated endpoint


def test_input_requests_on_the_job_thread_are_still_conversation(api):
    p = api.project()
    j = api.job(p)
    lease = api.claim(j)
    api.api.queue.log_line(j.id, lease.generation, "noise")
    api.api.queue.request_input(lease, question="Which size?", checkpoint={}, request_key="q")
    chat = api.client.get(f"/projects/{p.id}/messages").json()["messages"]
    assert [m["kind"] for m in chat] == ["input_request"] and chat[0]["input"]["status"] == "open"


# --- F4: the board must not rescan every job for every job ---------------------------------------------------------------------
def statements(api, path):
    seen = []
    def count(conn, cursor, statement, *rest): seen.append(statement)
    event.listen(api.db.engine, "before_cursor_execute", count)
    try:
        assert api.client.get(path).status_code == 200
    finally:
        event.remove(api.db.engine, "before_cursor_execute", count)
    return len(seen)


def test_board_statement_count_does_not_grow_with_runs_of_one_scope(api):
    p = api.project()
    t = api.ticket(p)
    for _ in range(2):
        api.job(p, ticket_id=t.id)
    few = statements(api, f"/projects/{p.id}/tickets")
    for _ in range(10):
        api.job(p, ticket_id=t.id)
    assert statements(api, f"/projects/{p.id}/tickets") == few  # one budget key, loaded once


# --- F5: guard rejections are hardened like every other response -------------------------------------------------------------
def test_guard_rejections_are_not_cacheable(api):
    r = api.client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 403
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert api.client.get("/health", headers={"Host": "evil.test"}).headers["referrer-policy"] == "no-referrer"
