"""DEV-014 domain rules for the frozen release: scope freeze, draft, approval checklist, export receipt."""
import pytest
from sqlalchemy import select

from app.domain import Conflict, Forbidden, Invalid
from app.persistence.models import Approval, Project, Release
from tests.persistence import factories as f
from .conftest import SCOPE
from .test_evidence import json_art

MANUAL_SCOPE = {**SCOPE, "uac": [{"id": "UAC-1", "text": "Add coffee"}, {"id": "UAC-M", "text": "Looks right", "mode": "manual"}]}


def verification_actor(world):
    return world.actor("verification")


def draft(world, entries, *, passed=True, tip=None, replaces=None, target_fields=None, extra_evidence=(), **overrides):
    """A release drafted by the verification service with a bound receipt, like the release runtime does."""
    tip = tip or f.SHA_B
    scope_digest = world.w.scope_digest(entries)
    build = json_art(world, "build_record", {"build_digest": "d" * 64}, "builder")
    target = json_art(world, "target_manifest", {"accepted_tip": tip, "build_artifact_id": build.id,
                                                  "scope_digest": scope_digest, **(target_fields or {})}, "builder")
    receipt = json_art(world, "report", {"kind": "release_verification", "status": "passed" if passed else "failed",
        "target_artifact_id": target.id, "target_digest": target.checksum, "accepted_tip": tip, "fake_provider": False,
        "infrastructure_failure": False, "expected_test_ids": ["regression"], "executed_test_ids": ["regression"],
        "counts": {"discovered": 1, "executed": 1, "passed": 1 if passed else 0, "failed": 0 if passed else 1, "skipped": 0},
        "commands": [{"exit_code": 0, "argv": ["contract-fixture"]}]})
    release = world.w.draft_release(verification_actor(world), scope_snapshot=entries, accepted_tip=tip,
        target_artifact_id=target.id, target_digest=target.checksum, build_artifact_id=build.id, evidence_ids=[receipt.id, *extra_evidence],
        verification_passed=passed, replaces=replaces, **overrides)
    return release, target, receipt


def approve(world, release, target, receipt, manual=()):
    return world.w.approve_release(world.user, release.id, release.revision, target.id, target.checksum, [receipt.id], list(manual))


def accepted(world, scope=None):
    t = world.approve(world.new(scope or SCOPE))
    t, c, target, v, ids = world.uat(t)
    manual = [u["id"] for u in (scope or SCOPE)["uac"] if u.get("mode") == "manual"]
    op = world.w.accept_uat(world.user, t.id, t.revision, c.id, t.current_version, target.id, target.checksum, v.id, ids, manual)
    world.w.finish_integration(world.actor("integrator"), t.id, world.ticket(t.id).revision, c.id, op["operation_id"], c.commit_sha)
    return world.ticket(t.id), c


def revision_of(world, release_id):
    with world.db.read() as s:
        return s.get(Release, release_id).revision


def test_freeze_lists_accepted_tickets_with_their_manual_checklist_and_excludes_released_ones(world):
    a, _ = accepted(world, MANUAL_SCOPE)
    tip, entries = world.w.freeze_release_scope(world.user)
    assert tip == f.SHA_A and [e["ticket_id"] for e in entries] == [a.id]
    assert entries[0]["checklist"] == [f"{a.id}:UAC-M"] and entries[0]["integrated_sha"] == f.SHA_A
    release, target, receipt = draft(world, entries, tip=tip)
    approve(world, release, target, receipt, [f"{a.id}:UAC-M"])
    b, _ = accepted(world)
    tip2, entries2 = world.w.freeze_release_scope(world.user)
    assert [e["ticket_id"] for e in entries2] == [b.id]  # accepted after the first release: next release only


def test_freeze_is_refused_without_accepted_work_while_integrating_or_with_a_draft_open(world):
    with pytest.raises(Invalid, match="no accepted"):
        world.w.freeze_release_scope(world.user)
    a, _ = accepted(world)
    t, c, op = world.integrating()
    with pytest.raises(Conflict, match="pending integrations"):
        world.w.freeze_release_scope(world.user)
    world.w.finish_integration(world.actor("integrator"), t.id, world.ticket(t.id).revision, c.id, op["operation_id"], c.commit_sha)
    _, entries = world.w.freeze_release_scope(world.user)
    draft(world, entries)
    with pytest.raises(Conflict, match="draft release already exists"):
        world.w.freeze_release_scope(world.user)
    with pytest.raises(Forbidden):
        world.w.freeze_release_scope(world.actor("integrator"))


