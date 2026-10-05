"""Agent definitions: SOUL.md + instructions.md per role, versioned by content digest.

A SOUL sets identity and behaviour only. It never grants permissions: tools come from the role
policy (tools.py) and every action is authorised by the backend from the run's identity.
The digest of the loaded definition is recorded in each run's context snapshot.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .redaction import Redactor

ROLES = ("po", "technical-lead", "developer", "qa")
REPO_AGENTS_DIR = Path(__file__).resolve().parents[4] / "agents"
MAX_FILE_BYTES = 16 * 1024


class AgentDefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class AgentDefinition:
    role: str
    soul: str
    instructions: str
    digest: str

    @property
    def text(self) -> str:
        return f"{self.soul.strip()}\n\n{self.instructions.strip()}\n"


def _read(path: Path) -> str:
    if not path.is_file():
        raise AgentDefinitionError(f"missing {path.parent.name}/{path.name}")
    raw = path.read_bytes()
    if not raw.strip():
        raise AgentDefinitionError(f"{path.parent.name}/{path.name} is empty")
    if len(raw) > MAX_FILE_BYTES:
        raise AgentDefinitionError(f"{path.parent.name}/{path.name} exceeds {MAX_FILE_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentDefinitionError(f"{path.parent.name}/{path.name} is not UTF-8") from exc
    if "\0" in text:
        raise AgentDefinitionError(f"{path.parent.name}/{path.name} contains NUL")
    if Redactor().contains_secret(text):
        raise AgentDefinitionError(f"{path.parent.name}/{path.name} looks like it contains a secret")
    return text.replace("\r\n", "\n")


def load_agents(root: Path | str = REPO_AGENTS_DIR) -> dict[str, AgentDefinition]:
    """Load all four roles; any missing, empty, oversized or secret-looking file is an error."""
    root = Path(root)
    agents: dict[str, AgentDefinition] = {}
    for role in ROLES:
        soul, instructions = _read(root / role / "SOUL.md"), _read(root / role / "instructions.md")
        digest = hashlib.sha256(json.dumps({"role": role, "soul": soul, "instructions": instructions},
                                           sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        agents[role] = AgentDefinition(role, soul, instructions, digest)
    return agents
