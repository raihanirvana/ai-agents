"""Model client: per-role configuration, providers, usage/cost normalisation, errors, redaction.

Every call goes through RunContext.model_call, so it is reserved against the scope budget BEFORE it
is made, limited by the provider limiter, and finalised afterwards. Usage a provider does not report
is recorded as unknown (None), never as zero. The only provider that ships here without a real key is
the labelled FakeProvider; the chat-completions adapter is contract-tested against a local stub and is
UNVERIFIED against a real provider until DEV-010/015.
"""
from __future__ import annotations

import json
import math
import os
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from app.workers.runtime import RunContext

from .redaction import Redactor
from .souls import ROLES


class ModelError(RuntimeError):
    """Base class; messages are redacted before they are stored or raised."""
    retryable = False


class ModelTimeout(ModelError):
    retryable = True


class ProviderUnavailable(ModelError):
    retryable = True


class ProviderRejected(ModelError):
    retryable = False


class ProviderQuota(ModelError):
    def __init__(self, retry_after_s: float, reason: str):
        super().__init__(reason)
        self.retry_after_s, self.reason = retry_after_s, reason


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Usage:
    """None means the provider did not report it (unknown), which is different from zero."""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    cost_usd: float | None = None

    def as_counters(self) -> dict[str, float | int | None]:
        return {"prompt_tokens": self.prompt_tokens, "output_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens, "cached_tokens": self.cached_tokens, "cost_usd": self.cost_usd}


@dataclass(frozen=True)
class ModelRequest:
    system: str
    user: str
    max_output_tokens: int
    model: str
    temperature: float = 0.2


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    usage: Usage = field(default_factory=Usage)
    provider: str = ""
    model: str = ""


class Provider(Protocol):
    name: str
    fake: bool

    def complete(self, request: ModelRequest, *, timeout_s: float) -> ProviderResponse: ...


@dataclass(frozen=True)
class ModelConfig:
    role: str
    provider: str
    model: str
    timeout_s: float = 60.0
    max_output_tokens: int = 2048
    temperature: float = 0.2


class ModelRegistry:
    """Per-role provider/model settings. A role without an override uses the default."""

    def __init__(self, configs: Mapping[str, ModelConfig], providers: Mapping[str, dict[str, str]]):
        self._configs, self._providers = dict(configs), dict(providers)

    def for_role(self, role: str) -> ModelConfig:
        if role not in self._configs:
            raise ConfigError(f"no model configured for role {role}")
        return self._configs[role]

    def provider_settings(self, name: str) -> dict[str, str]:
        if name not in self._providers:
            raise ConfigError(f"provider {name} is not configured")
        return self._providers[name]

    @property
    def providers(self) -> dict[str, dict[str, str]]:
        return dict(self._providers)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelRegistry":
        providers = data.get("providers", {})
        default = data.get("default") or {}
        if not isinstance(providers, dict) or not isinstance(default, dict):
            raise ConfigError("providers and default must be objects")
        configs = {}
        for role in ROLES:
            merged = {**default, **(data.get("roles", {}).get(role) or {})}
            for key in ("provider", "model"):
                if not isinstance(merged.get(key), str) or not merged[key].strip():
                    raise ConfigError(f"role {role} needs a {key}")
            if merged["provider"] not in providers and not merged["provider"].startswith("fake"):
                raise ConfigError(f"role {role} uses unknown provider {merged['provider']}")
            timeout = merged.get("timeout_s", 60)
            tokens = merged.get("max_output_tokens", 2048)
            if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 600 or
                    isinstance(tokens, bool) or not isinstance(tokens, int) or not 0 < tokens <= 32768):
                raise ConfigError(f"role {role} needs a finite timeout (0-600 s) and max_output_tokens")
            configs[role] = ModelConfig(role, merged["provider"], merged["model"], float(timeout), tokens,
                                        float(merged.get("temperature", 0.2)))
        for name, settings in providers.items():
            if not isinstance(settings, dict) or not settings.get("base_url") or not settings.get("api_key_env"):
                raise ConfigError(f"provider {name} needs base_url and api_key_env")
        return cls(configs, providers)

    @classmethod
    def load(cls, path: Path | str) -> "ModelRegistry":
        try:
            return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError(f"cannot read model config {path}: {type(exc).__name__}") from exc


# ---------------------------------------------------------------------------------------------
class FakeProvider:
    """LABELLED FAKE provider: scripted replies, optional failures. Never real model output."""

    name = "fake"
    fake = True

    def __init__(self, replies: list | Callable[[ModelRequest], Any] | None = None, *, usage: Usage | None = Usage(
            prompt_tokens=100, completion_tokens=40, total_tokens=140, cost_usd=0.0)):
        self._replies, self._usage = replies if replies is not None else [], usage
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest, *, timeout_s: float) -> ProviderResponse:
        self.requests.append(request)
        reply = self._replies(request) if callable(self._replies) else (self._replies.pop(0) if self._replies else "{}")
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, tuple):  # (text, usage) overrides the default usage, None = unreported
            reply, usage = reply
        else:
            usage = self._usage
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return ProviderResponse(text, usage if usage is not None else Usage(), "fake", request.model)


