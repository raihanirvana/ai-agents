from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.workspace import DockerSandbox, ResourceLimits, WorkspaceSupervisor, parse_manifest, reference_manifest_dict

IMAGE = "node:22.20.0-alpine"
FIXTURE = Path(__file__).parent / "fixtures" / "reference-react-vite"


class NoContainerSandbox:
    """Test double for git/fs/authorization tests; it never starts a container.

    Docker-dependent behaviour is covered separately by tests marked `docker`.
    """

    supervisor_id = "stub-supervisor"

    def owned(self, **_kw):
        return []

    def kill_and_remove(self, _name):
        pass

    def logs(self, _name, _cap):
        return b"", b""

    def labels(self, **kw):
        return {}

    def container_name(self, *_a):
        return "stub"

    def run(self, **_kw):
        raise AssertionError("stub sandbox cannot run commands")


def small_manifest(**overrides):
    raw = reference_manifest_dict(image=IMAGE)
    raw.update(overrides)
    return parse_manifest(raw)


@pytest.fixture
def manifest():
    return small_manifest()


@pytest.fixture
def stub_sup(tmp_path):
    sup = WorkspaceSupervisor(tmp_path / "ws", sandbox=NoContainerSandbox())  # type: ignore[arg-type]
    sup.create_project("demo")
    return sup


@pytest.fixture(scope="session")
def docker_ready():
    sandbox = DockerSandbox(supervisor_id="probe")
    if shutil.which("docker") is None or not sandbox.available():
        pytest.skip("Docker daemon is not running")
    try:
        sandbox.image_id(IMAGE)
    except Exception:
        pytest.skip(f"image {IMAGE} is not present locally (docker pull {IMAGE})")
    return sandbox


@pytest.fixture
def real_sup(tmp_path, docker_ready):
    sup = WorkspaceSupervisor(tmp_path / "ws")
    sup.create_project("demo")
    yield sup
    # Best-effort safety net so a failing test never leaves containers behind.
    for row in sup.sandbox.owned():
        subprocess.run(["docker", "rm", "-f", row["name"]], capture_output=True)


def start(sup, manifest, *, role="developer", limits=None, egress=False, project="demo", ticket="T-1"):
    return sup.start_attempt(project, ticket_id=ticket, scope_version=1, role=role, attempt=1, generation=1,
                             lease_id="lease-1", manifest=manifest, limits=limits or ResourceLimits(),
                             allow_install_egress=egress)
