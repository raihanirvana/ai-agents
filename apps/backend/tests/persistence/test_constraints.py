"""Storage-level rules: the database, not callers, refuses these writes."""
from __future__ import annotations

import pytest
from sqlalchemy import delete, text, update

from app.persistence.models import (
    Approval, Artifact, Candidate, Dependency, Event, Job, Message, Project, Release, Ticket,
    TicketVersion, Verification,
)

from . import factories as f


def run(db, work):
    with db.write() as session:
        return work(session)


# --- projects, tickets, scope versions ---------------------------------------------------
def test_project_rules(db, rejected):
    rejected(db, lambda s: f.project(s, mode="existing"), "existing_has_repo")
    rejected(db, lambda s: f.project(s, mode="fork"), "mode")
    rejected(db, lambda s: f.project(s, name="  "), "name")
    run(db, lambda s: f.project(s, mode="existing", repo_ref="/repos/shop"))


def test_ticket_numbers_unique_per_project_and_phase_valid(db, world, rejected):
    rejected(db, lambda s: f.ticket(s, world.project, number=world.ticket.number), "UNIQUE")
    rejected(db, lambda s: s.add(Ticket(project_id=world.project.id, number=90, title="t", phase="done")) or s.flush(), "phase")
    # Past draft a ticket must have a scope version.
    rejected(db, lambda s: s.add(Ticket(project_id=world.project.id, number=91, title="t", phase="ready")) or s.flush(),
             "phase_has_version")


def test_ticket_current_version_must_exist_and_only_moves_forward(db, world, rejected):
    def point_at(version):
        def work(s):
            s.execute(f.bump(Ticket, world.ticket.id, current_version=version))
        return work

    rejected(db, point_at(5), "existing ticket version")
    rejected(db, lambda s: s.add(Ticket(project_id=world.project.id, number=92, title="t", current_version=1)) or s.flush(),
             "existing ticket version")
    run(db, lambda s: s.add(TicketVersion(ticket_id=world.ticket.id, version=2, title="v2", uac=[],
                                          content_digest="2" * 64, created_by="po")))
    run(db, point_at(2))
    rejected(db, point_at(1), "only move forward")
    rejected(db, point_at(None), "only move forward")


def test_ticket_identity_is_frozen(db, world, rejected):
    rejected(db, lambda s: s.execute(f.bump(Ticket, world.ticket.id, number=99)), "immutable")


def test_ticket_versions_are_immutable_and_unique(db, world, rejected):
    version = run(db, lambda s: s.query(TicketVersion).filter_by(ticket_id=world.ticket.id).one())
    rejected(db, lambda s: s.execute(update(TicketVersion).where(TicketVersion.id == version.id).values(title="edited")),
             "immutable")
    rejected(db, lambda s: s.execute(delete(TicketVersion).where(TicketVersion.id == version.id)), "cannot be deleted")
    rejected(db, lambda s: s.add(TicketVersion(ticket_id=world.ticket.id, version=1, title="dup", uac=[],
                                               content_digest="9" * 64, created_by="po")) or s.flush(), "UNIQUE")
    rejected(db, lambda s: s.add(TicketVersion(ticket_id=world.ticket.id, version=3, title="bad", uac={},
                                               content_digest="9" * 64, created_by="po")) or s.flush(), "uac_json")


def test_history_tables_cannot_be_deleted(db, world, rejected):
    rejected(db, lambda s: s.execute(delete(Ticket).where(Ticket.id == world.ticket.id)), "cannot be deleted")
    rejected(db, lambda s: s.execute(delete(Project).where(Project.id == world.project.id)), "cannot be deleted")


# --- approvals -------------------------------------------------------------------------------
def test_approval_shape_depends_on_type(db, world, rejected):
    base = dict(project_id=world.project.id, user_id="user:local")

    def add(**kw):
        return lambda s: s.add(Approval(**base, **kw)) or s.flush()

    # BEFORE INSERT triggers run ahead of CHECKs, so a malformed row is refused by whichever fires first.
    rejected(db, add(type="scope"), "type_requirements|current version")
    rejected(db, add(type="uat"), "type_requirements|verified candidate")
    rejected(db, add(type="release"), "type_requirements|draft release")
    rejected(db, add(type="baseline_waiver"), "type_requirements")
    rejected(db, add(type="maybe"), "ck_approvals_type")
    rejected(db, lambda s: s.add(Approval(project_id=world.project.id, type="scope", user_id=" ",
                                          ticket_id=world.ticket.id, scope_version=1)) or s.flush(), "user_id")


