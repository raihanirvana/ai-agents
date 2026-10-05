"""SOUL files and structured output contracts."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.agents import AgentDefinitionError, InvalidOutput, ROLES, load_agents
from app.agents.outputs import (Clarification, LeadAnswer, LeadPlanOutput, PoOutput, PoProposal, PoReviseOutput,
                                parse_output)
from app.agents.souls import MAX_FILE_BYTES, REPO_AGENTS_DIR

from .conftest import proposal, ticket_spec


def copy_agents(tmp_path) -> Path:
    target = tmp_path / "agents"
    shutil.copytree(REPO_AGENTS_DIR, target, ignore=shutil.ignore_patterns("models*.json"))
    return target


def test_four_roles_load_with_stable_digests():
    agents = load_agents()
    assert tuple(agents) == ROLES and len(set(a.digest for a in agents.values())) == 4
    assert load_agents()["po"].digest == agents["po"].digest
    for role, agent in agents.items():
        assert agent.soul.startswith("#") and agent.instructions.strip()


def test_every_soul_states_that_the_role_cannot_approve_anything():
    for role, agent in load_agents().items():
        assert "cannot approve" in agent.soul.lower(), role
        assert "never" in agent.soul.lower() and "data" in agent.soul.lower()  # untrusted-input rule


def test_digest_changes_with_content(tmp_path):
    root = copy_agents(tmp_path)
    before = load_agents(root)["qa"].digest
    (root / "qa" / "SOUL.md").write_text((root / "qa" / "SOUL.md").read_text() + "\nExtra rule.\n")
    assert load_agents(root)["qa"].digest != before


@pytest.mark.parametrize("damage, message", [
    (lambda root: (root / "po" / "SOUL.md").unlink(), "missing"),
    (lambda root: (root / "po" / "instructions.md").write_text("  \n"), "empty"),
    (lambda root: (root / "qa" / "SOUL.md").write_bytes(b"x" * (MAX_FILE_BYTES + 1)), "exceeds"),
    (lambda root: (root / "qa" / "SOUL.md").write_bytes(b"\xff\xfe broken"), "UTF-8"),
    (lambda root: (root / "developer" / "SOUL.md").write_text("api_key = abcdef123456789012"), "secret"),
    (lambda root: shutil.rmtree(root / "technical-lead"), "missing"),
])
def test_broken_definitions_are_refused(tmp_path, damage, message):
    root = copy_agents(tmp_path)
    damage(root)
    with pytest.raises(AgentDefinitionError, match=message):
        load_agents(root)


# --- structured outputs ------------------------------------------------------------------------------
def parse(data, union=PoOutput):
    import json
    return parse_output(json.dumps(data), union)


def test_valid_proposal_orders_prerequisites_first():
    output = parse(proposal(ticket_spec("T2", "Cart", deps=["T1"]), ticket_spec("T1", "Menu")))
    assert [t.key for t in output.creation_order()] == ["T1", "T2"]


@pytest.mark.parametrize("bad, expect", [
    (proposal(ticket_spec("T1", deps=["T2"]), ticket_spec("T2", deps=["T1"])), "cycle"),
    (proposal(ticket_spec("T1", deps=["T1"])), "itself"),
    (proposal(ticket_spec("T1", deps=["NOPE"])), "unknown key"),
    (proposal(ticket_spec("T1"), ticket_spec("T1")), "duplicate ticket key"),
    (proposal({**ticket_spec("T1"), "uac": []}), "uac"),
    (proposal({**ticket_spec("T1"), "uac": [{"id": "U", "text": "a"}, {"id": "U", "text": "b"}]}), "duplicate UAC"),
    (proposal({**ticket_spec("T1"), "approved": True}), "Extra inputs"),
    (proposal({**ticket_spec("T1"), "title": "x" * 500}), "title"),
    ({**proposal(ticket_spec("T1")), "kind": "approve_everything"}, "kind"),
    ({"kind": "proposal", "summary": "s", "tickets": []}, "tickets"),
])
def test_invalid_proposals_are_rejected_with_visible_reasons(bad, expect):
    with pytest.raises(InvalidOutput) as error:
        parse(bad)
    assert expect.lower() in str(error.value).lower()


def test_non_json_and_non_object_answers_are_invalid():
    for text in ("I think we should build a menu", "[1, 2]", "", '{"kind": '):
        with pytest.raises(InvalidOutput):
            parse_output(text, PoOutput)


def test_code_fence_is_tolerated_but_content_is_still_validated():
    fenced = "```json\n" + __import__("json").dumps(proposal(ticket_spec("T1"))) + "\n```"
    assert isinstance(parse_output(fenced, PoOutput), PoProposal)
    with pytest.raises(InvalidOutput):
        parse_output("```json\n{\"kind\": \"proposal\"}\n```", PoOutput)


def test_clarification_and_other_contracts():
    clar = parse({"kind": "clarification", "questions": [{"id": "Q1", "question": "Remove at zero?"}]})
    assert isinstance(clar, Clarification) and "Remove at zero?" in clar.as_text()
    with pytest.raises(InvalidOutput):
        parse({"kind": "clarification", "questions": []})
    revision = parse({"kind": "revision", "summary": "s", "title": "T", "uac": [{"id": "U1", "text": "x"}]},
                     PoReviseOutput)
    assert revision.depends_on_ticket_ids == []
    plan = parse({"kind": "technical_plan", "summary": "s", "steps": [{"title": "do"}]}, LeadPlanOutput)
    assert plan.needs_user is False
    answer = parse_output('{"kind": "answer", "outcome": "proceed", "answer": "Yes"}', LeadAnswer)
    assert answer.outcome == "proceed"
    with pytest.raises(InvalidOutput):
        parse_output('{"kind": "answer", "outcome": "approve", "answer": "Yes"}', LeadAnswer)
