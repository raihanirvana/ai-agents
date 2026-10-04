"""Read-only prerequisites probe. Never invokes a model, target, or Hermes agent.

This module deliberately does not import app.workspace (POSIX-only DEV-005).
Exit 2 means prerequisites are missing; exit 0 is NOT spike/QA acceptance.
Provider authentication, tools and accounting still require the real experiment.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

PIN = json.loads(Path(__file__).with_name("hermes-pin.json").read_text(encoding="utf-8"))
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/~+-]{0,199}$")
KEY_ENV = {"openrouter": "OPENROUTER_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}


def probe(argv: list[str]) -> tuple[int | None, str]:
    """Suppress external errors/credentials; only selected successful fields leave collect()."""
    # Docker engine selectors are kept so the probe inspects the same engine that
    # the DEV-005 sandbox CLI uses (it inherits the supervisor environment).
    env = {k: v for k, v in os.environ.items()
           if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT",
                            "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_RUNTIME_DIR",
                            "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
                            "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"}}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    try:
        with tempfile.TemporaryDirectory(prefix="dev006-probe-") as scratch:
            result = subprocess.run(argv, capture_output=True, timeout=15, cwd=scratch, env=env)
        return result.returncode, result.stdout[:65536].decode("utf-8", errors="replace").strip()
    except (OSError, subprocess.TimeoutExpired):
        return None, ""


def collect(*, provider: str, model: str, hermes_source: Path | None = None,
            hermes_python: Path | None = None, docker_bin: str = "docker",
            image: str = "node:22.20.0-alpine", env: Mapping[str, str] | None = None,
            system: str | None = None, python_version: tuple[int, ...] | None = None,
            run: Callable[[list[str]], tuple[int | None, str]] = probe,
            which: Callable[[str], str | None] = shutil.which) -> dict:
    env = os.environ if env is None else env
    system = platform.system() if system is None else system
    python_version = sys.version_info[:3] if python_version is None else python_version
    checks: list[dict] = []

    def add(name: str, ready: bool, detail: str) -> None:
        checks.append({"name": name, "status": "ready" if ready else "blocked", "detail": detail})

    add("workspace_host", system in {"Linux", "Darwin"},
        "DEV-005 needs Linux/macOS with fcntl and dir_fd; on Windows run inside WSL2 Linux.")
    add("python", python_version[:2] >= (3, 11),
        "Backend preflight requires Python 3.11+; the Hermes venv interpreter is checked by hermes_install.")
    git = which("git")
    git_ok = bool(git and run([git, "--version"])[0] == 0)
    add("git", git_ok, "Git must be executable; no target repository commands are run.")

    docker = which(docker_bin)
    code, output = run([docker, "info", "--format", "{{.OSType}}"] ) if docker else (None, "")
    engine_ok = code == 0 and output == "linux"
    add("docker_engine", engine_ok, "A running Linux container engine is required (CLI presence is insufficient).")
    code, output = run([docker, "image", "inspect", "--format", "{{.Id}}", image]) if engine_ok else (None, "")
    image_id = output if code == 0 and re.fullmatch(r"sha256:[0-9a-f]{64}", output) else None
    add("target_image", image_id is not None, "Pinned node image must exist locally; this probe never pulls it.")

    key_name = KEY_ENV.get(provider)
    add("provider", key_name is not None, "Supported preflight providers: openrouter, deepseek; no implicit fallback.")
    model_ok = bool(MODEL_ID.fullmatch(model))
    add("model", model_ok, "Set an explicit model ID; validate tools, pricing and quota against the live provider.")
    add("provider_key", bool(key_name and env.get(key_name, "").strip()),
        "Provider key must be in the selected provider's environment variable; presence does not prove authentication.")

    source_ok = False
    if git_ok and hermes_source is not None and hermes_source.is_dir():
        source = str(hermes_source.resolve())
        git_args = [git, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, "-C", source]
        code, head = run([*git_args, "rev-parse", "HEAD"])
        clean_code, dirty = run([*git_args, "status", "--porcelain", "--untracked-files=all"])
        source_ok = code == 0 and head == PIN["commit"] and clean_code == 0 and dirty == ""
    add("hermes_source", source_ok, "Provide a clean checkout at the exact commit in hermes-pin.json.")

    installed_ok = False
    hermes_python_version = None
    if source_ok and hermes_python is not None and hermes_python.is_file():
        # Metadata only: no Hermes module import, config load, history, tools, or personal home.
        script = ("import importlib.metadata,json,sys; d=importlib.metadata.distribution('hermes-agent'); "
                  "print(json.dumps({'python':list(sys.version_info[:3]),'version':d.version,"
                  "'direct_url':json.loads(d.read_text('direct_url.json') or '{}')}))")
        # Preserve the venv launcher path: resolving a POSIX python symlink points
        # at the base interpreter and discards that venv's site-packages.
        code, metadata = run([str(hermes_python.absolute()), "-I", "-c", script])
        try:
            row = json.loads(metadata) if code == 0 else {}
            direct = row.get("direct_url", {})
            interpreter = tuple(int(part) for part in row.get("python", ()))
            hermes_python_version = ".".join(map(str, interpreter)) or None
            installed_ok = ((3, 11) <= interpreter[:2] < (3, 14) and
                            row.get("version") == PIN["package_version"] and
                            direct.get("dir_info", {}).get("editable") is True and
                            direct.get("url") == hermes_source.resolve().as_uri())
        except (ValueError, AttributeError, TypeError):
            installed_ok = False
    add("hermes_install", installed_ok,
        "Provide a Python >=3.11,<3.14 venv with an editable install from the pinned checkout.")

    ready = all(c["status"] == "ready" for c in checks)
    return {
        "ticket": "DEV-006", "observed_at": datetime.now(timezone.utc).isoformat(),
        "status": "prerequisites_ready" if ready else "blocked", "real_runtime_verified": False,
        "host": system, "python": ".".join(map(str, python_version)),
        "hermes_python": hermes_python_version if installed_ok else None,
        "runtime_pin": PIN, "target_image": image, "target_image_id": image_id,
        "provider": provider if provider in KEY_ENV else "unsupported",
        "model": model if model_ok else None, "checks": checks,
        "next": "Execute and record the real experiment in docs/spikes/DEV-006.md after prerequisites are ready.",
    }


def local_environment(path: Path | None) -> dict[str, str]:
    """Read explicit dotenv input without interpolation or mutation of os.environ."""
    selected = dict(os.environ)
    if path is not None:
        from dotenv import dotenv_values  # already pinned in backend requirements.lock
        allowed = {"SPIKE_PROVIDER", "SPIKE_MODEL", *KEY_ENV.values()}
        for key, value in dotenv_values(path, interpolate=False).items():
            if key in allowed and value is not None:
                selected.setdefault(key, value)  # process environment wins, even if empty
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Explicit local dotenv file; only provider/model/key fields are read.")
    parser.add_argument("--provider", choices=tuple(KEY_ENV))
    parser.add_argument("--model")
    parser.add_argument("--hermes-source", type=Path)
    parser.add_argument("--hermes-python", type=Path)
    parser.add_argument("--docker-bin", default="docker")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.env_file is not None and not args.env_file.is_file():
        parser.error("env file is missing")
    env = local_environment(args.env_file)
    report = collect(provider=args.provider or env.get("SPIKE_PROVIDER", "openrouter"),
                     model=args.model if args.model is not None else env.get("SPIKE_MODEL", ""),
                     hermes_source=args.hermes_source, hermes_python=args.hermes_python,
                     docker_bin=args.docker_bin, env=env)
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if report["status"] == "prerequisites_ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
