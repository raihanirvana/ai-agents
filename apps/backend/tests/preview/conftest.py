"""Preview tests need POSIX (fcntl, unix sockets) and a local Docker engine with the pinned node image."""
import uuid

import pytest

pytest.importorskip("fcntl")

from app.preview.service import PreviewService  # noqa: E402
from tests.persistence.conftest import db, db_path, store  # noqa: E402,F401
from tests.preview.helpers import NODE_IMAGE, PreviewEnv, docker_ready  # noqa: E402


@pytest.fixture
def env(db, store, tmp_path):
    return PreviewEnv(db, store, tmp_path)


@pytest.fixture
def docker():
    if not docker_ready():
        pytest.skip(f"Docker with {NODE_IMAGE} is required")


@pytest.fixture
def service(db, store, tmp_path, docker):
    created = []

    def make(**kw):
        svc = PreviewService(db, store, tmp_path / "root", owner=f"preview:test-{uuid.uuid4().hex[:8]}",
                             health_timeout_s=kw.pop("health_timeout_s", 20), **kw)
        created.append(svc)
        return svc

    yield make
    for svc in created:
        svc.shutdown()
        leftovers = svc.sandbox._docker("ps", "-a", "--filter", "label=aiagent.container-role=preview",
                                        "--filter", f"label=aiagent.supervisor={svc.owner}", "--format", "{{.Names}}").stdout
        assert not leftovers.strip(), "preview containers leaked: " + leftovers.decode()