def test_scope_approval_names_the_current_version_once(db, world, rejected):
    run(db, lambda s: s.add(TicketVersion(ticket_id=world.ticket.id, version=2, title="v2", uac=[],
                                          content_digest="2" * 64, created_by="po")))
    run(db, lambda s: s.execute(f.bump(Ticket, world.ticket.id, current_version=2)))

    def approve(version):
        return lambda s: s.add(Approval(project_id=world.project.id, type="scope", user_id="user:local",
                                        ticket_id=world.ticket.id, scope_version=version)) or s.flush()

    rejected(db, approve(1), "current version")  # approval of a superseded version
    rejected(db, approve(7), "current version")  # a version that does not exist
    run(db, approve(2))
    rejected(db, approve(2), "UNIQUE")


def test_approval_history_is_immutable(db, world, rejected):
    approval = run(db, lambda s: s.add(Approval(project_id=world.project.id, type="scope", user_id="user:local",
                                                ticket_id=world.ticket.id, scope_version=1)) or s.query(Approval).one())
    rejected(db, lambda s: s.execute(update(Approval).where(Approval.id == approval.id).values(user_id="agent:po")), "immutable")
    rejected(db, lambda s: s.execute(delete(Approval).where(Approval.id == approval.id)), "cannot be deleted")


def test_uat_approval_requires_verified_candidate_and_matching_digest(db, store, world, rejected):
    def prepare(s):
        cand = f.candidate(s, world.project, world.ticket)
        target = f.artifact(s, store, world.project, kind="target_manifest", data=b"{}", name="t.json")
        cand.target_artifact_id, cand.target_digest = target.id, target.checksum
        s.flush()
        return cand, target

    cand, target = run(db, prepare)
    # Not verified yet.
    rejected(db, lambda s: f.uat_approval(s, world.project, world.ticket, cand, target), "verified candidate")
    rejected(db, lambda s: s.add(Approval(project_id=world.project.id, type="uat", user_id="user:local",
                                          ticket_id=world.ticket.id, scope_version=1, candidate_id=cand.id,
                                          target_artifact_id=target.id, target_digest="0" * 64)) or s.flush(),
             "verified candidate")
    # A wrong digest for the artifact id is refused by the composite foreign key on its own
    # (baseline waivers have no trigger in front of it).
    rejected(db, lambda s: s.add(Approval(project_id=world.project.id, type="baseline_waiver", user_id="user:local",
                                          target_artifact_id=target.id, target_digest="0" * 64)) or s.flush(),
             "FOREIGN KEY")
    run(db, lambda s: s.add(Approval(project_id=world.project.id, type="baseline_waiver", user_id="user:local",
                                     target_artifact_id=target.id, target_digest=target.checksum,
                                     details={"base_sha": f.SHA_B, "scope": "UAC-1"})))


def test_evidence_must_exist_be_available_and_belong_to_the_project(db, store, world, rejected):
    def prepare(s):
        cand, target, evidence = f.verified_candidate(s, store, world.project, world.ticket)
        other = f.project(s, name="Other")
        foreign = f.artifact(s, store, other, name="foreign.txt")
        return cand, target, evidence, foreign

    cand, target, evidence, foreign = run(db, prepare)
    attempt = lambda ids: (lambda s: f.uat_approval(s, world.project, world.ticket, cand, target, ids))
    rejected(db, attempt(["no-such-artifact"]), "missing, unavailable or belongs")
    rejected(db, attempt([foreign.id]), "missing, unavailable or belongs")
    run(db, lambda s: s.execute(update(Artifact).where(Artifact.id == evidence.id)
                                .values(availability="unavailable", unavailable_reason="corrupt")))
    rejected(db, attempt([evidence.id]), "missing, unavailable or belongs")


