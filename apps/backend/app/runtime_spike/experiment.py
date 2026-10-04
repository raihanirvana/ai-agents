"""Reproducible POSIX-only DEV-006 harness; no production scheduler or approvals.

Run from apps/backend with --help. Runtime state and secrets stay in ignored
local storage. This embeds the exact preflight-verified Hermes installation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from app.workspace import WorkspaceSupervisor, parse_manifest, reference_manifest_dict
from app.workspace.runspec import ResourceLimits, RunRef, RunStore, atomic_write_json, hash_credential, utcnow
from app.workspace.supervisor import ROLE_OPERATIONS, _make_credential

from .journal import AdmissionError, Journal
from .relay import Relay

REPO = Path(__file__).resolve().parents[4]
DEFAULT_LIMITS = {"model_calls": 32, "tool_calls": 80, "active_s": 900, "output_tokens": 4096}
FEATURE = """Implement a small usable coffee cart in this React/Vite project. Existing
espresso is 250 cents and latte 400 cents. Add cappuccino at 350 cents. Show all
three menu items with accessible buttons named 'Add espresso', 'Add latte', and
'Add cappuccino'. Cart rows have data-testid='cart-<id>', display quantity,
and a button named 'Remove <id>' that decrements it. Total uses data-testid='cart-total'
and displays dollars with two decimals. Start empty. Ask the operator what should
happen when Remove reduces quantity to zero, using spike_request_input BEFORE
editing. On resumed run use the operator answer, do not ask again.
Use only supervisor tools. Read existing files, implement feature and useful node
unit tests, run test and build, inspect diff, submit_candidate exactly once after
gates pass. Do not change dependencies or the lockfile. Browser acceptance is
independent and unavailable to this target. Completion prose is not QA evidence."""


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, value)
    os.chmod(path, 0o600)


class Experiment:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.sup = WorkspaceSupervisor(self.root / "workspaces")
        self.journal = Journal(self.root / "journal.sqlite3")

    def path(self, scope):
        if not scope or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in scope) or len(scope) > 40:
            raise ValueError("invalid experiment scope")
        return self.root / "sessions" / scope / "session.json"

    def load(self, scope):
        return json.loads(self.path(scope).read_text())

    def ref(self, scope):
        c = self.load(scope)
        return RunRef(c["project_id"], c["run_id"])

    def prepare(self, scope, *, limits=None, role="developer", prompt=FEATURE):
        path = self.path(scope)
        if path.exists():
            raise AdmissionError("scope already exists")
        project = scope
        initial = self.sup.create_project(project)
        manifest = parse_manifest(reference_manifest_dict())
        seed = self.sup.start_attempt(project, ticket_id="DEV006-BASE", scope_version=1,
            role="developer", attempt=1, generation=1, lease_id="scoped-seed", manifest=manifest,
            allow_install_egress=True)
        fixture = REPO / "apps/backend/tests/workspace/fixtures/reference-react-vite"
        for p in sorted(fixture.rglob("*")):
            if p.is_file() and not {"node_modules", "dist"} & set(p.relative_to(fixture).parts):
                self.sup.write_file(seed.ref, seed.credential, p.relative_to(fixture).as_posix(), p.read_bytes())
        base = self.sup.submit_candidate(seed.ref, seed.credential, "Supervisor fixture initialization")
        # Explicit technical initialization of a NEW managed project, never an agent
        # tool or integration of its feature result. Existing source repo is untouched.
        self.sup.broker(project)._bare("update-ref", "refs/heads/accepted", base["sha"], initial)
        self.sup.stop_run(seed.ref, "fixture_initialized")
        run = self.sup.start_attempt(project, ticket_id="DEV006-FEATURE", scope_version=1,
            role=role, attempt=2, generation=1, lease_id="scoped-experiment-1", manifest=manifest,
            limits=ResourceLimits(command_timeout_s=120), allow_install_egress=True,
            provenance={"created_by": "dev006-scoped-experiment"})
        # Verify the checkpoint BEFORE asking for input. These are baseline repo
        # gates, not acceptance for the requested feature.
        for phase in ("install", "test", "build"):
            result = self.sup.run_phase(run.ref, run.credential, phase)
            if result.exit_code != 0:
                raise RuntimeError("baseline gate failed: " + phase)
        identity = {"project_id": project, "scope_version": 1, "base_sha": run.spec.base_sha,
                    "role": role, "ticket_id": run.spec.ticket_id}
        self.journal.create(scope, identity, limits or DEFAULT_LIMITS)
        config = {**identity, "run_id": run.ref.run_id, "credential": run.credential, "generation": 1,
                  "prompt": prompt, "candidate": None, "checkpoint": None}
        save(path, config)
        return {"scope": scope, "run_id": run.ref.run_id, "base_sha": run.spec.base_sha}

    def tools(self, scope, generation):
        c, ref = self.load(scope), self.ref(scope)
        cred = c["credential"]

        def phase(args):
            r = self.sup.run_phase(ref, cred, args["phase"])
            return {"exit_code": r.exit_code, "duration_s": r.duration_s,
                    "stdout": r.stdout[:24000].decode(errors="replace"), "stderr": r.stderr[:8000].decode(errors="replace")}

        def write(args):
            if self.load(scope)["candidate"]:
                return {"error": "candidate already immutable; start a new attempt before further edits"}
            self.sup.write_file(ref, cred, args["path"], args["content"].encode())
            return {"written": args["path"]}

        def submit(args):
            # Candidate operation reconciles authoritative broker evidence first.
            # One candidate per scoped experiment; duplicates never auto-recommit.
            with self.sup._store(ref).lock("dev006-candidate.lock"):
                current = self.load(scope)
                if current["candidate"]:
                    if self.sup.broker(ref.project_id).resolve(RunStore(self.sup.run_dir(ref)).spec().attempt_ref) != current["candidate"]["sha"] or self.sup.inspect_diff(ref, cred).strip():
                        raise AdmissionError("workspace differs from existing candidate; new attempt required")
                    return current["candidate"]
                records = list((self.sup.run_dir(ref) / "candidates").glob("candidate-*.json"))
                if records:
                    result = json.loads(records[0].read_text())
                else:
                    result = self.sup.submit_candidate(ref, cred, args["message"])
                current["candidate"] = result
                save(self.path(scope), current)
                return result

        def request(args):
            question = args["question"]
            if not isinstance(question, str) or not question.strip() or len(question) > 2000:
                raise ValueError("invalid clarification")
            # Bounded verified fallback: checkpoint only when repo gates pass.
            for p in ("test", "build"):
                if self.sup.run_phase(ref, cred, p).exit_code != 0:
                    return {"error": "checkpoint gate failed: " + p}
            checkpoint = self.sup.checkpoint(ref, cred, "Verified clarification checkpoint")
            iid = self.journal.request_input(scope, generation, question, checkpoint)
            current = self.load(scope)
            current["checkpoint"] = checkpoint
            save(self.path(scope), current)
            return {"request_id": iid, "status": "waiting_input", "checkpoint_sha": checkpoint["sha"]}

        return {"list_files": lambda a: {"files": self.sup.list_files(ref, cred)},
                "read_file": lambda a: {"content": self.sup.read_file(ref, cred, a["path"])[:48000].decode(errors="replace")},
                "write_file": write, "run_phase": phase,
                "inspect_diff": lambda a: {"diff": self.sup.inspect_diff(ref, cred)[:48000]},
                "submit_candidate": submit, "request_input": request}

    def start(self, scope, hermes_python: Path, env_file: Path, hermes_source: Path):
        from .preflight import collect, local_environment

        env = local_environment(env_file)
        preflight = collect(provider=env.get("SPIKE_PROVIDER", ""), model=env.get("SPIKE_MODEL", ""),
                            hermes_source=hermes_source, hermes_python=hermes_python, env=env)
        if preflight["status"] != "prerequisites_ready":
            raise AdmissionError("exact runtime preflight failed")
        if env.get("SPIKE_PROVIDER") != "openrouter" or not env.get("OPENROUTER_API_KEY"):
            raise AdmissionError("OpenRouter configuration missing")
        c = self.load(scope)
        gen = c["generation"]
        snapshot = self.journal.inspect(scope)
        if c["base_sha"] != self.sup.broker(c["project_id"]).accepted_sha():
            raise AdmissionError("base changed; replan required")
        private = self.path(scope).parent / f"generation-{gen}"
        # Fresh home per process/generation. Reusing home on failed start requires
        # explicit recovery; never quietly load another session's history.
        private.mkdir(mode=0o700)
        save(private / "preflight.json", preflight)
        home = private / "home"
        home.mkdir(mode=0o700)
        effective = {"agent": {"api_max_retries": 1, "auto_recovery_cycles": 0},
                     "tools": {"tool_search": {"enabled": "off"}},
                     "memory": {"memory_enabled": False, "user_profile_enabled": False},
                     "compression": {"enabled": False, "context_length": 262144}}
        (home / "config.yaml").write_text(json.dumps(effective))  # JSON is valid YAML.
        soul = (f"You are the {c['role']} for {scope}, generation {gen}. Canary identity: {scope}-only. "
                "Use supervisor tools only. Their filesystem is the isolated PROJECT SOURCE SNAPSHOT. "
                "Use project-relative paths such as src/cart.js and README.md, NEVER absolute paths. "
                "The Hermes runtime cwd is private state and is NOT the project source directory.")
        (private / "SOUL.md").write_text(soul)
        (private / "CONTEXT.md").write_text(json.dumps(snapshot["identity"], sort_keys=True))
        system = soul + "\nTrusted context: " + json.dumps(snapshot["identity"], sort_keys=True)
        self.journal.start(scope, gen)
        events_path = private / "transport.log"
        total_bytes = [0]
        proc = None
        with Relay(self.journal, scope, gen, env["SPIKE_MODEL"], env["OPENROUTER_API_KEY"], self.tools(scope, gen),
                   foreign_markers=c.get("foreign_markers", ()),
                   interval_s=21 if scope == "coffee-feature" else 0) as relay:
            worker_config = {"model": env["SPIKE_MODEL"], "relay_url": relay.url, "relay_token": relay.token,
                "output_tokens": snapshot["limits"]["output_tokens"], "session_id": f"{scope}-g{gen}",
                "system": system, "prompt": c["prompt"], "effective_config": effective,
                "tool_names": sorted(n for n in self.tools(scope, gen) if
                    n in ROLE_OPERATIONS[c["role"]] or (n == "request_input" and c["role"] == "developer"))}
            save(private / "worker.json", worker_config)
            worker = Path(__file__).with_name("hermes_worker.py")
            child_env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR")}
            child_env.update(HOME=str(home), HERMES_HOME=str(home), XDG_CONFIG_HOME=str(home),
                             PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1")
            proc = subprocess.Popen([str(hermes_python.absolute()), str(worker), str(private / "worker.json")],
                cwd=private, env=child_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            c["pid"] = proc.pid
            save(self.path(scope), c)

            def collect():
                with events_path.open("wb") as log:
                    for line in iter(lambda: proc.stdout.readline(65537), b""):
                        # A corrupt/unbounded runtime transport fails closed.
                        total_bytes[0] += len(line)
                        if total_bytes[0] > 2 * 1024 * 1024 or len(line) > 65536:
                            self.journal.pause(scope, gen, "failed")
                            os.killpg(proc.pid, signal.SIGTERM)
                            break
                        safe = line.replace(env["OPENROUTER_API_KEY"].encode(), b"[redacted]").replace(relay.token.encode(), b"[relay credential]")
                        log.write(safe)
                        if line.startswith(b"DEV006_EVENT "):
                            try:
                                e = json.loads(line[13:])
                                self.journal.event(scope, gen, e["kind"], e["payload"])
                            except (ValueError, AdmissionError):
                                pass

            reader = threading.Thread(target=collect, daemon=True)
            reader.start()
            waiting_deadline = None
            revoked = False
            while proc.poll() is None:
                state = self.journal.inspect(scope)
                if state["status"] == "waiting_input":
                    waiting_deadline = waiting_deadline or time.monotonic() + 3
                    if time.monotonic() >= waiting_deadline:
                        os.killpg(proc.pid, signal.SIGTERM)
                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            os.killpg(proc.pid, signal.SIGKILL)
                        break
                if state["active_s"] >= state["limits"]["active_s"] or state["status"] in ("stopped", "failed"):
                    self.journal.pause(scope, gen, "stopped")
                    self.revoke(scope, "budget_or_stop")
                    revoked = True
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                    break
                time.sleep(0.1)
            proc.wait()
            # Cooperative runtime completion does not excuse lingering children.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            reader.join(timeout=3)
            if revoked or self.journal.inspect(scope)["status"] in ("stopped", "failed"):
                # Join supervisor-owned command cleanup before shutting down its
                # relay. Killing only Hermes must not abandon a Docker child; this
                # includes reader failure followed by worker exit before the loop
                # observes it. Failed journal state stays failed after revocation.
                self.revoke(scope, "budget_or_stop")
                self.sup.stop_run(self.ref(scope), "budget_or_stop")
        state = self.journal.inspect(scope)
        if state["status"] == "running":
            # Complete only if broker has a candidate; prose alone does not suffice.
            runtime_results = [e["payload"] for e in self.journal.stream(scope)
                               if e["generation"] == gen and e["kind"] == "runtime.result"]
            completed = bool(runtime_results and runtime_results[-1].get("completed") and not runtime_results[-1].get("error"))
            self.journal.pause(scope, gen, "finished" if (self.load(scope)["candidate"] or c.get("probe")) and proc.returncode == 0 and completed else "failed")
        c = self.load(scope)
        c.update(pid=None, process_exit=proc.returncode, model=env["SPIKE_MODEL"],
                 system_digest=hashlib.sha256(system.encode()).hexdigest())
        if self.journal.inspect(scope)["status"] == "finished" and c["candidate"]:
            submissions = [r for r in self.journal.inspect(scope)["reservations"]
                           if r["generation"] == gen and r["kind"] == "tool" and r["name"] == "submit_candidate" and r["result"] and r["result"]["status"] == "complete"]
            if submissions:
                c["runtime_handoff"] = {"generation": gen, "model": env["SPIKE_MODEL"], "reservation": submissions[-1]["id"]}
        save(self.path(scope), c)
        return self.inspect(scope)

    def inspect(self, scope):
        c = self.load(scope)
        public = {k: v for k, v in c.items() if k not in ("credential", "prompt")}
        return {"session": public, "accounting": self.journal.inspect(scope),
                "event_count": len(self.journal.stream(scope))}

    def stream(self, scope, cursor=0):
        return self.journal.stream(scope, cursor)

    def send_input(self, scope, *, request_id, answer_id, text):
        return self.resume(scope, request_id, answer_id, text)

    def read_result(self, scope):
        report = self.inspect(scope)
        events = self.journal.stream(scope)
        generation = report["session"]["generation"]
        runtime_results = [e["payload"] for e in events if e["generation"] == generation and e["kind"] == "runtime.result"]
        return {"status": report["accounting"]["status"], "generation": generation,
                "candidate": report["session"]["candidate"], "accepted": False,
                "runtime_result": runtime_results[-1] if runtime_results else None,
                "accounting": report["accounting"], "qa": "separate verification evidence required"}

    def resume(self, scope, request_id, answer_id, answer):
        with self.sup._store(self.ref(scope)).lock("dev006-resume.lock"):
            return self._resume(scope, request_id, answer_id, answer)

    def _resume(self, scope, request_id, answer_id, answer):
        c = self.load(scope)
        ref = self.ref(scope)
        identity = {k: c[k] for k in ("project_id", "scope_version", "base_sha", "role", "ticket_id")}
        identity["base_sha"] = self.sup.broker(c["project_id"]).accepted_sha()
        state = self.journal.inspect(scope)
        recovered_credential = None
        if state["status"] == "waiting_input" and c.get("pid"):
            raise AdmissionError("reconcile old process before resume")
        if state["status"] in ("waiting_input", "resuming") and c["generation"] <= state["generation"]:
            if not c["checkpoint"] or self.sup.broker(c["project_id"]).resolve(
                    RunStore(self.sup.run_dir(ref)).spec().attempt_ref) != c["checkpoint"]["sha"]:
                raise AdmissionError("checkpoint identity changed")
            if state["status"] == "resuming" and self.sup._store(ref).state()["generation"] > c["generation"]:
                recovered_credential = self._reconcile_credential(scope, state["generation"])
            if self.sup.inspect_diff(ref, recovered_credential or c["credential"]).strip():
                raise AdmissionError("checkpoint workspace changed while waiting")
        result = self.journal.answer(scope, request_id, answer_id, answer, identity)
        if c["generation"] < result["generation"]:
            # Rotate credentials BEFORE starting any new runtime. Stale tools fail.
            credential = recovered_credential or self._reconcile_credential(scope, result["generation"])
            c.update(credential=credential, generation=result["generation"],
                     prompt=FEATURE.replace("Ask the operator what should", "Previously asked the operator what should") +
                     "\nSUPERVISOR RESUME: clarification already answered; do not ask again. Operator answer: " + answer +
                     "\nThis is a fresh Hermes process from a verified workspace checkpoint, not native continuation.")
            save(self.path(scope), c)
        return result

    def _reconcile_credential(self, scope, generation):
        ref = self.ref(scope)
        store = self.sup._store(ref)
        if store.state()["generation"] < generation:
            return self.sup.renew_generation(ref, generation=generation, lease_id=f"scoped-experiment-{generation}")
        # Crash after credential rotation but before session.json replacement.
        # Reissue only if the new runtime was NEVER started and no container is
        # owned by that generation. Otherwise demand explicit reconciliation.
        if (self.path(scope).parent / f"generation-{generation}").exists() or self.sup.sandbox.owned(run_id=ref.run_id):
            raise AdmissionError("new generation may have started; reconcile before credential recovery")
        with store.lock():
            state = store.state()
            if state["status"] != "active" or state["generation"] != generation:
                raise AdmissionError("cannot reconcile inactive or different generation")
            credential = _make_credential(ref.run_id, generation)
            state.update(credential_sha256=hash_credential(credential))
            store.write_state(state)
            return credential

    def revoke(self, scope, reason):
        store = RunStore(self.sup.run_dir(self.ref(scope)))
        with store.lock():
            state = store.state()
            state.update(status="stopped", credential_sha256=None, stop_reason=reason, revoked_at=utcnow())
            store.write_state(state)

    def restart(self, scope):
        c, ref = self.load(scope), self.ref(scope)
        if c.get("pid"):
            raise AdmissionError("reconcile old process first")
        identity = {k: c[k] for k in ("project_id", "scope_version", "base_sha", "role", "ticket_id")}
        identity["base_sha"] = self.sup.broker(c["project_id"]).accepted_sha()
        # Validate before gates/checkpoint: a rejected restart must not commit a
        # WIP checkpoint that moves the attempt ref past an existing candidate.
        state = self.journal.inspect(scope)
        if state["status"] != "failed" or state["identity"] != identity or state["generation"] != c["generation"]:
            raise AdmissionError("recovery requires failed scope with unchanged identity")
        for phase in ("test", "build"):
            if self.sup.run_phase(ref, c["credential"], phase).exit_code != 0:
                raise AdmissionError("recovery checkpoint gate failed")
        checkpoint = self.sup.checkpoint(ref, c["credential"], "Verified recovery checkpoint")
        gen = self.journal.restart_failed(scope, identity)
        c.update(generation=gen, checkpoint=checkpoint, credential=self.sup.renew_generation(
            ref, generation=gen, lease_id=f"scoped-experiment-{gen}"))
        c["prompt"] += ("\nRECOVERY CHECKPOINT: the previous partial implementation has already passed "
                         "node tests and Vite build. Inspect existing changes; do not rewrite finished code "
                         "or reinstall dependencies. Run test and build through tools, inspect_diff, "
                         "submit_candidate, then finish. All prior budgets and operator answers still apply.")
        if c["candidate"]:
            c["prompt"] = ("Finalize the existing immutable coffee cart candidate " + c["candidate"]["sha"] +
                ". Qwen wrote this feature and the supervisor promoted its verified checkpoint for browser QA. "
                "The operator already answered: remove the row when quantity reaches zero. "
                "Do NOT rewrite, reinstall, ask clarification or read runtime paths. "
                "Call spike_run_phase test and build, inspect_diff, then spike_submit_candidate "
                "with a meaningful message (idempotent: returns the existing candidate), and finish. "
                "The workspace root for file tools is project-relative; current diff should be empty "
                "because the code is already committed. This is checkpoint recovery, not native continuation.")
        save(self.path(scope), c)
        return {"generation": gen, "checkpoint_sha": checkpoint["sha"], "budget_preserved": True}

    def stop(self, scope):
        c = self.load(scope)
        # Fence the journal's CURRENT generation: after a crash between answer and
        # session.json replacement, session.json lags and stop must still revoke.
        self.journal.pause(scope, self.journal.inspect(scope)["generation"], "stopped")
        self.revoke(scope, "explicit_stop")
        # CLI PID alone is not proof after a supervisor crash. Reconciliation
        # requires /proc cmdline to identify our exact private worker config.
        if c.get("pid"):
            pid = c["pid"]
            expected = str(self.path(scope).parent / f"generation-{c['generation']}" / "worker.json").encode()
            cmdline = Path(f"/proc/{pid}/cmdline")
            if cmdline.exists() and expected in cmdline.read_bytes().split(b"\0"):
                os.killpg(pid, signal.SIGTERM)
                deadline = time.monotonic() + 3
                while cmdline.exists() and time.monotonic() < deadline:
                    time.sleep(0.1)
                if cmdline.exists():
                    os.killpg(pid, signal.SIGKILL)
        archive = self.sup.stop_run(self.ref(scope), "explicit_stop")
        return {"status": "stopped", "archive": str(archive)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "start", "inspect", "read-result", "resume", "restart", "stop", "build"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--scope", default="coffee-feature")
    parser.add_argument("--hermes-python", type=Path)
    parser.add_argument("--hermes-source", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--request-id")
    parser.add_argument("--answer-id")
    parser.add_argument("--answer")
    args = parser.parse_args()
    exp = Experiment(args.root)
    if args.operation == "prepare":
        result = exp.prepare(args.scope)
    elif args.operation == "start":
        if not args.hermes_python or not args.env_file or not args.hermes_source:
            parser.error("start requires --hermes-python, --hermes-source and --env-file")
        result = exp.start(args.scope, args.hermes_python, args.env_file, args.hermes_source)
    elif args.operation == "resume":
        result = exp.resume(args.scope, args.request_id, args.answer_id, args.answer)
    elif args.operation == "stop":
        result = exp.stop(args.scope)
    elif args.operation == "restart":
        result = exp.restart(args.scope)
    elif args.operation == "read-result":
        result = exp.read_result(args.scope)
    elif args.operation == "build":
        c = exp.load(args.scope)
        if not c["candidate"]:
            raise AdmissionError("no authoritative candidate")
        result = exp.sup.build_target(exp.ref(args.scope), c["candidate"]["sha"], run_tests=True)
        save(exp.path(args.scope).parent / ("target-" + result["target_id"] + ".json"), result)
    else:
        result = exp.inspect(args.scope)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
