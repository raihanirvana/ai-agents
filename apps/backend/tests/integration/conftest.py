"""Integration tests: real managed Git repository + broker refs, real SQLite. QA evidence is a domain contract
fixture (tests/domain), not model output; the Git commits, refs and CAS updates are real."""
import itertools

import pytest

pytest.importorskip("fcntl")

from app.integration.integrator import Integrator  # noqa: E402
from app.persistence.models import Project  # noqa: E402
from app.workspace import WorkspaceSupervisor  # noqa: E402
from app.workspace.manifest import parse_manifest, reference_manifest_dict  # noqa: E402
from tests.domain.conftest import World  # noqa: E402
from tests.persistence.conftest import db, db_path, store  # noqa: E402,F401

SEQ = itertools.count(1)


class IntegrationEnv:
    def __init__(self, db, store, tmp_path):
        self.db, self.store = db, store
        self.world = World(db, store)
        self.root = tmp_path / "workspaces"
        self.sup = WorkspaceSupervisor(self.root)
        self.pid = self.world.project.id
        self.base = self.sup.create_project(self.pid)
        with db.write() as s:  # the fixture world starts from a synthetic base; point it at the real managed repo
            p = s.get(Project, self.pid)
            p.workflow = {**p.workflow, "accepted_tip": self.base}
        self.manifest = parse_manifest(reference_manifest_dict())

    @property
    def broker(self):
        return self.sup.broker(self.pid)

    def commit(self, files, base=None):
        """A real candidate commit on top of `base` (default: the current accepted ref), via the broker."""
        started = self.sup.start_attempt(self.pid, ticket_id="fixture", scope_version=1, role="developer", attempt=1,
                                         generation=next(SEQ), lease_id="fixture", manifest=self.manifest)
        if base is not None and base != started.spec.base_sha:
            raise AssertionError("fixture commits build on the accepted ref")
        for name, content in files.items():
            self.sup.write_file(started.ref, started.credential, name, content.encode())
        record = self.sup.submit_candidate(started.ref, started.credential, "fixture change")
        self.sup.stop_run(started.ref, "fixture committed")
        return record["sha"]

    def to_uat(self, sha, t=None, document=None):
        """Ticket -> real commit candidate -> reviewed -> verified -> UAT (contract QA fixture)."""
        w = self.world
        t = t or w.approve(w.new(document))
        dev, ref = w.job(t, "developer")
        t = w.ticket(t.id)
        commit, receipt, base = w.commit_receipt(t, ref, sha=sha)
        c = w.w.submit_candidate(dev, t.id, t.revision, ref, commit_artifact_id=commit.id, commit_receipt_id=receipt.id,
                                 base_sha=base, submission_key=f"integration-{next(SEQ)}")
        target = w.target(w.ticket(t.id), c)
        lead, ref = w.job(t, "technical-lead")
        w.w.approve_review(lead, t.id, w.ticket(t.id).revision, ref, c.id)
        _, qa = w.job(t, "qa")
        v, smoke = w.proof(w.ticket(t.id), c, target)
        w.w.open_uat(w.actor("verification"), t.id, w.ticket(t.id).revision, qa, c.id, v.id, smoke.id)
        return w.ticket(t.id), c, target, v, [*v.evidence_artifact_ids, smoke.id]

    def accept(self, t, c, target, v, ids):
        t = self.world.ticket(t.id)
        return self.world.w.accept_uat(self.world.user, t.id, t.revision, c.id, t.current_version, target.id,
                                       target.checksum, v.id, ids)

    def integrator(self, **kw):
        return Integrator(self.db, self.store, self.world.w, self.root, **kw)


@pytest.fixture
def env(db, store, tmp_path):
    return IntegrationEnv(db, store, tmp_path)
