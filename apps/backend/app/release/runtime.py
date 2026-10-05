"""Release worker (POSIX + Docker + Git): freeze verification, local export, combined synchronisation.

One job per operation (stage `release`, lane execution). While one is active the integrator does not move the accepted
tip, so the freeze is real: a ticket accepted meanwhile stays pending and joins the next release.
Approval is never taken here. This runtime only produces evidence and drafts; the user approves the exact target.
"""
import json
import uuid
from pathlib import Path

from sqlalchemy import select

from app.domain import Actor, Conflict
from app.persistence import append_event, append_message
from app.persistence.columns import utcnow
from app.persistence.models import Artifact, Candidate, Job, Project, Release
from app.persistence.transactions import bind_service
from app.pipeline.contracts import BrowserTest, QaPlan, digest_of
from app.pipeline.harness import DockerHarness
from app.pipeline.workspace import FencedWorkspace, ProductWorkspace, cleanup_workspace, pack_tree
from app.workers.runtime import Outcome
from app.workspace.errors import WorkspaceError
from app.workspace.manifest import parse_manifest
from app.workspace.runspec import RunRef

HARNESS_TIMEOUT_S = 900


class ReleaseBlocked(Exception):
    """A concrete reason the operation cannot complete; stored on the job and shown to the user."""


