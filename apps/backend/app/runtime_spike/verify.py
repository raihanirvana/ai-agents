"""Real artifact/browser experiment, intentionally independent from Hermes."""
from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from app.workspace import fsutil, parse_manifest, reference_manifest_dict
from app.workspace.runspec import ResourceLimits, atomic_write_json

from .acceptance import run_acceptance
from .experiment import Experiment


def require(condition, message):
    # Identity invariants are evidence checks; unlike assert they survive python -O.
    if not condition:
        raise ValueError(message)


def new_run(exp, project, ticket, *, manifest=None, attempt=10):
    return exp.sup.start_attempt(project, ticket_id=ticket, scope_version=1, role="developer",
        attempt=attempt, generation=1, lease_id="dev006-verification-" + uuid.uuid4().hex,
        manifest=manifest or parse_manifest(reference_manifest_dict()),
        limits=ResourceLimits(command_timeout_s=120), allow_install_egress=True,
        provenance={"created_by": "dev006-trusted-verification-harness"})


def copy_candidate(exp, project, sha, run):
    exported = exp.root / "exports" / uuid.uuid4().hex
    exported.mkdir(parents=True)
    exp.sup.broker(project).export_commit(sha, exported)
    entries = fsutil.scan_tree(exported)
    for e in entries:
        if e.kind == "file":
            exp.sup.write_file(run.ref, run.credential, e.rel, (exported / e.rel).read_bytes())


def verify(exp, scope):
    c, ref = exp.load(scope), exp.ref(scope)
    if not c["candidate"]:
        raise ValueError("no real runtime candidate")
    sha, project = c["candidate"]["sha"], c["project_id"]
    target = exp.sup.build_target(ref, sha, run_tests=True)
    acceptance = run_acceptance(exp.sup, ref, target)
    summary = {"feature": {"target": target, "acceptance": acceptance}, "operator": "pending"}
    atomic_write_json(exp.path(scope).parent / "verification.json", summary)
    if acceptance["status"] != "passed":
        return summary

    base = new_run(exp, project, "DEV006-BASE-COMPARISON")
    base_candidate = exp.sup.submit_candidate(base.ref, base.credential, "Baseline comparison (no feature)")
    base_target = exp.sup.build_target(base.ref, base_candidate["sha"], run_tests=True)
    summary["baseline"] = {"target": base_target, "acceptance": run_acceptance(exp.sup, base.ref, base_target)}

    bug = new_run(exp, project, "DEV006-SEEDED-TOTAL-BUG", attempt=11)
    copy_candidate(exp, project, sha, bug)
    source = exp.sup.read_file(bug.ref, bug.credential, "src/cart.js").decode()
    needle = "item.priceCents * line.qty"
    if source.count(needle) != 1:
        raise ValueError("seed bug injection point changed; review fixture")
    exp.sup.write_file(bug.ref, bug.credential, "src/cart.js", source.replace(needle, "item.priceCents").encode())
    bug_candidate = exp.sup.submit_candidate(bug.ref, bug.credential, "Deliberate negative control: ignore quantity")
    bug_target = exp.sup.build_target(bug.ref, bug_candidate["sha"])
    bug_repo_gate = exp.sup.run_phase(bug.ref, bug.credential, "install")
    if bug_repo_gate.exit_code != 0:
        raise ValueError("negative control install failed")
    bug_repo_gate = exp.sup.run_phase(bug.ref, bug.credential, "test")
    summary["seeded_bug"] = {"candidate": bug_candidate, "target": bug_target,
                            "repo_gate_exit": bug_repo_gate.exit_code,
                            "acceptance": run_acceptance(exp.sup, bug.ref, bug_target)}

    rebuilt = exp.sup.build_target(ref, sha)
    summary["same_sha_rebuild"] = {"target": rebuilt, "acceptance": run_acceptance(exp.sup, ref, rebuilt)}
    require(rebuilt["target_id"] != target["target_id"] and rebuilt["candidate_sha"] == target["candidate_sha"],
            "same-SHA rebuild must get a new target for the same candidate")

    changed_manifest = parse_manifest({**reference_manifest_dict(), "revision": 2,
                                      "env": {"CI": "1", "VITE_SHOP": "config-probe"}})
    config_run = new_run(exp, project, "DEV006-CONFIG-PROBE", manifest=changed_manifest, attempt=12)
    copy_candidate(exp, project, sha, config_run)
    config_candidate = exp.sup.submit_candidate(config_run.ref, config_run.credential, "Same feature with changed runner configuration")
    config_target = exp.sup.build_target(config_run.ref, config_candidate["sha"], run_tests=True)
    summary["changed_config"] = {"target": config_target, "acceptance": run_acceptance(exp.sup, config_run.ref, config_target)}
    require(config_target["effective_config_digest"] != target["effective_config_digest"],
            "changed runner config must change the effective config digest")

    # Base movement simulation in its OWN managed project; the feature project's
    # accepted ref stays at the fixture. This is a trusted harness operation.
    probe_project = "identity-" + uuid.uuid4().hex[:8]
    initial = exp.sup.create_project(probe_project)
    base_run = new_run(exp, probe_project, "DEV006-BASE-IDENTITY-PROBE", attempt=1)
    exp.sup.write_file(base_run.ref, base_run.credential, "BASE-NOTE.md", b"A distinct technical base for the identity experiment.\n")
    new_base = exp.sup.submit_candidate(base_run.ref, base_run.credential, "Supervisor base identity fixture")
    exp.sup.broker(probe_project)._bare("update-ref", "refs/heads/accepted", new_base["sha"], initial)
    changed_base = new_run(exp, probe_project, "DEV006-BASE-PROBE", attempt=2)
    copy_candidate(exp, project, sha, changed_base)
    base_feature = exp.sup.submit_candidate(changed_base.ref, changed_base.credential, "Feature rebuilt on distinct base")
    changed_base_target = exp.sup.build_target(changed_base.ref, base_feature["sha"], run_tests=True)
    summary["changed_base"] = {"target": changed_base_target,
                               "acceptance": run_acceptance(exp.sup, changed_base.ref, changed_base_target)}
    require(changed_base_target["base_sha"] != target["base_sha"], "changed base must change target base")
    require(exp.sup.broker(project).accepted_sha() == c["base_sha"], "feature accepted ref moved")
    summary["feature_accepted_ref_unchanged"] = True
    summary["approval_carried"] = False  # No product approvals are created by this harness.
    atomic_write_json(exp.path(scope).parent / "verification.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--scope", default="coffee-feature")
    args = parser.parse_args()
    result = verify(Experiment(args.root), args.scope)
    print(json.dumps({k: {"target_id": v["target"]["target_id"], "status": v["acceptance"]["status"]}
                      for k, v in result.items() if isinstance(v, dict) and "target" in v}, indent=2))


if __name__ == "__main__":
    main()