# --- candidates, verifications, UAT, accepted --------------------------------------------------
def test_candidate_must_start_submitted_and_commit_ids_are_hashes(db, world, rejected):
    rejected(db, lambda s: f.candidate(s, world.project, world.ticket, status="verified"), "starts as submitted")
    rejected(db, lambda s: f.candidate(s, world.project, world.ticket, commit_sha="main"), "commit_sha")
    rejected(db, lambda s: f.candidate(s, world.project, world.ticket, base_sha="G" * 40), "base_sha")
    run(db, lambda s: f.candidate(s, world.project, world.ticket, idempotency_key="submit-1"))
    rejected(db, lambda s: f.candidate(s, world.project, world.ticket, idempotency_key="submit-1"), "UNIQUE")


def test_candidate_identity_is_frozen(db, world, rejected):
    cand = run(db, lambda s: f.candidate(s, world.project, world.ticket))
    for column, value in (("commit_sha", f.SHA_C), ("base_sha", f.SHA_C), ("scope_version", 1)):
        if column == "scope_version":
            continue  # same value is not a change
        rejected(db, lambda s: s.execute(f.bump(Candidate, cand.id, **{column: value})),
                 "immutable")


def test_verified_needs_a_passed_verification_for_the_current_target(db, store, world, rejected):
    def prepare(s):
        cand = f.candidate(s, world.project, world.ticket)
        target = f.artifact(s, store, world.project, kind="target_manifest", data=b"{}", name="t.json")
        cand.target_artifact_id, cand.target_digest = target.id, target.checksum
        s.flush()
        return cand, target

    cand, target = run(db, prepare)
    set_status = lambda s: s.execute(f.bump(Candidate, cand.id, status="verified"))
    rejected(db, set_status, "passed verification")

    def failed_run(s):
        s.add(Verification(candidate_id=cand.id, target_artifact_id=target.id, target_digest=target.checksum,
                           evidence_id="e-failed", suite_digest="d" * 64, expected_test_ids=["t"],
                           counts={"discovered": 1, "executed": 1, "passed": 0, "failed": 1, "skipped": 0},
                           uac_coverage={}, status="failed", results={}, evidence_artifact_ids=[]))
        s.flush()
        set_status(s)

    rejected(db, failed_run, "passed verification")


@pytest.mark.parametrize("counts, evidence_count, expected", [
    ({"discovered": 0, "executed": 0, "passed": 0, "failed": 0, "skipped": 0}, 1, "passed_has_execution"),
    ({"discovered": 2, "executed": 1, "passed": 1, "failed": 0, "skipped": 1}, 1, "passed_has_execution"),
    ({"discovered": 2, "executed": 2, "passed": 1, "failed": 1, "skipped": 0}, 1, "passed_has_execution"),
    ({"executed": 2, "passed": 2}, 1, "passed_has_execution"),
    ({"discovered": 2, "executed": 2, "passed": 2, "failed": 0, "skipped": 0}, 0, "passed_has_execution"),
])
def test_passed_verification_requires_real_execution(db, store, world, rejected, counts, evidence_count, expected):
    def prepare(s):
        cand = f.candidate(s, world.project, world.ticket)
        target = f.artifact(s, store, world.project, kind="target_manifest", data=b"{}", name="t.json")
        cand.target_artifact_id, cand.target_digest = target.id, target.checksum
        s.flush()
        return cand, f.artifact(s, store, world.project, name="e.json")

    cand, evidence = run(db, prepare)
    rejected(db, lambda s: f.verification(s, cand, counts=counts, evidence_artifact_ids=[evidence.id][:evidence_count]),
             expected)