def test_release_approval_needs_the_exact_manual_checklist_and_records_it(world):
    a, _ = accepted(world, MANUAL_SCOPE)
    _, entries = world.w.freeze_release_scope(world.user)
    release, target, receipt = draft(world, entries, tip=f.SHA_A)
    for wrong in ([], [f"{a.id}:UAC-1"], [f"{a.id}:UAC-M", f"{a.id}:UAC-M"], [f"{a.id}:UAC-M", "other:UAC-M"]):
        with pytest.raises(Invalid, match="checklist"):
            approve(world, release, target, receipt, wrong)
    assert world.w.release_checklist(entries) == [f"{a.id}:UAC-M"]
    done = approve(world, release, target, receipt, [f"{a.id}:UAC-M"])
    assert done.status == "approved"
    with world.db.read() as s:
        ap = s.scalar(select(Approval).where(Approval.type == "release"))
        assert ap.details == {"manual_uac_ids": [f"{a.id}:UAC-M"], "scope_digest": world.w.scope_digest(entries), "accepted_tip": f.SHA_A}
        assert ap.target_digest == target.checksum and ap.evidence_artifact_ids == [receipt.id]


def accept_on_new_tip(world, sha):
    """A second accepted ticket whose integration really moves the accepted tip to `sha`."""
    t = world.approve(world.new())
    dev, ref = world.job(t, "developer")
    t = world.ticket(t.id)
    commit, receipt, base = world.commit_receipt(t, ref, sha=sha)
    c = world.w.submit_candidate(dev, t.id, t.revision, ref, commit_artifact_id=commit.id, commit_receipt_id=receipt.id,
                                 base_sha=base, submission_key="second-" + sha[:6])
    target = world.target(world.ticket(t.id), c)
    lead, ref = world.job(t, "technical-lead")
    world.w.approve_review(lead, t.id, world.ticket(t.id).revision, ref, c.id)
    _, qa = world.job(t, "qa")
    v, smoke = world.proof(world.ticket(t.id), c, target)
    world.w.open_uat(world.actor("verification"), t.id, world.ticket(t.id).revision, qa, c.id, v.id, smoke.id)
    t = world.ticket(t.id)
    op = world.w.accept_uat(world.user, t.id, t.revision, c.id, 1, target.id, target.checksum, v.id, [*v.evidence_artifact_ids, smoke.id])
    world.w.finish_integration(world.actor("integrator"), t.id, world.ticket(t.id).revision, c.id, op["operation_id"], sha)
    return world.ticket(t.id)


def test_the_frozen_tip_stays_approvable_after_later_tickets_move_the_accepted_tip(world):
    accepted(world)  # tip = SHA_A
    _, entries = world.w.freeze_release_scope(world.user)
    release, target, receipt = draft(world, entries, tip=f.SHA_A)
    accept_on_new_tip(world, f.SHA_C)  # another ticket is accepted while the user reviews: the tip moves on
    with world.db.read() as s:
        project = s.get(Project, world.project.id)
        assert project.workflow["accepted_tip"] == f.SHA_C and project.workflow["tip_history"][-1] == f.SHA_A
        assert world.w.tip_known(project, f.SHA_A) and not world.w.tip_known(project, "9" * 40)
    assert approve(world, release, target, receipt).status == "approved"
    _, next_entries = world.w.freeze_release_scope(world.user)
    assert len(next_entries) == 1 and next_entries[0]["integrated_sha"] == f.SHA_C  # only the ticket accepted after the freeze


def test_a_release_of_a_tip_the_project_never_accepted_is_refused(world):
    accepted(world)
    _, entries = world.w.freeze_release_scope(world.user)
    with pytest.raises(Conflict, match="not a tip"):
        draft(world, entries, tip=f.SHA_C)


def test_frozen_tip_remains_approvable_when_bounded_tip_history_has_evicted_it(world):
    accepted(world)
    _, entries = world.w.freeze_release_scope(world.user)
    release, target, receipt = draft(world, entries, tip=f.SHA_A)
    accept_on_new_tip(world, f.SHA_C)
    with world.db.write() as s:
        p = s.get(Project, world.project.id)
        p.workflow = {**p.workflow, 'tip_history': []}  # equivalent to eviction after 2,000 later integrations
    assert approve(world, release, target, receipt).status == 'approved'


