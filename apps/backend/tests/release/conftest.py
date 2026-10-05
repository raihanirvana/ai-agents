"""Release tests: real managed Git repository, real SQLite/artifacts, real Docker sandbox and browser runner.

Ticket QA evidence is a domain contract fixture (as in tests/integration); everything the release does with it, the Git
commits, the build, the repository gate, the combined browser regression, the exports and the synchronisation, is real.
"""
import itertools
import uuid

from sqlalchemy import select

import pytest

pytest.importorskip("fcntl")

from app.agents import Redactor  # noqa: E402
from app.http.service import bind  # noqa: E402
from app.release import requests as rr  # noqa: E402
from app.release.runtime import ReleaseRuntime  # noqa: E402
from app.persistence.models import Job, Project, Release  # noqa: E402
from app.workers import JobQueue, ProviderLimiter  # noqa: E402
from app.workers.runtime import RunContext  # noqa: E402
from app.workspace import WorkspaceSupervisor  # noqa: E402
from app.workspace.manifest import reference_manifest_dict  # noqa: E402
from tests.domain.conftest import SCOPE  # noqa: E402
from tests.integration.conftest import IntegrationEnv  # noqa: E402
from tests.persistence.conftest import db, db_path, store  # noqa: E402,F401

SEQ = itertools.count(1)
TEST_JS = "const t=require('node:test');t('repository smoke',()=>{});\n"


def page(*parts, footer="v1"):
    """One element per line, with padding, so a change far from the ticket hunks applies cleanly after a sync."""
    pad = [f"<!-- padding {i} -->" for i in range(8)]
    return "\n".join(["<!doctype html>", "<title>Coffee</title>", "<main>", *parts, "</main>", *pad, f"<footer>{footer}</footer>"]) + "\n"


def release_manifest():
    raw = reference_manifest_dict()
    raw["commands"].update(
        install={"argv": ["node", "-e", "process.exit(0)"]},
        build={"argv": ["node", "-e", "const fs=require('fs');fs.mkdirSync('dist',{recursive:true});"
                                       "fs.copyFileSync('index.html','dist/index.html')"]},
        test={"argv": ["node", "--test", "test.cjs"]},
        start={"argv": ["node", "serve.cjs"]})
    return raw


def browser_test(test_id, uac, selector, text=None):
    steps = [{"action": "assert_visible", "selector": selector}]
    if text is not None:
        steps = [{"action": "assert_text", "selector": selector, "value": text}]
    return {"id": test_id, "uac": uac, "purpose": "regression", "steps": steps}


class ReleaseEnv(IntegrationEnv):
    def __init__(self, db, store, tmp_path):
        super().__init__(db, store, tmp_path)
        self.manifest_dict = release_manifest()
        with db.write() as s:
            p = s.get(Project, self.pid)
            p.workflow = {**p.workflow, "pipeline": {"manifest": self.manifest_dict}}
        self.queue = JobQueue(db, lease_s=3600, retry_backoff_s=0, startable=self.world.w.startable)
        self.runtime = ReleaseRuntime(db, store, self.world.w, self.root, Redactor())
        self.actor = self.world.user

    # -- accepted tickets ----------------------------------------------------------------------------------
    def suite(self, tests):
        with self.db.write() as s:
            return self.store.put_json(s, project_id=self.pid, kind="report", name="qa-suite.json", meta={"producer": "qa-plan"},
                document={"kind": "qa_plan", "summary": "ticket suite", "tests": tests}).id

    def accept_ticket(self, title, files, tests, *, manual=()):
        """A real commit on the accepted tip -> verified candidate (contract QA) -> user UAT -> integrator fast-forward."""
        w = self.world
        sha = self.commit(files)
        uac = [{"id": f"UAC-{i + 1}", "text": f"{title} behaviour {i + 1}"} for i in range(max(1, len(tests)))]
        uac += [{"id": m, "text": f"{title} manual check {m}", "mode": "manual"} for m in manual]
        t = w.approve(w.new({**SCOPE, "title": title, "uac": uac}))
        dev, ref = w.job(t, "developer")
        t = w.ticket(t.id)
        commit, receipt, base = w.commit_receipt(t, ref, sha=sha)
        c = w.w.submit_candidate(dev, t.id, t.revision, ref, commit_artifact_id=commit.id, commit_receipt_id=receipt.id,
                                 base_sha=base, submission_key=f"release-test-{next(SEQ)}")
        target = w.target(w.ticket(t.id), c, suite_artifact_id=self.suite(tests))
        lead, ref = w.job(t, "technical-lead")
        w.w.approve_review(lead, t.id, w.ticket(t.id).revision, ref, c.id)
        _, qa = w.job(t, "qa")
        v, smoke = w.proof(w.ticket(t.id), c, target)
        w.w.open_uat(w.actor("verification"), t.id, w.ticket(t.id).revision, qa, c.id, v.id, smoke.id)
        t = w.ticket(t.id)
        ids = [*v.evidence_artifact_ids, smoke.id]
        w.w.accept_uat(w.user, t.id, t.revision, c.id, t.current_version, target.id, target.checksum, v.id, ids, list(manual))
        assert self.integrator().run_once() == [(t.id, "updated")]
        return w.ticket(t.id), c

    # -- release operations --------------------------------------------------------------------------------
    def services(self, s):
        return bind(s, self.store, Redactor())

    def request(self, kind, *args):
        with self.db.write() as s:
            svc = self.services(s)
            key = uuid.uuid4().hex
            if kind == "freeze":
                with self.db.read() as r:
                    revision = r.get(Project, self.pid).revision
                return rr.request_freeze(s, svc, self.actor_for(), revision, key)[0]
            release_id = args[0]
            revision = s.get(Release, release_id).revision
            fn = {"export": rr.request_export, "sync": rr.request_sync}[kind]
            return fn(s, svc, self.actor_for(), release_id, revision, key)

    def actor_for(self):
        from app.domain import Actor
        return Actor("user:local", "user", self.pid)

    def run_job(self, job, runtime=None):
        job_id = job.id if hasattr(job, "id") else job
        with self.db.write() as s:  # the fixture's finished ticket attempts are still marked running and hold the slot
            for other in s.scalars(select(Job).where(Job.status == "running", Job.stage != "release")):
                other.status = "succeeded"
        lease = self.queue.claim("worker:release-test", "execution", capacity=1, runtimes=("release",))
        if lease is None:
            with self.db.read() as s:
                rows = [(j.stage, j.status, j.lane, bool((j.runtime_ref or {}).get("cleanup")), j.runtime_ref.get("runtime"))
                        for j in s.scalars(select(Job))]
            raise AssertionError(f"release job was not claimable; jobs: {rows}")
        assert lease.job_id == job_id
        with self.db.read() as s:
            j = s.get(Job, job_id)
            snapshot = {"id": j.id, "project_id": j.project_id, "ticket_id": None, "stage": j.stage,
                        "runtime_ref": j.runtime_ref, "limits": j.limits, "lane": "execution"}
        ctx = RunContext(queue=self.queue, limiter=ProviderLimiter(), lease=lease, job=snapshot)
        try:
            return (runtime or self.runtime).run(ctx)
        finally:
            assert ctx.stop_resources()
            self.queue.finish_cleanup(job_id, lease.generation)  # what the supervisor does after the run

    def release(self, status=None):
        from sqlalchemy import select
        with self.db.read() as s:
            rows = list(s.scalars(select(Release).where(Release.project_id == self.pid).order_by(Release.created_at, Release.id)))
        rows = [r for r in rows if status is None or r.status == status]
        return rows[-1] if rows else None

    def approve_release(self, release, manual=()):
        evidence = list(release.evidence_artifact_ids)
        with self.db.read() as s:
            import json
            target = json.loads(self.store.read_bytes(s, release.target_artifact_id))
        # A named test-user explicitly attests to the pinned diffs. This fixture is not real manual code review.
        return self.world.w.approve_release(self.world.user, release.id, release.revision, release.target_artifact_id,
                                            release.target_digest, evidence, list(manual), target.get("technical_review_evidence_ids", []))


