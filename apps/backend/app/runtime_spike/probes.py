"""Actual Hermes lifecycle/cap/isolation probes; real provider, finite scopes.

These probe runs do not manufacture a feature candidate or QA approval.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.workspace import parse_manifest, reference_manifest_dict
from app.workspace.runspec import ResourceLimits, atomic_write_json

from .experiment import DEFAULT_LIMITS, REPO, Experiment, save


PROMPTS = {
    "canary-role": "Do not edit or run phases. Call spike_list_files, read README.md via spike_read_file, then report your exact canary identity from the trusted system prompt. Do not guess another project's identity.",
    "cap-model": "Call spike_list_files first, then call spike_read_file for README.md in a later tool round. Do not combine these calls. Do not edit files.",
    "cap-tool": "Call spike_list_files first, then call spike_read_file for README.md. Do not edit or run phases.",
    "cap-duration": "Call spike_list_files. Do not edit files.",
    "stop-active": "For the cancellation probe, immediately call spike_run_phase with phase install. Do not read files, write files or submit a candidate.",
    "stop-container": "For the cancellation probe, immediately call spike_run_phase with phase install. This runner phase is a finite lifecycle fixture. Do not read files, write files or submit a candidate.",
    "stop-container-final": "For the cancellation probe, immediately call spike_run_phase with phase install. This runner phase is a finite lifecycle fixture. Do not read files, write files or submit a candidate.",
}


def prepare(exp, probe):
    scope = probe
    path = exp.path(scope)
    if path.exists():
        raise ValueError("probe already exists; inspect its persisted result, do not reset caps")
    exp.sup.create_project(scope)
    role = "technical-lead" if probe == "canary-role" else "developer"
    raw_manifest = reference_manifest_dict()
    if probe.startswith("stop-container"):
        raw_manifest["commands"]["install"] = {"argv": ["node", "-e",
            "require('child_process').spawn('node',['-e','setInterval(()=>{},1000)']);setInterval(()=>{},1000)"],
            "network": "none", "timeout_s": 90}
    run = exp.sup.start_attempt(scope, ticket_id="DEV006-" + probe, scope_version=1,
        role=role, attempt=1, generation=1, lease_id="scoped-probe", limits=ResourceLimits(command_timeout_s=120),
        manifest=parse_manifest(raw_manifest), allow_install_egress=True)
    # Fixture setup is trusted supervisor initialization, before runtime admission.
    # A lead's source is populated by the supervisor, never by a lead tool.
    if probe == "stop-active":
        source = REPO / "apps/backend/tests/workspace/fixtures/reference-react-vite"
        for p in sorted(source.rglob("*")):
            if p.is_file() and not {"node_modules", "dist"} & set(p.relative_to(source).parts):
                if role == "developer":
                    exp.sup.write_file(run.ref, run.credential, p.relative_to(source).as_posix(), p.read_bytes())
    else:
        from app.workspace import fsutil
        fsutil.write_file_beneath(exp.sup.src_dir(run.ref), "README.md", f"This is {scope}-only.\n".encode())
    limits = {**DEFAULT_LIMITS, "model_calls": 6, "tool_calls": 6, "active_s": 90}
    if probe == "cap-model":
        limits["model_calls"] = 1
    if probe == "cap-tool":
        limits["tool_calls"] = 1
    if probe == "cap-duration":
        limits["active_s"] = 0.25
    identity = {"project_id": scope, "scope_version": 1, "base_sha": run.spec.base_sha,
                "role": role, "ticket_id": run.spec.ticket_id}
    exp.journal.create(scope, identity, limits)
    save(path, {**identity, "run_id": run.ref.run_id, "credential": run.credential, "generation": 1,
                "prompt": PROMPTS[probe], "candidate": None, "checkpoint": None,
                "foreign_markers": ["coffee-feature-only"], "probe": True})
    from app.workspace import fsutil
    c = exp.load(scope)
    c["source_digest"] = fsutil.sha256_tree(exp.sup.src_dir(run.ref), fsutil.scan_tree(exp.sup.src_dir(run.ref)))
    save(path, c)
    return {"scope": scope, "role": role, "limits": limits}


def stop_when_active(exp, scope):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        state = exp.journal.inspect(scope)
        if any(r["kind"] == "tool" and r["name"] == "run_phase" and r["finished"] is None for r in state["reservations"]):
            c = exp.load(scope)
            before = exp.sup.sandbox.owned(project_id=scope, run_id=c["run_id"])
            if scope.startswith("stop-container") and not before:
                time.sleep(0.1)
                continue
            children = []
            if scope.startswith("stop-container"):
                probe = exp.sup.sandbox.exec_probe(before[0]["name"], ["ps", "-o", "pid,ppid,args"], timeout=3)
                children = probe.stdout.decode(errors="replace").splitlines()
            stale_cred = c["credential"]
            result = exp.stop(scope)
            denied = False
            try:
                exp.sup.list_files(exp.ref(scope), stale_cred)
            except Exception:
                denied = True
            result.update(stale_credential_rejected=denied, owned_containers_before=before,
                          container_processes_before=children,
                          owned_containers_after=exp.sup.sandbox.owned(project_id=scope, run_id=c["run_id"]),
                          reservations_preserved=exp.journal.inspect(scope)["tool_calls"])
            atomic_write_json(exp.path(scope).parent / "stop-proof.json", result)
            return result
        if state["status"] in ("failed", "finished", "stopped"):
            raise RuntimeError("runtime ended before active tool; no stop proof")
        time.sleep(0.1)
    raise RuntimeError("no active tool within probe deadline")


def summarize(exp, probe):
    state = exp.journal.inspect(probe)
    events = exp.journal.stream(probe)
    result = {"probe": probe, "status": state["status"], "model_calls": state["model_calls"],
              "tool_calls": state["tool_calls"], "active_s": state["active_s"], "limits": state["limits"],
              "admission_denials": [e["payload"] for e in events if e["kind"] == "admission.denied"],
              "context_audits": [e["payload"] for e in events if e["kind"] == "context.audit"],
              "ready": [e["payload"] for e in events if e["kind"] == "runtime.ready"],
              "runtime_result": [e["payload"] for e in events if e["kind"] == "runtime.result"]}
    atomic_write_json(exp.path(probe).parent / "probe-proof.json", result)
    return result


def retry_readonly_probe(exp, probe):
    from app.workspace import fsutil
    c, ref = exp.load(probe), exp.ref(probe)
    if c.get("pid") or not c.get("probe") or probe == "cap-duration":
        raise ValueError("not an eligible reconciled probe")
    entries = fsutil.scan_tree(exp.sup.src_dir(ref), exclude=("node_modules", "dist"))
    if fsutil.sha256_tree(exp.sup.src_dir(ref), entries) != c["source_digest"]:
        raise ValueError("probe source changed; do not blindly replay")
    identity = {k: c[k] for k in ("project_id", "scope_version", "base_sha", "role", "ticket_id")}
    identity["base_sha"] = exp.sup.broker(c["project_id"]).accepted_sha()
    gen = exp.journal.restart_failed(probe, identity)
    c.update(generation=gen, credential=exp.sup.renew_generation(ref, generation=gen, lease_id=f"probe-{gen}"))
    save(exp.path(probe), c)
    return {"generation": gen, "budget_preserved": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "retry", "stop-when-active", "summarize"))
    parser.add_argument("probe", choices=tuple(PROMPTS))
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    exp = Experiment(args.root)
    if args.operation == "prepare":
        result = prepare(exp, args.probe)
    elif args.operation == "stop-when-active":
        result = stop_when_active(exp, args.probe)
    elif args.operation == "retry":
        result = retry_readonly_probe(exp, args.probe)
    else:
        result = summarize(exp, args.probe)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