def test_incomplete_and_failed_verifications_are_recordable_and_immutable(db, store, world, rejected):
    def prepare(s):
        cand = f.candidate(s, world.project, world.ticket)
        target = f.artifact(s, store, world.project, kind="target_manifest", data=b"{}", name="t.json")
        cand.target_artifact_id, cand.target_digest = target.id, target.checksum
        s.flush()
        f.verification(s, cand, status="incomplete",
                       counts={"discovered": 0, "executed": 0, "passed": 0, "failed": 0, "skipped": 0})
        f.verification(s, cand, status="failed",
                       counts={"discovered": 2, "executed": 2, "passed": 1, "failed": 1, "skipped": 0})

    run(db, prepare)
    rejected(db, lambda s: s.execute(update(Verification).values(status="passed")), "immutable")
    rejected(db, lambda s: s.execute(delete(Verification)), "cannot be deleted")


def test_verification_must_snapshot_the_current_target_and_artifacts(db, store, world, rejected):
    cand, target, evidence = run(db, lambda s: f.verified_candidate(s, store, world.project, world.ticket))
    other = run(db, lambda s: f.artifact(s, store, world.project, kind="build_record", data=b"other", name="o.json"))

    def forged(**changes):
        def work(s):
            row = f.Verification(
                candidate_id=cand.id, target_artifact_id=target.id, target_digest=target.checksum,
                commit_artifact_id=cand.commit_artifact_id, build_artifact_id=cand.build_artifact_id,
                context_artifact_id=cand.context_artifact_id, evidence_id=f"e-{len(changes)}-{id(changes)}",
                suite_digest="d" * 64, expected_test_ids=["t"], counts={"discovered": 1, "executed": 1, "passed": 1,
                                                                        "failed": 0, "skipped": 0},
                uac_coverage={}, status="passed", results={}, evidence_artifact_ids=[evidence.id])
            for key, value in changes.items():
                setattr(row, key, value)
            s.add(row)
            s.flush()
        return work

    for changes in ({"build_artifact_id": other.id}, {"build_artifact_id": None}, {"context_artifact_id": None},
                    {"commit_artifact_id": None}):
        rejected(db, forged(**changes), "snapshot")
    run(db, forged())  # the exact snapshot is accepted


def test_accepted_needs_uat_approval_for_the_current_target(db, store, world, rejected):
    cand, target, evidence = run(db, lambda s: f.verified_candidate(s, store, world.project, world.ticket))
    accept = lambda s: s.execute(f.bump(Candidate, cand.id,
        status="accepted", integrated_sha=f.SHA_C))
    rejected(db, accept, "UAT approval")
    run(db, lambda s: f.uat_approval(s, world.project, world.ticket, cand, target, [evidence.id]))
    rejected(db, lambda s: s.execute(f.bump(Candidate, cand.id, status="accepted")),
             "accepted_integrated")
    run(db, accept)
    # Accepted is final.
    rejected(db, lambda s: s.execute(f.bump(Candidate, cand.id, status="superseded")), "final")


def test_same_sha_rebuilt_needs_new_target_qa_and_uat(db, store, world, rejected):
    """AGENTS.md: a rebuild is a new target; the old UAT approval does not carry over."""
    cand, target, evidence = run(db, lambda s: f.verified_candidate(s, store, world.project, world.ticket))
    run(db, lambda s: f.uat_approval(s, world.project, world.ticket, cand, target, [evidence.id]))

    def rebuild(s):
        new_target = f.artifact(s, store, world.project, kind="target_manifest", data=b'{"rebuild":1}', name="t2.json")
        return new_target

    new_target = run(db, rebuild)
    # The verified/accepted target cannot be swapped in place...
    rejected(db, lambda s: s.execute(f.bump(Candidate, cand.id,
        target_artifact_id=new_target.id, target_digest=new_target.checksum)), "new QA and UAT")
    # ...it has to leave 'verified' first, and then verification and approval start over.
    run(db, lambda s: s.execute(f.bump(Candidate, cand.id,
        status="review_approved", target_artifact_id=new_target.id, target_digest=new_target.checksum)))
    rejected(db, lambda s: s.execute(f.bump(Candidate, cand.id, status="verified")),
             "passed verification")
    # Even with a passed verification of the new target, the OLD approval is not enough.
    def verify_new(s):
        evidence2 = f.artifact(s, store, world.project, name="e2.json")
        f.verification(s, cand, evidence_artifact_ids=[evidence2.id])
        s.execute(f.bump(Candidate, cand.id, status="verified"))

    run(db, verify_new)
    rejected(db, lambda s: s.execute(f.bump(Candidate, cand.id,
        status="accepted", integrated_sha=f.SHA_C)), "UAT approval")


