import os
from sqlalchemy import select
from app.persistence.models import Approval, Job
from app.workers.queue import Lease
from tests.domain.conftest import World, SCOPE
from tests.domain.test_evidence import fingerprint, release


def test_artifact_attachment_digest_and_missing_evidence(api):
    p = api.project()
    with api.db.write() as s:
        a = api.store.put_bytes(s, project_id=p.id, kind="report", name="evidence.html", data=b"<script>bad()</script>")
    meta = api.client.get(f"/artifacts/{a.id}")
    assert meta.status_code == 200 and "path" not in meta.json()["artifact"]
    content = api.client.get(f"/artifacts/{a.id}/content")
    assert content.content == b"<script>bad()</script>" and content.headers["content-type"] == "application/octet-stream"
    assert content.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in content.headers["content-security-policy"]
    assert content.headers["x-artifact-digest"] == a.checksum
    os.chmod(api.store.resolve(a.path), 0o600)
    api.store.resolve(a.path).unlink()
    assert api.client.get(f"/artifacts/{a.id}/content").status_code == 409
    assert api.client.get(f"/artifacts/{a.id}").json()["artifact"]["availability"] == "unavailable"


def test_file_disappearing_after_verification_persists_unavailable(api, monkeypatch):
    p = api.project()
    with api.db.write() as s:
        a = api.store.put_bytes(s, project_id=p.id, kind="report", name="race.json", data=b"{}")
    read = api.store.read_bytes
    def disappear(s, artifact_id):
        os.chmod(api.store.resolve(a.path), 0o600)
        api.store.resolve(a.path).unlink()
        return read(s, artifact_id)
    monkeypatch.setattr(api.store, "read_bytes", disappear)
    assert api.client.get(f"/artifacts/{a.id}/content").status_code == 409
    from app.persistence.models import Artifact, Event
    with api.db.read() as s:
        assert s.get(Artifact, a.id).availability == "unavailable"
        assert s.scalar(select(Event).where(Event.entity_id == a.id, Event.type == "artifact.unavailable"))


def test_uat_is_pinned_user_only_and_idempotent(api):
    # Synthetic trusted receipts exercise API/domain contracts, not real QA.
    w = World(api.db, api.store)
    t, c, target, v, ids = w.uat()
    detail = api.client.get(f"/tickets/{t.id}")
    assert detail.status_code == 200
    assert detail.json()["candidates"][0]["verifications"][0]["evidence_ids"] == v.evidence_artifact_ids
    body = {"expected_revision": t.revision, "candidate_id": c.id, "scope_version": 1,
        "target_artifact_id": target.id, "target_digest": target.checksum, "verification_id": v.id, "evidence_ids": ids}
    path = f"/tickets/{t.id}/uat-decisions"
    assert api.cmd(path, {**body, "target_digest": "e"*64}).status_code == 409
    assert api.cmd(path, {**body, "evidence_ids": []}).status_code in (409, 422)
    first = api.cmd(path, body, "uat")
    assert first.status_code == 200, first.text
    assert api.cmd(path, body, "uat").json() == first.json()
    with api.db.read() as s:
        approvals = list(s.scalars(select(Approval).where(Approval.ticket_id == t.id, Approval.type == "uat")))
        assert len(approvals) == 1 and approvals[0].user_id == "user:local"
    assert api.client.get(f"/tickets/{t.id}").json()["ticket"]["phase"] == "integrating"


def test_baseline_waiver_and_release_keep_specific_evidence(api):
    w = World(api.db, api.store)
    t = w.approve(w.new())
    fp = fingerprint(w, t)
    body = {"expected_revision": t.revision, "ticket_id": t.id,
            "fingerprint_artifact_id": fp.id, "reason": "Existing failure"}
    path = f"/projects/{w.project.id}/baseline-waivers"
    first = api.cmd(path, body, "waiver")
    assert first.status_code == 200, first.text
    assert api.cmd(path, body, "waiver").json() == first.json()
    with api.db.read() as s:
        a = s.get(Approval, first.json()["approval_id"])
        assert a.type == "baseline_waiver" and a.details["status"] == "waived"
    bad = fingerprint(w, t, category="uac", uac_ids=["UAC-1"])
    assert api.cmd(path, {**body, "expected_revision": w.ticket(t.id).revision,
                         "fingerprint_artifact_id": bad.id}).status_code == 422
    r, target, proof = release(w)
    body = {"expected_revision": r.revision, "target_artifact_id": target.id,
            "target_digest": target.checksum, "evidence_ids": [proof.id]}
    first = api.cmd(f"/releases/{r.id}/decisions", body, "release")
    assert first.status_code == 200 and first.json()["status"] == "approved", first.text
    assert api.cmd(f"/releases/{r.id}/decisions", body, "release").json() == first.json()


def test_candidate_submission_is_attempt_bound_and_runtime_has_no_user_power(api):
    w = World(api.db, api.store)
    t = w.approve(w.new())
    actor, ref = w.job(t, "developer")
    with api.db.write() as s:
        j = s.get(Job, ref.job_id)
        j.runtime_ref = {**j.runtime_ref, "runtime": "structured:fake", "fake": True}
    commit, receipt, base = w.commit_receipt(t, ref)
    body = {"expected_revision": w.ticket(t.id).revision, "commit_artifact_id": commit.id,
            "commit_receipt_id": receipt.id, "base_sha": base}
    lease = Lease(ref.job_id, actor.id, ref.generation)
    with api.runtime_client(lease) as client:
        r = client.post(f"/runtime/tickets/{t.id}/candidates", json=body, headers={"Idempotency-Key": "candidate"})
        assert r.status_code == 200, r.text
        assert client.post(f"/runtime/tickets/{t.id}/candidates", json=body, headers={"Idempotency-Key": "candidate"}).json() == r.json()
        assert client.post(f"/tickets/{t.id}/uat-decisions", json={}, headers={"Origin": "http://127.0.0.1:5173"}).status_code == 401
    other = w.approve(w.new())
    assert api.client.get(f"/tickets/{other.id}/candidates/{r.json()['candidate']['id']}").status_code == 404


def test_scope_changed_while_waiting_cannot_resume(api):
    w = World(api.db, api.store)
    t = w.approve(w.new())
    j = api.job(w.project, ticket_id=t.id)
    lease = api.claim(j)
    rid = api.api.queue.request_input(lease, question="Question?", checkpoint={}, request_key="q")
    before = api.client.get(f"/runs/{j.id}").json()["run"]
    w.w.edit_scope(w.user, t.id, w.ticket(t.id).revision, {**SCOPE, "title": "Changed"})
    r = api.cmd(f"/runs/{j.id}/input", {"expected_revision": before["revision"], "request_id": rid,
        "scope_version": t.current_version, "generation": lease.generation, "answer": "Stale"})
    assert r.status_code == 409
    assert api.client.get(f"/runs/{j.id}").json()["run"]["status"] == "cancelled"
