"""LABELLED FAKE runtime/provider for tests and dry runs. It never produces real QA evidence.

Jobs run by it carry runtime_ref.fake=true, every job event has "fake": true, and completed
jobs get result.fake_provider=true (the domain refuses fake QA receipts). The script lives in
the job payload:

    {"script": [{"model": {"output_tokens": 10}}, {"tool": "read_file"}, {"finish": {...}}],
     "resume_script": [...]}   # used after an input request was answered

Steps: model (usage dict, or null = provider did not report usage), tool, loop_tools,
sleep (seconds), ask ({question, key, checkpoint}), quota (retry seconds), fail
({error, retryable}), crash (message), spawn (seconds; real POSIX process group with a
child), finish (result dict).
"""
from __future__ import annotations

import os
import sys

from app.workers.runtime import Cancelled, Outcome, RunContext

# The child starts a grandchild, so stopping must take the whole process group.
_CHILD = ("import subprocess, sys, time; "
          "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(%(s)s)']); time.sleep(%(s)s)")


class FakeRuntime:
    name = "fake"

    def __init__(self, workspace=None, manifest=None):
        # Optional DEV-005 WorkspaceSupervisor: the "workspace" step opens a real run whose
        # credential/containers must be revoked and archived when this attempt is stopped.
        self.workspace, self.manifest = workspace, manifest
        self.workspace_runs: dict[str, object] = {}

    def run(self, ctx: RunContext) -> Outcome:
        payload = ctx.job["runtime_ref"].get("payload", {})
        script = payload.get("resume_script" if ctx.answer is not None else "script", [])
        ctx.log(f"FAKE runtime start (answer={ctx.answer!r})")
        for step in script:
            if ctx.cancelled.is_set():
                raise Cancelled()
            (kind, value), = step.items()
            if kind == "model":
                ctx.model_call(lambda max_tokens: ("fake reply", value))
            elif kind == "tool":
                ctx.tool_call(value, lambda: {"fake": True})
            elif kind == "loop_tools":
                while True:  # a model that never stops asking for tools: the cap must stop it
                    ctx.tool_call("fake_loop", lambda: {"fake": True})
            elif kind == "sleep":
                if ctx.cancelled.wait(value):
                    raise Cancelled()
            elif kind == "ask":
                ctx.request_input(value["question"], value.get("checkpoint", {}), value["key"])
            elif kind == "quota":
                raise ctx.provider_quota(value, "fake provider 429")
            elif kind == "fail":
                return Outcome("failed", error=value["error"], retryable=value.get("retryable", False))
            elif kind == "crash":
                raise RuntimeError(value)
            elif kind == "spawn":
                self._spawn(ctx, value)
            elif kind == "workspace":
                self._workspace(ctx, value)
            elif kind == "finish":
                return Outcome("succeeded", {**value, "answer": ctx.answer})
            else:
                raise ValueError(f"unknown fake step {kind}")
        return Outcome("succeeded", {"answer": ctx.answer})

    @staticmethod
    def _spawn(ctx: RunContext, seconds: float) -> None:
        if not hasattr(os, "killpg"):
            raise NotImplementedError("process-group supervision needs a POSIX host (use WSL on Windows)")
        pgid = ctx.spawn_process([sys.executable, "-c", _CHILD % {"s": seconds}])
        ctx.log(f"spawned process group {pgid}")

    def _workspace(self, ctx: RunContext, project: str) -> None:
        job = ctx.job
        # Persist the creation intent first: recovery also finds a run created just before
        # a worker died, even if its callback/run id was never published in memory.
        self.workspace._project_dir(project)  # validate the workspace project locator
        resource = {"kind": "workspace", "project": project, "supervisor_id": self.workspace.supervisor_id,
                    "generation": ctx.lease.generation}
        ctx.queue.register_resource(ctx.lease, resource)
        run = self.workspace.start_attempt(
            project, ticket_id=job["ticket_id"] or job["id"], scope_version=job["scope_version"] or 1,
            role=job["runtime_ref"]["role"], attempt=1, generation=ctx.lease.generation,
            lease_id=f"{ctx.lease.job_id}:{ctx.lease.generation}", manifest=self.manifest,
            provenance={"job_id": ctx.lease.job_id})
        self.workspace_runs[ctx.lease.job_id] = run
        ctx.add_stopper(lambda: self.workspace.stop_run(run.ref, "attempt_stopped"), resource=resource)
        ctx.log(f"workspace run {run.ref.run_id} started")

    def reconcile(self, snapshot: dict) -> bool:
        for resource in snapshot["runtime_ref"].get("resources", []):
            if resource.get("kind") == "process_groups":
                continue  # verified/reaped by the supervisor before the adapter callback
            from app.workspace.runspec import RunRef, RunStore
            if self.workspace is None:
                return False
            if resource.get("kind") != "workspace" or resource.get("supervisor_id") != self.workspace.supervisor_id:
                return False
            generation = resource["generation"]
            for path in (self.workspace._project_dir(resource["project"]) / "runs").glob("run-*"):
                if not (path / "runspec.json").is_file():
                    continue
                spec = RunStore(path).spec()
                if (spec.provenance.get("job_id") == snapshot["id"] and spec.generation == generation
                        and spec.lease_id == f"{snapshot['id']}:{generation}"
                        and spec.provenance.get("supervisor_id") == self.workspace.supervisor_id):
                    self.workspace.stop_run(RunRef(spec.project_id, spec.run_id), "worker_recovery")
        return True

    def stop(self, ctx: RunContext) -> None:
        ctx.cancelled.set()
        ctx.stop_resources()