def test_only_a_verified_candidate_can_be_accepted(db, world, rejected):
    cand = run(db, lambda s: f.candidate(s, world.project, world.ticket))
    rejected(db, lambda s: s.execute(f.bump(Candidate, cand.id,
        status="accepted", integrated_sha=f.SHA_C)), "verified candidate")


def test_candidate_cannot_reference_unavailable_artifacts(db, store, world, rejected):
    def prepare(s):
        build = f.artifact(s, store, world.project, kind="build_record", data=b"{}", name="build.json")
        s.execute(update(Artifact).where(Artifact.id == build.id).values(availability="unavailable",
                                                                         unavailable_reason="missing"))
        return build

    build = run(db, prepare)
    rejected(db, lambda s: f.candidate(s, world.project, world.ticket, build_artifact_id=build.id), "unavailable")
    cand = run(db, lambda s: f.candidate(s, world.project, world.ticket))
    rejected(db, lambda s: s.execute(f.bump(Candidate, cand.id, build_artifact_id=build.id)),
             "unavailable")


# --- releases ------------------------------------------------------------------------------------
def test_release_needs_approval_for_exactly_its_target(db, store, world, rejected):
    release, target = run(db, lambda s: f.draft_release(s, store, world.project))
    approve = lambda s: s.execute(f.bump(Release, release.id, status="approved"))
    rejected(db, approve, "approval for exactly this target")
    rejected(db, lambda s: f.draft_release(s, store, world.project, status="approved"), "starts as draft")

    other = run(db, lambda s: f.artifact(s, store, world.project, kind="target_manifest", data=b'{"x":1}', name="o.json"))
    rejected(db, lambda s: s.add(Approval(project_id=world.project.id, type="release", user_id="user:local",
                                          release_id=release.id, target_artifact_id=other.id,
                                          target_digest=other.checksum)) or s.flush(), "same target")
    run(db, lambda s: s.add(Approval(project_id=world.project.id, type="release", user_id="user:local",
                                     release_id=release.id, target_artifact_id=target.id,
                                     target_digest=target.checksum)))
    run(db, approve)
    # The frozen scope and target never change.
    rejected(db, lambda s: s.execute(f.bump(Release, release.id, accepted_tip=f.SHA_A)), "immutable")
    rejected(db, lambda s: s.execute(f.bump(Release, release.id,
        target_artifact_id=other.id, target_digest=other.checksum)), "immutable")


# --- jobs, messages, dependencies, artifacts, events -------------------------------------------------
def test_job_rules(db, world, rejected):
    rejected(db, lambda s: f.job(s, world.project, lane="batch"), "lane")
    rejected(db, lambda s: f.job(s, world.project, status="running"), "running_has_lease")
    rejected(db, lambda s: f.job(s, world.project, status="waiting_input"), "waiting_has_request")
    rejected(db, lambda s: s.add(Job(project_id=world.project.id, ticket_id=world.ticket.id, lane="execution",
                                     stage="dev", idempotency_key="k")) or s.flush(), "scope_pair")
    rejected(db, lambda s: s.add(Job(project_id=world.project.id, ticket_id=world.ticket.id, scope_version=9,
                                     lane="execution", stage="dev", idempotency_key="k2")) or s.flush(), "FOREIGN KEY")
    run(db, lambda s: f.job(s, world.project, world.ticket, idempotency_key="same"))
    rejected(db, lambda s: f.job(s, world.project, world.ticket, idempotency_key="same"), "UNIQUE")


def test_lease_generation_never_decreases(db, world, rejected):
    job = run(db, lambda s: f.job(s, world.project, lease_generation=3))
    rejected(db, lambda s: s.execute(update(Job).where(Job.id == job.id).values(lease_generation=2)), "cannot decrease")
    run(db, lambda s: s.execute(update(Job).where(Job.id == job.id).values(lease_generation=4)))