def test_a_target_that_does_not_identify_the_frozen_scope_cannot_be_drafted_or_approved(world):
    accepted(world)
    _, entries = world.w.freeze_release_scope(world.user)
    build = json_art(world, "build_record", {"build_digest": "d" * 64}, "builder")
    target = json_art(world, "target_manifest", {"accepted_tip": f.SHA_A, "build_artifact_id": build.id,
                                                  "scope_digest": world.w.scope_digest([])}, "builder")
    receipt = json_art(world, "report", {"kind": "release_verification", "status": "passed"})
    with pytest.raises(Conflict, match="frozen tip/build/scope"):
        world.w.draft_release(verification_actor(world), scope_snapshot=entries, accepted_tip=f.SHA_A,
            target_artifact_id=target.id, target_digest=target.checksum, build_artifact_id=build.id,
            evidence_ids=[receipt.id], verification_passed=True)


def test_a_failed_regression_is_recorded_with_its_evidence_and_can_never_be_approved(world):
    accepted(world)
    _, entries = world.w.freeze_release_scope(world.user)
    release, target, receipt = draft(world, entries, passed=False, tip=f.SHA_A)
    assert release.status == "failed" and release.evidence_artifact_ids == [receipt.id]
    with pytest.raises(Conflict):
        approve(world, release, target, receipt)
    world.w.freeze_release_scope(world.user)  # a failed draft does not block the next attempt


def test_approval_pins_target_and_evidence_so_a_changed_build_or_sha_needs_a_new_release(world):
    accepted(world)
    _, entries = world.w.freeze_release_scope(world.user)
    release, target, receipt = draft(world, entries, tip=f.SHA_A)
    rebuilt = json_art(world, "target_manifest", {"accepted_tip": f.SHA_A, "build_artifact_id": release.build_artifact_id,
                                                   "scope_digest": world.w.scope_digest(entries), "rebuild": 2}, "builder")
    with pytest.raises(Conflict):
        world.w.approve_release(world.user, release.id, release.revision, rebuilt.id, rebuilt.checksum, [receipt.id], [])
    with pytest.raises(Conflict):
        world.w.approve_release(world.user, release.id, release.revision, target.id, target.checksum, [], [])
    assert approve(world, release, target, receipt).status == "approved"
    with pytest.raises(Conflict):  # an approved release cannot be approved again
        world.w.approve_release(world.user, release.id, revision_of(world, release.id), target.id, target.checksum, [receipt.id], [])


def test_discard_and_replacement_rules(world):
    accepted(world)
    _, entries = world.w.freeze_release_scope(world.user)
    first, *_ = draft(world, entries, tip=f.SHA_A)
    with pytest.raises(Conflict, match="draft release already exists"):
        draft(world, entries, tip=f.SHA_A)
    second, *_ = draft(world, entries, tip=f.SHA_A, replaces=first.id)  # a replacement supersedes its draft
    with world.db.read() as s:
        assert s.get(Release, first.id).status == "failed" and s.get(Release, second.id).status == "draft"
    done = world.w.discard_release(world.user, second.id, revision_of(world, second.id))
    assert done.status == "failed"
    with pytest.raises(Conflict, match="only a draft"):
        world.w.discard_release(world.user, second.id, done.revision)


def export_receipt(world, release, **override):
    patch = json_art(world, "other", {"p": 1}, "integrator")
    bundle = json_art(world, "other", {"b": 1}, "integrator")
    return {"tip": release.accepted_tip, "pushed": False, "deployed": False, "patch_artifact_id": patch.id,
            "bundle_artifact_id": bundle.id, **override}


def test_export_receipt_is_integrator_only_requires_approval_and_never_claims_push_or_deploy(world):
    accepted(world)
    _, entries = world.w.freeze_release_scope(world.user)
    release, target, receipt = draft(world, entries, tip=f.SHA_A)
    integrator = world.actor("integrator")
    with pytest.raises(Conflict, match="approved"):
        world.w.record_export(integrator, release.id, release.revision, export_receipt(world, release))
    approved = approve(world, release, target, receipt)
    with pytest.raises(Forbidden):
        world.w.record_export(world.user, approved.id, approved.revision, export_receipt(world, approved))
    for bad in ({"pushed": True}, {"deployed": True}, {"tip": f.SHA_C}):
        with pytest.raises(Invalid):
            world.w.record_export(integrator, approved.id, approved.revision, export_receipt(world, approved, **bad))
    not_integrator = json_art(world, "other", {"x": 1}, "model")
    with pytest.raises(Invalid, match="integrator"):
        world.w.record_export(integrator, approved.id, approved.revision, export_receipt(world, approved, patch_artifact_id=not_integrator.id))
    done = world.w.record_export(integrator, approved.id, approved.revision, export_receipt(world, approved))
    assert done.status == "exported" and done.export_result["pushed"] is False and done.deployment_result is None
    with pytest.raises(Conflict):
        world.w.record_export(integrator, done.id, done.revision, export_receipt(world, done))
