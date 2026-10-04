"""AC: product pins protect commit/build/evidence/context of active candidates, approvals and
releases; cleanup only removes unpinned data and respects ownership."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import update

from app.persistence import cleanup_unpinned, pin_owners, pinned_artifacts, read_events
from app.persistence.columns import utcnow
from app.persistence.models import Approval, Artifact, Candidate, Job, Message, Release

from . import factories as f

LATER = timedelta(days=2)  # "now" far enough ahead that nothing is too recent


def owners(db, artifact_id):
    with db.read() as s:
        return sorted((p.owner_kind, p.owner_id) for p in pin_owners(s, artifact_id))


def available(db, artifact_id):
    with db.read() as s:
        return s.get(Artifact, artifact_id).availability


def run_cleanup(db, store, world, **kw):
    kw.setdefault("now", utcnow() + LATER)
    return cleanup_unpinned(db, store, project_id=world.project.id, **kw)


def test_unreferenced_artifacts_are_not_pinned(db, store, world):
    with db.write() as s:
        art = f.artifact(s, store, world.project)
    assert owners(db, art.id) == []


def test_active_candidate_pins_commit_build_context_target_and_evidence(db, store, world):
    with db.write() as s:
        commit = store.put_git_commit(s, project_id=world.project.id, sha=f.SHA_A)
        build = f.artifact(s, store, world.project, kind="build_record", data=b"b", name="build.json")
        context = f.artifact(s, store, world.project, kind="context", data=b"c", name="context.json")
        evidence = f.artifact(s, store, world.project, data=b"e", name="e.json")
        cand = f.candidate(s, world.project, world.ticket, commit_artifact_id=commit.id,
                           build_artifact_id=build.id, context_artifact_id=context.id,
                           evidence_artifact_ids=[evidence.id])
    for art in (commit, build, context, evidence):
        assert owners(db, art.id) == [("candidate", cand.id)]


def test_superseded_candidate_with_approval_keeps_its_whole_closure_pinned(db, store, world):
    """Regression (review R002-01): supersede must not expose the approved build/commit/context."""
    with db.write() as s:
        cand, target, evidence = f.verified_candidate(s, store, world.project, world.ticket)
        f.uat_approval(s, world.project, world.ticket, cand, target, [evidence.id])
        junk = f.artifact(s, store, world.project, kind="log", data=b"noise", name="old.log")
    closure = [cand.commit_artifact_id, cand.build_artifact_id, cand.context_artifact_id, target.id, evidence.id]
    with db.write() as s:
        s.execute(f.bump(Candidate, cand.id, status="superseded"))
    for artifact_id in closure:
        assert {kind for kind, _ in owners(db, artifact_id)} >= {"verification"}, artifact_id
        assert ("candidate", cand.id) not in owners(db, artifact_id)  # the candidate itself no longer pins
    report = run_cleanup(db, store, world, dry_run=False)
    assert report.removed == [junk.id]  # only the genuinely unreferenced artifact goes
    for artifact_id in closure:
        assert available(db, artifact_id) == "available"
    with db.read() as s:
        assert store.read_bytes(s, cand.build_artifact_id) == b"build"  # the bytes are there, not just the row


def test_rebuilt_candidate_keeps_the_build_the_old_target_was_verified_against(db, store, world):
    with db.write() as s:
        cand, target, evidence = f.verified_candidate(s, store, world.project, world.ticket)
        f.uat_approval(s, world.project, world.ticket, cand, target, [evidence.id])
        old_build = cand.build_artifact_id
        new_build = f.artifact(s, store, world.project, kind="build_record", data=b"rebuilt", name="rebuilt.json")
        new_target = f.artifact(s, store, world.project, kind="target_manifest", data=b'{"n":2}', name="t2.json")
        s.execute(f.bump(Candidate, cand.id, status="review_approved", build_artifact_id=new_build.id,
                         target_artifact_id=new_target.id, target_digest=new_target.checksum))
    # The candidate points at the new build now; the approved target's build is still protected.
    assert {kind for kind, _ in owners(db, old_build)} == {"verification"}
    run_cleanup(db, store, world, dry_run=False)
    assert available(db, old_build) == "available"


def test_candidate_without_any_verification_releases_its_artifacts_when_rejected(db, store, world):
    with db.write() as s:
        build = f.artifact(s, store, world.project, kind="build_record", data=b"b", name="build.json")
        cand = f.candidate(s, world.project, world.ticket, build_artifact_id=build.id)
    assert owners(db, build.id) == [("candidate", cand.id)]
    with db.write() as s:
        s.execute(f.bump(Candidate, cand.id, status="rejected"))
    assert owners(db, build.id) == []
    assert run_cleanup(db, store, world, dry_run=False).removed == [build.id]


def test_release_job_message_and_verification_pins(db, store, world):
    with db.write() as s:
        evidence = f.artifact(s, store, world.project, data=b"r", name="r.json")
        release, target = f.draft_release(s, store, world.project, evidence_artifact_ids=[evidence.id])
        context = f.artifact(s, store, world.project, kind="context", data=b"c", name="c.json")
        job = f.job(s, world.project, world.ticket, context_artifact_id=context.id)
        attachment = f.artifact(s, store, world.project, data=b"a", name="a.png")
        s.add(Message(project_id=world.project.id, thread_id="t", seq=1, sender="user", body="see",
                      attachment_ids=[attachment.id]))
        s.flush()
    assert owners(db, target.id) == [("release", release.id)]
    assert owners(db, evidence.id) == [("release", release.id)]
    assert owners(db, context.id) == [("job", job.id)]
    assert [kind for kind, _ in owners(db, attachment.id)] == ["message"]
    with db.write() as s:  # a finished job no longer needs its resume context
        s.execute(update(Job).where(Job.id == job.id).values(status="failed"))
    assert owners(db, context.id) == []


def test_cleanup_removes_only_unpinned_old_artifacts(db, store, world):
    with db.write() as s:
        junk = f.artifact(s, store, world.project, kind="log", data=b"noise", name="old.log")
        cand, target, evidence = f.verified_candidate(s, store, world.project, world.ticket)
        f.uat_approval(s, world.project, world.ticket, cand, target, [evidence.id])
    junk_path = store.resolve(junk.path)
    pinned_path = store.resolve(evidence.path)

    dry = run_cleanup(db, store, world)  # dry run is the default: nothing changes
    assert dry.dry_run and dry.removed == [junk.id] and evidence.id in dry.kept_pinned
    assert junk_path.exists() and available(db, junk.id) == "available"

    report = run_cleanup(db, store, world, dry_run=False)
    assert report.removed == [junk.id]
    assert not junk_path.exists() and pinned_path.exists()
    with db.read() as s:
        row = s.get(Artifact, junk.id)
        assert (row.availability, row.unavailable_reason) == ("unavailable", "cleaned")
        assert [e.type for e in read_events(s) if e.type == "artifact.cleaned"] == ["artifact.cleaned"]
    assert available(db, evidence.id) == available(db, target.id) == "available"
    assert run_cleanup(db, store, world, dry_run=False).removed == []  # idempotent


def test_recent_artifacts_are_skipped_by_default(db, store, world):
    with db.write() as s:
        fresh = f.artifact(s, store, world.project, kind="log", data=b"fresh", name="fresh.log")
    report = cleanup_unpinned(db, store, project_id=world.project.id, dry_run=False)  # default min_age 1h
    assert report.removed == [] and report.skipped == {fresh.id: "too_recent"}
    assert available(db, fresh.id) == "available"


def test_cleanup_is_scoped_to_one_project_and_kind(db, store, world):
    with db.write() as s:
        other = f.project(s, name="other")
        mine = f.artifact(s, store, world.project, kind="log", data=b"1", name="mine.log")
        shot = f.artifact(s, store, world.project, kind="screenshot", data=b"2", name="s.png")
        theirs = f.artifact(s, store, other, kind="log", data=b"3", name="theirs.log")
    report = run_cleanup(db, store, world, dry_run=False, kinds=("log",))
    assert report.removed == [mine.id]
    assert available(db, shot.id) == "available" and available(db, theirs.id) == "available"


def test_cleanup_skips_artifacts_whose_path_is_not_under_the_project(db, store, world):
    with db.write() as s:
        s.add(Artifact(id="stray", project_id=world.project.id, kind="log", storage="file",
                       path="another-project/x/stray.log", checksum="1" * 64, size_bytes=1))
    report = run_cleanup(db, store, world, dry_run=False)
    assert report.skipped == {"stray": "ownership_mismatch"} and report.removed == []


def test_pinned_data_survives_every_cleanup_even_when_it_is_old(db, store, world):
    with db.write() as s:
        release, target = f.draft_release(s, store, world.project)
        s.add(Approval(project_id=world.project.id, type="release", user_id="user:local", release_id=release.id,
                       target_artifact_id=target.id, target_digest=target.checksum))
        s.flush()
        s.execute(f.bump(Release, release.id, status="approved"))
    report = run_cleanup(db, store, world, dry_run=False, min_age=timedelta(0), now=utcnow() + timedelta(days=3650))
    assert report.removed == [] and target.id in report.kept_pinned
    assert store.resolve(target.path).exists()


def test_cleaned_artifact_is_not_resurrected_by_a_stray_file(db, store, world):
    with db.write() as s:
        junk = f.artifact(s, store, world.project, kind="log", data=b"noise", name="old.log")
    run_cleanup(db, store, world, dry_run=False)
    path = store.resolve(junk.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"noise")  # identical bytes reappear
    with db.write() as s:
        assert store.verify(s, junk.id) == "unavailable"
    with db.read() as s:
        assert s.get(Artifact, junk.id).unavailable_reason == "cleaned"


def test_pinned_artifacts_lists_every_owner(db, store, world):
    with db.write() as s:
        cand, target, evidence = f.verified_candidate(s, store, world.project, world.ticket)
        f.uat_approval(s, world.project, world.ticket, cand, target, [evidence.id])
    with db.read() as s:
        pins = pinned_artifacts(s)
    assert {p.owner_kind for p in pins[target.id]} == {"candidate", "approval", "verification"}
    assert {p.owner_kind for p in pins[evidence.id]} == {"approval", "verification"}


def approved_release(db, store, world):
    """A release approved for its target, built from a build record, commit and context."""
    with db.write() as s:
        commit = store.put_git_commit(s, project_id=world.project.id, sha=f.SHA_B)
        context = f.artifact(s, store, world.project, kind="context", data=b"ctx", name="release-ctx.json")
        evidence = f.artifact(s, store, world.project, data=b"ev", name="release-ev.json")
        release, target = f.draft_release(s, store, world.project, evidence_artifact_ids=[evidence.id],
                                          commit_artifact_id=commit.id, context_artifact_id=context.id)
        s.add(Approval(project_id=world.project.id, type="release", user_id="user:local", release_id=release.id,
                       target_artifact_id=target.id, target_digest=target.checksum, evidence_artifact_ids=[evidence.id]))
        s.flush()
        s.execute(f.bump(Release, release.id, status="approved"))
    return release, target, commit, context, evidence


def test_approved_release_pins_the_build_commit_and_context_it_was_built_from(db, store, world):
    """Regression (review follow-up): cleanup must not delete what an approved release rests on."""
    release, target, commit, context, evidence = approved_release(db, store, world)
    with db.write() as s:
        junk = f.artifact(s, store, world.project, kind="log", data=b"noise", name="old.log")
    closure = [release.build_artifact_id, commit.id, context.id, target.id, evidence.id]
    for artifact_id in closure:
        assert ("release", release.id) in owners(db, artifact_id), artifact_id
    report = run_cleanup(db, store, world, dry_run=False, min_age=timedelta(0), now=utcnow() + timedelta(days=3650))
    assert report.removed == [junk.id]
    for artifact_id in closure:
        assert available(db, artifact_id) == "available"
    with db.read() as s:
        assert store.read_bytes(s, release.build_artifact_id) == b"release-build"  # bytes, not just the row
        assert store.read_bytes(s, context.id) == b"ctx"
