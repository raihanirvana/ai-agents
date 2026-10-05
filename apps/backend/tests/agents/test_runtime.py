"""Structured PO/lead runtime end to end: supervisor + domain + context + the LABELLED fake provider."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.agents import (FakeProvider, ModelClient, ModelRegistry, ModelTimeout, ProviderQuota, StructuredAgentRuntime,
                        Usage)
from app.agents.models import ProviderRejected
from app.persistence.models import Approval, Artifact, Job, Message, Ticket
from app.persistence.pins import pinned_artifacts
from tests.domain.conftest import SCOPE

from .conftest import CONFIG, LIMITS, SECRET, proposal, ticket_spec

THREE = proposal(ticket_spec("T1", "Menu"), ticket_spec("T2", "Cart", deps=["T1"]), ticket_spec("T3", "Checkout", deps=["T2"]),
                 summary="Coffee shop in three steps", assumptions=["Prices are in cents"])
CLARIFY = {"kind": "clarification", "questions": [{"id": "Q1", "question": "Should a cart row disappear at zero?"}]}
PLAN = {"kind": "technical_plan", "summary": "Add cart state", "steps": [{"title": "Reducer", "files": ["src/cart.js"]}],
        "decisions": [{"title": "Use cents", "rationale": "No float money"}], "risks": ["state shape"], "needs_user": False}


def tickets(env):
    with env.db.read() as s:
        return list(s.scalars(select(Ticket).where(Ticket.project_id == env.project.id).order_by(Ticket.number)))


def po_breakdown(env, **kw):
    return env.job("po", "breakdown", payload={"request": "Build a coffee shop menu and cart"}, **kw)


# --- PO ----------------------------------------------------------------------------------------------------------------
def test_po_breakdown_creates_validated_unapproved_tickets_with_real_dependencies(agent_env):
    env = agent_env
    env.script(THREE)
    sup = env.supervisor()
    job = po_breakdown(env)
    done = env.finish(sup, job.id)
    created = done.result["created_ticket_ids"]
    made = tickets(env)
    assert [t.id for t in made] == created and all(t.phase == "scope_review" and t.current_version == 1 for t in made)
    with env.db.read() as s:
        assert s.scalar(select(func.count()).select_from(Approval)) == 0  # PO proposals are never approvals
        from app.persistence.models import Dependency
        deps = {(d.ticket_id, d.depends_on_ticket_id) for d in s.scalars(select(Dependency))}
    key_map = done.result["key_map"]
    assert deps == {(key_map["T2"], key_map["T1"]), (key_map["T3"], key_map["T2"])}
    assert done.result["approved"] is False and done.result["fake_provider"] is True and done.result["model_calls"] == 1
    [note] = env.messages(intent="po_breakdown")
    assert note.recipient == "user" and "none is approved" in note.body and note.meta["fake"] is True
    assert done.result["context_artifact_id"] in note.attachment_ids
    with env.db.read() as s:
        assert done.result["context_artifact_id"] in pinned_artifacts(s)  # the evidence of what the PO saw is kept
    assert all(e.payload["fake"] for e in env.events("job."))


def test_the_model_sees_soul_brief_and_task_and_usage_is_recorded(agent_env):
    env = agent_env
    with env.db.write() as s:
        from app.persistence.models import Project
        s.get(Project, env.project.id).brief = "A tiny coffee shop"
    env.script((THREE, Usage(prompt_tokens=900, completion_tokens=120, total_tokens=1020, cost_usd=0.002)))
    done = env.finish(env.supervisor(), po_breakdown(env).id)
    request = env.provider.requests[0]
    assert request.system.startswith("# Product Owner") and "A tiny coffee shop" in request.user
    assert '"name": "breakdown"' in request.user and "Build a coffee shop menu and cart" in request.user
    assert done.result["usage"] == {"prompt_tokens": 900, "output_tokens": 120, "total_tokens": 1020,
                                    "cached_tokens": None, "cost_usd": 0.002}
    assert done.usage["prompt_tokens"] == 900 and done.usage["model_calls"] == 1


def test_invalid_output_gets_one_repair_call_that_shows_the_errors(agent_env):
    env = agent_env
    env.script({"kind": "proposal", "summary": "s", "tickets": []}, THREE)
    done = env.finish(env.supervisor(), po_breakdown(env).id)
    assert done.result["model_calls"] == 2 and done.usage["model_calls"] == 2
    repair = env.provider.requests[1].user
    assert "Your previous answer was rejected" in repair and "tickets" in repair
    assert len(tickets(env)) == 3


def test_output_that_stays_invalid_fails_visibly_and_creates_nothing(agent_env):
    env = agent_env
    env.script("I would build a menu", {"kind": "proposal", "summary": "s", "tickets": [ticket_spec("T1", deps=["T1"])]})
    done = env.finish(env.supervisor(), po_breakdown(env).id, "failed")
    assert "invalid structured output after one repair attempt" in done.result["error"]
    assert done.result["needs_human"] is True and done.usage["model_calls"] == 2  # both calls were counted
    assert tickets(env) == [] and done.result.get("retry_job_id") is None


def test_a_dependency_cycle_in_the_answer_is_rejected_before_anything_is_created(agent_env):
    env = agent_env
    cyclic = proposal(ticket_spec("A", deps=["B"]), ticket_spec("B", deps=["A"]))
    env.script(cyclic, cyclic)
    done = env.finish(env.supervisor(), po_breakdown(env).id, "failed")
    assert "cycle" in done.result["error"] and tickets(env) == []


def test_dependency_on_a_missing_ticket_is_refused_before_any_ticket_is_created(agent_env):
    env = agent_env
    env.script(proposal({**ticket_spec("T1"), "depends_on_ticket_ids": ["no-such-ticket"]}, ticket_spec("T2")))
    done = env.finish(env.supervisor(), po_breakdown(env).id, "failed")
    assert "nothing was created" in done.result["error"] and tickets(env) == []


def test_a_retried_run_never_duplicates_tickets(agent_env, monkeypatch):
    env = agent_env
    env.script(THREE, THREE)
    original = env.world.w.create_ticket
    calls = {"n": 0}

    def crash_on_third(actor, document, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("worker lost power after two tickets")
        return original(actor, document, **kw)

    monkeypatch.setattr(env.world.w, "create_ticket", crash_on_third)
    env.queue.retry_backoff_s = 3600  # hold the retry so the state in between can be inspected
    sup = env.supervisor()
    job = po_breakdown(env)
    first = env.finish(sup, job.id, "failed")
    assert len(tickets(env)) == 2 and first.result["retry_job_id"]  # the crash left a half-finished breakdown
    with env.db.write() as s:
        s.get(Job, first.result["retry_job_id"]).available_at = None  # release the retry now
    retry = env.finish(sup, first.result["retry_job_id"])
    assert retry.attempt == 2 and len(tickets(env)) == 3  # the two existing tickets were reused, one was added
    assert retry.result["created_ticket_ids"] == [t.id for t in tickets(env)]
    assert len(env.messages(intent="po_breakdown")) == 1


def test_clarification_waits_for_the_user_then_resumes_with_the_answer(agent_env):
    env = agent_env
    env.script(CLARIFY, THREE)
    sup = env.supervisor()
    job = po_breakdown(env)
    env.run_until(sup, lambda: env.get(job.id).status == "waiting_input")
    waiting = env.get(job.id)
    [request] = env.messages(kind="input_request")
    assert request.recipient == "user" and "disappear at zero" in request.body
    assert env.get(job.id).lease_owner is None and tickets(env) == []
    env.queue.answer(waiting.waiting_request_id, body="Yes, remove the row at zero.", answer_key="a1", user="user:local")
    done = env.finish(sup, job.id)
    assert done.lease_generation > waiting.lease_generation and len(tickets(env)) == 3
    resumed_prompt = env.provider.requests[1].user
    assert "Yes, remove the row at zero." in resumed_prompt and "resume from your checkpoint" in resumed_prompt
    assert done.usage["model_calls"] == 2  # usage of the first generation was kept


def test_po_revision_is_only_a_proposal_until_the_user_accepts_it(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    revision = {"kind": "revision", "summary": "Add removal", "title": "Menu", "description": "Coffee menu",
                "uac": [{"id": "UAC-1", "text": "Add coffee"}, {"id": "UAC-2", "text": "Remove coffee"}]}
    env.script(revision, revision)
    sup = env.supervisor()
    job = env.job("po", "revise", ticket=ticket, stage="chat", payload={"request": "users must remove items"})
    done = env.finish(sup, job.id)
    assert done.result["approved"] is False
    assert env.world.ticket(ticket.id).current_version == 1  # approved scope untouched
    [proposal_message] = env.messages(intent="scope_proposal")
    assert proposal_message.id == done.result["proposal_id"]
    t = env.world.ticket(ticket.id)
    accepted = env.world.w.decide_proposal(env.world.user, t.id, t.revision, proposal_message.id, True)
    assert accepted.current_version == 2 and accepted.phase == "scope_review"  # accepting re-opens review


# --- technical lead ------------------------------------------------------------------------------------------------------------
def test_lead_plan_is_stored_as_non_authoritative_with_decision_proposals(agent_env):
    env = agent_env
    env.script(PLAN)
    ticket = env.approved_ticket()
    sup = env.supervisor()
    done = env.finish(sup, env.job("technical-lead", "technical_plan", ticket=ticket, stage="plan").id)
    [plan] = env.messages(intent="technical_plan")
    assert plan.meta["authoritative"] is False and plan.meta["plan"]["steps"][0]["files"] == ["src/cart.js"]
    assert done.result["authoritative"] is False and len(done.result["decision_proposal_ids"]) == 1
    with env.db.read() as s:
        assert env.threads.accepted_decisions(s, env.project.id) == []
    prompt = env.provider.requests[0].user
    assert "APPROVED by the user" in prompt and "UAC-1" in prompt


def test_lead_refuses_a_ticket_the_user_has_not_approved(agent_env):
    env = agent_env
    env.script(PLAN)
    draft = env.world.new(SCOPE)
    done = env.finish(env.supervisor(), env.job("technical-lead", "technical_plan", ticket=draft, stage="plan").id, "failed")
    assert "approved" in done.result["error"] and env.provider.requests == []  # no model call, no budget spent


def test_lead_cannot_say_the_user_decides_for_them(agent_env):
    env = agent_env
    env.script({**PLAN, "kind": "answer"}, {"kind": "technical_plan", "summary": "x", "steps": [{"title": "t"}],
                                            "approved": True})
    done = env.finish(env.supervisor(), env.job("technical-lead", "technical_plan", ticket=env.approved_ticket(),
                                                stage="plan").id, "failed")
    assert "invalid structured output" in done.result["error"]


def test_a_result_for_a_revoked_attempt_is_dropped_not_persisted(agent_env):
    env = agent_env
    ticket = env.approved_ticket()

    def edit_scope_while_the_model_thinks(request):
        t = env.world.ticket(ticket.id)
        env.world.w.edit_scope(env.world.user, t.id, t.revision, {**SCOPE, "uac": [{"id": "UAC-9", "text": "Different"}]})
        return json.dumps(PLAN)

    env.script()
    env.provider._replies = edit_scope_while_the_model_thinks
    sup = env.supervisor()
    job = env.job("technical-lead", "technical_plan", ticket=ticket, stage="plan")
    env.run_until(sup, lambda: env.get(job.id).status == "cancelled")
    sup.wait_idle(5)
    assert env.messages(intent="technical_plan") == [] and env.messages(intent="decision_proposal") == []


# --- provider failures, labels, secrets, budgets --------------------------------------------------------------------------------
def test_a_provider_timeout_is_retried_once_then_succeeds(agent_env):
    env = agent_env
    env.script(ModelTimeout("provider did not answer within 5s"), THREE)
    sup = env.supervisor()
    first = env.finish(sup, po_breakdown(env).id, "failed")
    assert first.result["retry_job_id"] and "model call failed" in first.result["error"]
    retry = env.finish(sup, first.result["retry_job_id"])
    assert retry.attempt == 2 and len(tickets(env)) == 3
    assert env.get(first.id).usage["model_calls"] + retry.usage["model_calls"] == 2  # both counted, same scope


def test_a_rejected_request_is_not_retried(agent_env):
    env = agent_env
    env.script(ProviderRejected("provider rejected the request HTTP 401"))
    done = env.finish(env.supervisor(), po_breakdown(env).id, "failed")
    assert done.result["needs_human"] is True and "401" in done.result["error"] and not done.result.get("retry_job_id")


def test_provider_quota_puts_the_job_in_waiting_quota(agent_env):
    env = agent_env
    env.script(ProviderQuota(120, "provider quota (HTTP 429)"))
    sup = env.supervisor()
    job = po_breakdown(env)
    env.run_until(sup, lambda: env.get(job.id).status == "waiting_quota")
    waiting = env.get(job.id)
    assert "429" in waiting.result["quota"]["reason"] and tickets(env) == []
    assert waiting.usage["model_calls"] == 1  # the attempt was counted even though nothing came back


def test_a_fake_provider_under_a_real_label_is_refused_and_the_reverse(agent_env):
    env = agent_env
    real_label = StructuredAgentRuntime(db=env.db, workflow=env.world.w, threads=env.threads, builder=env.builder,
                                        client=env.client, redactor=env.redactor, fake=False)
    sup = env.supervisor(**{"structured": real_label})
    job = env.job("po", "breakdown", runtime="structured", payload={"request": "x"})  # real label, fake provider
    done = env.finish(sup, job.id, "failed")
    assert "labels disagree" in done.result["error"] and env.provider.requests == []
    mislabelled = env.job("po", "breakdown", runtime="structured:fake", payload={"request": "x"})
    not_fake = FakeProvider([THREE])
    not_fake.fake = False  # a provider that claims to be real, under a runtime labelled fake
    env.client.providers["fake"] = not_fake
    done = env.finish(sup, mislabelled.id, "failed")
    assert "labels disagree" in done.result["error"] and not_fake.requests == []


def test_unsupported_tasks_and_roles_fail_explicitly(agent_env):
    env = agent_env
    sup = env.supervisor()
    for role, task in (("po", "write_code"), ("developer", "implement"), ("qa", "verify")):
        job = env.job(role, task, ticket=env.approved_ticket() if role != "po" else None, stage="reply")
        done = env.finish(sup, job.id, "failed")
        assert "no task" in done.result["error"]


def test_secrets_from_the_provider_never_reach_messages_artifacts_results_or_logs(agent_env):
    env = agent_env
    leaky = proposal(ticket_spec("T1", "Menu"), summary=f"Use key {SECRET} to deploy")
    leaky["assumptions"] = [f"password: hunter2hunter2 and {SECRET}"]
    env.script(leaky)
    done = env.finish(env.supervisor(), po_breakdown(env).id)
    with env.db.read() as s:
        haystack = json.dumps([m.body for m in s.scalars(select(Message))] + [m.meta for m in s.scalars(select(Message))]
                              + [done.result, done.usage], default=str)
        for artifact in s.scalars(select(Artifact).where(Artifact.storage == "file")):
            haystack += env.store.read_bytes(s, artifact.id).decode(errors="replace")
    assert SECRET not in haystack and "hunter2hunter2" not in haystack and "[REDACTED]" in haystack


def test_the_job_budget_stops_repair_calls_too(agent_env):
    env = agent_env
    env.script("not json", THREE)
    job = po_breakdown(env, limits={**LIMITS, "model_calls": 1})
    done = env.finish(env.supervisor(), job.id, "stopped")
    assert done.result["reason"] == "budget_exhausted" and done.result["limit"] == "model_calls"
    assert tickets(env) == [] and done.usage["model_calls"] == 1


def test_the_context_for_a_run_is_attached_to_the_job_and_matches_what_the_model_saw(agent_env):
    env = agent_env
    env.script(THREE)
    done = env.finish(env.supervisor(), po_breakdown(env).id)
    assert done.context_artifact_id == done.result["context_artifact_id"]
    with env.db.read() as s:
        stored = json.loads(env.store.read_bytes(s, done.context_artifact_id))
    assert stored["user"] == env.provider.requests[0].user and stored["system"] == env.provider.requests[0].system
    assert stored["sha256"] == done.result["context_sha256"] and stored["manifest"]["estimated"] is True
