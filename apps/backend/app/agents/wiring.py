"""Assemble the structured PO/lead runtime from configuration (used by the worker CLI and by tests)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from app.workers import JobQueue

from .context import ContextBuilder
from .models import FakeProvider, ModelClient, ModelRegistry
from .redaction import Redactor
from .runtime import StructuredAgentRuntime
from .souls import REPO_AGENTS_DIR, load_agents
from .threads import Threads


def model_config_path(env: Mapping[str, str] | None = None) -> Path:
    """AGENT_MODELS_FILE, else agents/models.json (local, gitignored), else the tracked example."""
    env = os.environ if env is None else env
    if env.get("AGENT_MODELS_FILE"):
        return Path(env["AGENT_MODELS_FILE"])
    local = REPO_AGENTS_DIR / "models.json"
    return local if local.is_file() else REPO_AGENTS_DIR / "models.example.json"


def build_structured_runtime(db, store, workflow, queue: JobQueue, *, env: Mapping[str, str] | None = None,
                             fake_provider: FakeProvider | None = None, config_path: Path | None = None):
    """Returns (runtime, threads, notes). With fake_provider the runtime is the labelled structured:fake one.

    Real providers are built only from environment keys; a role whose provider has no key fails its jobs with a
    clear message instead of blocking the worker. The chat-completions adapter is UNVERIFIED against a real provider.
    """
    env = os.environ if env is None else env
    agents = load_agents()
    notes = []
    if fake_provider is not None:
        # Every role is bound to the scripted provider; the real model config is not consulted.
        registry = ModelRegistry.from_dict({"default": {"provider": "fake", "model": "fake-model"}})
        client = ModelClient(registry, {"fake": fake_provider}, Redactor())
        notes.append("structured:fake runtime uses a scripted FAKE provider; results are not real model output")
    else:
        registry = ModelRegistry.load(config_path or model_config_path(env))
        client = ModelClient.from_environment(registry, env)
        missing = sorted({registry.for_role(r).provider for r in ("po", "technical-lead")} - set(client.providers))
        if missing:
            notes.append("no API key for provider(s) " + ", ".join(missing) + ": PO/lead jobs will fail until one is set")
        notes.append("real provider adapter is UNVERIFIED until a real run (DEV-010/015)")
    threads = Threads(db, queue, redactor=client.redactor)
    runtime = StructuredAgentRuntime(db=db, workflow=workflow, threads=threads,
                                     builder=ContextBuilder(db, store, agents, client.redactor), client=client,
                                     redactor=client.redactor, fake=fake_provider is not None)
    return runtime, threads, notes
