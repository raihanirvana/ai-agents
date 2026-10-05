import time
from app.agents import (ContextBuilder, FakeProvider, ModelClient, ModelRegistry, StructuredAgentRuntime,
                        load_agents)
from app.domain import Workflow
from app.workers import Supervisor, WorkerConfig
from tests.agents.conftest import proposal, ticket_spec, CONFIG


def test_chat_runs_through_fake_worker_final_message_survives_api_reconnect(api):
    p = api.project()
    result = api.cmd(f"/projects/{p.id}/messages", {"expected_revision": p.revision, "body": "Build menu"}, "worker-chat")
    jid = result.json()["job_id"]
    provider = FakeProvider([proposal(ticket_spec("menu"))])
    client = ModelClient(ModelRegistry.from_dict(CONFIG), {"fake": provider}, api.api.redactor)
    wf = Workflow(api.db, api.store)
    runtime = StructuredAgentRuntime(db=api.db, workflow=wf, threads=api.api.threads,
        builder=ContextBuilder(api.db, api.store, load_agents(), api.api.redactor),
        client=client, redactor=api.api.redactor, fake=True)
    supervisor = Supervisor(api.db, api.store, {"structured:fake": runtime}, queue=api.api.queue,
        workflow=wf, limiter=api.api.limiter, config=WorkerConfig(worker_id="worker:api", heartbeat_s=.05))
    try:
        end = time.monotonic() + 10
        while time.monotonic() < end:
            supervisor.tick()
            run = api.client.get(f"/runs/{jid}").json()["run"]
            if run["status"] in ("failed", "succeeded"): break
            time.sleep(.02)
        assert run["status"] == "succeeded", run
        supervisor.wait_idle(5)
        log_response = api.client.get(f"/runs/{jid}/logs?generation={run['generation']}")
        assert log_response.status_code == 200
        logs = log_response.json()["messages"]
        assert logs and all(m["metadata"]["runtime_log"] for m in logs)
        assert [m["seq"] for m in logs] == sorted(m["seq"] for m in logs)
        assert api.client.get(f"/runs/{jid}/logs?generation={run['generation']}&after_seq={logs[-1]['seq']}").json()["messages"] == []
        assert api.client.get(f"/runs/{jid}/logs?generation={run['generation'] + 1}").status_code == 422
        board = api.client.get(f"/projects/{p.id}/tickets").json()
        assert len(board["tickets"]) == 1 and board["tickets"][0]["phase"] == "scope_review"
        messages = api.client.get(f"/projects/{p.id}/messages").json()["messages"]
        assert any(m["sender"] == "agent:po" for m in messages)
        assert api.cmd(f"/projects/{p.id}/messages", {"expected_revision": p.revision, "body": "Build menu"}, "worker-chat").json() == result.json()
        assert api.client.get(f"/projects/{p.id}/messages").json()["messages"] == messages
        assert "job.succeeded" in api.client.get(f"/projects/{p.id}/events?follow=false").text
    finally: supervisor.shutdown(timeout_s=5)
