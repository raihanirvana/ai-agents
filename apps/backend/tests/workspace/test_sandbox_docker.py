"""Real-container checks (marker `docker`). These run actual Docker, not fakes."""
import json
import os
import subprocess
import threading
import time

import pytest

from app.workspace import AuthorizationError, PathViolation, ResourceLimits

from .conftest import IMAGE, start

pytestmark = pytest.mark.docker


def sh(sup, run, script, **kw):
    return sup.run_command(run.ref, run.credential, ["sh", "-c", script], **kw)


def test_sandbox_has_no_git_secret_socket_network_or_root(real_sup, manifest, monkeypatch):
    monkeypatch.setenv("PROVIDER_API_KEY", "canary-provider-secret-123")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///canary-control.db")
    run = start(real_sup, manifest)
    script = r"""
echo "uid=$(id -u)"
echo "git=$(ls -A /work | grep -c '^\.git$')"
echo "sock=$(ls /var/run/docker.sock 2>/dev/null | wc -l)"
echo "env_canary=$(env | grep -ci 'canary')"
echo "caps=$(grep CapEff /proc/self/status | awk '{print $2}')"
echo "ifaces=$(ls /sys/class/net | tr '\n' ' ')"
touch /rootfs-write 2>/dev/null && echo rootfs=writable || echo rootfs=readonly
wget -q -T 3 -O- http://host.docker.internal:8000/health >/dev/null 2>&1 && echo net=reachable || echo net=blocked
wget -q -T 3 -O- http://1.1.1.1/ >/dev/null 2>&1 && echo internet=reachable || echo internet=blocked
"""
    result = sh(real_sup, run, script)
    out = result.stdout.decode()
    assert result.exit_code == 0, result.stderr
    assert "uid=1000" in out and "git=0" in out and "sock=0" in out and "env_canary=0" in out
    assert "caps=0000000000000000" in out
    ifaces = next(l for l in out.splitlines() if l.startswith("ifaces=")).split("=", 1)[1].split()
    assert "eth0" not in ifaces and "lo" in ifaces  # only loopback + inert tunnel stubs, no routable NIC
    assert "net=blocked" in out and "internet=blocked" in out
    assert "rootfs=readonly" in out
    assert b"canary-provider-secret-123" not in result.stdout + result.stderr
    assert b"canary-control.db" not in result.stdout + result.stderr


def test_command_timeout_stops_the_container(real_sup, manifest):
    run = start(real_sup, manifest)
    started = time.monotonic()
    result = sh(real_sup, run, "sleep 60", timeout_s=3)
    assert result.timed_out and result.exit_code is None
    assert time.monotonic() - started < 20
    assert real_sup.sandbox.owned(run_id=run.ref.run_id) == []


def test_cancel_while_tool_is_active_kills_container_and_archives(real_sup, manifest):
    run = start(real_sup, manifest)
    box = {}
    thread = threading.Thread(target=lambda: box.update(result=sh(real_sup, run, "echo started; sleep 120")))
    thread.start()
    for _ in range(100):
        if real_sup.sandbox.owned(run_id=run.ref.run_id, include_stopped=False):
            break
        time.sleep(0.1)
    else:
        pytest.fail("container never started")
    archive = real_sup.stop_run(run.ref, "cancelled")
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert box["result"].cancelled and box["result"].exit_code is None
    assert real_sup.sandbox.owned(run_id=run.ref.run_id) == []
    assert (archive / "ARCHIVE-MANIFEST.json").exists()
    finals = list((archive / "evidence").glob("final-*.log"))
    assert finals and b"started" in finals[0].read_bytes()
    with pytest.raises(AuthorizationError):
        sh(real_sup, run, "true")


def test_memory_and_pid_limits_are_enforced(real_sup, manifest):
    run = start(real_sup, manifest, limits=ResourceLimits(memory_mb=128, pids=32))
    oom = real_sup.run_command(run.ref, run.credential, ["node", "-e", "const a=[];for(;;)a.push(Buffer.alloc(8*1024*1024,1))"])
    assert oom.oom_killed or oom.exit_code not in (0, None)
    forks = sh(real_sup, run, "for i in $(seq 1 200); do sleep 20 & done 2>&1; wait", timeout_s=10)
    text = (forks.stdout + forks.stderr).decode().lower()
    assert "resource temporarily unavailable" in text or "can't fork" in text or forks.exit_code != 0


