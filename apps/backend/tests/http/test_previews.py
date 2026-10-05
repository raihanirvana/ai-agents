"""Preview commands/queries over HTTP. The supervisor is not running here: rows are driven directly where it would act."""
import pytest

pytest.importorskip("fcntl")  # verified-target fixtures use the POSIX workspace helpers

from sqlalchemy import select

from app.persistence.models import Artifact, Preview
from tests.http.conftest import api, db, db_path, store  # noqa: E402,F401
from tests.preview.helpers import PreviewEnv  # noqa: E402


@pytest.fixture
def uat(api, db, store, tmp_path):
    from tests.preview.helpers import docker_ready
    if not docker_ready():
        pytest.skip("Docker with the pinned node image is required to build the target fixture")
    env = PreviewEnv(db, store, tmp_path)
    return env, env.to_uat()


def start(api, t, c, key="open-1"):
    return api.cmd(f"/tickets/{t.id}/candidates/{c.id}/previews", {}, key)


def test_start_is_idempotent_reopens_the_live_request_and_links_target_identity(api, uat):
    env, (t, c, target, bundle) = uat
    r = start(api, t, c)
    assert r.status_code == 200, r.text
    preview = r.json()["preview"]
    assert preview["status"] == "requested" and preview["url"] is None
    assert preview["candidate_id"] == c.id and preview["target_digest"] == target.checksum
    assert preview["details"]["bundle_artifact_id"] == bundle.id and preview["details"]["fixture"] == {"id": "coffee-menu-v1"}
    assert start(api, t, c).json() == r.json()  # same key: the stored receipt
    assert start(api, t, c, "open-2").json()["preview"]["id"] == preview["id"]  # a new key reuses the live request
    with env.db.read() as s:
        assert len(list(s.scalars(select(Preview)))) == 1
    assert "preview.requested" in api.client.get(f"/projects/{t.project_id}/events?follow=false").text


def test_ready_preview_is_on_localhost_never_on_the_control_host_and_is_visible_in_board_and_candidate(api, uat):
    env, (t, c, target, _) = uat
    pid = start(api, t, c).json()["preview"]["id"]
    with env.db.write() as s:
        row = s.get(Preview, pid)
        row.status = "ready"
    got = api.client.get(f"/previews/{pid}").json()["preview"]
    assert got["url"] == "http://localhost:5180/" and "127.0.0.1" not in got["url"]
    board = api.client.get(f"/projects/{t.project_id}/tickets").json()
    assert board["preview"]["id"] == pid and board["preview"]["status"] == "ready"
    candidate = api.client.get(f"/tickets/{t.id}/candidates/{c.id}").json()["candidate"]
    assert candidate["live_preview"]["id"] == pid and candidate["target_digest"] == target.checksum


def test_stop_is_idempotent_and_a_second_stop_changes_nothing(api, uat):
    env, (t, c, _, _) = uat
    pid = start(api, t, c).json()["preview"]["id"]
    first = api.cmd(f"/previews/{pid}/stop", {}).json()["preview"]
    assert first["status"] == "stopped" and first["stop_reason"] == "user_stop"  # not started yet: nothing to stop
    again = api.cmd(f"/previews/{pid}/stop", {}).json()["preview"]
    assert again["status"] == "stopped" and again["revision"] == first["revision"]
    assert api.client.get(f"/projects/{t.project_id}/tickets").json()["preview"] is None


def test_errors_are_structured_for_wrong_state_unavailable_artifacts_and_unsupported_stacks(api, uat, tmp_path):
    env, (t, c, _, bundle) = uat
    other, other_c = env.world.submitted()
    wrong = start(api, other, other_c)
    assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "conflict"
    with env.db.read() as s:
        path = env.store.resolve(s.get(Artifact, bundle.id).path)
    path.chmod(0o600)
    path.unlink()
    gone = start(api, t, c, "gone")
    assert gone.status_code == 409 and gone.json()["error"]["code"] == "artifact_unavailable"
    unsupported_t, unsupported_c, _, _ = env.to_uat(execution_manifest={"fixture": {"id": "x"}, "migrations": {"id": "alembic-head"}})
    bad = start(api, unsupported_t, unsupported_c)
    assert bad.status_code in (400, 422) and "stateless" in bad.json()["error"]["message"]
    assert api.client.get("/previews/missing").status_code == 404


def test_preview_commands_need_the_csrf_token_and_a_user_session(api, uat):
    env, (t, c, _, _) = uat
    path = f"/tickets/{t.id}/candidates/{c.id}/previews"
    headers = {"Idempotency-Key": "csrf"}
    saved = api.client.headers.pop("X-CSRF-Token")
    assert api.client.post(path, json={}, headers=headers).status_code == 403
    api.client.headers["X-CSRF-Token"] = saved
    assert api.client.post(path, json={"unexpected": 1}, headers=headers).status_code == 422  # strict body: {} only
    with env.db.read() as s:
        assert not list(s.scalars(select(Preview)))
