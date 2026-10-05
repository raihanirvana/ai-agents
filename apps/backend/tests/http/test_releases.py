"""Release commands/queries over HTTP: freeze as a job, approval with the manual checklist, export request, no 'deployed'."""
import pytest
from sqlalchemy import select

from app.persistence.models import Job, Project
from tests.domain.conftest import World
from tests.domain.test_release import MANUAL_SCOPE, accepted, draft
from tests.http.conftest import api, db, db_path, store  # noqa: F401
from tests.persistence import factories as f


@pytest.fixture
def world(api):
    w = World(api.db, api.store)
    with api.db.write() as s:  # the fixture world has a synthetic base; a runner configuration makes it freezable
        p = s.get(Project, w.project.id)
        p.workflow = {**p.workflow, "pipeline": {"manifest": {"runner": "react-vite"}}}
    return w


def project_revision(api, world):
    with api.db.read() as s:
        return s.get(Project, world.project.id).revision


def test_freeze_is_a_job_not_an_approval_and_a_second_freeze_is_refused_while_it_runs(api, world):
    a, _ = accepted(world, MANUAL_SCOPE)
    path = f"/projects/{world.project.id}/releases"
    assert api.cmd(path, {"expected_revision": project_revision(api, world) + 5}).status_code == 409  # stale revision
    first = api.cmd(path, {"expected_revision": project_revision(api, world)}, "freeze-1")
    assert first.status_code == 200, first.text
    assert first.json()["tickets"] == [a.id] and first.json()["accepted_tip"] == f.SHA_A
    assert api.cmd(path, {"expected_revision": project_revision(api, world)}, "freeze-1").json() == first.json()  # same receipt
    assert api.cmd(path, {"expected_revision": project_revision(api, world)}, "freeze-2").status_code == 409  # one at a time
    with api.db.read() as s:
        jobs = list(s.scalars(select(Job).where(Job.stage == "release")))
        assert len(jobs) == 1 and jobs[0].runtime_ref["runtime"] == "release" and jobs[0].runtime_ref["payload"]["task"] == "verify"
    assert api.client.get(f"/projects/{world.project.id}/releases").json() == {"releases": []}  # nothing is a release yet


def test_freeze_needs_accepted_work_and_a_runner_configuration(api):
    w = World(api.db, api.store)  # no pipeline manifest
    path = f"/projects/{w.project.id}/releases"
    revision = project_revision(api, w)
    assert api.cmd(path, {"expected_revision": revision}).status_code == 422
    with api.db.write() as s:
        p = s.get(Project, w.project.id)
        p.workflow = {**p.workflow, "pipeline": {"manifest": {"runner": "react-vite"}}}
    assert api.cmd(path, {"expected_revision": project_revision(api, w)}).status_code == 422  # nothing accepted yet


def test_approval_over_http_requires_the_checklist_and_a_release_is_never_shown_as_deployed(api, world):
    a, _ = accepted(world, MANUAL_SCOPE)
    _, entries = world.w.freeze_release_scope(world.user)
    release, target, receipt = draft(world, entries, tip=f.SHA_A)
    listed = api.client.get(f"/projects/{world.project.id}/releases").json()["releases"]
    assert [r["id"] for r in listed] == [release.id] and listed[0]["checklist"] == [f"{a.id}:UAC-M"]
    assert listed[0]["status"] == "draft" and listed[0]["deployed"] is False and listed[0]["export"] is None
    board = api.client.get(f"/projects/{world.project.id}/tickets").json()
    assert [r["id"] for r in board["releases"]] == [release.id]
    body = {"expected_revision": release.revision, "target_artifact_id": target.id, "target_digest": target.checksum,
            "evidence_ids": [receipt.id], "manual_uac_ids": []}
    refused = api.cmd(f"/releases/{release.id}/decisions", body)
    assert refused.status_code == 422 and "checklist" in refused.json()["error"]["message"]
    wrong_sha = api.cmd(f"/releases/{release.id}/decisions", {**body, "target_digest": "e" * 64, "manual_uac_ids": [f"{a.id}:UAC-M"]})
    assert wrong_sha.status_code == 409
    ok = api.cmd(f"/releases/{release.id}/decisions", {**body, "manual_uac_ids": [f"{a.id}:UAC-M"]})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    shown = api.client.get(f"/releases/{release.id}").json()["release"]
    assert shown["status"] == "approved" and shown["deployed"] is False and shown["deployment"] is None


