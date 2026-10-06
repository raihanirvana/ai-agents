"""Docker sandbox for target code, driven only by the trusted supervisor.

Containers get: source snapshot mounted at /work (no .git), no network,
no Docker socket, no inherited host
environment, dropped capabilities, read-only root, and finite memory/CPU/PIDs/
time. Every container carries labels so cleanup can prove ownership.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
import tempfile
from dataclasses import replace
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .errors import SandboxError, WorkspaceError
from .runspec import ResourceLimits, utcnow

LABEL_MANAGED = "aiagent.managed"
LABEL_ROLE = "aiagent.container-role"
ROLE_ATTEMPT = "attempt-sandbox"
CONTAINER_USER = "1000:1000"
_NAME_PART = re.compile(r"[^a-z0-9-]")


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    oom_killed: bool
    duration_s: float
    stdout: bytes
    stderr: bytes
    truncated: bool
    network: str
    container: str


def redact(data: bytes, secrets: Sequence[str]) -> bytes:
    for secret in secrets:
        if secret and len(secret) >= 6:
            data = data.replace(secret.encode(), b"[REDACTED]")
    return data


class DockerSandbox:
    def __init__(self, *, supervisor_id: str, docker_bin: str = "docker", dependency_cache: Path | None = None) -> None:
        self.supervisor_id = supervisor_id
        self.docker = docker_bin
        self.dependency_cache = dependency_cache
        self.dependency_progress = None

    # -- docker CLI ----------------------------------------------------------
    def _docker(self, *args: str, timeout: float = 60, check: bool = True, input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        try:
            proc = subprocess.run([self.docker, *args], capture_output=True, timeout=timeout, input=input)
        except FileNotFoundError as exc:
            raise SandboxError("docker CLI not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise SandboxError(f"docker {args[0]} timed out") from exc
        if check and proc.returncode != 0:
            raise SandboxError(f"docker {args[0]} failed: {proc.stderr.decode(errors='replace').strip()}")
        return proc

    def available(self) -> bool:
        try:
            return self._docker("info", "--format", "{{.ServerVersion}}", timeout=15, check=False).returncode == 0
        except SandboxError:
            return False

    def image_id(self, image: str) -> str:
        """Local image ID; images are never pulled implicitly."""
        proc = self._docker("image", "inspect", "--format", "{{.Id}}", image, check=False)
        if proc.returncode != 0:
            raise SandboxError(f"image {image} is not present locally (pull it explicitly first)")
        return proc.stdout.decode().strip()

    # -- containers ----------------------------------------------------------
    def container_name(self, project_id: str, run_id: str, generation: int) -> str:
        suffix = uuid.uuid4().hex[:8]
        return f"aiagent-{_NAME_PART.sub('-', project_id)}-{run_id}-g{generation}-{suffix}"

    def labels(self, *, project_id: str, run_id: str, attempt: int, generation: int) -> dict[str, str]:
        return {
            LABEL_MANAGED: "1", LABEL_ROLE: ROLE_ATTEMPT, "aiagent.supervisor": self.supervisor_id,
            "aiagent.project": project_id, "aiagent.run": run_id,
            "aiagent.attempt": str(attempt), "aiagent.generation": str(generation),
        }

    def create(self, *, name: str, image: str, source: Path, argv: Sequence[str], network: str,
               limits: ResourceLimits, env: dict[str, str], labels: dict[str, str],
               artifact: Path | None = None, build_output: str = "dist",
               dependency_cache: Path | None = None) -> None:
        if network != "none":
            raise SandboxError("target containers must use network none")
        args = [
            "create", "--name", name, "--user", CONTAINER_USER,
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", f"{limits.memory_mb}m", "--memory-swap", f"{limits.memory_mb}m",
            "--cpus", str(limits.cpus), "--pids-limit", str(limits.pids),
            "--ulimit", "nofile=4096:4096", "--ulimit", "core=0",
            "--tmpfs", f"/tmp:rw,nosuid,size={limits.tmpfs_mb}m",
            "--network", "none",
            "--log-driver", "json-file", "--log-opt", "max-size=4m", "--log-opt", "max-file=1",
            "--mount", f"type=bind,source={source},target=/work",
            "--workdir", "/work",
            "-e", "HOME=/tmp", "-e", "npm_config_cache=/tmp/npm-cache", "-e", "npm_config_update_notifier=false",
        ]
        for key, value in sorted(env.items()):
            args += ["-e", f"{key}={value}"]
        for key, value in sorted(labels.items()):
            args += ["--label", f"{key}={value}"]
        if dependency_cache is not None:
            args += ["--mount", f"type=bind,source={dependency_cache},target=/dependencies,readonly"]
        if artifact is not None:
            args += ["--mount", f"type=bind,source={artifact},target=/work/{build_output},readonly"]
        args += [image, *argv]
        self._docker(*args)

    def _state(self, name: str) -> dict:
        proc = self._docker("inspect", "--format", "{{json .State}}", name, check=False)
        return json.loads(proc.stdout or b"{}") if proc.returncode == 0 else {}

    def run(self, **kwargs) -> CommandResult:
        """Install egress downloads verified bytes; target processes remain offline."""
        if kwargs["network"] != "egress":
            return self._run(**kwargs)
        from .dependencies import fetch_tarballs
        started = time.monotonic()
        requested_argv = tuple(kwargs["argv"])
        deadline = started + min(kwargs["timeout_s"], kwargs["limits"].command_timeout_s)
        cancelled = kwargs.get("is_cancelled", lambda: False)
        try:
            if requested_argv != ("npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"):
                raise SandboxError("install egress only supports fixed npm ci")
            # A project npmrc can override cache, proxies and install behaviour.
            if (kwargs["source"] / ".npmrc").is_symlink() or (kwargs["source"] / ".npmrc").exists():
                raise SandboxError("project .npmrc is unsupported by the offline installer")
            with tempfile.TemporaryDirectory(prefix="aiagent-npm-") as temporary:
                cache = Path(temporary)
                cache.chmod(0o755)
                stats = fetch_tarballs(kwargs["source"], cache, kwargs["limits"], deadline=deadline, is_cancelled=cancelled,
                                       cache_root=self.dependency_cache, progress=self.dependency_progress)
                remaining = deadline - time.monotonic()
                if remaining <= 0 or cancelled():
                    raise SandboxError("dependency acquisition cancelled or timed out")
                offline = {**kwargs, "network": "none", "dependency_cache": cache, "timeout_s": remaining}
                offline["argv"] = ["sh", "-c",
                    'if [ -f /dependencies/00000.tgz ]; then '
                    'npm cache add /dependencies/*.tgz --offline --ignore-scripts --no-audit --no-fund || exit $?; fi; '
                    'exec npm ci --offline --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org']
                result = self._run(**offline)
                return replace(result, argv=requested_argv, network="egress", duration_s=round(time.monotonic()-started, 3),
                               stdout=(json.dumps({'dependency_acquisition': stats}) + '\n').encode() + result.stdout)
        except (WorkspaceError, OSError, ValueError) as exc:
            return CommandResult(argv=requested_argv, exit_code=None if cancelled() else 1,
                timed_out=time.monotonic() >= deadline, cancelled=cancelled(), oom_killed=False,
                duration_s=round(time.monotonic()-started, 3), stdout=b"", stderr=str(exc).encode()[:kwargs["limits"].max_log_bytes],
                truncated=False, network="egress", container=kwargs["name"])

    def _run(self, *, name: str, image: str, source: Path, argv: Sequence[str], network: str,
            limits: ResourceLimits, env: dict[str, str], labels: dict[str, str], timeout_s: float,
            secrets: Sequence[str] = (), is_cancelled=lambda: False,
             dependency_cache: Path | None = None) -> CommandResult:
        """Run one command to completion (or timeout/cancel) and remove the container."""
        if is_cancelled():
            return CommandResult(tuple(argv), None, False, True, False, 0, b"", b"", False, network, name)
        self.create(name=name, image=image, source=source, argv=argv, network=network,
                    limits=limits, env=env, labels=labels, dependency_cache=dependency_cache)
        started = time.monotonic()
        timed_out = cancelled = False
        try:
            if is_cancelled():
                return CommandResult(tuple(argv), None, False, True, False, 0, b"", b"", False, network, name)
            self._docker("start", name)
            deadline = started + timeout_s
            while True:
                state = self._state(name)
                if is_cancelled():
                    cancelled = True
                    break
                if not state.get("Running", False):
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                time.sleep(0.2)
            if timed_out or cancelled:
                self._docker("kill", name, check=False)
                for _ in range(50):
                    if not self._state(name).get("Running", False):
                        break
                    time.sleep(0.1)
            state = self._state(name)
            out = self._docker("logs", name, check=False)
            cap = limits.max_log_bytes
            truncated = len(out.stdout) > cap or len(out.stderr) > cap
            exit_code = state.get("ExitCode")
            return CommandResult(
                argv=tuple(argv), exit_code=None if (timed_out or cancelled) else exit_code,
                timed_out=timed_out, cancelled=cancelled, oom_killed=bool(state.get("OOMKilled")),
                duration_s=round(time.monotonic() - started, 3),
                stdout=redact(out.stdout[:cap], secrets), stderr=redact(out.stderr[:cap], secrets),
                truncated=truncated, network=network, container=name,
            )
        finally:
            self._docker("rm", "-f", name, check=False)

    def start_detached(self, *, name: str, image: str, source: Path, argv: Sequence[str],
                       limits: ResourceLimits, env: dict[str, str], labels: dict[str, str],
                       artifact: Path | None = None, build_output: str = "dist") -> None:
        self.create(name=name, image=image, source=source, argv=argv, network="none",
                    limits=limits, env=env, labels=labels, artifact=artifact, build_output=build_output)
        try:
            self._docker("start", name)
        except BaseException:
            self.kill_and_remove(name)
            raise

    def exec_probe(self, name: str, argv: Sequence[str], timeout: float = 10) -> subprocess.CompletedProcess[bytes]:
        return self._docker("exec", name, *argv, timeout=timeout, check=False)

    def logs(self, name: str, cap: int) -> tuple[bytes, bytes]:
        out = self._docker("logs", name, check=False)
        return out.stdout[:cap], out.stderr[:cap]

    # -- ownership-aware listing and removal ---------------------------------
    def owned(self, *, project_id: str | None = None, run_id: str | None = None, include_stopped: bool = True) -> list[dict[str, str]]:
        """Containers labelled as this supervisor's attempt sandboxes (never previews)."""
        filters = ["--filter", f"label={LABEL_MANAGED}=1", "--filter", f"label={LABEL_ROLE}={ROLE_ATTEMPT}",
                   "--filter", f"label=aiagent.supervisor={self.supervisor_id}"]
        if project_id:
            filters += ["--filter", f"label=aiagent.project={project_id}"]
        if run_id:
            filters += ["--filter", f"label=aiagent.run={run_id}"]
        args = ["ps", *(["-a"] if include_stopped else []), *filters, "--format", "{{json .}}"]
        rows = []
        for line in self._docker(*args).stdout.decode().splitlines():
            row = json.loads(line)
            labels = dict(kv.split("=", 1) for kv in row.get("Labels", "").split(",") if "=" in kv)
            rows.append({"name": row["Names"], "state": row["State"], **labels})
        return rows

    def kill_and_remove(self, name: str) -> None:
        self._docker("kill", name, check=False)
        self._docker("rm", "-f", name, check=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now() -> str:
    return utcnow()
