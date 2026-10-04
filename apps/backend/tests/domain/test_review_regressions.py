"""DEV-003 review fixes: real DB/files and synthetic trusted broker/runner receipts."""
import pytest
from sqlalchemy import select
from app.domain import Attempt, Conflict, Forbidden, Invalid
from app.persistence import read_events
from app.persistence.models import Candidate, Dependency, Job, Ticket
from app.persistence.pins import pin_owners
from .conftest import SCOPE
from .test_dependencies import revalidation


def exhaust_repairs(world, t):
    for _ in range(3):
        t, c = world.submitted(t)
        lead, ref = world.job(t, "technical-lead")
        world.w.request_changes(lead, t.id, world.ticket(t.id).revision, c.id, "Fix contract", ref)
        t = world.ticket(t.id)
    assert t.workflow["repair_cycles"] == 3 and t.blocker["reason"] == "needs_human"
    return t


@pytest.mark.parametrize("authorize_first", [False, True])
def test_contract_revalidation_cannot_grant_repair_budget(world, authorize_first):
    up, uc = world.accepted()
    t = exhaust_repairs(world, world.approve(world.new({**SCOPE, "dependencies": [up.id]})))
    world.w.contract_changed(world.actor("integrator"), up.id, up.revision, "Changed contract")
    t = world.ticket(t.id)
    assert t.blocker["reason"] == "needs_human" and not world.w.eligible(world.user, t.id)
    if authorize_first:
        t = world.w.authorize_repair(world.user, t.id, t.revision, 1)
        assert t.blocker["reason"] == "dependency_revalidation"
        assert not world.w.eligible(world.user, t.id)
    report = revalidation(world, t, uc)
    t = world.w.revalidate_dependency(world.actor("verification"), t.id, t.revision, up.id, report.id)
    if not authorize_first:
        assert t.blocker["reason"] == "needs_human" and not world.w.eligible(world.user, t.id)
        # A valid live job cannot make the exhausted ticket schedulable.
        _, attempt = world.job(t, "developer", bind=False)
        with pytest.raises(Conflict):
            world.w.bind_attempt(world.actor("scheduler"), t.id, t.revision, attempt)
        t = world.w.authorize_repair(world.user, t.id, t.revision, 1)
        world.w.bind_attempt(world.actor("scheduler"), t.id, t.revision, attempt)
    else:
        world.job(t, "developer")
    current = world.ticket(t.id)
    assert current.workflow["repair_cycles"] == 3 and current.workflow["repair_limit"] == 4
    assert world.w.eligible(world.user, t.id)


def test_counter_fences_repair_even_if_display_blocker_is_missing(world):
    t = exhaust_repairs(world, world.approve(world.new()))
    with world.db.write() as s:
        s.get(Ticket, t.id).blocker = None  # malformed legacy/display metadata
    t = world.ticket(t.id)
    assert not world.w.eligible(world.user, t.id)
    _, ref = world.job(t, "developer", bind=False)
    with pytest.raises(Conflict):
        world.w.bind_attempt(world.actor("scheduler"), t.id, t.revision, ref)
    t = world.w.authorize_repair(world.user, t.id, t.revision)
    world.w.bind_attempt(world.actor("scheduler"), t.id, t.revision, ref)


def test_revalidation_preserves_unrelated_blocker(world):
    up, c = world.accepted()
    down = world.approve(world.new({**SCOPE, "dependencies": [up.id]}))
    hold = {"reason": "manual_hold", "resolution": "resolve separately"}
    with world.db.write() as s:
        s.get(Ticket, down.id).blocker = hold
    world.w.contract_changed(world.actor("integrator"), up.id, up.revision, "Change")
    down = world.ticket(down.id)
    report = revalidation(world, down, c)
    result = world.w.revalidate_dependency(world.actor("verification"), down.id, down.revision, up.id, report.id)
    assert result.blocker == hold and not world.w.eligible(world.user, down.id)


def test_unrelated_lead_cannot_invalidate_uat_candidate(world):
    up, _ = world.accepted()
    down, candidate, _, _, _ = world.uat(world.approve(world.new({**SCOPE, "dependencies": [up.id]})))
    unrelated, _ = world.submitted()
    lead, _ = world.job(unrelated, "technical-lead")
    with world.db.read() as s:
        events_before = [e.cursor for e in read_events(s)]
    with pytest.raises(Forbidden):
        world.w.contract_changed(lead, up.id, up.revision, "Lead tries to invalidate another ticket")
    with world.db.read() as s:
        assert [e.cursor for e in read_events(s)] == events_before
        assert s.get(Ticket, down.id).phase == "uat"
        assert s.get(Candidate, candidate.id).status == "verified"


