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
import time
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from app.workers.runtime import RunContext

from .redaction import Redactor
from .souls import ROLES
from app.provider_routing import routing_fields, retry_after_seconds


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
    stream: bool = False
    reasoning_effort: str | None = None
    fallback_models: tuple[str, ...] = ()
    allow_provider_fallbacks: bool = False


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
    max_output_tokens: int | None = 2048
    temperature: float = 0.2
    stream: bool = False
    reasoning_effort: str | None = None
    fallback_models: tuple[str, ...] = ()
    allow_provider_fallbacks: bool = False


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
            stream = merged.get('stream', False)
            effort = merged.get('reasoning_effort')
            fallbacks = merged.get('fallback_models', [])
            allow_fallbacks = merged.get('allow_provider_fallbacks', bool(fallbacks))
            if (not isinstance(fallbacks, list) or any(not isinstance(m, str) or not m.strip()
                    or m != m.strip() for m in fallbacks)):
                raise ConfigError(f'role {role} fallback_models must be a list of model IDs')
            if len(set([merged['model'], *fallbacks])) != 1 + len(fallbacks):
                raise ConfigError(f'role {role} fallback models must be unique and exclude the primary')
            if type(allow_fallbacks) is not bool:
                raise ConfigError(f'role {role} allow_provider_fallbacks must be boolean')
            if fallbacks or allow_fallbacks:
                settings = providers.get(merged['provider'])
                if not isinstance(settings, dict) or urlsplit(settings.get('base_url', '')).hostname != 'openrouter.ai':
                    raise ConfigError(f'role {role} fallback routing is supported only through OpenRouter')
            if effort not in (None, 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'):
                raise ConfigError(f'role {role} has an invalid reasoning_effort')
            if type(stream) is not bool:
                raise ConfigError(f'role {role} stream must be boolean')
            if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 600 or
                    (tokens is not None and (type(tokens) is not int or tokens <= 0))):
                raise ConfigError(f"role {role} needs a finite timeout (0-600 s) and positive max_output_tokens or null")
            configs[role] = ModelConfig(role, merged["provider"], merged["model"], float(timeout), tokens,
                                        float(merged.get("temperature", 0.2)), stream, effort,
                                        tuple(fallbacks), allow_fallbacks)
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
        self._output_limits: dict[str, tuple[float, int]] = {}

    def output_limit(self, model: str) -> int:
        """No application cap: resolve the provider's advertised completion maximum.

        Cached briefly per adapter; never substitute a small runtime/default cap.
        Other providers need an explicit supported metadata adapter before using null.
        """
        cached = self._output_limits.get(model)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        if urlsplit(self.base_url).hostname != 'openrouter.ai':
            raise ConfigError('max_output_tokens=null requires OpenRouter model metadata')
        try:
            with self._opener.open(self.base_url + '/models', timeout=15) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError('model metadata exceeds transport bound')
            rows = json.loads(raw)['data']
            row = next(item for item in rows if item.get('id') == model)
            limit = (row.get('top_provider') or {}).get('max_completion_tokens')
            if type(limit) is not int or limit <= 0:
                raise ValueError('provider completion maximum is unavailable')
        except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
            raise ProviderUnavailable('cannot resolve provider output maximum') from exc
        self._output_limits[model] = (time.monotonic(), limit)
        return limit

    def complete(self, request: ModelRequest, *, timeout_s: float, progress=None, check=None) -> ProviderResponse:
        payload = {"model": request.model, "max_tokens": request.max_output_tokens,
                   "temperature": request.temperature,
                   "messages": [{"role": "system", "content": request.system},
                                {"role": "user", "content": request.user}]}
        if urlsplit(self.base_url).hostname == "openrouter.ai":
            if request.fallback_models or request.allow_provider_fallbacks:
                if request.fallback_models:
                    payload.pop('model')
                payload.update(routing_fields(request.model, request.fallback_models, request.allow_provider_fallbacks))
            payload["usage"] = {"include": True}
            if request.reasoning_effort is not None:
                payload['reasoning'] = {'effort': request.reasoning_effort}
        elif request.fallback_models or request.allow_provider_fallbacks:
            raise ProviderRejected('fallback routing is currently supported only through OpenRouter')
        elif request.reasoning_effort is not None:
            raise ProviderRejected('reasoning_effort is currently supported only through OpenRouter')
        if request.stream:
            payload.update(stream=True, stream_options={'include_usage': True})
        body = json.dumps(payload).encode()
        http = urllib.request.Request(self.base_url + "/chat/completions", data=body, method="POST", headers={
            "Authorization": "Bearer " + self._key, "Content-Type": "application/json"})
        deadline = time.monotonic() + timeout_s
        try:
            with self._opener.open(http, timeout=timeout_s) as response:
                if request.stream and 'text/event-stream' in response.headers.get('Content-Type', ''):
                    # urllib's socket timeout is an idle timeout. A wall timer
                    # also interrupts a stream that keeps sending small chunks.
                    sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                    if sock is None:
                        raise ProviderRejected('stream transport cannot enforce its response deadline')
                    def abort():
                        try:
                            sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    timer = threading.Timer(max(0.01, deadline - time.monotonic()), abort)
                    timer.daemon = True
                    timer.start()
                    try:
                        return self._stream(response, request, deadline=deadline, progress=progress, check=check)
                    finally:
                        timer.cancel()
                raw = response.read(8 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            self._raise_http(exc)
        except (socket.timeout, TimeoutError) as exc:
            raise ModelTimeout(f"provider did not answer within {timeout_s:g}s") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise ModelTimeout(f"provider did not answer within {timeout_s:g}s") from exc
            raise ProviderUnavailable(self.redactor.redact(f"provider unreachable: {exc.reason}")[:300]) from exc
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise ModelTimeout('provider exceeded the response deadline') from exc
            raise ProviderUnavailable('provider connection interrupted') from exc
        if len(raw) > 8 * 1024 * 1024:
            raise ProviderRejected("provider answer too large")
        return self._parse(raw, request)

    def _raise_http(self, exc: urllib.error.HTTPError):
        detail = self.redactor.redact(exc.read(1000).decode(errors="replace"))[:300]
        if exc.code == 429:
            retry = retry_after_seconds((exc.headers or {}).get('Retry-After'))
            raise ProviderQuota(retry, f"provider quota (HTTP 429): {detail}") from exc
        if exc.code >= 500:
            error = ProviderUnavailable(f"provider error HTTP {exc.code}: {detail}")
            error.http_status = exc.code
            raise error from exc
        raise ProviderRejected(f"provider rejected the request HTTP {exc.code}: {detail}") from exc

    def _parse(self, raw: bytes, request: ModelRequest) -> ProviderResponse:
        try:
            data = json.loads(raw)
            choice = data["choices"][0]
            message = choice['message']
            if not isinstance(choice, dict) or not isinstance(message, dict):
                raise ValueError()
            text = message.get('content')
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailable("provider returned a malformed answer") from exc
        usage = self._usage(data)
        if isinstance(text, list) and all(isinstance(p, dict) and p.get('type') == 'text'
                                         and isinstance(p.get('text'), str) for p in text):
            text = ''.join(p['text'] for p in text)
        finish = choice.get('finish_reason')
        reason = message.get('reasoning_content') or message.get('reasoning')
        details = data.get('usage', {}).get('completion_tokens_details', {}) if isinstance(data.get('usage'), dict) else {}
        if not isinstance(details, dict):
            details = {}
        diagnostic = (f'finish_reason={finish if finish in ("stop", "length", "tool_calls", "content_filter", "error", None) else "other"}; '
                      f'content_type={type(text).__name__}; reasoning_present={bool(reason)}; '
                      f'reasoning_tokens={details.get("reasoning_tokens") if type(details.get("reasoning_tokens")) is int else "unknown"}; '
                      f'completion_tokens={usage.completion_tokens}')
        if finish in ('length', 'tool_calls', 'content_filter', 'error') or not isinstance(text, str) or not text.strip():
            error = ProviderRejected if finish in ('length', 'tool_calls', 'content_filter') else ProviderUnavailable
            exc = error('provider returned incomplete/non-text answer: ' + diagnostic)
            exc.usage_counters = usage.as_counters()
            raise exc
        return ProviderResponse(text, usage, self.name, str(data.get("model") or request.model))

    @staticmethod
    def _usage(data):
        raw_usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}

        def number(*path):
            node: Any = raw_usage
            for step in path:
                node = node.get(step) if isinstance(node, dict) else None
            return node if isinstance(node, (int, float)) and not isinstance(node, bool) and math.isfinite(node) and node >= 0 else None

        return Usage(prompt_tokens=number("prompt_tokens"), completion_tokens=number("completion_tokens"),
                      total_tokens=number("total_tokens"), cached_tokens=number("prompt_tokens_details", "cached_tokens"),
                      cost_usd=number("cost"))

    def _stream(self, response, request, *, deadline, progress, check):
        """Bounded SSE framing; reasoning is counted, never used as a verdict or logged."""
        last_progress = time.monotonic()
        text, fields, raw_usage = [], [], {}
        total = content_chars = reasoning_chars = chunks = 0
        finish = None
        done = False
        model = request.model
        while not done:
            if check:
                check()
            if time.monotonic() >= deadline:
                raise ModelTimeout('provider stream exceeded the response deadline')
            line = response.readline(1024 * 1024 + 1)
            total += len(line)
            if len(line) > 1024 * 1024 or total > 8 * 1024 * 1024:
                raise ProviderRejected('provider stream exceeded its byte bound')
            if not line:
                break
            try:
                line = line.decode('utf-8').rstrip('\r\n')
            except UnicodeDecodeError as exc:
                raise ProviderUnavailable('provider stream is not UTF-8') from exc
            if line.startswith('data:'):
                fields.append(line[5:].lstrip(' '))
                continue
            if line:
                continue  # SSE comments and other framing fields are not JSON.
            if not fields:
                continue
            body, fields = '\n'.join(fields), []
            if body == '[DONE]':
                done = True
                break
            try:
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise ValueError()
                if isinstance(data.get('usage'), dict):
                    raw_usage = data['usage']
                if data.get('error'):
                    exc = ProviderUnavailable('provider reported a mid-stream error')
                    exc.usage_counters = self._usage({'usage': raw_usage}).as_counters()
                    raise exc
                choices = data.get('choices') or []
                if not isinstance(choices, list):
                    raise ValueError()
                choice = next((c for c in choices if isinstance(c, dict) and c.get('index', 0) == 0), {})
                delta = choice.get('delta') or {}
                if not isinstance(delta, dict):
                    raise ValueError()
                content = delta.get('content')
                if content is not None and not isinstance(content, str):
                    raise ValueError()
                if content:
                    text.append(content)
                    content_chars += len(content)
                reason = delta.get('reasoning_content') or delta.get('reasoning')
                if isinstance(reason, str):
                    reasoning_chars += len(reason)
                if choice.get('finish_reason') is not None:
                    finish = choice['finish_reason']
                if isinstance(data.get('model'), str):
                    model = data['model']
                chunks += 1
            except (ValueError, TypeError, KeyError) as exc:
                raise ProviderUnavailable('provider stream contains an invalid chunk') from exc
            if progress and (chunks == 1 or time.monotonic() - last_progress >= 5):
                progress(f'model streaming: chunks={chunks}, answer_chars={content_chars}, reasoning_chars={reasoning_chars}')
                last_progress = time.monotonic()
        usage = self._usage({'usage': raw_usage})
        if time.monotonic() >= deadline:
            exc = ModelTimeout('provider stream exceeded the response deadline')
            exc.usage_counters = usage.as_counters()
            raise exc
        if progress:
            progress(f'model stream ended: chunks={chunks}, answer_chars={content_chars}, reasoning_chars={reasoning_chars}, '
                     f'finish_reason={finish if finish in ("stop", "length", "tool_calls", "content_filter", "error", None) else "other"}, done={done}')
        if not done or finish is None:
            exc = ProviderUnavailable('provider stream ended without complete terminal evidence')
            exc.usage_counters = usage.as_counters()
            raise exc
        # Reuse the same final-answer checks as non-streaming responses.
        return self._parse(json.dumps({'model': model, 'usage': raw_usage, 'choices': [{'finish_reason': finish,
            'message': {'content': ''.join(text), 'reasoning': bool(reasoning_chars)}}]}).encode(), request)


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

    def output_limit(self, role: str, job_cap: int | None = None) -> int:
        config = self.registry.for_role(role)
        cap = config.max_output_tokens
        if cap is None:
            provider = self.provider_for(role)
            if not isinstance(provider, ChatCompletionsProvider):
                raise ConfigError('provider does not support output maximum metadata')
            # A routed request has one shared output parameter. Use the provider's
            # supported maximum for every authorized model, never an invented cap.
            cap = min(provider.output_limit(model) for model in (config.model, *config.fallback_models))
        return min(cap, job_cap) if job_cap is not None else cap

    def complete(self, ctx: RunContext, role: str, system: str, user: str, *, source_safe: bool = False) -> ModelResult:
        """Account one call; source_safe preserves code in already-scrubbed contexts."""
        config, provider = self.registry.for_role(role), self.provider_for(role)
        redact = self.redactor.redact_source if source_safe else self.redactor.redact
        system, user = redact(system), redact(user)

        def call(job_cap):
            cap = self.output_limit(role, job_cap)
            request = ModelRequest(system, user, cap, config.model, config.temperature, config.stream,
                                   config.reasoning_effort, config.fallback_models, config.allow_provider_fallbacks)
            try:
                if isinstance(provider, ChatCompletionsProvider):
                    response = provider.complete(request, timeout_s=config.timeout_s, progress=ctx.log, check=ctx._check)
                else:
                    response = provider.complete(request, timeout_s=config.timeout_s)
            except ProviderQuota as quota:
                raise ctx.provider_quota(quota.retry_after_s, self.redactor.redact(quota.reason)) from quota
            except ModelError as exc:
                error = type(exc)(self.redactor.redact(str(exc)))
                error.usage_counters = getattr(exc, 'usage_counters', {})
                error.http_status = getattr(exc, 'http_status', None)
                raise error from None
            return response, response.usage.as_counters()

        for attempt in range(4):
            try:
                response = ctx.model_call(call)
                break
            except ProviderUnavailable as exc:
                if provider.fake or getattr(exc, 'http_status', None) not in (502, 503, 504) or attempt == 3:
                    raise
                delay = (5, 15, 30)[attempt]
                ctx.log(f'provider.retry attempt={attempt + 1} http_status={exc.http_status} delay_s={delay}')
                deadline = time.monotonic() + delay
                while time.monotonic() < deadline:
                    ctx._check()
                    ctx.cancelled.wait(min(0.1, max(0, deadline - time.monotonic())))
        ctx.log(self.redactor.redact(f'model.response requested={config.model} actual={response.model or config.model}'))
        return ModelResult(redact(response.text), response.usage,
                           self.redactor.redact(response.provider or provider.name),
                           self.redactor.redact(response.model or config.model), bool(provider.fake))
