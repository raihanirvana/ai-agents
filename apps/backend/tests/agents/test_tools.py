"""Tool authorisation: the role policy is enforced from the run identity, never from model arguments."""
from __future__ import annotations

import pytest

from app.agents import NotWired, TOOL_POLICY
from app.domain import Forbidden, Invalid
from app.persistence.models import Ticket
from app.workers import BudgetExhausted, StaleLease
from app.workers.runtime import WaitingForInput

from .conftest import LIMITS

ALL_TOOLS = set().union(*TOOL_POLICY.values())


def test_policy_has_no_approval_or_status_setting_tool():
    for role, tools in TOOL_POLICY.items():
        for tool in tools:
            assert not any(word in tool for word in ("approve", "accept", "release", "waive", "set_status", "sql")), tool
    assert set(TOOL_POLICY) == {"po", "technical-lead", "developer", "qa"}
    # Architecture table (section 6) is the reference for the role matrix.
    assert {"read_brief", "propose_ticket", "propose_criteria", "send_message", "request_input"} <= TOOL_POLICY["po"]
    assert {"propose_decision", "plan_dependencies", "review_candidate"} <= TOOL_POLICY["technical-lead"]
    assert {"submit_candidate", "run_command", "inspect_diff"} <= TOOL_POLICY["developer"]
    assert {"propose_tests", "report_bug", "request_test_run"} <= TOOL_POLICY["qa"]


@pytest.mark.parametrize("role", ["po", "technical-lead", "developer", "qa"])
def test_a_role_cannot_call_tools_of_other_roles(agent_env, role):
    env = agent_env
    ticket = env.approved_ticket()
    stage = "chat" if role == "po" else "reply"
    ctx = env.ctx(env.job(role, "answer_message", ticket=ticket, stage=stage, limits={**LIMITS, "tool_calls": 60}))
    for tool in sorted(ALL_TOOLS - TOOL_POLICY[role]):
        with pytest.raises(Forbidden, match="has no tool"):
            env.tools.call(ctx, tool, {})


def test_unwired_tools_in_policy_fail_explicitly_after_authorisation(agent_env):
    env = agent_env
    ctx = env.ctx(env.job("qa", "x", ticket=env.approved_ticket(), stage="reply"))
    with pytest.raises(NotWired, match="DEV-010"):
        env.tools.call(ctx, "report_bug", {"title": "x"})
    with pytest.raises(Forbidden):
        env.tools.call(ctx, "submit_candidate", {})  # authorised check comes first, wired-ness second


@pytest.mark.parametrize("field", ["actor", "role", "user", "project_id", "job_id", "generation", "lease"])
def test_identity_in_arguments_is_refused(agent_env, field):
    ctx = agent_env.ctx(agent_env.job("po", "breakdown"))
    with pytest.raises(Invalid, match="come from the run"):
        agent_env.tools.call(ctx, "read_brief", {field: "technical-lead"})
    with pytest.raises(Invalid):
        agent_env.tools.call(ctx, "read_brief", "not an object")


def test_every_attempt_counts_against_the_tool_budget_even_when_forbidden(agent_env):
    env = agent_env
    ctx = env.ctx(env.job("po", "breakdown", limits={**LIMITS, "tool_calls": 3}))
    for _ in range(3):
        with pytest.raises(Forbidden):
            env.tools.call(ctx, "propose_decision", {"title": "t", "rationale": "r"})
    with pytest.raises(BudgetExhausted):
        env.tools.call(ctx, "read_brief", {})  # a model spamming forbidden tools hits the cap
    assert env.get(ctx.lease.job_id).status == "stopped"


def test_a_revoked_attempt_cannot_use_tools(agent_env):
    env = agent_env
    job = env.job("po", "breakdown")
    ctx = env.ctx(job)
    env.queue.cancel(job.id, reason="user cancelled", actor="user:local")
    with pytest.raises(StaleLease):
        env.tools.call(ctx, "read_brief", {})


def test_po_reads_brief_and_proposes_unapproved_tickets(agent_env):
    env = agent_env
    ctx = env.ctx(env.job("po", "breakdown"))
    assert env.tools.call(ctx, "read_brief", {})["brief_version"] == 1
    scope = {"title": "Cart", "description": "", "uac": [{"id": "UAC-1", "text": "Add to cart"}], "dependencies": []}
    first = env.tools.call(ctx, "propose_ticket", scope)
    again = env.tools.call(ctx, "propose_ticket", scope)  # a retried tool call maps to the same ticket
    assert first == again and first["approved"] is False and first["phase"] == "scope_review"
    with env.db.read() as s:
        assert s.get(Ticket, first["ticket_id"]).phase == "scope_review"


def test_propose_criteria_creates_a_proposal_not_a_new_scope(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    ctx = env.ctx(env.job("po", "revise", ticket=ticket, stage="chat"))
    result = env.tools.call(ctx, "propose_criteria", {"uac": [{"id": "UAC-1", "text": "Add coffee"},
                                                              {"id": "UAC-2", "text": "Remove coffee"}]})
    assert result["approved"] is False
    after = env.world.ticket(ticket.id)
    assert after.current_version == 1  # the approved scope did not change
    [stored] = env.messages(intent="scope_proposal")
    assert [c["id"] for c in stored.meta["document"]["uac"]] == ["UAC-1", "UAC-2"]
    with pytest.raises(Invalid):
        env.tools.call(ctx, "propose_criteria", {"uac": [], "title": "sneaky"})


def test_lead_proposes_decisions_that_are_not_authoritative(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    ctx = env.ctx(env.job("technical-lead", "technical_plan", ticket=ticket, stage="plan"))
    result = env.tools.call(ctx, "propose_decision", {"title": "Use CSS modules", "rationale": "Scoped styles"})
    assert result["accepted"] is False
    with env.db.read() as s:
        assert env.threads.accepted_decisions(s, env.project.id) == []
    plan = env.tools.call(ctx, "plan_dependencies", {"depends_on_ticket_ids": [], "reason": "independent"})
    assert plan["applied"] is False
    assert env.messages(intent="dependency_plan")


def test_po_cannot_propose_a_decision_and_lead_cannot_create_tickets(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    po = env.ctx(env.job("po", "breakdown"))
    with pytest.raises(Forbidden):
        env.tools.call(po, "propose_decision", {"title": "t", "rationale": "r"})
    lead = env.ctx(env.job("technical-lead", "technical_plan", ticket=ticket, stage="plan"))
    with pytest.raises(Forbidden):
        env.tools.call(lead, "propose_ticket", {"title": "t"})


def test_po_request_input_waits_for_the_user(agent_env):
    env = agent_env
    job = env.job("po", "breakdown")
    ctx = env.ctx(job)
    with pytest.raises(WaitingForInput):
        env.tools.call(ctx, "request_input", {"question": "Which payment methods?"})
    assert env.get(job.id).status == "waiting_input"
    [request] = env.messages(kind="input_request")
    assert request.recipient == "user" and request.sender == "agent:po"