@pytest.mark.parametrize("source", ["old_scope", "other_ticket", "old_generation"])
def test_commit_receipt_is_bound_to_attempt_despite_sha_deduplication(world, source):
    old = world.approve(world.new())
    _, old_ref = world.job(old, "developer")
    commit, old_receipt, base = world.commit_receipt(old, old_ref)
    if source == "old_scope":
        t = world.w.edit_scope(world.user, old.id, world.ticket(old.id).revision,
            {**SCOPE, "uac": [{"id": "UAC-2", "text": "Revised requirement"}]})
        t = world.approve(t)
        actor, ref = world.job(t, "developer")
    elif source == "other_ticket":
        t = world.approve(world.new())
        actor, ref = world.job(t, "developer")
    else:
        t = world.ticket(old.id)
        with world.db.write() as s:
            s.get(Job, old_ref.job_id).lease_generation += 1
        ref = Attempt(old_ref.job_id, 2, old_ref.scope_version)
        actor = world.actor("developer", job_id=ref.job_id, generation=ref.generation)
        world.w.bind_attempt(world.actor("scheduler"), t.id, t.revision, ref)
    t = world.ticket(t.id)
    with pytest.raises(Conflict, match="commit receipt"):
        world.w.submit_candidate(actor, t.id, t.revision, ref, commit_artifact_id=commit.id,
            commit_receipt_id=old_receipt.id, base_sha=base, submission_key="stale-receipt")
    same_commit, fresh, base = world.commit_receipt(t, ref)
    assert same_commit.id == commit.id and fresh.id != old_receipt.id
    c = world.w.submit_candidate(actor, t.id, t.revision, ref, commit_artifact_id=commit.id,
        commit_receipt_id=fresh.id, base_sha=base, submission_key="fresh-receipt")
    assert c.integration["source_attempt"]["generation"] == ref.generation
    world.w.edit_scope(world.user, t.id, world.ticket(t.id).revision, SCOPE)
    with world.db.read() as s:
        assert s.get(Candidate, c.id).status == "superseded"
        assert pin_owners(s, fresh.id)  # immutable submission history survives invalidation


def test_model_published_commit_receipt_cannot_authorize_submission(world):
    t = world.approve(world.new())
    actor, ref = world.job(t, "developer")
    t = world.ticket(t.id)
    commit, receipt, base = world.commit_receipt(t, ref, producer="developer")
    with pytest.raises(Conflict, match="commit receipt"):
        world.w.submit_candidate(actor, t.id, t.revision, ref, commit_artifact_id=commit.id,
            commit_receipt_id=receipt.id, base_sha=base, submission_key="model-receipt")


def test_contract_change_invalidates_only_edges_on_affected_paths(world):
    up, _ = world.accepted()
    unrelated, _ = world.accepted()
    down = world.approve(world.new({**SCOPE, "dependencies": [up.id, unrelated.id]}))
    leaf = world.approve(world.new({**SCOPE, "dependencies": [down.id, unrelated.id]}))
    with world.db.read() as s:
        untouched = {d.id: (d.revision, d.accepted_candidate_id, d.integration_sha) for d in
            s.scalars(select(Dependency).where(Dependency.depends_on_ticket_id == unrelated.id))}
    world.w.contract_changed(world.actor("integrator"), up.id, up.revision, "Changed one upstream")
    with world.db.read() as s:
        for tid, changed_upstream in [(down.id, up.id), (leaf.id, down.id)]:
            for d in s.scalars(select(Dependency).where(Dependency.ticket_id == tid)):
                if d.depends_on_ticket_id == changed_upstream:
                    assert d.state == "needs_revalidation"
                    assert d.revalidation["reported_by"] == "principal:integrator"
                else:
                    assert d.state == "satisfied"
                    assert (d.revision, d.accepted_candidate_id, d.integration_sha) == untouched[d.id]