def test_export_is_an_explicit_job_for_an_approved_release_and_discard_is_for_drafts(api, world):
    accepted(world)
    _, entries = world.w.freeze_release_scope(world.user)
    release, target, receipt = draft(world, entries, tip=f.SHA_A)
    assert api.cmd(f"/releases/{release.id}/export", {"expected_revision": release.revision}).status_code == 409  # not approved
    discarded = api.cmd(f"/releases/{release.id}/discard", {"expected_revision": release.revision})
    assert discarded.status_code == 200 and discarded.json()["release"]["status"] == "failed"
    assert api.cmd(f"/releases/{release.id}/discard", {"expected_revision": discarded.json()["release"]["revision"]}).status_code == 409
    _, entries = world.w.freeze_release_scope(world.user)
    second, target, receipt = draft(world, entries, tip=f.SHA_A)
    world.w.approve_release(world.user, second.id, second.revision, target.id, target.checksum, [receipt.id], [])
    with api.db.read() as s:
        from app.persistence.models import Release
        revision = s.get(Release, second.id).revision
    exported = api.cmd(f"/releases/{second.id}/export", {"expected_revision": revision}, "export-1")
    assert exported.status_code == 200 and exported.json()["job_id"]
    with api.db.read() as s:
        job = s.get(Job, exported.json()["job_id"])
        assert job.stage == "release" and job.runtime_ref["payload"] == {"task": "export", "release_id": second.id}
    assert api.cmd(f"/releases/{second.id}/export", {"expected_revision": revision}, "export-2").status_code == 409  # one operation at a time
    sync = api.cmd(f"/releases/{second.id}/sync", {"expected_revision": revision}, "sync-1")
    assert sync.status_code in (409, 422)  # not an onboarded existing repository (and a job is already running)


def test_release_commands_need_a_user_session_and_csrf(api, world):
    accepted(world)
    path = f"/projects/{world.project.id}/releases"
    saved = api.client.headers.pop("X-CSRF-Token")
    assert api.client.post(path, json={"expected_revision": 1}, headers={"Idempotency-Key": "x"}).status_code == 403
    api.client.headers["X-CSRF-Token"] = saved
    with api.db.read() as s:
        assert not list(s.scalars(select(Job).where(Job.stage == "release")))


def test_sync_approval_over_http_requires_the_exact_pinned_diff_review(api, world):
    from tests.domain.test_evidence import json_art
    from app.persistence.models import Approval
    accepted(world)
    _, entries = world.w.freeze_release_scope(world.user)
    diffs = [json_art(world, 'other', {'diff': i}, 'verification').id for i in range(2)]
    release, target, receipt = draft(world, entries, tip=f.SHA_A,
        target_fields={'sync': {'candidate_sha': f.SHA_A}, 'technical_review_evidence_ids': diffs}, extra_evidence=diffs)
    path = f'/releases/{release.id}/decisions'
    body = {'expected_revision': release.revision, 'target_artifact_id': target.id, 'target_digest': target.checksum,
            'evidence_ids': [receipt.id, *diffs], 'manual_uac_ids': []}
    assert api.cmd(path, body).status_code == 422
    assert api.cmd(path, {**body, 'reviewed_diff_ids': [diffs[0], 'another-target']}).status_code == 422
    assert api.cmd(path, {**body, 'reviewed_diff_ids': diffs}).status_code == 200
    with api.db.read() as s:
        approval = s.scalar(select(Approval).where(Approval.release_id == release.id))
        assert approval.details['technical_review']['diff_artifact_ids'] == diffs
        assert approval.details['technical_review']['target_digest'] == target.checksum