class ChatCompletionsProvider:
    """OpenAI-compatible /chat/completions (OpenRouter, DeepSeek). UNVERIFIED against a real provider.

    The key goes only into the Authorization header, redirects are refused, the request has a hard
    timeout, and every error text is redacted before it is raised.
    """

    fake = False

    def __init__(self, name: str, base_url: str, api_key: str, redactor: Redactor):
        if not api_key:
            raise ConfigError(f"no API key for provider {name}")
        self.name, self.base_url, self._key = name, base_url.rstrip("/"), api_key
        self.redactor = redactor.with_secrets(api_key)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def complete(self, request: ModelRequest, *, timeout_s: float) -> ProviderResponse:
        payload = {"model": request.model, "max_tokens": request.max_output_tokens,
                   "temperature": request.temperature,
                   "messages": [{"role": "system", "content": request.system},
                                {"role": "user", "content": request.user}]}
        if urlsplit(self.base_url).hostname == "openrouter.ai":
            payload["usage"] = {"include": True}
        body = json.dumps(payload).encode()
        http = urllib.request.Request(self.base_url + "/chat/completions", data=body, method="POST", headers={
            "Authorization": "Bearer " + self._key, "Content-Type": "application/json"})
        try:
            with self._opener.open(http, timeout=timeout_s) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            self._raise_http(exc)
        except (socket.timeout, TimeoutError) as exc:
            raise ModelTimeout(f"provider did not answer within {timeout_s:g}s") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise ModelTimeout(f"provider did not answer within {timeout_s:g}s") from exc
            raise ProviderUnavailable(self.redactor.redact(f"provider unreachable: {exc.reason}")[:300]) from exc
        if len(raw) > 8 * 1024 * 1024:
            raise ProviderRejected("provider answer too large")
        return self._parse(raw, request)

    def _raise_http(self, exc: urllib.error.HTTPError):
        detail = self.redactor.redact(exc.read(1000).decode(errors="replace"))[:300]
        if exc.code == 429:
            try:
                retry = float(exc.headers.get("Retry-After", "") or 30)
            except ValueError:
                retry = 30.0
            if not math.isfinite(retry):
                retry = 30.0
            retry = min(3600.0, max(1.0, retry))
            raise ProviderQuota(retry, f"provider quota (HTTP 429): {detail}") from exc
        if exc.code >= 500:
            raise ProviderUnavailable(f"provider error HTTP {exc.code}: {detail}") from exc
        raise ProviderRejected(f"provider rejected the request HTTP {exc.code}: {detail}") from exc

    def _parse(self, raw: bytes, request: ModelRequest) -> ProviderResponse:
        try:
            data = json.loads(raw)
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailable("provider returned a malformed answer") from exc
        if not isinstance(text, str):
            raise ProviderUnavailable("provider returned a non-text answer")
        raw_usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}

        def number(*path):
            node: Any = raw_usage
            for step in path:
                node = node.get(step) if isinstance(node, dict) else None
            return node if isinstance(node, (int, float)) and not isinstance(node, bool) and node >= 0 else None

        usage = Usage(prompt_tokens=number("prompt_tokens"), completion_tokens=number("completion_tokens"),
                      total_tokens=number("total_tokens"), cached_tokens=number("prompt_tokens_details", "cached_tokens"),
                      cost_usd=number("cost"))
        return ProviderResponse(text, usage, self.name, str(data.get("model") or request.model))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ModelResult:
    text: str
    usage: Usage
    provider: str
    model: str
    fake: bool


class ModelClient:
    def __init__(self, registry: ModelRegistry, providers: Mapping[str, Provider], redactor: Redactor):
        self.registry, self.providers, self.redactor = registry, dict(providers), redactor

    @classmethod
    def from_environment(cls, registry: ModelRegistry, env: Mapping[str, str] | None = None) -> "ModelClient":
        """Build real providers from the environment; a provider without a key is left out and fails
        clearly when a role uses it (absence of a key never blocks fake/foundation work)."""
        env = os.environ if env is None else env
        keys = [env.get(s["api_key_env"], "") for s in registry.providers.values()]
        redactor = Redactor(keys)
        providers: dict[str, Provider] = {}
        for name, settings in registry.providers.items():
            key = env.get(settings["api_key_env"], "")
            if key:
                providers[name] = ChatCompletionsProvider(name, settings["base_url"], key, redactor)
        return cls(registry, providers, redactor)

    def provider_for(self, role: str) -> Provider:
        config = self.registry.for_role(role)
        provider = self.providers.get(config.provider)
        if provider is None:
            raise ConfigError(f"provider {config.provider} for role {role} is unavailable (no API key configured)")
        return provider

    def complete(self, ctx: RunContext, role: str, system: str, user: str) -> ModelResult:
        """One reserved, limited, accounted model call for this run. Text is redacted in and out."""
        config, provider = self.registry.for_role(role), self.provider_for(role)
        system, user = self.redactor.redact(system), self.redactor.redact(user)

        def call(job_cap):
            cap = min(config.max_output_tokens, job_cap) if job_cap else config.max_output_tokens
            request = ModelRequest(system, user, cap, config.model, config.temperature)
            try:
                response = provider.complete(request, timeout_s=config.timeout_s)
            except ProviderQuota as quota:
                raise ctx.provider_quota(quota.retry_after_s, self.redactor.redact(quota.reason)) from quota
            except ModelError as exc:
                raise type(exc)(self.redactor.redact(str(exc))) from None
            return response, response.usage.as_counters()

        response = ctx.model_call(call)
        return ModelResult(self.redactor.redact(response.text), response.usage,
                           self.redactor.redact(response.provider or provider.name),
                           self.redactor.redact(response.model or config.model), bool(provider.fake))