def test_accepted_dependency_history_remains_accepted_without_unresolvable_blocker(world):
    up, _ = world.accepted()
    down, accepted = world.accepted(world.approve(world.new({**SCOPE, "dependencies": [up.id]})))
    original = dict(down.workflow)
    leaf = world.approve(world.new({**SCOPE, "dependencies": [down.id]}))
    world.w.contract_changed(world.actor("integrator"), up.id, up.revision, "Contract changed after acceptance")
    down = world.ticket(down.id)
    assert down.phase == "accepted" and down.blocker is None and down.workflow == original
    assert not world.w.eligible(world.user, leaf.id)
    with world.db.read() as s:
        assert s.get(Candidate, accepted.id).status == "accepted"
        assert s.scalar(select(Dependency).where(Dependency.ticket_id == down.id)).state == "needs_revalidation"
        assert any(e.type == "ticket.dependency_followup_required" and e.entity_id == down.id for e in read_events(s))
    # New work depending on historical acceptance also needs current contract checks.
    new = world.approve(world.new({**SCOPE, "dependencies": [down.id]}))
    assert not world.w.eligible(world.user, new.id)
    report = revalidation(world, new, accepted)
    world.w.revalidate_dependency(world.actor("verification"), new.id, new.revision, down.id, report.id)
    assert world.w.eligible(world.user, new.id)


def test_title_edit_cannot_clear_contract_revalidation_or_reuse_old_scope_proof(world):
    up, c = world.accepted()
    down = world.approve(world.new({**SCOPE, "dependencies": [up.id]}))
    world.w.contract_changed(world.actor("integrator"), up.id, up.revision, "Change")
    down = world.ticket(down.id)
    old_proof = revalidation(world, down, c)
    down = world.w.edit_scope(world.user, down.id, down.revision,
        {**SCOPE, "title": "A renamed ticket", "dependencies": [up.id]})
    down = world.approve(down)
    assert not world.w.eligible(world.user, down.id)
    with pytest.raises(Invalid):
        world.w.revalidate_dependency(world.actor("verification"), down.id, down.revision, up.id, old_proof.id)
    with world.db.read() as s:
        d = s.scalar(select(Dependency).where(Dependency.ticket_id == down.id))
        assert d.state == "needs_revalidation" and d.accepted_candidate_id == c.id
    report = revalidation(world, down, c)
    world.w.revalidate_dependency(world.actor("verification"), down.id, down.revision, up.id, report.id)
    assert world.w.eligible(world.user, down.id)


def test_known_contract_change_requires_checks_for_new_or_readded_dependency(world):
    up, c = world.accepted()
    world.w.contract_changed(world.actor("integrator"), up.id, up.revision, "Changed before new work")
    t = world.approve(world.new({**SCOPE, "dependencies": [up.id]}))
    assert not world.w.eligible(world.user, t.id)
    # Removing an edge is an explicit user scope decision; re-adding it cannot
    # erase the upstream contract-change history.
    t = world.approve(world.w.edit_scope(world.user, t.id, t.revision, SCOPE))
    assert world.w.eligible(world.user, t.id)
    t = world.approve(world.w.edit_scope(world.user, t.id, t.revision, {**SCOPE, "dependencies": [up.id]}))
    assert not world.w.eligible(world.user, t.id)
    proof = revalidation(world, t, c)
    world.w.revalidate_dependency(world.actor("verification"), t.id, t.revision, up.id, proof.id)
    assert world.w.eligible(world.user, t.id)


@pytest.mark.parametrize("document", [None, [], "scope", 1, {"title": ["wrong"], "uac": SCOPE["uac"]}])
def test_malformed_scope_is_domain_invalid_without_partial_ticket_or_event(world, document):
    with world.db.read() as s:
        before = [e.cursor for e in read_events(s)]
    with pytest.raises(Invalid):
        world.w.create_ticket(world.user, document)
    with world.db.read() as s:
        assert not s.scalars(select(Ticket)).all()
        assert [e.cursor for e in read_events(s)] == before


@pytest.mark.filterwarnings("error::sqlalchemy.exc.SAWarning")
def test_review_without_target_is_domain_error_without_null_primary_key_warning(world):
    t, c = world.submitted()
    lead, ref = world.job(t, "technical-lead")
    with pytest.raises(Invalid, match="artifact ID"):
        world.w.approve_review(lead, t.id, world.ticket(t.id).revision, ref, c.id)
