"""Model client: per-role config, accounting, redaction, error mapping. Providers are fake or a local stub."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.agents import (ChatCompletionsProvider, ConfigError, FakeProvider, ModelClient, ModelRegistry, ModelTimeout,
                        ProviderQuota, Redactor, Usage)
from app.agents.models import ModelRequest, ProviderRejected, ProviderUnavailable
from app.workers import QuotaWait

from .conftest import CONFIG, SECRET


def registry(**changes):
    data = json.loads(json.dumps(CONFIG))
    data.update(changes)
    return ModelRegistry.from_dict(data)


# --- configuration --------------------------------------------------------------------------------------
def test_each_role_can_use_its_own_provider_and_model():
    reg = registry(providers={"openrouter": {"base_url": "https://x/api", "api_key_env": "K1"},
                              "deepseek": {"base_url": "https://y/api", "api_key_env": "K2"}},
                   roles={"po": {"provider": "openrouter", "model": "free-model"},
                          "qa": {"provider": "deepseek", "model": "cheap-model", "timeout_s": 120}})
    assert (reg.for_role("po").provider, reg.for_role("po").model) == ("openrouter", "free-model")
    assert reg.for_role("qa").timeout_s == 120 and reg.for_role("qa").provider == "deepseek"
    assert reg.for_role("developer").provider == "fake"  # inherits the default


@pytest.mark.parametrize("data", [
    {"default": {"provider": "nowhere", "model": "m"}},
    {"default": {"provider": "fake"}},
    {"default": {"provider": "fake", "model": "m", "timeout_s": 0}},
    {"default": {"provider": "fake", "model": "m", "timeout_s": 9999}},
    {"default": {"provider": "fake", "model": "m", "max_output_tokens": -1}},
    {"default": {"provider": "fake", "model": "m"}, "providers": {"p": {"base_url": "x"}}},
    {"default": {"provider": "fake", "model": "m"}, "roles": {"po": {"timeout_s": True}}},
])
def test_bad_model_config_is_refused(data):
    with pytest.raises(ConfigError):
        ModelRegistry.from_dict(data)


def test_example_config_is_valid_and_has_no_secrets():
    from app.agents.souls import REPO_AGENTS_DIR
    path = REPO_AGENTS_DIR / "models.example.json"
    reg = ModelRegistry.load(path)
    assert reg.for_role("technical-lead").model
    assert not Redactor().contains_secret(path.read_text())
    with pytest.raises(ConfigError):
        ModelRegistry.load(REPO_AGENTS_DIR / "does-not-exist.json")


def test_missing_key_never_blocks_other_roles_and_fails_clearly_for_its_own():
    reg = registry(providers={"openrouter": {"base_url": "https://x", "api_key_env": "K1"}},
                   roles={"po": {"provider": "openrouter", "model": "m"}})
    client = ModelClient.from_environment(reg, env={})
    with pytest.raises(ConfigError, match="no API key"):
        client.provider_for("po")
    assert ModelClient.from_environment(reg, env={"K1": "k" * 20}).provider_for("po").name == "openrouter"


# --- accounting through the run context --------------------------------------------------------------------------
def po_ctx(env):
    return env.ctx(env.job("po", "breakdown"))


def test_a_call_is_reserved_limited_and_its_usage_recorded(agent_env):
    env = agent_env
    env.script(('{"ok": true}', Usage(prompt_tokens=120, completion_tokens=30, total_tokens=150, cached_tokens=100,
                                       cost_usd=0.0021)))
    ctx = po_ctx(env)
    result = env.client.complete(ctx, "po", "system text", "user text")
    request = env.provider.requests[0]
    assert (request.system, request.user, request.model) == ("system text", "user text", "fake-model")
    assert request.max_output_tokens == 512  # min(role config 512, job cap 1024)
    assert result.fake is True and result.provider == "fake"
    usage = env.get(ctx.lease.job_id).usage
    assert usage["model_calls"] == 1 and usage["prompt_tokens"] == 120 and usage["output_tokens"] == 30
    assert usage["cached_tokens"] == 100 and usage["cost_usd"] == pytest.approx(0.0021)


def test_unreported_usage_and_cost_are_unknown_not_zero(agent_env):
    env = agent_env
    env.script(('{"ok": true}', None))
    ctx = po_ctx(env)
    env.client.complete(ctx, "po", "s", "u")
    usage = env.get(ctx.lease.job_id).usage
    assert usage["model_calls"] == 1
    assert {"cost_usd", "output_tokens", "total_tokens"} <= set(usage["_unknown"])
    assert "cost_usd" not in usage  # never a fake zero


def test_job_token_cap_lowers_the_request_cap(agent_env):
    env = agent_env
    env.script('{"ok": true}')
    ctx = env.ctx(env.job("po", "breakdown", limits={**{"model_calls": 3, "tool_calls": 3, "active_s": 30}, "output_tokens": 64}))
    env.client.complete(ctx, "po", "s", "u")
    assert env.provider.requests[0].max_output_tokens == 64


def test_failed_provider_call_still_counts_and_is_unknown(agent_env):
    env = agent_env
    env.script(ModelTimeout("provider did not answer within 5s"))
    ctx = po_ctx(env)
    with pytest.raises(ModelTimeout):
        env.client.complete(ctx, "po", "s", "u")
    usage = env.get(ctx.lease.job_id).usage
    assert usage["model_calls"] == 1 and "output_tokens" in usage["_unknown"]
    assert ModelTimeout.retryable and not ProviderRejected.retryable


def test_provider_quota_becomes_a_shared_wait(agent_env):
    env = agent_env
    env.script(ProviderQuota(90, "provider quota (HTTP 429)"))
    with pytest.raises(QuotaWait) as wait:
        env.client.complete(po_ctx(env), "po", "s", "u")
    assert "429" in wait.value.reason and env.limiter.status()["blocked_until"] is not None


# --- redaction ---------------------------------------------------------------------------------------------------------
def test_secrets_never_reach_the_provider_and_never_come_back(agent_env):
    env = agent_env
    env.script(f"here is your key {SECRET} and Bearer abcdefghijklmnopqrstuvwxyz0123")
    result = env.client.complete(po_ctx(env), "po", f"system with {SECRET}", f"brief says token: {SECRET}")
    sent = env.provider.requests[0]
    assert SECRET not in sent.system and SECRET not in sent.user and "[REDACTED]" in sent.user
    assert SECRET not in result.text and "abcdefghijklmnopqrstuvwxyz0123" not in result.text


def test_error_messages_are_redacted(agent_env):
    env = agent_env
    env.script(ProviderUnavailable(f"upstream echoed {SECRET}"))
    with pytest.raises(ProviderUnavailable) as error:
        env.client.complete(po_ctx(env), "po", "s", "u")
    assert SECRET not in str(error.value)


# --- the HTTP adapter against a local stub (contract double; NOT a real provider) -----------------------------------------
class Stub:
    def __init__(self):
        self.requests, self.respond = [], lambda handler: handler.reply(200, {})
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, body, headers=None, raw=None):
                data = raw if raw is not None else json.dumps(body).encode()
                self.send_response(code)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                stub.requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                      "body": json.loads(self.rfile.read(length))})
                stub.respond(self)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub():
    server = Stub()
    yield server
    server.close()


def provider(stub, key="live-key-0123456789abcdef"):
    return ChatCompletionsProvider("stub", stub.url, key, Redactor())


REQUEST = ModelRequest("system", "user", 100, "vendor/model")


def test_adapter_sends_the_key_only_in_the_header_and_parses_usage_and_cost(stub):
    stub.respond = lambda h: h.reply(200, {"model": "vendor/model-2", "choices": [{"message": {"content": "hello"}}],
                                           "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18,
                                                     "cost": 0.0003, "prompt_tokens_details": {"cached_tokens": 4}}})
    response = provider(stub).complete(REQUEST, timeout_s=5)
    seen = stub.requests[0]
    assert seen["path"] == "/v1/chat/completions" and seen["auth"] == "Bearer live-key-0123456789abcdef"
    assert "live-key" not in json.dumps(seen["body"])
    assert seen["body"]["max_tokens"] == 100 and seen["body"]["model"] == "vendor/model"
    assert response.text == "hello" and response.model == "vendor/model-2"
    assert response.usage == Usage(11, 7, 18, 4, 0.0003)


def test_adapter_marks_missing_or_invalid_usage_as_unknown(stub):
    stub.respond = lambda h: h.reply(200, {"choices": [{"message": {"content": "x"}}],
                                           "usage": {"prompt_tokens": -3, "completion_tokens": "7"}})
    assert provider(stub).complete(REQUEST, timeout_s=5).usage == Usage()
    stub.respond = lambda h: h.reply(200, {"choices": [{"message": {"content": "x"}}]})
    assert provider(stub).complete(REQUEST, timeout_s=5).usage.cost_usd is None


def test_adapter_maps_http_failures_to_distinct_errors(stub):
    cases = [(429, {"Retry-After": "45"}, ProviderQuota), (500, {}, ProviderUnavailable),
             (503, {}, ProviderUnavailable), (400, {}, ProviderRejected), (401, {}, ProviderRejected),
             (302, {"Location": "http://elsewhere/"}, ProviderRejected)]
    for code, headers, expected in cases:
        stub.respond = lambda h, code=code, headers=headers: h.reply(code, {"error": "nope"}, headers)
        with pytest.raises(expected) as error:
            provider(stub).complete(REQUEST, timeout_s=5)
        if code == 429:
            assert error.value.retry_after_s == 45


def test_adapter_rejects_malformed_answers(stub):
    for raw in (b"not json", json.dumps({"choices": []}).encode(), json.dumps({"choices": [{"message": {"content": 5}}]}).encode(),
                json.dumps({"nothing": 1}).encode()):
        stub.respond = lambda h, raw=raw: h.reply(200, None, raw=raw)
        with pytest.raises(ProviderUnavailable):
            provider(stub).complete(REQUEST, timeout_s=5)


def test_adapter_times_out_and_never_follows_a_redirect(stub):
    stub.respond = lambda h: time.sleep(1.0)
    started = time.monotonic()
    with pytest.raises(ModelTimeout):
        provider(stub).complete(REQUEST, timeout_s=0.2)
    assert time.monotonic() - started < 0.9


def test_adapter_redacts_the_key_if_the_server_echoes_it(stub):
    key = "live-key-0123456789abcdef"
    stub.respond = lambda h: h.reply(400, {"error": f"bad token {key}"})
    with pytest.raises(ProviderRejected) as error:
        provider(stub, key).complete(REQUEST, timeout_s=5)
    assert key not in str(error.value) and "[REDACTED]" in str(error.value)


def test_unreachable_provider_is_retryable_and_a_keyless_adapter_is_refused():
    dead = ChatCompletionsProvider("dead", "http://127.0.0.1:9/v1", "k" * 20, Redactor())
    # Linux refuses the connection at once, Windows may hang until the timeout: both are retryable.
    with pytest.raises((ProviderUnavailable, ModelTimeout)) as error:
        dead.complete(REQUEST, timeout_s=1)
    assert error.value.retryable
    with pytest.raises(ConfigError):
        ChatCompletionsProvider("x", "http://x", "", Redactor())