def test_target_shell_cannot_reach_git_metadata_or_other_runs(real_sup, manifest):
    victim = start(real_sup, manifest, ticket="T-victim")
    attacker = start(real_sup, manifest, ticket="T-attacker")
    base = real_sup.broker("demo").accepted_sha()
    script = f"""
find / -xdev -name HEAD -path '*attempts*' 2>/dev/null
ls -d /work/.git 2>&1
ls {real_sup.run_dir(victim.ref)} 2>&1
ls {real_sup.root}/demo/repo.git 2>&1
echo marker > /work/note.txt
"""
    result = sh(real_sup, attacker, script)
    text = (result.stdout + result.stderr).decode()
    assert text.count("No such file or directory") == 3, text  # .git, victim run dir, repo.git: all absent
    assert "attempts" not in text.replace("No such file", "")
    refs = real_sup.broker("demo").refs()
    assert refs["refs/heads/accepted"] == base
    assert refs[victim.spec.attempt_ref] == base and refs[attacker.spec.attempt_ref] == base
    assert (real_sup.src_dir(attacker.ref) / "note.txt").read_text().strip() == "marker"
    assert not (real_sup.src_dir(victim.ref) / "note.txt").exists()


def test_planted_symlink_fifo_and_git_dir_from_real_container_are_rejected(real_sup, manifest):
    run = start(real_sup, manifest)
    sh(real_sup, run, "ln -s /etc/passwd evil; mkfifo pipe; mkdir -p .git/hooks; echo 'touch /tmp/pwned' > .git/hooks/pre-commit")
    before = real_sup.broker("demo").refs()
    with pytest.raises(PathViolation):
        real_sup.submit_candidate(run.ref, run.credential, "evil")
    assert real_sup.broker("demo").refs() == before
    assert not os.path.exists("/tmp/pwned")


def test_legitimate_commit_from_real_sandbox_work(real_sup, manifest):
    run = start(real_sup, manifest)
    sh(real_sup, run, "mkdir -p src && echo 'export const n = 1;' > src/n.js && chmod +x src/n.js")
    record = real_sup.submit_candidate(run.ref, run.credential, "add n")
    assert record["changed"]
    assert real_sup.broker("demo").file_at(record["sha"], "src/n.js") == b"export const n = 1;\n"
    assert real_sup.broker("demo").refs()["refs/heads/accepted"] == run.spec.base_sha


def test_cleanup_respects_ownership_labels(real_sup, manifest):
    a = start(real_sup, manifest, ticket="T-a")
    b = start(real_sup, manifest, ticket="T-b")
    labels = real_sup.sandbox.labels(project_id="demo", run_id=a.ref.run_id, attempt=1, generation=1)

    def run_idle(name, **override):
        merged = {**labels, **override}
        args = ["docker", "run", "-d", "--name", name, "--network", "none", "--user", "1000:1000"]
        for k, v in merged.items():
            args += ["--label", f"{k}={v}"]
        subprocess.run([*args, IMAGE, "sleep", "300"], check=True, capture_output=True)

    run_idle("aiagent-test-mine")
    run_idle("aiagent-test-preview", **{"aiagent.container-role": "preview"})  # same run id, but a preview
    run_idle("aiagent-test-other-run", **{"aiagent.run": b.ref.run_id})
    run_idle("aiagent-test-foreign-supervisor", **{"aiagent.supervisor": "someone-else"})
    try:
        real_sup.stop_run(a.ref)
        alive = subprocess.run(["docker", "ps", "--format", "{{.Names}}", "--filter", "name=aiagent-test-"],
                               capture_output=True, text=True).stdout.split()
        assert "aiagent-test-mine" not in alive
        assert {"aiagent-test-preview", "aiagent-test-other-run", "aiagent-test-foreign-supervisor"} <= set(alive)
    finally:
        subprocess.run("docker rm -f $(docker ps -aq --filter name=aiagent-test-)", shell=True, capture_output=True)