def test_messages_are_immutable_ordered_and_idempotent(db, world, rejected):
    def add(seq, **kw):
        return lambda s: s.add(Message(project_id=world.project.id, thread_id="th", seq=seq, sender="user",
                                       body="hi", **kw)) or s.flush()

    run(db, add(1, idempotency_key="k"))
    rejected(db, add(1), "UNIQUE")
    rejected(db, add(2, idempotency_key="k"), "UNIQUE")
    rejected(db, add(2, kind="input_answer"), "answer_replies")
    rejected(db, lambda s: s.execute(update(Message).values(body="edited")), "immutable")
    rejected(db, lambda s: s.execute(delete(Message)), "cannot be deleted")


def test_one_answer_per_input_request(db, world, rejected):
    def seed(s):
        request = Message(project_id=world.project.id, thread_id="th", seq=1, sender="agent:developer",
                          kind="input_request", body="remove at zero?", meta={"generation": 1})
        s.add(request)
        s.flush()
        s.add(Message(project_id=world.project.id, thread_id="th", seq=2, sender="user", kind="input_answer",
                      body="yes", reply_to=request.id))
        s.flush()
        return request

    request = run(db, seed)
    rejected(db, lambda s: s.add(Message(project_id=world.project.id, thread_id="th", seq=3, sender="user",
                                         kind="input_answer", body="no", reply_to=request.id)) or s.flush(), "UNIQUE")


def test_waiting_job_points_at_a_persisted_request(db, world):
    def seed(s):
        request = Message(project_id=world.project.id, thread_id="th", seq=1, sender="agent", kind="input_request", body="?")
        s.add(request)
        s.flush()
        return f.job(s, world.project, world.ticket, status="waiting_input", waiting_request_id=request.id)

    assert run(db, seed).status == "waiting_input"


def test_dependency_rules(db, world, rejected):
    other = run(db, lambda s: f.ticket(s, world.project))
    dep = lambda **kw: (lambda s: s.add(Dependency(ticket_id=world.ticket.id, depends_on_ticket_id=other.id, **kw)) or s.flush())
    rejected(db, lambda s: s.add(Dependency(ticket_id=other.id, depends_on_ticket_id=other.id)) or s.flush(), "not_self")
    rejected(db, dep(state="satisfied"), "satisfied_has_pin")
    rejected(db, dep(accepted_scope_version=1), "pin_complete")
    run(db, dep())
    rejected(db, dep(), "UNIQUE")


def test_dependency_pins_accepted_version_candidate_and_sha(db, store, world):
    def seed(s):
        upstream = f.ticket(s, world.project)
        cand, target, evidence = f.verified_candidate(s, store, world.project, upstream)
        f.uat_approval(s, world.project, upstream, cand, target, [evidence.id])
        s.execute(f.bump(Candidate, cand.id, status="accepted", integrated_sha=f.SHA_C))
        s.add(Dependency(ticket_id=world.ticket.id, depends_on_ticket_id=upstream.id, state="satisfied",
                         accepted_scope_version=1, accepted_candidate_id=cand.id, integration_sha=f.SHA_C))
        s.flush()

    run(db, seed)


def test_artifact_rules(db, store, world, rejected):
    art = run(db, lambda s: f.artifact(s, store, world.project))
    rejected(db, lambda s: s.execute(update(Artifact).where(Artifact.id == art.id).values(checksum="1" * 64)), "immutable")
    rejected(db, lambda s: s.execute(update(Artifact).where(Artifact.id == art.id).values(path="x/y/z")), "immutable")
    rejected(db, lambda s: s.execute(delete(Artifact).where(Artifact.id == art.id)), "cannot be deleted")
    rejected(db, lambda s: s.execute(update(Artifact).where(Artifact.id == art.id).values(availability="unavailable")),
             "ck_artifacts_availability")
    rejected(db, lambda s: s.add(Artifact(project_id=world.project.id, kind="log", storage="file", path=None,
                                          checksum="1" * 64, size_bytes=1)) or s.flush(), "storage_shape")
    rejected(db, lambda s: s.add(Artifact(project_id=world.project.id, kind="log", storage="file", path="p/a/b.txt",
                                          checksum="1" * 64, size_bytes=None)) or s.flush(), "storage_shape")
    rejected(db, lambda s: s.add(Artifact(project_id=world.project.id, kind="log", storage="git", path=None,
                                          checksum="xyz")) or s.flush(), "checksum")
    rejected(db, lambda s: s.add(Artifact(project_id=world.project.id, kind="movie", storage="git", path=None,
                                          checksum=f.SHA_A)) or s.flush(), "kind")


