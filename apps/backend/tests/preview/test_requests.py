"""Preview requests (database rules). Contract fixtures for QA evidence; no model, no browser."""
import pytest
from sqlalchemy import select

from app.domain.types import Conflict, Invalid
from app.persistence import ArtifactUnavailable, NotFound
from app.persistence.models import Artifact, Preview
from app.persistence.pins import pinned_artifacts
from app.preview import requests as pr
from tests.preview.helpers import SITE


def request(env, t, c, port=5190):
    with env.db.write() as s:
        return pr.public(pr.request_preview(s, env.store, ticket_id=t.id, candidate_id=c.id, user_id="user:local", port=port))


def test_only_the_current_verified_candidate_of_a_ticket_in_uat_can_be_previewed(env, docker):
    t, c = env.world.submitted()
    with env.db.write() as s, pytest.raises(Conflict, match="verified candidate"):
        pr.request_preview(s, env.store, ticket_id=t.id, candidate_id=c.id, user_id="user:local", port=5190)
    t, c, target, bundle = env.to_uat()
    first = request(env, t, c)
    assert first["status"] == "requested" and first["target_digest"] == target.checksum and first["url"] is None
    assert first["details"]["bundle_artifact_id"] == bundle.id and first["details"]["fixture"] == {"id": "coffee-menu-v1"}
    other_t, other_c = env.world.submitted()
    with env.db.write() as s, pytest.raises(NotFound):  # another ticket's candidate is not this ticket's candidate
        pr.request_preview(s, env.store, ticket_id=t.id, candidate_id=other_c.id, user_id="user:local", port=5190)


def test_reopening_returns_the_live_request_and_switching_retires_the_previous_one(env, docker):
    a, ca, _, _ = env.to_uat()
    b, cb, _, _ = env.to_uat()
    first = request(env, a, ca)
    assert request(env, a, ca)["id"] == first["id"]  # idempotent while requested/starting/ready
    second = request(env, b, cb)
    assert second["id"] != first["id"]
    with env.db.read() as s:
        rows = {p.id: p for p in s.scalars(select(Preview))}
        assert rows[first["id"]].status == "stopped" and rows[first["id"]].stop_reason == "switched"
        assert [p.id for p in pr.active(s)] == [second["id"]]
    # A running preview is asked to stop (the supervisor does it); a stop on a finished one is a no-op.
    with env.db.write() as s:
        live = s.get(Preview, second["id"])
        live.status = "ready"
    third = request(env, a, ca)
    with env.db.read() as s:
        assert s.get(Preview, second["id"]).status == "stopping" and s.get(Preview, second["id"]).stop_reason == "switched"
        assert [p.status for p in pr.active(s)] == ["stopping", "requested"] and third["status"] == "requested"
    with env.db.write() as s:
        assert pr.request_stop(s, third["id"], "user:local").status == "stopped"
        assert pr.request_stop(s, third["id"], "user:local").status == "stopped"


def test_a_missing_or_corrupt_build_bundle_makes_the_target_unavailable_not_previewable(env, docker):
    t, c, target, bundle = env.to_uat()
    with env.db.read() as s:
        path = env.store.resolve(s.get(Artifact, bundle.id).path)
    path.chmod(0o600)
    path.write_bytes(b"{}")
    with env.db.write() as s, pytest.raises(ArtifactUnavailable):
        pr.request_preview(s, env.store, ticket_id=t.id, candidate_id=c.id, user_id="user:local", port=5190)
    path.unlink()
    with env.db.write() as s, pytest.raises(ArtifactUnavailable):
        pr.request_preview(s, env.store, ticket_id=t.id, candidate_id=c.id, user_id="user:local", port=5190)
    with env.db.read() as s:
        assert not list(s.scalars(select(Preview)))


def test_stacks_that_need_migrations_are_refused_instead_of_previewed_without_them(env, docker):
    t, c, _, _ = env.to_uat(execution_manifest={"fixture": {"id": "x"}, "migrations": {"id": "alembic-head"}})
    with env.db.write() as s, pytest.raises(Invalid, match="stateless"):
        pr.request_preview(s, env.store, ticket_id=t.id, candidate_id=c.id, user_id="user:local", port=5190)


def test_an_active_preview_pins_the_exact_artifacts_it_serves_and_a_stopped_one_does_not(env, docker):
    t, c, target, bundle = env.to_uat()
    first = request(env, t, c)
    with env.db.read() as s:
        owners = {aid: {(r.owner_kind, r.owner_id) for r in refs} for aid, refs in pinned_artifacts(s).items()}
    for artifact_id in (bundle.id, target.id, first["details"]["build_artifact_id"]):
        assert ("preview", first["id"]) in owners[artifact_id]
    with env.db.write() as s:
        pr.request_stop(s, first["id"], "user:local")
    with env.db.read() as s:
        owners = {aid: {(r.owner_kind, r.owner_id) for r in refs} for aid, refs in pinned_artifacts(s).items()}
    assert all(("preview", first["id"]) not in refs for refs in owners.values())
    assert ("candidate", c.id) in owners[bundle.id] or any(kind == "message" for kind, _ in owners[bundle.id])