def test_reap_orphans_removes_containers_of_dead_runs_only(real_sup, manifest):
    live = start(real_sup, manifest, ticket="T-live")
    dead = start(real_sup, manifest, ticket="T-dead")
    for run, name in ((live, "aiagent-test-live"), (dead, "aiagent-test-dead")):
        labels = real_sup.sandbox.labels(project_id="demo", run_id=run.ref.run_id, attempt=1, generation=1)
        args = ["docker", "run", "-d", "--name", name, "--network", "none"]
        for k, v in labels.items():
            args += ["--label", f"{k}={v}"]
        subprocess.run([*args, IMAGE, "sleep", "300"], check=True, capture_output=True)
    state = real_sup.run_dir(dead.ref) / "state.json"
    data = json.loads(state.read_text())
    data["status"] = "stopped"
    state.write_text(json.dumps(data))
    try:
        assert real_sup.reap_orphans("demo") == ["aiagent-test-dead"]
        names = subprocess.run(["docker", "ps", "--format", "{{.Names}}", "--filter", "name=aiagent-test-"],
                               capture_output=True, text=True).stdout.split()
        assert names == ["aiagent-test-live"]
    finally:
        subprocess.run("docker rm -f $(docker ps -aq --filter name=aiagent-test-)", shell=True, capture_output=True)


def test_command_cap_is_finite_and_not_reset(real_sup, manifest):
    run = start(real_sup, manifest, limits=ResourceLimits(max_commands=2))
    sh(real_sup, run, "true")
    sh(real_sup, run, "true")
    from app.workspace import WorkspaceError
    with pytest.raises(WorkspaceError, match="command cap"):
        sh(real_sup, run, "true")
    new_credential = real_sup.renew_generation(run.ref, generation=2, lease_id="l2")
    with pytest.raises(WorkspaceError, match="command cap"):  # renewal does not reset usage
        real_sup.run_command(run.ref, new_credential, ["true"])


def test_evidence_record_has_command_exit_image_and_checksums(real_sup, manifest):
    import hashlib
    run = start(real_sup, manifest)
    sh(real_sup, run, "echo out; echo err >&2; exit 3")
    record = json.loads(next((real_sup.run_dir(run.ref) / "evidence").glob("cmd-0001-*.json")).read_text())
    assert record["exit_code"] == 3 and record["network"] == "none" and record["image"] == IMAGE
    assert record["image_id"].startswith("sha256:") and record["limits"]["memory_mb"] == 1024
    stdout = (real_sup.run_dir(run.ref) / "evidence" / record["stdout_file"]).read_bytes()
    assert stdout == b"out\n" and hashlib.sha256(stdout).hexdigest() == record["stdout_sha256"]
    assert record["argv"][0] == "sh"


def test_run_credential_is_redacted_from_logs(real_sup, manifest):
    run = start(real_sup, manifest)
    result = sh(real_sup, run, f"echo {run.credential}")
    assert run.credential.encode() not in result.stdout and b"[REDACTED]" in result.stdout
    record = next((real_sup.run_dir(run.ref) / "evidence").glob("cmd-*.json")).read_text()
    assert run.credential not in record


def test_install_executes_offline_and_never_runs_lifecycle_script(real_sup, manifest, monkeypatch):
    run = start(real_sup, manifest, egress=True)
    pkg = {"name": "offline-probe", "version": "1.0.0",
           "scripts": {"preinstall": "node -e \"require('fs').writeFileSync('/work/lifecycle-ran', 'bad')\""}}
    lock = {"name": "offline-probe", "version": "1.0.0", "lockfileVersion": 3,
            "packages": {"": {"name": "offline-probe", "version": "1.0.0"}}}
    for name, data in (("package.json", pkg), ("package-lock.json", lock)):
        real_sup.write_file(run.ref, run.credential, name, json.dumps(data).encode())
    original = real_sup.sandbox.create
    networks = []
    def capture(**kwargs):
        networks.append(kwargs["network"])
        return original(**kwargs)
    monkeypatch.setattr(real_sup.sandbox, "create", capture)
    result = real_sup.run_phase(run.ref, run.credential, "install")
    assert result.exit_code == 0, result.stderr
    assert networks == ["none"]
    assert not (real_sup.src_dir(run.ref) / "lifecycle-ran").exists()
