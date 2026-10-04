import pytest
from sqlalchemy import select
from app.domain import Conflict, Invalid
from app.persistence.models import Dependency, Job, Project
from .conftest import SCOPE
from .test_evidence import json_art


def revalidation(world, t, up_candidate, **override):
    with world.db.read() as s:
        base = s.get(Project, world.project.id).workflow["accepted_tip"]
        dep = s.scalar(select(Dependency).where(Dependency.ticket_id == t.id,
            Dependency.depends_on_ticket_id == up_candidate.ticket_id))
        request_id = dep.revalidation["request_id"]
    return json_art(world, "report", {"status": "passed", "ticket_id": t.id,
        "scope_version": t.current_version, "upstream_candidate_id": up_candidate.id,
        "base_sha": base, "uac_changed": False, "fake_provider": False,
        "revalidation_id": request_id, "infrastructure_failure": False,
        "expected_test_ids": ["contract-check"], "executed_test_ids": ["contract-check"],
        "counts": {"discovered": 1, "executed": 1, "passed": 1, "failed": 0, "skipped": 0},
        "commands": [{"exit_code": 0, "argv": ["contract-fixture"]}], **override})


def test_contract_change_preserves_pin_and_blocks_transitive_downstream(world):
    up, c = world.accepted()
    down = world.approve(world.new({**SCOPE, "dependencies": [up.id]}))
    leaf = world.approve(world.new({**SCOPE, "dependencies": [down.id]}))
    actor, attempt = world.job(down, "developer")
    world.w.contract_changed(world.actor("integrator"), up.id, up.revision, "Upstream contract changed")
    assert not world.w.eligible(world.user, down.id) and not world.w.eligible(world.user, leaf.id)
    with world.db.read() as s:
        d = s.scalar(select(Dependency).where(Dependency.ticket_id == down.id))
        assert d.state == "needs_revalidation" and d.accepted_candidate_id == c.id and d.integration_sha == c.commit_sha
        assert s.get(Job, attempt.job_id).status == "cancelled"
    down = world.ticket(down.id)
    report = revalidation(world, down, c)
    world.w.revalidate_dependency(world.actor("verification"), down.id, down.revision, up.id, report.id)
    assert world.w.eligible(world.user, down.id)
    assert not world.w.eligible(world.user, leaf.id)


@pytest.mark.parametrize("override", [{"uac_changed": True}, {"commands": []}, {"fake_provider": True},
    {"base_sha": "c" * 40}, {"scope_version": 2}, {"commands": [{"exit_code": 1}]},
    {"revalidation_id": "previous-change"}, {"fake_provider": None}, {"infrastructure_failure": True},
    {"expected_test_ids": []}, {"executed_test_ids": ["another-check"]},
    {"counts": {"discovered": 2, "executed": 1, "passed": 1, "failed": 0, "skipped": 1}}])
def test_required_checks_revalidate_only_current_contract_and_unchanged_uac(world, override):
    up, c = world.accepted()
    down = world.approve(world.new({**SCOPE, "dependencies": [up.id]}))
    world.w.contract_changed(world.actor("integrator"), up.id, up.revision, "Change")
    down = world.ticket(down.id)
    proof = revalidation(world, down, c, **override)
    with pytest.raises(Invalid):
        world.w.revalidate_dependency(world.actor("verification"), down.id, down.revision, up.id, proof.id)
    assert not world.w.eligible(world.user, down.id)


def test_uac_change_requires_new_scope_approval(world):
    up, c = world.accepted()
    down = world.approve(world.new({**SCOPE, "dependencies": [up.id]}))
    world.w.contract_changed(world.actor("integrator"), up.id, up.revision, "Change")
    down = world.ticket(down.id)
    revised = world.w.edit_scope(world.user, down.id, down.revision, {**SCOPE, "dependencies": [up.id],
        "uac": [{"id": "UAC-2", "text": "Changed contract must work"}]})
    assert revised.current_version == 2 and not world.w.eligible(world.user, down.id)
    revised = world.approve(revised)
    assert not world.w.eligible(world.user, revised.id)
    report = revalidation(world, revised, c)
    world.w.revalidate_dependency(world.actor("verification"), revised.id, revised.revision, up.id, report.id)
    assert world.w.eligible(world.user, revised.id)


def test_previous_contract_receipt_cannot_satisfy_next_change_and_history_is_pinned(world):
    from app.persistence.pins import pinned_artifacts
    up, c = world.accepted()
    down = world.approve(world.new({**SCOPE, "dependencies": [up.id]}))
    world.w.contract_changed(world.actor("integrator"), up.id, up.revision, "First change")
    down = world.ticket(down.id)
    old = revalidation(world, down, c)
    world.w.revalidate_dependency(world.actor("verification"), down.id, down.revision, up.id, old.id)
    up = world.ticket(up.id)
    world.w.contract_changed(world.actor("integrator"), up.id, up.revision, "Second change, same base")
    down = world.ticket(down.id)
    with pytest.raises(Invalid):
        world.w.revalidate_dependency(world.actor("verification"), down.id, down.revision, up.id, old.id)
    with world.db.read() as s:
        assert old.id in pinned_artifacts(s)
    fresh = revalidation(world, down, c)
    world.w.revalidate_dependency(world.actor("verification"), down.id, down.revision, up.id, fresh.id)
    assert world.w.eligible(world.user, down.id)


def test_contract_change_during_integration_rolls_back_entire_invalidation(world):
    up, c = world.accepted()
    down = world.approve(world.new({**SCOPE, "dependencies": [up.id]}))
    integrating, _, _ = world.integrating(down)
    with pytest.raises(Conflict, match="integration"):
        world.w.contract_changed(world.actor("integrator"), up.id, up.revision, "Change")
    with world.db.read() as s:
        assert s.scalar(select(Dependency).where(Dependency.ticket_id == down.id)).state == "satisfied"
