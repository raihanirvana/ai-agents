"""Row builders for persistence tests. They follow the real lifecycle so triggers pass."""
from __future__ import annotations

import itertools

from sqlalchemy import update

from app.persistence.models import (
    Approval, Candidate, Job, Project, Release, Ticket, TicketVersion, Verification,
)

SHA_A, SHA_B, SHA_C = "a" * 40, "b" * 40, "c" * 40
_seq = itertools.count(1)


def project(session, **kw) -> Project:
    row = Project(name=kw.pop("name", "Coffee shop"), mode=kw.pop("mode", "new"), **kw)
    session.add(row)
    session.flush()
    return row


def ticket(session, proj, *, number=None, versions=1, phase="scope_review", **kw) -> Ticket:
    """A ticket with `versions` immutable scope versions; current_version points at the last."""
    row = Ticket(project_id=proj.id, number=number or next(_seq), title=kw.pop("title", "Menu"), **kw)
    session.add(row)
    session.flush()
    for version in range(1, versions + 1):
        session.add(TicketVersion(ticket_id=row.id, version=version, title=row.title, uac=[{"id": "UAC-1", "text": "x"}],
                                  content_digest=f"{version:064x}", created_by="user:local"))
        session.flush()
        row.current_version = version
        session.flush()
    if versions:
        row.phase = phase
        session.flush()
    return row


def artifact(session, store, proj, *, kind="evidence", data=b"evidence", name="evidence.txt", **kw):
    return store.put_bytes(session, project_id=proj.id, kind=kind, data=data, name=name, **kw)


def job(session, proj, tick=None, **kw) -> Job:
    row = Job(project_id=proj.id, ticket_id=tick.id if tick else None,
              scope_version=tick.current_version if tick else None, lane=kw.pop("lane", "execution"),
              stage=kw.pop("stage", "development"), idempotency_key=kw.pop("idempotency_key", f"job-{next(_seq)}"), **kw)
    session.add(row)
    session.flush()
    return row


def candidate(session, proj, tick, **kw) -> Candidate:
    row = Candidate(project_id=proj.id, ticket_id=tick.id, scope_version=tick.current_version,
                    idempotency_key=kw.pop("idempotency_key", f"cand-{next(_seq)}"),
                    commit_sha=kw.pop("commit_sha", SHA_A), base_sha=kw.pop("base_sha", SHA_B), **kw)
    session.add(row)
    session.flush()
    return row


def verification(session, cand, **kw) -> Verification:
    """A verification that snapshots the candidate's CURRENT target, commit, build and context."""
    cand = session.get(Candidate, cand.id, populate_existing=True)
    row = Verification(
        candidate_id=cand.id, target_artifact_id=cand.target_artifact_id, target_digest=cand.target_digest,
        commit_artifact_id=cand.commit_artifact_id, build_artifact_id=cand.build_artifact_id,
        context_artifact_id=cand.context_artifact_id, evidence_id=kw.pop("evidence_id", f"e2e-{next(_seq)}"),
        suite_digest="d" * 64, expected_test_ids=kw.pop("expected_test_ids", ["t1", "t2"]),
        counts=kw.pop("counts", {"discovered": 2, "executed": 2, "passed": 2, "failed": 0, "skipped": 0}),
        uac_coverage={"UAC-1": ["t1", "t2"]}, status=kw.pop("status", "passed"), results={"commands": []},
        evidence_artifact_ids=kw.pop("evidence_artifact_ids", []), **kw)
    session.add(row)
    session.flush()
    return row


def verified_candidate(session, store, proj, tick, **kw):
    """Candidate with commit/build/context -> target manifest -> passed verification -> verified."""
    sha = kw.pop("commit_sha", SHA_A)
    commit = store.put_git_commit(session, project_id=proj.id, sha=sha)  # identity column: set at insert
    cand = candidate(session, proj, tick, commit_sha=sha, commit_artifact_id=commit.id, **kw)
    target = store.put_json(session, project_id=proj.id, kind="target_manifest",
                            document={"candidate": sha, "n": next(_seq)}, name="target.json")
    build = artifact(session, store, proj, kind="build_record", data=b"build", name=f"build-{next(_seq)}.json")
    context = artifact(session, store, proj, kind="context", data=b"context", name=f"context-{next(_seq)}.json")
    cand.target_artifact_id, cand.target_digest = target.id, target.checksum
    cand.build_artifact_id, cand.context_artifact_id = build.id, context.id
    session.flush()
    evidence = artifact(session, store, proj, name=f"e2e-{next(_seq)}.json")
    verification(session, cand, evidence_artifact_ids=[evidence.id])
    cand.status = "verified"
    session.flush()
    return cand, target, evidence


def uat_approval(session, proj, tick, cand, target, evidence_ids=()) -> Approval:
    row = Approval(project_id=proj.id, type="uat", user_id="user:local", ticket_id=tick.id,
                   scope_version=tick.current_version, candidate_id=cand.id, target_artifact_id=target.id,
                   target_digest=target.checksum, evidence_artifact_ids=list(evidence_ids))
    session.add(row)
    session.flush()
    return row


def draft_release(session, store, proj, **kw) -> tuple[Release, object]:
    target = store.put_json(session, project_id=proj.id, kind="target_manifest",
                            document={"release": next(_seq)}, name="release-target.json")
    build = kw.pop("build", None) or artifact(session, store, proj, kind="build_record", data=b"release-build",
                                               name=f"release-build-{next(_seq)}.json")
    row = Release(project_id=proj.id, scope_snapshot=[{"ticket": "t", "version": 1}], accepted_tip=SHA_C,
                  target_artifact_id=target.id, target_digest=target.checksum, build_artifact_id=build.id,
                  evidence_artifact_ids=kw.pop("evidence_artifact_ids", []), **kw)
    session.add(row)
    session.flush()
    return row, target


def bump(model, entity_id, **values):
    """Raw UPDATE that follows the storage rule: revision must rise by exactly one."""
    return update(model).where(model.id == entity_id).values(**values, revision=model.revision + 1)
