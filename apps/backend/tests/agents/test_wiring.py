"""Assembling the structured runtime from configuration."""
from __future__ import annotations

import json

import pytest

from app.agents import ChatCompletionsProvider, ConfigError, FakeProvider
from app.agents.souls import REPO_AGENTS_DIR
from app.agents.wiring import build_structured_runtime, model_config_path


def test_config_path_prefers_the_environment_then_local_then_the_example(tmp_path, monkeypatch):
    explicit = tmp_path / "models.json"
    assert model_config_path({"AGENT_MODELS_FILE": str(explicit)}) == explicit
    assert model_config_path({"AGENT_MODELS_FILE": ""}).name in ("models.json", "models.example.json")
    assert model_config_path({}).parent == REPO_AGENTS_DIR


def test_fake_runtime_is_labelled_and_uses_the_scripted_provider(agent_env):
    env = agent_env
    runtime, threads, notes = build_structured_runtime(env.db, env.store, env.world.w, env.queue,
                                                       fake_provider=FakeProvider([]), env={})
    assert runtime.name == "structured:fake" and runtime.fake is True
    assert any("FAKE provider" in note for note in notes)
    assert runtime.client.provider_for("po").fake is True


def test_real_runtime_without_keys_starts_and_says_which_provider_is_missing(agent_env):
    env = agent_env
    runtime, _, notes = build_structured_runtime(env.db, env.store, env.world.w, env.queue, env={})
    assert runtime.name == "structured" and runtime.fake is False
    assert any("no API key" in note and "openrouter" in note for note in notes)
    assert any("UNVERIFIED" in note for note in notes)
    with pytest.raises(ConfigError, match="no API key"):
        runtime.client.provider_for("po")  # fails the job clearly; it never blocks the worker


def test_real_runtime_with_a_key_builds_the_http_provider_per_role_config(agent_env, tmp_path):
    env = agent_env
    config = tmp_path / "models.json"
    config.write_text(json.dumps({
        "providers": {"openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
                      "deepseek": {"base_url": "https://api.deepseek.com/v1", "api_key_env": "DEEPSEEK_API_KEY"}},
        "default": {"provider": "openrouter", "model": "free/model"},
        "roles": {"technical-lead": {"provider": "deepseek", "model": "cheap/model", "timeout_s": 90}}}))
    runtime, _, notes = build_structured_runtime(env.db, env.store, env.world.w, env.queue, config_path=config,
                                                 env={"OPENROUTER_API_KEY": "k" * 24, "DEEPSEEK_API_KEY": "j" * 24})
    assert isinstance(runtime.client.provider_for("po"), ChatCompletionsProvider)
    assert runtime.client.registry.for_role("technical-lead").model == "cheap/model"
    assert runtime.client.registry.for_role("technical-lead").timeout_s == 90
    assert runtime.client.provider_for("technical-lead").name == "deepseek"
    assert not any("no API key" in note for note in notes)
    assert runtime.client.redactor.redact("k" * 24) == "[REDACTED]"  # configured keys are masked everywhere
