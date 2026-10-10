"""Docker sandbox for target code, driven only by the trusted supervisor.

Containers get: readonly source input, bounded tmpfs /work (no .git), no network,
no Docker socket, no inherited host
environment, dropped capabilities, read-only root, and finite memory/CPU/PIDs/
time. Every container carries labels so cleanup can prove ownership.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import tempfile
import tarfile
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
# RunnerManifest requires a Node toolchain. Copy symlinks verbatim; fs.cp's
# default rewrites relative symlinks into absolute /source paths.
_SEED_SOURCE = (
    "const fs=require('node:fs'); const path=require('node:path'); "
    "const skip=process.argv[1] ? path.join('/source',process.argv[1]) : null; "
    "for(const entry of fs.readdirSync('/source')) fs.cpSync(path.join('/source',entry),path.join('/work',entry),"
    "{recursive:true,verbatimSymlinks:true,"
    "filter:p=>(!skip || (p!==skip && !p.startsWith(skip+'/'))) && "
    "(!process.argv[2] || (p!=='/source/node_modules' && !p.startsWith('/source/node_modules/')))}); "
    "if(process.argv[2]==='readonly') fs.symlinkSync('/installed/node_modules','/work/node_modules');"
)


def seeded_command(argv, *, skip='', readonly_dependencies=False, install_phase=False):
    return ['sh', '-c', 'NODE_OPTIONS= NODE_PATH= node -e "$1" "$2" "$3" || exit $?; shift 3; cd /work || exit $?; exec "$@"',
            'sandbox-seed', _SEED_SOURCE, skip, 'readonly' if readonly_dependencies else 'install' if install_phase else '', *argv]


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
    cache_key: str | None = None
    cache_origin: dict | None = None


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
               dependency_cache: Path | None = None, writable_work: bool = False,
               readonly_dependencies: Path | None = None, install_phase: bool = False) -> None:
        if network != "none":
            raise SandboxError("target containers must use network none")
        limits.validate()
        args = [
            "create", "--name", name, "--user", CONTAINER_USER,
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", f"{limits.memory_mb}m", "--memory-swap", f"{limits.memory_mb}m",
            "--cpus", str(limits.cpus), "--pids-limit", str(limits.pids),
            "--ulimit", "nofile=4096:4096", "--ulimit", "core=0",
            "--tmpfs", f"/tmp:rw,nosuid,nodev,size={limits.tmpfs_mb if install_phase else min(limits.tmpfs_mb, max(16, limits.memory_mb // 4))}m",
            "--network", "none",
            "--log-driver", "json-file", "--log-opt", "max-size=4m", "--log-opt", "max-file=1",
            # Docker 24's create-time working-dir setup chmods a mounted /work
            # to 0755. Keep the tmpfs mountpoint intact; seed/exec select cwd later.
            "--workdir", "/" if writable_work else "/work",
            "-e", "HOME=/tmp", "-e", "npm_config_cache=/tmp/npm-cache", "-e", "npm_config_update_notifier=false",
        ]
        if writable_work:
            args += ["--mount", f"type=bind,source={source},target=/source,readonly",
                     "--tmpfs", f"/work:rw,nosuid,nodev,size={min(limits.work_mb, max(16, limits.memory_mb // 2))}m,mode=1777"]
        else:
            # Explicit readonly source mode; normal commands/start use tmpfs.
            args += ["--mount", f"type=bind,source={source},target=/work,readonly"]
        args += ["--entrypoint", "/bin/sh"]
        for key, value in sorted(env.items()):
            args += ["-e", f"{key}={value}"]
        for key, value in sorted(labels.items()):
            args += ["--label", f"{key}={value}"]
        if dependency_cache is not None:
            args += ["--mount", f"type=bind,source={dependency_cache},target=/dependencies,readonly"]
        if readonly_dependencies is not None:
            args += ["--mount", f"type=bind,source={readonly_dependencies},target=/installed/node_modules,readonly"]
            from .bounded_io import DEPENDENCY_SCRATCH
            for scratch in DEPENDENCY_SCRATCH:
                args += ['--tmpfs', f'/installed/node_modules/{scratch}:rw,nosuid,nodev,size=32m,mode=1777,uid=1000,gid=1000']
        if artifact is not None:
            args += ["--mount", f"type=bind,source={artifact},target=/work/{build_output},readonly"]
        args += [image, "-c", 'exec "$@"', "sandbox", *argv]
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
            installed = key = None
            if self.dependency_cache is not None and os.environ.get('PIPELINE_INSTALL_CACHE', '1') != '0':
                from .installation_cache import InstallationCache
                from .dependencies import registry_tarballs
                from .fsutil import read_file_beneath
                # Validate registry/integrity restrictions even on snapshot hits.
                registry_tarballs(json.loads(read_file_beneath(kwargs['source'], 'package-lock.json',
                    max_bytes=kwargs['limits'].max_snapshot_bytes)))
                installed = InstallationCache(self.dependency_cache.parent / '.dependency-snapshots', kwargs['limits'])
                image = self.image_id(kwargs['image'])
                key = installed.key(kwargs['source'], image, kwargs['env'], kwargs['limits'])
                def check():
                    if cancelled() or time.monotonic() >= deadline:
                        raise SandboxError('installation snapshot interrupted')
                receipt = installed.restore(key, kwargs['source'], check)
                if receipt is not None:
                    stats = {'phase': 'installation-snapshot', 'cache_hit': True, 'key': key,
                             'bytes': receipt['bytes']}
                    if self.dependency_progress:
                        self.dependency_progress(stats)
                    return CommandResult(requested_argv, 0, False, False, False,
                        round(time.monotonic()-started, 3), json.dumps(stats).encode(), b'', False,
                        'egress', '', cache_key=key, cache_origin=receipt['origin'])
            with tempfile.TemporaryDirectory(prefix="aiagent-npm-") as temporary:
                cache = Path(temporary)
                cache.chmod(0o755)
                stats = fetch_tarballs(kwargs["source"], cache, kwargs["limits"], deadline=deadline, is_cancelled=cancelled,
                                       cache_root=self.dependency_cache, progress=self.dependency_progress)
                remaining = deadline - time.monotonic()
                if remaining <= 0 or cancelled():
                    raise SandboxError("dependency acquisition cancelled or timed out")
                offline = {**kwargs, "network": "none", "dependency_cache": cache, "timeout_s": remaining, "install_phase": True}
                if installed is not None:
                    offline['image'] = image  # Execute the exact image used by the snapshot key.
                offline["argv"] = ["sh", "-c",
                    'if [ -f /dependencies/00000.tgz ]; then '
                    'npm cache add /dependencies/*.tgz --offline --ignore-scripts --no-audit --no-fund || exit $?; fi; '
                    'exec npm ci --offline --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org']
                result = self._run(**offline)
                if (installed is not None and result.exit_code == 0 and not
                        (result.timed_out or result.cancelled or result.oom_killed or result.truncated)):
                    # This is before any project build/test command; never cache
                    # node_modules from a developer-controlled later workspace.
                    try:
                        installed.publish(key, kwargs['source'], {
                            'argv': list(requested_argv), 'image_id': image, 'exit_code': 0,
                            'stdout_sha256': sha256_bytes(result.stdout),
                            'stderr_sha256': sha256_bytes(result.stderr)}, check)
                    except (WorkspaceError, OSError, ValueError):
                        if self.dependency_progress:
                            self.dependency_progress({'phase': 'installation-snapshot', 'cache_hit': False,
                                                      'stored': False})
                return replace(result, argv=requested_argv, network="egress", duration_s=round(time.monotonic()-started, 3),
                               stdout=(json.dumps({'dependency_acquisition': stats}) + '\n').encode() + result.stdout)
        except (WorkspaceError, OSError, ValueError, tarfile.TarError) as exc:
            return CommandResult(argv=requested_argv, exit_code=None if cancelled() else 1,
                timed_out=time.monotonic() >= deadline, cancelled=cancelled(), oom_killed=False,
                duration_s=round(time.monotonic()-started, 3), stdout=b"", stderr=str(exc).encode()[:kwargs["limits"].max_log_bytes],
                truncated=False, network="egress", container=kwargs["name"])

    def _run(self, *, name: str, image: str, source: Path, argv: Sequence[str], network: str,
            limits: ResourceLimits, env: dict[str, str], labels: dict[str, str], timeout_s: float,
            secrets: Sequence[str] = (), is_cancelled=lambda: False,
             dependency_cache: Path | None = None, install_phase: bool = False) -> CommandResult:
        """Run one command to completion (or timeout/cancel) and remove the container."""
        if is_cancelled():
            return CommandResult(tuple(argv), None, False, True, False, 0, b"", b"", False, network, name)
        from .bounded_io import stream_command, import_work
        # PID 1 stays alive after docker exec completes so /work tmpfs remains
        # mounted until the trusted supervisor freezes and exports its bytes.
        started = time.monotonic()
        deadline = started + min(timeout_s, limits.command_timeout_s)
        timed_out = cancelled = False
        stdout = stderr = b''
        truncated, exit_code, oom = False, None, False
        try:
            from . import fsutil
            from .bounded_io import DEPENDENCY_LIMITS, prepare_dependency_scratch
            dependencies = Path(source) / 'node_modules'
            readonly_dependencies = None
            if not install_phase and (dependencies.exists() or dependencies.is_symlink()):
                fsutil.scan_tree(dependencies, limits=DEPENDENCY_LIMITS)
                prepare_dependency_scratch(dependencies)
                readonly_dependencies = dependencies.resolve()
            idle = seeded_command(['sh', '-c', 'touch /tmp/supervisor-ready && exec sleep 2147483647'],
                                  readonly_dependencies=readonly_dependencies is not None, install_phase=install_phase)
            self.create(name=name, image=image, source=source, argv=idle, network=network,
                        limits=limits, env=env, labels=labels, dependency_cache=dependency_cache, writable_work=True,
                        readonly_dependencies=readonly_dependencies, install_phase=install_phase)
            self._docker("start", name, timeout=max(0.1, deadline-time.monotonic()))
            while True:
                cancelled, timed_out = is_cancelled(), time.monotonic() >= deadline
                if cancelled or timed_out:
                    break
                state = self._state(name)
                if not state.get('Running'):
                    oom = bool(state.get('OOMKilled'))
                    init_out, init_err = self.logs(name, limits.max_log_bytes)
                    stdout = init_out[:limits.max_log_bytes]
                    stderr = init_err[:limits.max_log_bytes]
                    raise SandboxError('sandbox source initialization failed')
                ready = self._docker('exec', name, 'test', '-f', '/tmp/supervisor-ready',
                                     timeout=max(0.1, min(10, deadline-time.monotonic())), check=False)
                if ready.returncode == 0:
                    break
                time.sleep(0.1)
            if not (cancelled or timed_out):
                exit_code, stdout, stderr, timed_out, cancelled, truncated = stream_command(
                    [self.docker, 'exec', '--workdir', '/work', name, *argv], deadline=deadline,
                    cancelled=is_cancelled, cap=limits.max_log_bytes)
                state = self._state(name)
                oom = bool(state.get('OOMKilled'))
                if not (timed_out or cancelled or oom) and state.get('Running'):
                    import_work(self.docker, name, source, limits, deadline=deadline, cancelled=is_cancelled,
                                preserve_dependencies=readonly_dependencies is not None, install_phase=install_phase)
                elif not state.get('Running') and exit_code == 0:
                    raise SandboxError('sandbox stopped before source export')
        except (WorkspaceError, OSError, ValueError, tarfile.TarError) as exc:
            exit_code = 1
            stderr = (stderr + b'\n' + str(exc).encode())[:limits.max_log_bytes]
            timed_out, cancelled = time.monotonic() >= deadline, is_cancelled()
        finally:
            self._docker('rm', '-f', name, check=False)
        return CommandResult(tuple(argv), None if timed_out or cancelled else exit_code,
            timed_out, cancelled, oom, round(time.monotonic()-started, 3),
            redact(stdout, secrets), redact(stderr, secrets), truncated, network, name)

    def start_detached(self, *, name: str, image: str, source: Path, argv: Sequence[str],
                       limits: ResourceLimits, env: dict[str, str], labels: dict[str, str],
                       artifact: Path | None = None, build_output: str = "dist") -> None:
        from . import fsutil
        from .bounded_io import DEPENDENCY_LIMITS, prepare_dependency_scratch
        dependencies = Path(source) / 'node_modules'
        readonly_dependencies = None
        if dependencies.exists() or dependencies.is_symlink():
            fsutil.scan_tree(dependencies, limits=DEPENDENCY_LIMITS)
            prepare_dependency_scratch(dependencies)
            readonly_dependencies = dependencies.resolve()
        self.create(name=name, image=image, source=source,
                    argv=seeded_command(argv, skip=build_output if artifact is not None else '',
                                        readonly_dependencies=readonly_dependencies is not None), network="none",
                    limits=limits, env=env, labels=labels, artifact=artifact, build_output=build_output,
                    writable_work=True, readonly_dependencies=readonly_dependencies)
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
