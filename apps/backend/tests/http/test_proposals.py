from tests.domain.conftest import World


def test_proposed_criteria_are_visible_and_acceptance_still_requires_scope_approval(api):
    w = World(api.db, api.store)
    t = w.approve(w.new())
    j = api.job(w.project, ticket_id=t.id)
    lease = api.claim(j)
    with api.runtime_client(lease) as c:
        r = c.post("/runtime/tools", json={"name": "propose_criteria",
            "args": {"uac": [{"id": "UAC-1", "text": "New criteria", "mode": "manual"}]}},
            headers={"Idempotency-Key": "criteria"})
        assert r.status_code == 200, r.text
        pid = r.json()["result"]["proposal_id"]
    detail = api.client.get(f"/tickets/{t.id}").json()
    assert any(m["id"] == pid and m["metadata"]["intent"] == "scope_proposal" for m in detail["messages"])
    first = api.cmd(f"/tickets/{t.id}/proposals/{pid}/decisions",
                    {"expected_revision": detail["ticket"]["revision"], "accept": True}, "accept-proposal")
    assert first.status_code == 200 and first.json()["ticket"]["scope_version"] == 2, first.text
    assert first.json()["ticket"]["phase"] == "scope_review"
    assert api.cmd(f"/tickets/{t.id}/proposals/{pid}/decisions",
                  {"expected_revision": detail["ticket"]["revision"], "accept": True}, "accept-proposal").json() == first.json()
    assert api.client.get(f"/tickets/{t.id}").json()["versions"][-1]["uac"][0]["mode"] == "manual"


def test_existing_repo_registration_is_metadata_only_and_brief_is_revisioned(api, tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    sentinel = original / "README.md"
    sentinel.write_text("Do not execute or modify this repository")
    assert api.cmd("/projects", {"name": "Existing", "mode": "existing"}).status_code == 422
    r = api.cmd("/projects", {"name": "Existing", "mode": "existing", "repo_ref": str(original)})
    assert r.status_code == 200 and r.json()["project"]["onboarding"] == "pending"
    assert sentinel.read_text() == "Do not execute or modify this repository" and list(original.iterdir()) == [sentinel]
    p = r.json()["project"]
    body = {"expected_revision": p["revision"], "brief": "Coffee"}
    first = api.cmd(f"/projects/{p['id']}/brief", body, "brief")
    assert first.status_code == 200 and first.json()["project"]["brief_version"] == 2
    assert api.cmd(f"/projects/{p['id']}/brief", body, "brief").json() == first.json()
    assert api.cmd(f"/projects/{p['id']}/brief", body).status_code == 409
