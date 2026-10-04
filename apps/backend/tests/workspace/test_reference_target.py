"""Build/run the bundled React/Vite reference target through the real sandbox.

Needs Docker, the pinned node image, and registry access for the install phase
(the only phase allowed egress). No fake is involved.
"""
import json
from pathlib import Path

import pytest

from app.workspace import AuthorizationError, WorkspaceError, parse_manifest, reference_manifest_dict
from app.workspace.manifest import digest_of

from .conftest import FIXTURE, IMAGE, small_manifest, start

pytestmark = pytest.mark.docker


def load_fixture(sup, run):
    for path in sorted(FIXTURE.rglob("*")):
        if path.is_file() and not {"node_modules", "dist"} & set(path.relative_to(FIXTURE).parts):
            sup.write_file(run.ref, run.credential, str(path.relative_to(FIXTURE)), path.read_bytes())


@pytest.fixture(scope="module")
def built(tmp_path_factory, docker_ready):
    from app.workspace import WorkspaceSupervisor

    sup = WorkspaceSupervisor(tmp_path_factory.mktemp("ref") / "ws")
    sup.create_project("demo")
    manifest = small_manifest()
    run = start(sup, manifest, egress=True)
    load_fixture(sup, run)
    phases = {name: sup.run_phase(run.ref, run.credential, name) for name in ("install", "build", "test")}
    candidate = sup.submit_candidate(run.ref, run.credential, "reference coffee menu")
    target = sup.build_target(run.ref, candidate["sha"], run_tests=True)
    yield sup, run, manifest, phases, candidate, target
    for row in sup.sandbox.owned():
        sup.sandbox.kill_and_remove(row["name"])


def test_install_build_test_phases_run_for_real(built):
    _, _, _, phases, _, _ = built
    assert phases["install"].exit_code == 0 and phases["install"].network == "egress", phases["install"].stderr
    assert phases["build"].exit_code == 0 and b"built in" in phases["build"].stdout, phases["build"].stderr
    assert phases["build"].network == "none" and phases["test"].network == "none"
    assert phases["test"].exit_code == 0
    assert b"# pass 2" in phases["test"].stdout and b"# fail 0" in phases["test"].stdout


def test_candidate_pins_base_scope_and_excludes_runner_output(built):
    sup, run, _, _, candidate, _ = built
    assert candidate["base_sha"] == run.spec.base_sha and candidate["scope_version"] == 1
    broker = sup.broker("demo")
    paths = broker.run(["--git-dir", str(broker.repo), "ls-tree", "-r", "--name-only", candidate["sha"]]).decode().split()
    assert {"src/cart.js", "package-lock.json", "package.json"} <= set(paths)
    assert not any(p.startswith(("node_modules/", "dist/")) for p in paths)
    assert sup.broker("demo").refs()["refs/heads/accepted"] == run.spec.base_sha


def test_target_manifest_records_build_toolchain_config_and_fixture_identity(built):
    sup, run, manifest, _, candidate, target = built
    assert target["candidate_sha"] == candidate["sha"] and target["base_sha"] == run.spec.base_sha
    assert target["scope_version"] == 1 and target["runner_manifest_revision"] == 1
    assert len(target["build_digest"]) == 64 and len(target["dependency_digest"]) == 64
    assert target["toolchain"]["image"] == IMAGE and target["toolchain"]["image_id"].startswith("sha256:")
    assert target["effective_config_digest"] == manifest.effective_config_digest
    assert target["fixture"] == {"id": "coffee-menu-v1"} and target["migrations"] == {"id": "none"}
    assert target["evidence"]
    path = sup.run_dir(run.ref) / "evidence" / f"target-{target['target_id']}.json"
    stored = json.loads(path.read_text())
    unsigned = {k: v for k, v in stored.items() if k != "target_id"}
    assert digest_of(unsigned) == stored["target_id"]
    assert (path.stat().st_mode & 0o222) == 0  # read-only artifact
    assert (sup.run_dir(run.ref) / "builds" / target["build_id"] / "artifact" / "index.html").is_file()


def test_smoke_start_serves_health_without_publishing_a_port(built):
    sup, run, _, _, _, target = built
    result = sup.smoke_target(run.ref, target["build_id"])
    assert result["healthy"], result
    assert "Coffee menu reference" in result["body_prefix"] or "<!doctype" in result["body_prefix"].lower()
    assert sup.sandbox.owned(run_id=run.ref.run_id, include_stopped=False) == []


def test_rebuild_of_same_sha_is_a_new_target(built):
    sup, run, _, _, candidate, first = built
    second = sup.build_target(run.ref, candidate["sha"])
    assert second["candidate_sha"] == first["candidate_sha"]
    assert second["build_id"] != first["build_id"] and second["target_id"] != first["target_id"]
    assert second["build_digest"] == first["build_digest"]  # deterministic output, still a new record


def test_unknown_candidate_and_install_egress_policy_are_enforced(built, tmp_path):
    sup, run, manifest, _, candidate, _ = built
    with pytest.raises(WorkspaceError, match="not a recorded commit"):
        sup.build_target(run.ref, "a" * 40)
    no_egress = start(sup, manifest, ticket="T-noegress", egress=False)
    with pytest.raises(AuthorizationError, match="not allowed install egress"):
        sup.run_phase(no_egress.ref, no_egress.credential, "install")


def test_changed_config_changes_effective_config_digest_not_just_sha(built):
    sup, run, manifest, _, candidate, first = built
    changed = parse_manifest({**reference_manifest_dict(image=IMAGE), "revision": 2, "env": {"CI": "1", "VITE_SHOP": "b"}})
    assert changed.effective_config_digest != first["effective_config_digest"]
    assert changed.digest != manifest.digest


def test_smoke_uses_pinned_artifact_even_if_build_workspace_changes(built):
    sup, run, _, _, _, target = built
    mutable = sup.run_dir(run.ref) / "verify" / target["build_id"] / "src" / "dist" / "index.html"
    original = mutable.read_bytes()
    try:
        mutable.write_text("WRONG MUTABLE BUILD")
        result = sup.smoke_target(run.ref, target["build_id"])
        assert result["healthy"] and "WRONG MUTABLE BUILD" not in result["body_prefix"]
        assert result["target_id"] == target["target_id"]
    finally:
        mutable.write_bytes(original)


def test_smoke_refuses_corrupt_pinned_artifact(built):
    sup, run, _, _, _, target = built
    artifact = sup.run_dir(run.ref) / "builds" / target["build_id"] / "artifact" / "index.html"
    original = artifact.read_bytes()
    try:
        artifact.write_text("tampered")
        with pytest.raises(WorkspaceError, match="artifact digest"):
            sup.smoke_target(run.ref, target["build_id"])
    finally:
        artifact.write_bytes(original)
