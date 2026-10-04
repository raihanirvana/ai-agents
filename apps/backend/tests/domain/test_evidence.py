import pytest
from sqlalchemy import select
from app.domain import Conflict, Forbidden, Invalid
from app.persistence import ArtifactUnavailable
from app.persistence.models import Approval, Artifact, Candidate, Project, Release, Verification
from tests.persistence import factories as f
from .conftest import HASH, SCOPE


def json_art(world, kind, document, producer="verification"):
    with world.db.write() as s:
        return world.store.put_json(s, project_id=world.project.id, kind=kind, name="report.json",
            document=document, meta={"producer": producer})


@pytest.mark.parametrize("override", [{"fake_provider": True}, {"executed_test_ids": []},
    {"executed_test_ids": ["unrelated"]}, {"commands": []}, {"commands": [{"exit_code": 1}]},
    {"infrastructure_failure": True}, {"infrastructure_failure": None},
    {"executed_test_ids": [{"id": "mandatory-1"}]}, {"commands": [{"exit_code": False, "argv": ["test"]}]}])
def test_incomplete_or_fake_evidence_cannot_open_uat(world, override):
    t, c, target, attempt = world.qa()
    v, smoke = world.proof(t, c, target, **override)
    with pytest.raises(Invalid):
        world.w.open_uat(world.actor("verification"), t.id, t.revision, attempt, c.id, v.id, smoke.id)
    assert world.ticket(t.id).phase == "qa"


def test_agent_cannot_promote_qa_and_smoke_must_match_target(world):
    t, c, target, attempt = world.qa()
    v, smoke = world.proof(t, c, target)
    with pytest.raises(Forbidden):
        world.w.open_uat(world.actor("qa"), t.id, t.revision, attempt, c.id, v.id, smoke.id)
    wrong = json_art(world, "report", {"status": "passed", "kind": "preview_smoke",
        "target_artifact_id": "another-target", "target_digest": target.checksum})
    with pytest.raises(Invalid):
        world.w.open_uat(world.actor("verification"), t.id, t.revision, attempt, c.id, v.id, wrong.id)


def test_wrong_manifest_identity_rejected_without_attaching(world):
    t, c = world.submitted()
    with pytest.raises(Conflict):
        world.target(t, c, scope_version=2)
    with world.db.read() as s:
        assert s.get(Candidate, c.id).target_artifact_id is None


@pytest.mark.parametrize("which", ["target", "scope", "evidence", "verification"])
def test_uat_must_pin_exact_displayed_identity(world, which):
    t, c, target, v, ids = world.uat()
    args = [world.user, t.id, t.revision, c.id, t.current_version, target.id, target.checksum, v.id, ids]
    if which == "target":
        args[6] = "e" * 64
    elif which == "scope":
        args[4] += 1
    elif which == "evidence":
        args[8] = ids[:-1]
    else:
        args[7] = "missing"
    with pytest.raises(Conflict):
        world.w.accept_uat(*args)
    assert world.ticket(t.id).phase == "uat"
    with world.db.read() as s:
        assert not s.scalar(select(Approval.id).where(Approval.type == "uat"))


def test_manual_uac_confirmation_is_explicit(world):
    t = world.approve(world.new({**SCOPE, "uac": [*SCOPE["uac"], {"id": "MANUAL", "text": "Try UI", "mode": "manual"}]}))
    t, c, target, v, ids = world.uat(t)
    args = [world.user, t.id, t.revision, c.id, 1, target.id, target.checksum, v.id, ids]
    with pytest.raises(Invalid):
        world.w.accept_uat(*args)
    world.w.accept_uat(*args, manual_uac_ids=["MANUAL"])
    assert world.ticket(t.id).phase == "integrating"