def require_docker(root):
    sup = WorkspaceSupervisor(root)
    if not sup.sandbox.available():
        pytest.skip("actual Docker required")
    sup.sandbox.image_id("node:22.20.0-alpine")
    sup.sandbox.image_id("aiagent-verification:1.63.0")


@pytest.fixture
def env(db, store, tmp_path):
    require_docker(tmp_path / "workspaces")
    return ReleaseEnv(db, store, tmp_path)



class ExistingEnv(ReleaseEnv):
    """A project onboarded from an existing local Git repository (real import), accepted tickets on top of it."""

    def __init__(self, db, store, tmp_path):
        from app.onboarding.source import import_source
        from app.workspace.gitbroker import GitBroker
        from tests.domain.conftest import World
        self.db, self.store = db, store
        self.world = World(db, store)
        self.root = tmp_path / "workspaces"
        self.sup = WorkspaceSupervisor(self.root)
        self.pid = self.world.project.id
        self.source = tmp_path / "user-repo"
        self.git = GitBroker(tmp_path / "unused.git", tmp_path / "git-home")
        self.source.mkdir()
        self.git.run(["init", "--template=", "--initial-branch=main", str(self.source)])
        files = {"index.html": page('<h1 id="home">Home</h1>'), "test.cjs": TEST_JS,
                 "package.json": '{"dependencies":{"react":"18.3.1","vite":"6.4.3"}}\n', "package-lock.json": "{}\n"}
        for name, text in files.items():
            (self.source / name).write_text(text)
        self.git.run(["add", "."], cwd=self.source)
        self.git.run(["commit", "-m", "the user's own repository"], cwd=self.source)
        self.manifest_dict = release_manifest()
        from app.workspace.manifest import parse_manifest
        self.manifest = parse_manifest(self.manifest_dict)
        saved = import_source(self.sup, self.pid, self.source, "a" * 32)
        self.base = saved["baseline_sha"]
        self.source_sha = saved["source_sha"]
        with db.write() as s:
            p = s.get(Project, self.pid)
            p.mode, p.repo_ref = "existing", str(self.source)
            p.workflow = {**p.workflow, "accepted_tip": self.base, "pipeline": {"manifest": self.manifest_dict},
                          "onboarding_detail": {"job_id": "x", "source_sha": self.source_sha, "baseline_sha": self.base}}
        self.queue = JobQueue(db, lease_s=3600, retry_backoff_s=0, startable=self.world.w.startable)
        self.runtime = ReleaseRuntime(db, store, self.world.w, self.root, Redactor())

    def drift(self, files, message="the user keeps working"):
        for name, text in files.items():
            (self.source / name).write_text(text)
        self.git.run(["add", "."], cwd=self.source)
        self.git.run(["commit", "-m", message], cwd=self.source)
        return self.git.run(["rev-parse", "HEAD"], cwd=self.source).decode().strip()

    def fingerprint(self):
        import hashlib
        return {str(q.relative_to(self.source)): hashlib.sha256(q.read_bytes()).hexdigest()
                for q in sorted(self.source.rglob("*")) if q.is_file()}


@pytest.fixture
def existing(db, store, tmp_path):
    require_docker(tmp_path / "workspaces")
    return ExistingEnv(db, store, tmp_path)
