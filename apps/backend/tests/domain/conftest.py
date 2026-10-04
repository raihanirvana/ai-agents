"""Real DB/artifacts, synthetic trusted receipts: domain contracts, not real QA/provider evidence."""
from datetime import timedelta
from types import SimpleNamespace
import itertools
import pytest
from app.domain import Actor, ApprovalItem, Attempt, Workflow
from app.persistence import ArtifactStore
from app.persistence.columns import utcnow
from app.persistence.models import Project, Ticket, Job, Candidate, Verification
from tests.persistence.conftest import db, db_path, store
from tests.persistence import factories as f

SEQ = itertools.count(1)
HASH = "d" * 64
SCOPE = {"title": "Menu", "description": "Coffee menu", "uac": [{"id": "UAC-1", "text": "Add coffee"}], "dependencies": []}


class World:
    def __init__(self, db, store):
        self.db, self.store = db, store
        with db.write() as s:
            self.project = f.project(s)
        self.w = Workflow(db, store)
        self.user = self.actor("user")
        self.w.initialize_base(self.actor("integrator"), 1, f.SHA_B)

    def actor(self, role, **kw):
        return Actor("principal:" + role, role, self.project.id, **kw)

    def ticket(self, tid):
        with self.db.read() as s:
            return s.get(Ticket, tid)

    def new(self, document=None):
        return self.w.create_ticket(self.user, document or SCOPE)

    def approve(self, t):
        t = self.ticket(t.id)
        self.w.approve_scope(self.user, [ApprovalItem(t.id, t.current_version, t.revision)])
        return self.ticket(t.id)

    def job(self, t, role, stage=None, bind=True):
        t = self.ticket(t.id)
        stage = stage or {"developer": "development", "technical-lead": "technical_review", "qa": "qa", "po": "po"}[role]
        with self.db.write() as s:
            j = f.job(s, self.project, t, stage=stage, status="running", lease_owner="principal:" + role,
                lease_generation=1, lease_expires_at=utcnow() + timedelta(hours=1), runtime_ref={"role": role})
        a, ref = self.actor(role, job_id=j.id, generation=1), Attempt(j.id, 1, t.current_version)
        if bind:
            self.w.bind_attempt(self.actor("scheduler"), t.id, t.revision, ref)
        return a, ref

    def commit_receipt(self, t, ref, sha=f.SHA_A, producer="broker", **override):
        """Synthetic trusted broker receipt; one per attempt despite Git SHA dedupe."""
        with self.db.write() as s:
            commit = self.store.put_git_commit(s, project_id=t.project_id, sha=sha)
            base = s.get(Project, t.project_id).workflow["accepted_tip"]
            receipt = self.store.put_json(s, project_id=t.project_id, kind="report", name="commit-receipt.json",
                document={"kind": "candidate_commit", "project_id": t.project_id, "ticket_id": t.id,
                    "commit_artifact_id": commit.id, "commit_sha": commit.checksum, "base_sha": base,
                    "source_attempt": {"job_id": ref.job_id, "generation": ref.generation,
                        "scope_version": ref.scope_version}, **override}, meta={"producer": producer})
        return commit, receipt, base

    def submitted(self, t=None):
        t = t or self.approve(self.new())
        a, ref = self.job(t, "developer")
        t = self.ticket(t.id)
        commit, receipt, base = self.commit_receipt(t, ref)
        c = self.w.submit_candidate(a, t.id, t.revision, ref, commit_artifact_id=commit.id,
            commit_receipt_id=receipt.id, base_sha=base, submission_key="submission-" + str(next(SEQ)))
        return self.ticket(t.id), c

    def target(self, t, c, **override):
        with self.db.write() as s:
            build = self.store.put_json(s, project_id=t.project_id, kind="build_record",
                name="build.json", document={"build_digest": HASH})
            document = {"project_id": t.project_id, "ticket_id": t.id, "candidate_id": c.id,
                "scope_version": t.current_version, "source_sha": c.commit_sha, "base_sha": c.base_sha,
                "build_artifact_id": build.id, **{k: HASH for k in ("build_digest", "runner_manifest_digest",
                    "toolchain_digest", "config_digest", "fixture_digest", "migration_digest")}, **override}
            current_candidate = s.get(Candidate, c.id)
            target = self.store.put_json(s, project_id=t.project_id, kind="target_manifest", name="target.json", document=document,
                meta={"producer": "builder", "source_attempt": current_candidate.integration["source_attempt"]})
        self.w.attach_target(self.actor("builder"), t.id, self.ticket(t.id).revision, c.id,
            build_artifact_id=build.id, target_artifact_id=target.id, target_digest=target.checksum)
        return target

    def qa(self, t=None):
        t, c = self.submitted(t)
        target = self.target(t, c)
        a, ref = self.job(t, "technical-lead")
        self.w.approve_review(a, t.id, self.ticket(t.id).revision, ref, c.id)
        a, ref = self.job(t, "qa")
        return self.ticket(t.id), c, target, ref

    def proof(self, t, c, target, **results_override):
        with self.db.write() as s:
            c = s.get(Candidate, c.id)
            evidence = self.store.put_json(s, project_id=t.project_id, kind="report", name="evidence.json",
                document={"contract_fixture": True}, meta={"producer": "verification"})
            v = Verification(candidate_id=c.id, target_artifact_id=target.id, target_digest=target.checksum,
                commit_artifact_id=c.commit_artifact_id, build_artifact_id=c.build_artifact_id,
                context_artifact_id=c.context_artifact_id, evidence_id="proof-" + str(next(SEQ)), suite_digest=HASH,
                expected_test_ids=["mandatory-1"], counts={"discovered": 1, "executed": 1, "passed": 1, "failed": 0, "skipped": 0},
                uac_coverage={"UAC-1": ["mandatory-1"]}, status="passed", evidence_artifact_ids=[evidence.id],
                results={"executed_test_ids": ["mandatory-1"], "commands": [{"exit_code": 0, "argv": ["contract-fixture"]}],
                    "job_id": self.ticket(t.id).workflow["attempts"]["qa"]["job_id"],
                    "generation": self.ticket(t.id).workflow["attempts"]["qa"]["generation"],
                    "fake_provider": False, "infrastructure_failure": False, "contract_fixture": True, **results_override})
            s.add(v)
            s.flush()
            smoke = self.store.put_json(s, project_id=t.project_id, kind="report", name="smoke.json",
                document={"target_artifact_id": target.id, "target_digest": target.checksum,
                    "status": "passed", "kind": "preview_smoke"}, meta={"producer": "verification"})
        return v, smoke

    def uat(self, t=None):
        t, c, target, ref = self.qa(t)
        v, smoke = self.proof(t, c, target)
        self.w.open_uat(self.actor("verification"), t.id, self.ticket(t.id).revision, ref, c.id, v.id, smoke.id)
        return self.ticket(t.id), c, target, v, [*v.evidence_artifact_ids, smoke.id]

    def integrating(self, t=None):
        t, c, target, v, ids = self.uat(t)
        op = self.w.accept_uat(self.user, t.id, t.revision, c.id, t.current_version, target.id, target.checksum, v.id, ids)
        return self.ticket(t.id), c, op

    def accepted(self, t=None):
        t, c, op = self.integrating(t)
        self.w.finish_integration(self.actor("integrator"), t.id, t.revision, c.id, op["operation_id"], c.commit_sha)
        return self.ticket(t.id), c


@pytest.fixture
def world(db, store):
    return World(db, store)