def test_same_sha_rebuild_old_evidence_and_approval_do_not_transfer(world):
    t, c, old_target, v, ids = world.uat()
    op = world.w.accept_uat(world.user, t.id, t.revision, c.id, 1, old_target.id, old_target.checksum, v.id, ids)
    world.w.integration_diverged(world.actor("integrator"), t.id, world.ticket(t.id).revision,
        c.id, op["operation_id"], f.SHA_C)
    t, new_candidate = world.submitted(world.ticket(t.id))
    assert new_candidate.commit_sha == c.commit_sha and new_candidate.id != c.id
    new_target = world.target(t, new_candidate, config_digest="e" * 64)
    assert new_target.id != old_target.id and new_target.checksum != old_target.checksum
    lead, ref = world.job(t, "technical-lead")
    world.w.approve_review(lead, t.id, world.ticket(t.id).revision, ref, new_candidate.id)
    _, qa = world.job(t, "qa")
    new_v, smoke = world.proof(t, new_candidate, new_target)
    world.w.open_uat(world.actor("verification"), t.id, world.ticket(t.id).revision,
        qa, new_candidate.id, new_v.id, smoke.id)
    t = world.ticket(t.id)
    with pytest.raises(Conflict):
        world.w.accept_uat(world.user, t.id, t.revision, c.id, 1, old_target.id, old_target.checksum, v.id, ids)
    with pytest.raises(Conflict):
        world.w.accept_uat(world.user, t.id, t.revision, new_candidate.id, 1, new_target.id, new_target.checksum, v.id, ids)
    with world.db.read() as s:
        approvals = s.scalars(select(Approval).where(Approval.type == "uat")).all()
        assert len(approvals) == 1 and approvals[0].candidate_id == c.id
    world.w.accept_uat(world.user, t.id, t.revision, new_candidate.id, 1, new_target.id,
        new_target.checksum, new_v.id, [*new_v.evidence_artifact_ids, smoke.id])
    with world.db.read() as s:
        assert len(s.scalars(select(Approval).where(Approval.type == "uat")).all()) == 2


def test_base_movement_prevents_uat_and_requires_reconciled_new_attempt(world):
    t, c, target, v, ids = world.uat()
    with world.db.write() as s:
        p = s.get(Project, world.project.id)
        p.workflow = {**p.workflow, "accepted_tip": f.SHA_C}  # contract double for another integration
    with pytest.raises(Conflict, match="base changed"):
        world.w.accept_uat(world.user, t.id, t.revision, c.id, 1, target.id, target.checksum, v.id, ids)


def test_divergent_integration_receipt_never_accepts_old_candidate(world):
    t, c, op = world.integrating()
    world.w.integration_diverged(world.actor("integrator"), t.id, t.revision, c.id, op["operation_id"], f.SHA_C)
    current = world.ticket(t.id)
    assert current.phase == "development" and world.w.eligible(world.user, t.id)
    with world.db.read() as s:
        assert s.get(Candidate, c.id).status == "superseded"
        assert s.get(Candidate, c.id).integration["status"] == "diverged"
        assert s.get(Project, world.project.id).workflow["accepted_tip"] == f.SHA_C
    _, replacement = world.submitted(current)
    assert replacement.base_sha == f.SHA_C


def fingerprint(world, t, **override):
    return json_art(world, "report", {"kind": "baseline_failure", "category": "baseline",
        "ticket_id": t.id, "scope_version": t.current_version, "base_sha": f.SHA_B,
        "test_id": "existing-test", "signature": "failure-before-feature", "environment": "node:test",
        "environment_digest": HASH, "uac_ids": [], "infrastructure_failure": False, **override})


@pytest.mark.parametrize("override", [{"category": "uac"}, {"uac_ids": ["UAC-1"]},
    {"infrastructure_failure": True}, {"signature": ""}, {"environment_digest": ""}, {"base_sha": f.SHA_C},
    {"scope_version": 2}])
def test_baseline_waiver_cannot_cover_new_uac_or_infrastructure(world, override):
    t = world.approve(world.new())
    fp = fingerprint(world, t, **override)
    with pytest.raises((Invalid, Conflict)):
        world.w.waive_baseline(world.user, t.id, t.revision, fp.id, "Known failure")


def test_waiver_is_user_only_exact_and_explicitly_waived(world):
    t = world.approve(world.new())
    fp = fingerprint(world, t)
    with pytest.raises(Forbidden):
        world.w.waive_baseline(world.actor("verification"), t.id, t.revision, fp.id, "Known")
    ap = world.w.waive_baseline(world.user, t.id, t.revision, fp.id, "Existing baseline")
    assert ap.details["status"] == "waived"
    match = dict(ticket_id=t.id, scope_version=1, base_sha=f.SHA_B, environment_digest=HASH,
        test_id="existing-test", signature="failure-before-feature")
    assert world.w.waiver_matches(world.user, ap.id, **match)
    for k, value in [("scope_version", 2), ("base_sha", f.SHA_C), ("test_id", "another"), ("environment_digest", "e" * 64), ("signature", "new failure")]:
        assert not world.w.waiver_matches(world.user, ap.id, **{**match, k: value})


