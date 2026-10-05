"""Accept over HTTP: retries replay the receipt, a second accept is refused, and the DTO shows the operation."""
from tests.http.conftest import api  # noqa: F401


def test_accept_retry_replays_the_receipt_and_the_candidate_shows_pending_then_done(api, env):
    t, c, target, v, ids = env.to_uat(env.commit({"a.txt": "A\n"}))
    body = {"expected_revision": env.world.ticket(t.id).revision, "candidate_id": c.id, "scope_version": t.current_version,
            "target_artifact_id": target.id, "target_digest": target.checksum, "verification_id": v.id, "evidence_ids": ids}
    first = api.cmd(f"/tickets/{t.id}/uat-decisions", body, "accept-1")
    assert first.status_code == 200, first.text
    assert api.cmd(f"/tickets/{t.id}/uat-decisions", body, "accept-1").json() == first.json()
    again = api.cmd(f"/tickets/{t.id}/uat-decisions", body, "accept-2")
    assert again.status_code == 409
    shown = api.client.get(f"/tickets/{t.id}/candidates/{c.id}").json()["candidate"]
    assert shown["integration"]["status"] == "pending" and shown["integrated_sha"] is None
    assert shown["integration"]["operation_id"] == first.json()["integration"]["operation_id"]
    assert env.integrator().run_once() == [(t.id, "updated")]
    shown = api.client.get(f"/tickets/{t.id}/candidates/{c.id}").json()["candidate"]
    assert shown["integration"]["status"] == "done" and shown["integrated_sha"] == c.commit_sha
    assert api.client.get(f"/tickets/{t.id}").json()["ticket"]["phase"] == "accepted"
