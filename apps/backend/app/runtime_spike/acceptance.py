"""Trusted admission and validation for the separate DEV-006 browser runner."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from app.workspace import fsutil
from app.workspace.manifest import digest_of
from app.workspace.runspec import atomic_write_json

SUITE = Path(__file__).resolve().parents[4] / "contracts/dev006"
RUNNER_IMAGE = "aiagent-dev006-acceptance:1.63.0"


def suite_digest():
    return fsutil.sha256_tree(SUITE, fsutil.scan_tree(SUITE, exclude=("__pycache__",)))


def validate_report(report, *, invocation, target_id, digest):
    mandatory = json.loads((SUITE / "mandatory.json").read_text())
    if not isinstance(report, dict) or type(report.get("schema")) is not int or any(report.get(k) != v for k, v in {
        "schema": 1, "invocation_id": invocation, "target_id": target_id, "suite_digest": digest}.items()):
        return "incomplete"
    tests = report.get("tests")
    if not isinstance(tests, list) or not tests:
        return "incomplete"
    if any(not isinstance(r, dict) for r in tests):
        return "incomplete"
    ids = [r.get("id") for r in tests]
    if any(not isinstance(test_id, str) for test_id in ids):
        return "incomplete"
    if len(set(ids)) != len(ids) or set(ids) != set(mandatory["mandatory"]):
        return "incomplete"
    if any(type(report.get(k)) is not int for k in ("discovered", "executed", "passed", "failed", "skipped")):
        return "incomplete"
    if report["discovered"] != len(tests) or report["executed"] != len(tests) or report["skipped"] != 0:
        return "incomplete"
    coverage = set()
    for r in tests:
        if r.get("status") not in ("passed", "failed") or r.get("uac") != mandatory["mandatory"][r["id"]]:
            return "incomplete"
        coverage.update(r["uac"])
    if not set(mandatory["required_uac"]) <= coverage:
        return "incomplete"
    passed = sum(r["status"] == "passed" for r in tests)
    if report["passed"] != passed or report["failed"] != len(tests) - passed:
        return "incomplete"
    return "passed" if passed == len(tests) else "failed"


def verify_artifact(sup, ref, target):
    if target.get("run_id") != ref.run_id or target.get("project_id") != ref.project_id:
        raise ValueError("target belongs to another run")
    if digest_of({k: v for k, v in target.items() if k != "target_id"}) != target.get("target_id"):
        raise ValueError("target digest mismatch")
    stored = sup.run_dir(ref) / "evidence" / ("target-" + target["target_id"] + ".json")
    if json.loads(stored.read_text()) != target:
        raise ValueError("not an authoritative supervisor target")
    artifact = sup.run_dir(ref) / "builds" / target["build_id"] / "artifact"
    if fsutil.sha256_tree(artifact, fsutil.scan_tree(artifact)) != target["build_digest"]:
        raise ValueError("artifact digest mismatch")
    return artifact


def run_acceptance(sup, ref, target):
    # inspect_diff/checkpoint also stop attempt containers. Hold the same operation
    # lock so verification cannot race those operations. Revocation still happens
    # before waiting for this lock and is checked before publishing the report.
    with sup._store(ref).lock("operation.lock"):
        return _run_acceptance(sup, ref, target)


def _run_acceptance(sup, ref, target):
    artifact = verify_artifact(sup, ref, target)
    spec, _, store = sup._require_active(ref)
    generation = store.state()["generation"]
    digest, invocation = suite_digest(), "e2e-" + uuid.uuid4().hex
    image_id = sup.sandbox.image_id(RUNNER_IMAGE)
    target_name = sup.sandbox.container_name(spec.project_id, spec.run_id, generation) + "-browser-target"
    runner_name = target_name + "-runner"
    docker = sup.sandbox._docker
    labels = sup._labels(spec, generation)
    common = ["--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
              "--pids-limit", "256", "--memory", "1g", "--cpus", "1", "--user", "1000:1000",
              "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m"]
    label_args = [a for k, v in labels.items() for a in ("--label", f"{k}={v}")]
    report, stdout, stderr, exit_code = None, b"", b"", None
    started = time.monotonic()
    try:
        docker("create", "--name", target_name, "--network", "none", *common, *label_args,
            "--mount", f"type=bind,src={artifact},dst=/site,readonly",
            "--mount", f"type=bind,src={SUITE / 'static-server.cjs'},dst=/server.cjs,readonly",
            target["toolchain"]["image_id"], "node", "/server.cjs")
        docker("start", target_name)
        # Runner shares ONLY the target's network-none namespace. There is no
        # host/control route, port publication, target report mount or Docker socket.
        docker("create", "--name", runner_name, "--network", "container:" + target_name,
            *common, *label_args, "--mount", f"type=bind,src={SUITE},dst=/suite,readonly",
            image_id, invocation, target["target_id"], digest, "http://127.0.0.1:4173/")
        result = docker("start", "--attach", runner_name, timeout=120, check=False)
        stdout, stderr = result.stdout, result.stderr
        exit_code = int(docker("inspect", "--format", "{{.State.ExitCode}}", runner_name).stdout)
        if len(stdout) <= 1024 * 1024:
            frames = [line[14:] for line in stdout.splitlines() if line.startswith(b"DEV006_REPORT ")]
            if len(frames) == 1:
                try:
                    report = json.loads(frames[0])
                except ValueError:
                    pass
    finally:
        sup.sandbox.kill_and_remove(runner_name)
        sup.sandbox.kill_and_remove(target_name)
    status = validate_report(report, invocation=invocation, target_id=target["target_id"], digest=digest)
    if (status == "passed" and exit_code != 0) or sup._revoked(store, generation):
        status = "incomplete"
    evidence = {"schema": 1, "evidence_id": invocation, "target_id": target["target_id"],
                "suite_digest": digest, "runner_image": image_id, "author": "separate-browser-runner",
                "execution_generation": generation,
                "container_network": "target-network-none-namespace", "duration_s": time.monotonic() - started,
                "exit_code": exit_code, "status": status, "report": report}
    directory = sup.run_dir(ref) / "evidence"
    atomic_write_json(directory / (invocation + ".json"), evidence)
    (directory / (invocation + ".stdout.log")).write_bytes(stdout)
    (directory / (invocation + ".stderr.log")).write_bytes(stderr)
    os.chmod(directory / (invocation + ".json"), 0o444)
    return evidence