def release(world, **override):
    build = json_art(world, "build_record", {"build_digest": HASH}, "builder")
    target = json_art(world, "target_manifest", {"accepted_tip": f.SHA_B, "build_artifact_id": build.id}, "builder")
    receipt = json_art(world, "report", {"kind": "release_verification", "status": "passed",
        "target_artifact_id": target.id, "target_digest": target.checksum, "accepted_tip": f.SHA_B,
        "fake_provider": False, "infrastructure_failure": False, "expected_test_ids": ["regression"],
        "executed_test_ids": ["regression"], "counts": {"discovered": 1, "executed": 1, "passed": 1, "failed": 0, "skipped": 0},
        "commands": [{"exit_code": 0, "argv": ["contract-fixture"]}], **override})
    with world.db.write() as s:
        r = Release(project_id=world.project.id, scope_snapshot=[], accepted_tip=f.SHA_B,
            target_artifact_id=target.id, target_digest=target.checksum, build_artifact_id=build.id,
            evidence_artifact_ids=[receipt.id])
        s.add(r)
        s.flush()
    return r, target, receipt


@pytest.mark.parametrize("override", [{"fake_provider": True}, {"commands": []}, {"status": "failed"},
    {"target_digest": "e" * 64}, {"executed_test_ids": []}, {"expected_test_ids": []}, {"infrastructure_failure": True}])
def test_release_has_own_proof_not_ticket_approval(world, override):
    r, target, proof = release(world, **override)
    with pytest.raises(Invalid):
        world.w.approve_release(world.user, r.id, r.revision, target.id, target.checksum, [proof.id])


def test_release_approval_is_user_and_frozen_target_only(world):
    r, target, proof = release(world)
    with pytest.raises(Forbidden):
        world.w.approve_release(world.actor("integrator"), r.id, r.revision, target.id, target.checksum, [proof.id])
    with pytest.raises(Conflict):
        world.w.approve_release(world.user, r.id, r.revision, target.id, "e" * 64, [proof.id])
    approved = world.w.approve_release(world.user, r.id, r.revision, target.id, target.checksum, [proof.id])
    assert approved.status == "approved"


def test_missing_corrupt_artifact_cannot_produce_approval(world):
    t, c, target, v, ids = world.uat()
    with world.db.read() as s:
        path = world.store.resolve(s.get(Artifact, ids[0]).path)
    path.chmod(0o600)
    path.write_bytes(b"corrupt")
    with pytest.raises(ArtifactUnavailable):
        world.w.accept_uat(world.user, t.id, t.revision, c.id, 1, target.id, target.checksum, v.id, ids)
    assert world.ticket(t.id).phase == "uat"


def test_old_qa_generation_cannot_publish_into_renewed_attempt(world):
    from app.domain import Attempt
    from app.persistence.models import Job
    t, c, target, old = world.qa()
    proof, smoke = world.proof(t, c, target)
    with world.db.write() as s:
        job = s.get(Job, old.job_id)
        job.lease_generation = 2
    renewed = Attempt(old.job_id, 2, old.scope_version)
    world.w.bind_attempt(world.actor("scheduler"), t.id, t.revision, renewed)
    with pytest.raises(Conflict, match="stale QA"):
        world.w.open_uat(world.actor("verification"), t.id, world.ticket(t.id).revision,
            renewed, c.id, proof.id, smoke.id)
    assert world.ticket(t.id).phase == "qa"


def test_builder_cannot_attach_result_after_source_generation_revoked(world):
    from app.persistence.models import Job
    t, c = world.submitted()
    with world.db.write() as s:
        s.get(Job, c.job_id).lease_generation += 1
    with pytest.raises(Conflict, match="stale source"):
        world.target(t, c)


def test_qa_suite_must_match_runner_in_immutable_target(world):
    t, c, target, attempt = world.qa()
    # Persisted verification rows are immutable: publish another synthetic receipt
    # with a different suite, rather than changing an existing receipt.
    v, smoke = world.proof(t, c, target)
    with world.db.write() as s:
        wrong = Verification(candidate_id=c.id, target_artifact_id=target.id, target_digest=target.checksum,
            commit_artifact_id=v.commit_artifact_id, build_artifact_id=v.build_artifact_id,
            context_artifact_id=v.context_artifact_id, evidence_id="different-suite", suite_digest="e" * 64,
            expected_test_ids=v.expected_test_ids, counts=v.counts, uac_coverage=v.uac_coverage,
            status="passed", evidence_artifact_ids=v.evidence_artifact_ids, results=v.results)
        s.add(wrong)
        s.flush()
    with pytest.raises(Conflict, match="suite"):
        world.w.open_uat(world.actor("verification"), t.id, t.revision, attempt, c.id, wrong.id, smoke.id)