def test_cleaned_artifact_cannot_become_available_again(db, store, world, rejected):
    art = run(db, lambda s: f.artifact(s, store, world.project))
    run(db, lambda s: s.execute(update(Artifact).where(Artifact.id == art.id).values(
        availability="unavailable", unavailable_reason="cleaned")))
    rejected(db, lambda s: s.execute(update(Artifact).where(Artifact.id == art.id).values(
        availability="available", unavailable_reason=None)), "cleaned artifact")


def test_events_are_append_only(db, world, rejected):
    event = run(db, lambda s: s.add(Event(project_id=world.project.id, type="x.y", actor="user")) or s.query(Event).one())
    rejected(db, lambda s: s.execute(update(Event).where(Event.cursor == event.cursor).values(type="z")), "immutable")
    rejected(db, lambda s: s.execute(delete(Event)), "cannot be deleted")
    rejected(db, lambda s: s.add(Event(project_id=world.project.id, type=" ", actor="user")) or s.flush(), "type_actor")
    rejected(db, lambda s: s.add(Event(project_id="missing", type="x", actor="user")) or s.flush(), "FOREIGN KEY")


def test_json_columns_are_validated(db, world, rejected):
    rejected(db, lambda s: s.execute(text(
        "INSERT INTO events (project_id, type, actor, payload, created_at) "
        f"VALUES ('{world.project.id}', 'x', 'u', 'not json', '2026-01-01')")), "payload_json")
    rejected(db, lambda s: s.execute(text(
        "INSERT INTO events (project_id, type, actor, payload, created_at) "
        f"VALUES ('{world.project.id}', 'x', 'u', '[1]', '2026-01-01')")), "payload_json")


def test_release_requires_a_build_and_freezes_its_snapshot(db, store, world, rejected):
    release, target = run(db, lambda s: f.draft_release(s, store, world.project))
    other = run(db, lambda s: f.artifact(s, store, world.project, kind="build_record", data=b"x", name="x.json"))
    rejected(db, lambda s: s.add(Release(
        project_id=world.project.id, scope_snapshot=[], accepted_tip=f.SHA_C, target_artifact_id=target.id,
        target_digest=target.checksum, build_artifact_id=None, evidence_artifact_ids=[])) or s.flush(), "NOT NULL")
    for column in ("build_artifact_id", "commit_artifact_id", "context_artifact_id"):
        rejected(db, lambda s: s.execute(f.bump(Release, release.id, **{column: other.id})), "immutable")


def test_release_cannot_reference_unavailable_or_foreign_build_artifacts(db, store, world, rejected):
    def prepare(s):
        gone = f.artifact(s, store, world.project, kind="build_record", data=b"g", name="g.json")
        s.execute(update(Artifact).where(Artifact.id == gone.id).values(availability="unavailable",
                                                                         unavailable_reason="missing"))
        foreign = f.artifact(s, store, f.project(s, name="other"), kind="build_record", data=b"f", name="f.json")
        return gone, foreign

    gone, foreign = run(db, prepare)
    for build in (gone, foreign):
        rejected(db, lambda s: f.draft_release(s, store, world.project, build=build),
                 "missing, unavailable or belongs")
    context = run(db, lambda s: f.artifact(s, store, world.project, kind="context", data=b"c", name="c.json"))
    run(db, lambda s: s.execute(update(Artifact).where(Artifact.id == context.id).values(
        availability="unavailable", unavailable_reason="corrupt")))
    rejected(db, lambda s: f.draft_release(s, store, world.project, context_artifact_id=context.id),
             "missing, unavailable or belongs")