class ReleaseRuntime:
    name = "release"

    def __init__(self, db, store, workflow, root, redactor, *, harness_timeout_s=HARNESS_TIMEOUT_S):
        self.db, self.store, self.workflow = db, store, workflow
        self.root, self.redactor = Path(root).resolve(), redactor
        self.harness_timeout_s = harness_timeout_s

    # -- supervisor contract ---------------------------------------------------------------------
    def stop(self, ctx):
        ctx.cancelled.set()
        ctx.stop_resources()

    def reconcile(self, snapshot):
        from app.workspace import WorkspaceSupervisor
        with self.db.read() as s:
            job = s.get(Job, snapshot["id"])
            if job is None or job.status == "running" and job.lease_expires_at and job.lease_expires_at > utcnow():
                return False
        generation = snapshot["runtime_ref"]["cleanup"]["generation"]
        sup = WorkspaceSupervisor(self.root)
        for resource in snapshot["runtime_ref"].get("resources", []):
            owner = snapshot["id"] + ":" + str(generation)
            if resource.get("generation") != generation or resource.get("owner", owner) != owner:
                return False
            if resource.get("kind") == "pipeline_workspace":
                cleanup_workspace(sup, RunRef(resource["project_id"], resource["run_id"]), snapshot["id"], generation)
            elif resource.get("kind") == "pipeline_containers":
                from app.pipeline.harness import remove_owned
                for name in resource["names"]:
                    remove_owned(sup.sandbox, name, resource["owner"])
            elif resource.get("kind") != "process_groups":
                return False
        return True

    def run(self, ctx):
        identity = ctx.queue.verify(ctx.lease)
        payload = ctx.job["runtime_ref"]["payload"]
        task = payload.get("task")
        if identity["role"] != "technical-lead" or identity["ticket_id"] is not None or ctx.job["stage"] != "release":
            return Outcome("failed", error="invalid release job identity")
        handler = {"verify": self._verify, "export": self._export, "sync": self._sync}.get(task)
        if handler is None:
            return Outcome("failed", error="unknown release task")
        try:
            return handler(ctx, identity, payload)
        except ReleaseBlocked as exc:
            return self._blocked(ctx, identity, task, str(exc))
        except (ValueError, OSError, WorkspaceError, Conflict) as exc:
            return self._blocked(ctx, identity, task, str(exc)[:500])

    def _blocked(self, ctx, identity, task, reason):
        """Permanent, visible failure: the job fails (no retry) and a message with the reason is recorded."""
        with self.db.write() as s:
            ctx.queue.verify_identity(s, identity)
            append_message(s, project_id=identity["project_id"], thread_id="release:" + identity["project_id"],
                sender="service:release", recipient="user", kind="message", body=f"Release {task} blocked: {reason}"[:7900],
                idempotency_key=f"release-blocked:{identity['root_job_id']}:{identity['generation']}",
                meta={"intent": "release_blocked", "task": task, "job_id": identity["job_id"]})
            bind_service(ctx.queue, s).fail(ctx.lease, error=reason[:500], retryable=False)
        return Outcome("failed", {}, reason)

    # -- shared build + combined regression ----------------------------------------------------------------
    def _manifest(self, payload):
        try:
            return parse_manifest(payload["manifest"])
        except Exception as exc:  # noqa: BLE001
            raise ReleaseBlocked(f"runner manifest is invalid: {exc}") from exc

    def combined_suite(self, entries):
        """The regression of a release is the union of the QA suites that accepted each included ticket."""
        tests, required = [], set()
        with self.db.read() as s:
            for entry in entries:
                candidate = s.get(Candidate, entry["candidate_id"])
                if candidate is None or candidate.status != "accepted" or not candidate.target_artifact_id:
                    raise ReleaseBlocked(f"ticket #{entry['number']} has no accepted verified target")
                target = json.loads(self.store.read_bytes(s, candidate.target_artifact_id))
                if not target.get("suite_artifact_id"):
                    raise ReleaseBlocked(f"ticket #{entry['number']} has no stored QA suite")
                plan = QaPlan.model_validate(json.loads(self.store.read_bytes(s, target["suite_artifact_id"])))
                for test in plan.tests:
                    name = f"t{entry['number']}-{test.id}"
                    if len(name) > 64:
                        name = f"t{entry['number']}-{digest_of(test.id)[:20]}"
                    tests.append(BrowserTest(id=name, purpose=test.purpose, steps=test.steps,
                                             uac=[f"{entry['ticket_id']}:{u}" for u in test.uac]))
        required = {f"{e['ticket_id']}:{u['id']}" for e in entries for u in e["uac"] if u.get("mode", "automated") == "automated"}
        if not tests:
            raise ReleaseBlocked("the included tickets have no automated regression tests")
        if len({t.id for t in tests}) != len(tests):
            raise ReleaseBlocked("regression test IDs collide between tickets")
        suite = QaPlan.model_construct(kind="qa_plan", summary=f"Release regression of {len(entries)} accepted tickets", tests=tests)
        return suite, required

    def verify_commit(self, ctx, identity, *, commit_sha, entries, manifest, accepted_tip, extra_target=None, label="release",
                      regression_entries=None):
        """Build `commit_sha` in a sandbox, run the repo gate and the combined browser regression on ONE target.

        Returns artifact ids, the receipt facts and whether it passed. Stored artifacts are immutable evidence."""
        from app.workspace import WorkspaceSupervisor  # noqa: F401  (POSIX only)
        sup = FencedWorkspace(self.root, ctx)
        harness = DockerHarness(sup.sandbox, timeout_s=self.harness_timeout_s)
        helper = ProductWorkspace(self.db, self.store, self.workflow, self.root, harness, self.redactor)
        regression_entries = entries if regression_entries is None else regression_entries
        suite, required = self.combined_suite(regression_entries)
        run_id = "run-" + uuid.uuid4().hex[:12]
        ref = RunRef(identity["project_id"], run_id)
        descriptor = {"kind": "pipeline_workspace", "project_id": ref.project_id, "run_id": run_id,
                      "generation": identity["generation"], "owner": ctx.tag}
        ctx.queue.register_resource(ctx.lease, descriptor)
        ctx.add_stopper(lambda: cleanup_workspace(sup, ref, ctx.lease.job_id, ctx.lease.generation), resource=descriptor)
        started = sup.start_attempt(identity["project_id"], ticket_id=label, scope_version=1, role="developer",
            attempt=identity["attempt"], generation=identity["generation"], lease_id=identity["lease_owner"],
            manifest=manifest, run_id=run_id, allow_install_egress=True,
            provenance={"created_by": "product-release", "job_id": identity["job_id"]})
        built = sup.build_baseline(ref, commit_sha)
        source = sup.run_dir(ref) / "verify" / built["build_id"] / "src"
        gate = ctx.tool_call("repo_gate", lambda: helper.run_gate(ctx, sup, started, manifest, source))
        site = sup.run_dir(ref) / "builds" / built["build_id"] / "artifact"
        bundle_files = pack_tree(site)
        scope_digest = self.workflow.scope_digest(entries)
        local = sup._store(ref)
        with self.db.write() as s:
            current = ctx.queue.identity(s, ctx.lease)
            pid = current["project_id"]
            commands = helper.command_reports(s, current, local.dir / "evidence")
            commit = self.store.put_git_commit(s, project_id=pid, sha=commit_sha)
            bundle = self.store.put_json(s, project_id=pid, kind="other", name="release-build-files.json",
                document=bundle_files, meta={"producer": "builder"})
            gate_doc = self.store.put_json(s, project_id=pid, kind="report", name="release-repo-gates.json",
                document={"commit_sha": commit_sha, "gate": gate, "commands": commands}, meta={"producer": "verification"})
            build = self.store.put_json(s, project_id=pid, kind="build_record", name="release-build.json",
                document={**built, "bundle_artifact_id": bundle.id, "gate_artifact_id": gate_doc.id}, meta={"producer": "builder"})
            suite_art = self.store.put_json(s, project_id=pid, kind="report", name="release-qa-suite.json",
                document=suite.model_dump(), meta={"producer": "release-composer"})
            runner = harness.identity()
            document = {"kind": "release_target", "project_id": pid, "accepted_tip": accepted_tip, "commit_sha": commit_sha,
                "build_artifact_id": build.id, "build_digest": built["build_digest"], "scope_digest": scope_digest,
                "regression_scope": regression_entries, "regression_scope_digest": self.workflow.scope_digest(regression_entries),
                "suite_artifact_id": suite_art.id, "suite_digest": suite.digest, "gate_artifact_id": gate_doc.id,
                "toolchain_digest": digest_of(built["toolchain"]), "config_digest": digest_of(manifest.effective_config()),
                "fixture_digest": digest_of(manifest.fixture), "migration_digest": digest_of(manifest.migrations),
                "runner": runner, "execution_manifest": manifest.to_dict(), "node_image_id": built["toolchain"]["image_id"],
                "tickets": [{"ticket_id": e["ticket_id"], "integrated_sha": e["integrated_sha"],
                             "target_digest": e["target_digest"]} for e in entries], **(extra_target or {})}
            target = self.store.put_json(s, project_id=pid, kind="target_manifest", name="release-target.json",
                document=document, meta={"producer": "builder",
                    "technical_review_evidence_ids": document.get("technical_review_evidence_ids", [])})
            logs = [r[k] for r in commands for k in ("stdout_file_artifact_id", "stderr_file_artifact_id")]
        proof = ctx.tool_call("combined_regression", lambda: harness.run(ctx, site, target.checksum, suite,
            built["toolchain"]["image_id"], expected_runner=runner))
        covered = set(proof["coverage"])
        missing = sorted(required - covered)
        passed = proof["status"] == "passed" and gate["status"] == "passed" and not gate.get("infrastructure_failure") and not missing
        reason = ("" if passed else
                  "repository tests did not pass: " + gate["status"] if gate["status"] != "passed" else
                  "combined browser regression " + proof["status"] if proof["status"] != "passed" else
                  "automated UAC without regression coverage: " + ", ".join(missing[:5]))
        receipt_doc = {"kind": "release_verification", "status": "passed" if passed else ("failed" if proof["status"] == "failed" or gate["status"] == "failed" else "incomplete"),
            "target_artifact_id": target.id, "target_digest": target.checksum, "accepted_tip": accepted_tip,
            "commit_sha": commit_sha, "fake_provider": False,
            "infrastructure_failure": bool(proof.get("infrastructure_failure") or gate.get("infrastructure_failure")),
            "counts": proof["counts"], "expected_test_ids": [t.id for t in suite.tests], "executed_test_ids": proof["executed"],
            "uac_coverage": proof["coverage"], "missing_automated_uac": missing, "repo_gate": {
                "status": gate["status"], "counts": gate["counts"]}, "scope_digest": scope_digest, "suite_digest": suite.digest,
            "invocation_id": proof["invocation_id"], "reason": reason,
            "commands": [{"argv": r["argv"], "exit_code": r["exit_code"]} for r in commands if r["exit_code"] is not None]
                        + proof["commands"]}
        with self.db.write() as s:
            ctx.queue.identity(s, ctx.lease)
            pid = identity["project_id"]
            diagnostics = []
            for item in proof.pop("diagnostics", []):
                art = self.store.put_bytes(s, project_id=pid, kind=item["kind"], data=item["data"],
                    name=item["kind"] + (".png" if item["kind"] == "screenshot" else ".zip"),
                    meta={"producer": "verification", "test_id": item["test_id"]})
                diagnostics.append(art.id)
            acceptance = self.store.put_json(s, project_id=pid, kind="report", name="release-acceptance.json",
                document=self.redactor.redact_value(proof), meta={"producer": "verification"})
            receipt = self.store.put_json(s, project_id=pid, kind="report", name="release-verification.json",
                document=receipt_doc, meta={"producer": "verification"})
        return {"passed": passed, "reason": reason, "target": target, "build": build, "commit": commit,
                "evidence_ids": [receipt.id, acceptance.id, gate_doc.id, suite_art.id, bundle.id, *diagnostics, *logs],
                "receipt_id": receipt.id, "proof": proof, "scope_digest": scope_digest}

    # -- tasks ---------------------------------------------------------------------------------------------------
    def _verify(self, ctx, identity, payload):
        manifest = self._manifest(payload)
        tip, entries = payload["accepted_tip"], payload["scope"]
        with self.db.read() as s:
            project = s.get(Project, identity["project_id"])
            if project.workflow.get("accepted_tip") != tip:
                raise ReleaseBlocked("the accepted tip moved after the freeze was requested; request a new release")
        result = self.verify_commit(ctx, identity, commit_sha=tip, entries=entries, manifest=manifest, accepted_tip=tip,
                                    regression_entries=payload.get("regression_scope", entries))
        return self._publish(ctx, identity, entries, tip, result, replaces=None)

    def _publish(self, ctx, identity, entries, tip, result, *, replaces, sync_candidate=False, extra_evidence=()):
        actor = Actor("service:release-verification", "verification", identity["project_id"])
        with self.db.write() as s:
            ctx.queue.verify_identity(s, identity)
            wf = bind_service(self.workflow, s)
            release = wf.draft_release(actor, scope_snapshot=entries, accepted_tip=tip,
                target_artifact_id=result["target"].id, target_digest=result["target"].checksum,
                build_artifact_id=result["build"].id, commit_artifact_id=result["commit"].id,
                evidence_ids=[*result["evidence_ids"], *extra_evidence], verification_passed=result["passed"],
                sync_candidate=sync_candidate, replaces=replaces)
            append_message(s, project_id=release.project_id, thread_id="release:" + release.id, sender="service:release",
                recipient="user", kind="message", attachment_ids=[result["receipt_id"]],
                body=("Release verified on one combined target; waiting for your approval." if result["passed"]
                      else "Release verification failed: " + result["reason"]),
                idempotency_key="release-draft:" + identity["root_job_id"],
                meta={"intent": "release_drafted", "release_id": release.id, "passed": result["passed"]})
            done = {"release_id": release.id, "status": release.status, "pipeline_completion": {
                "job_id": identity["job_id"], "generation": identity["generation"]}}
            bind_service(ctx.queue, s).complete(ctx.lease, done)
        return Outcome("succeeded", done)

    def _export(self, ctx, identity, payload):
        from .export import export_release
        return export_release(self, ctx, identity, payload)

    def _sync(self, ctx, identity, payload):
        from .sync import sync_release
        return sync_release(self, ctx, identity, payload)
