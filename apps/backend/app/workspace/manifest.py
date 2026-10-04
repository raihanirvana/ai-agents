"""Runner manifest (React/Vite) and immutable build/verification target manifests."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .errors import ManifestError

PHASES = ("install", "build", "test", "start")
ALLOWED_EXECUTABLES = {"npm", "node", "npx"}
NETWORKS = ("none", "egress")
_SECRET_KEY = re.compile(r"(secret|token|password|passwd|credential|api[_-]?key|private)", re.I)
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(?::(?P<tag>[A-Za-z0-9._-]+))?(?:@sha256:[0-9a-f]{64})?$")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True)
class PhaseCommand:
    argv: tuple[str, ...]
    network: str = "none"
    timeout_s: int = 300


@dataclass(frozen=True)
class RunnerManifest:
    revision: int
    runner: str
    image: str
    toolchain: dict[str, str]
    commands: dict[str, PhaseCommand]
    port: int
    health_path: str
    fixture: dict[str, Any]
    migrations: dict[str, Any]
    env: dict[str, str]
    exclude_from_sync: tuple[str, ...]
    build_output: str
    dependency_files: tuple[str, ...] = field(default=("package-lock.json",))

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "runner": self.runner,
            "image": self.image,
            "toolchain": dict(self.toolchain),
            "commands": {
                name: {"argv": list(c.argv), "network": c.network, "timeout_s": c.timeout_s}
                for name, c in sorted(self.commands.items())
            },
            "port": self.port,
            "health_path": self.health_path,
            "fixture": self.fixture,
            "migrations": self.migrations,
            "env": dict(sorted(self.env.items())),
            "exclude_from_sync": list(self.exclude_from_sync),
            "build_output": self.build_output,
            "dependency_files": list(self.dependency_files),
        }

    @property
    def digest(self) -> str:
        return digest_of(self.to_dict())

    def effective_config(self) -> dict[str, Any]:
        """Behaviour-affecting config only (no process/container IDs or locators)."""
        data = self.to_dict()
        data.pop("revision")
        return data

    @property
    def effective_config_digest(self) -> str:
        return digest_of(self.effective_config())


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ManifestError(message)


def parse_manifest(raw: dict[str, Any]) -> RunnerManifest:
    """Validate an untrusted manifest dict; raise ManifestError with a concrete reason."""
    _require(isinstance(raw, dict), "manifest must be an object")
    unknown = set(raw) - {
        "revision", "runner", "image", "toolchain", "commands", "port", "health_path",
        "fixture", "migrations", "env", "exclude_from_sync", "build_output", "dependency_files",
    }
    _require(not unknown, f"unknown manifest keys: {sorted(unknown)}")
    _require(raw.get("runner") == "react-vite", "unsupported runner; only 'react-vite' is implemented")
    revision = raw.get("revision")
    _require(isinstance(revision, int) and not isinstance(revision, bool) and revision >= 1, "revision must be a positive integer")

    image = raw.get("image")
    _require(isinstance(image, str) and bool(_IMAGE.match(image)), "image must be a valid image reference")
    tag = _IMAGE.match(image).group("tag")  # type: ignore[union-attr]
    _require("@sha256:" in image or (tag is not None and tag != "latest"), "image must be pinned by version tag or digest, not 'latest'")

    toolchain = raw.get("toolchain")
    _require(isinstance(toolchain, dict) and isinstance(toolchain.get("node"), str) and toolchain["node"], "toolchain.node is required")
    _require(all(isinstance(k, str) and isinstance(v, str) for k, v in toolchain.items()), "toolchain values must be strings")

    commands_raw = raw.get("commands")
    _require(isinstance(commands_raw, dict), "commands must be an object")
    missing = [p for p in PHASES if p not in commands_raw]
    _require(not missing, f"commands missing phases: {missing}")
    _require(set(commands_raw) <= set(PHASES), f"unknown phases: {sorted(set(commands_raw) - set(PHASES))}")
    commands: dict[str, PhaseCommand] = {}
    for name, spec in commands_raw.items():
        _require(isinstance(spec, dict) and set(spec) <= {"argv", "network", "timeout_s"}, f"{name}: invalid command object")
        argv = spec.get("argv")
        _require(isinstance(argv, list) and argv and all(isinstance(a, str) and a and "\0" not in a for a in argv), f"{name}: argv must be a non-empty list of strings")
        _require(argv[0] in ALLOWED_EXECUTABLES, f"{name}: executable {argv[0]!r} is not allowed (allowed: {sorted(ALLOWED_EXECUTABLES)})")
        network = spec.get("network", "none")
        _require(network in NETWORKS, f"{name}: network must be one of {NETWORKS}")
        _require(network == "none" or name == "install", f"{name}: only the install phase may request egress")
        timeout_s = spec.get("timeout_s", 300)
        _require(isinstance(timeout_s, int) and not isinstance(timeout_s, bool) and 1 <= timeout_s <= 1800, f"{name}: timeout_s must be 1..1800")
        commands[name] = PhaseCommand(tuple(argv), network, timeout_s)
        if network == "egress":
            _require(argv == ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                     "install egress only supports the fixed npm ci command with --ignore-scripts")

    port = raw.get("port")
    _require(isinstance(port, int) and not isinstance(port, bool) and 1024 <= port <= 65535, "port must be an integer 1024..65535")
    health_path = raw.get("health_path", "/")
    _require(isinstance(health_path, str) and health_path.startswith("/") and re.fullmatch(r"/[A-Za-z0-9._~/%-]*", health_path) is not None, "health_path must be a simple absolute path")

    fixture = raw.get("fixture")
    _require(isinstance(fixture, dict) and isinstance(fixture.get("id"), str) and fixture["id"], "fixture.id is required (use an explicit id even for 'none')")
    migrations = raw.get("migrations", {"id": "none"})
    _require(isinstance(migrations, dict) and isinstance(migrations.get("id"), str), "migrations.id is required")

    env = raw.get("env", {})
    _require(isinstance(env, dict), "env must be an object")
    for key, value in env.items():
        _require(isinstance(key, str) and _ENV_NAME.match(key) is not None, f"invalid env name {key!r}")
        _require(not _SECRET_KEY.search(key), f"env name {key!r} looks like a secret; secrets are never given to targets")
        _require(key not in ("NODE_OPTIONS", "NODE_PATH", "PATH", "HOME") and not key.startswith("NPM_CONFIG_"),
                 f"env {key} overrides runner internals")
        _require(isinstance(value, str) and "\0" not in value, f"env {key} must be a string")

    exclude = raw.get("exclude_from_sync", ["node_modules", "dist"])
    _require(isinstance(exclude, list) and all(isinstance(e, str) and e and "/" not in e and e not in (".", "..", ".git") for e in exclude), "exclude_from_sync must be top-level directory names")
    build_output = raw.get("build_output", "dist")
    _require(isinstance(build_output, str) and re.fullmatch(r"[A-Za-z0-9_-]+", build_output) is not None, "build_output must be a top-level directory name")
    dependency_files = raw.get("dependency_files", ["package-lock.json"])
    _require(isinstance(dependency_files, list) and all(isinstance(f, str) and "/" not in f and f for f in dependency_files), "dependency_files must be top-level file names")

    return RunnerManifest(
        revision=revision, runner="react-vite", image=image, toolchain=dict(toolchain), commands=commands,
        port=port, health_path=health_path, fixture=fixture, migrations=migrations, env=dict(env),
        exclude_from_sync=tuple(exclude), build_output=build_output, dependency_files=tuple(dependency_files),
    )


def reference_manifest_dict(*, image: str = "node:22.20.0-alpine", port: int = 4173) -> dict[str, Any]:
    """Manifest for the bundled React/Vite reference target."""
    return {
        "revision": 1,
        "runner": "react-vite",
        "image": image,
        "toolchain": {"node": "22.20.0", "npm": "10.9.3"},
        "commands": {
            "install": {"argv": ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], "network": "egress", "timeout_s": 300},
            "build": {"argv": ["npm", "run", "build"], "network": "none", "timeout_s": 300},
            "test": {"argv": ["npm", "test"], "network": "none", "timeout_s": 300},
            "start": {"argv": ["npx", "vite", "preview", "--host", "0.0.0.0", "--port", str(port), "--strictPort"], "network": "none", "timeout_s": 300},
        },
        "port": port,
        "health_path": "/",
        "fixture": {"id": "coffee-menu-v1"},
        "migrations": {"id": "none"},
        "env": {"CI": "1"},
        "exclude_from_sync": ["node_modules", "dist"],
        "build_output": "dist",
        "dependency_files": ["package-lock.json"],
    }
