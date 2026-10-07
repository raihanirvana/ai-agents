"""Trusted, loopback-only provider/tool relay for the DEV-006 scoped harness."""
from __future__ import annotations

import hashlib
import hmac
import json
import random
import http.client
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .journal import AdmissionError
from app.provider_routing import routing_fields, retry_after_seconds


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Relay:
    ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, journal, scope, generation, model, key, tools, *, transport=None, foreign_markers=(), interval_s=0, request_projection=None,
                 provider_retry_delays=(), fallback_models=(), allow_provider_fallbacks=False):
        self.journal, self.scope, self.generation = journal, scope, generation
        self.model, self.key, self.tools = model, key, tools
        self.foreign_markers = tuple(foreign_markers)
        if (not isinstance(fallback_models, (tuple, list)) or
                any(not isinstance(m, str) or not m.strip() or m != m.strip() for m in fallback_models) or
                len(set([model, *fallback_models])) != 1 + len(fallback_models) or
                type(allow_provider_fallbacks) is not bool):
            raise ValueError('invalid supervisor model fallback configuration')
        self.fallback_models = tuple(fallback_models)
        self.allow_provider_fallbacks = allow_provider_fallbacks
        self.token = secrets.token_urlsafe(32)
        self.opener = transport or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.request_projection = request_projection
        self.raw_request_max_bytes = 8 * 1024 * 1024 if request_projection is not None else 1024 * 1024
        self.provider_request_max_bytes = 1024 * 1024
        self.provider_retry_delays = tuple(provider_retry_delays)
        if any(type(delay) not in (int, float) or not 0 <= delay <= 45 for delay in self.provider_retry_delays) or len(self.provider_retry_delays) > 3:
            raise ValueError('provider retries require at most three bounded delays')
        self.interval_s = interval_s
        self.pacing_lock = threading.Lock()
        previous = [r["started"] for r in journal.inspect(scope)["reservations"] if r["kind"] == "model" and r["name"] == model]
        self.next_request_at = max(previous, default=0) + interval_s
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass  # Never log credentials, source or provider bodies.

            def reply(self, code, body):
                if getattr(self, "streaming_started", False):
                    # A status line was already relayed; a second response would
                    # corrupt the stream. Closing makes the client see a transport error.
                    self.close_connection = True
                    return
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                self.wfile.write(raw)

            def do_GET(self):
                self.reply(404, {"error": "no discovery or alternate provider route"})

            def do_POST(self):
                rid = None
                try:
                    if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + owner.token):
                        self.reply(401, {"error": "unauthorized"})
                        return
                    if self.headers.get("Origin") or self.headers.get("Transfer-Encoding"):
                        self.reply(403, {"error": "browser or chunked requests forbidden"})
                        return
                    size = int(self.headers.get("Content-Length", "0"))
                    ingress_limit = owner.raw_request_max_bytes if self.path == "/v1/chat/completions" else 1024 * 1024
                    if not 0 < size <= ingress_limit:
                        owner.journal.event(owner.scope, owner.generation, 'relay.rejected',
                            {'phase': 'ingress', 'request_bytes': size, 'limit_bytes': ingress_limit})
                        self.reply(422, {"error": {"message": "Local relay ingress exceeds its bounded request limit; no provider call was made.",
                            "type": "relay_request_limit", "code": "relay_ingress_limit"}})
                        return
                    body = json.loads(self.rfile.read(size))
                    if self.path == "/tools":
                        name, args = body["name"], body.get("arguments", {})
                        if name not in owner.tools or not isinstance(args, dict):
                            self.reply(400, {"error": "unknown tool or invalid arguments"})
                            return
                        rid = owner.journal.reserve(owner.scope, owner.generation, "tool", name)
                        owner.journal.event(owner.scope, owner.generation, "tool.started", {"name": name, "reservation": rid})
                        try:
                            result = owner.tools[name](args)
                            raw = json.dumps(result).encode()
                        except AdmissionError:
                            raise
                        except Exception as exc:
                            # Missing arguments, undecodable files or workspace refusals
                            # are tool errors for the model, not supervisor admission
                            # denials (409 makes the worker interrupt the whole run).
                            owner.journal.finish(rid, {"status": "failed", "error": type(exc).__name__})
                            self.reply(400, {"error": {"message": owner.redact(str(exc)[:400]),
                                                       "type": "tool_error", "class": type(exc).__name__}})
                            return
                        if len(raw) > 65536:
                            result = {"error": "tool output exceeded 65536 bytes"}
                            raw = json.dumps(result).encode()
                        owner.journal.finish(rid, {"status": "complete", "result_sha256": hashlib.sha256(raw).hexdigest()})
                        owner.journal.event(owner.scope, owner.generation, "tool.completed", {"name": name, "reservation": rid})
                        self.reply(200, result)
                        return
                    if self.path != "/v1/chat/completions":
                        self.reply(404, {"error": "alternate provider route forbidden"})
                        return
                    if body.get("model") != owner.model:
                        self.reply(403, {"error": "model differs from supervisor configuration"})
                        return
                    # Only the supervisor selects model routes. Runtime-supplied
                    # backup IDs and routing preferences cannot authorize spend.
                    for field in ('models', 'fallbacks', 'route'):
                        body.pop(field, None)
                    limits = owner.journal.inspect(owner.scope)["limits"]
                    if limits.get('output_tokens') is not None:
                        body["max_tokens"] = limits["output_tokens"]
                    body.pop("max_completion_tokens", None)
                    if urlsplit(owner.ENDPOINT).hostname == "openrouter.ai":
                        body["usage"] = {"include": True}
                        if owner.fallback_models:
                            body.pop('model')
                        body.update(routing_fields(owner.model, owner.fallback_models, owner.allow_provider_fallbacks))
                    else:
                        if owner.fallback_models or owner.allow_provider_fallbacks:
                            raise ValueError('fallback routing requires OpenRouter')
                        # Routing and usage extensions belong to OpenRouter,
                        # including fields supplied by the runtime itself.
                        body.pop("usage", None)
                        body.pop("provider", None)
                    if body.get("stream"):
                        body["stream_options"] = {"include_usage": True}
                    rid = owner.journal.reserve(owner.scope, owner.generation, "model", owner.model)
                    payload_text = json.dumps(body)
                    leaked = [m for m in owner.foreign_markers if m in payload_text]
                    owner.journal.event(owner.scope, owner.generation, "context.audit", {
                        "request_digest": hashlib.sha256(payload_text.encode()).hexdigest(),
                        "own_canary_present": owner.scope + "-only" in payload_text,
                        "foreign_canaries_present": leaked})
                    if leaked:
                        raise ValueError("foreign context canary found; request blocked")
                    # Inspect the original context before projection can omit old
                    # observations; compaction must never hide a cross-run canary.
                    if owner.request_projection is not None:
                        body = owner.request_projection(body)
                    projected_payload = json.dumps(body).encode()
                    owner.journal.event(owner.scope, owner.generation, 'context.size',
                        {'ingress_bytes': size, 'projected_bytes': len(projected_payload),
                         'ingress_limit_bytes': ingress_limit, 'provider_limit_bytes': owner.provider_request_max_bytes})
                    if len(projected_payload) > owner.provider_request_max_bytes:
                        owner.journal.finish(rid, {'status': 'local_rejected'})
                        rid = None
                        owner.journal.event(owner.scope, owner.generation, 'relay.rejected',
                            {'phase': 'projection', 'request_bytes': len(projected_payload),
                             'limit_bytes': owner.provider_request_max_bytes})
                        self.reply(422, {"error": {"message": "Projected context exceeds the local relay limit; no provider call was made.",
                            "type": "relay_request_limit", "code": "relay_projection_limit"}})
                        return
                    owner.journal.event(owner.scope, owner.generation, "model.started", {"reservation": rid, "model": owner.model})
                    req = urllib.request.Request(owner.ENDPOINT, data=projected_payload, headers={
                        "Authorization": "Bearer " + owner.key, "Content-Type": "application/json",
                        "X-Title": "DEV-006 scoped compatibility experiment"})
                    # Quota pacing never resets journal counters and remains part
                    # of active duration. Admission is rechecked during the wait.
                    with owner.pacing_lock:
                        while time.time() < owner.next_request_at:
                            state = owner.journal.inspect(owner.scope)
                            cap = state['limits'].get('active_s')
                            if state["generation"] != owner.generation or state["status"] != "running" or (cap is not None and state["active_s"] >= cap):
                                raise AdmissionError("inactive, stale or duration exhausted during quota wait")
                            time.sleep(max(0, min(0.1, owner.next_request_at - time.time())))
                        owner.next_request_at = time.time() + owner.interval_s
                    upstream, rid = owner.open_model(req, rid)
                    with upstream:
                        usage, count, error, response_model = None, 0, False, None
                        self.send_response(upstream.status)
                        self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/json"))
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.close_connection = True
                        self.streaming_started = True
                        if body.get("stream"):
                            while True:
                                line = upstream.readline(65537)
                                if not line:
                                    break
                                count += len(line)
                                if len(line) > 65536 or count > 8 * 1024 * 1024:
                                    raise ValueError("provider output too large")
                                if line.startswith(b"data: ") and line.strip() != b"data: [DONE]":
                                    frame = json.loads(line[6:])
                                    usage = frame.get("usage") or usage
                                    error = error or bool(frame.get("error"))
                                    response_model = frame.get('model') or response_model
                                self.wfile.write(line)
                                self.wfile.flush()
                        else:
                            raw = upstream.read(8 * 1024 * 1024 + 1)
                            if len(raw) > 8 * 1024 * 1024:
                                raise ValueError("provider output too large")
                            frame = json.loads(raw)
                            usage, error = frame.get("usage"), bool(frame.get("error"))
                            response_model = frame.get('model')
                            self.wfile.write(raw)
                        owner.journal.finish(rid, {"http_status": upstream.status,
                            "status": "provider_error" if error else "complete", "usage": usage,
                            "model": response_model, "requested_model": owner.model,
                            "cost": usage.get("cost") if isinstance(usage, dict) else None})
                        owner.journal.event(owner.scope, owner.generation, "model.completed", {"reservation": rid, "usage": usage})
                except urllib.error.HTTPError as exc:
                    rid = getattr(exc, 'provider_reservation_id', rid)
                    detail = exc.read(2000).decode(errors="replace").replace(owner.key, "[redacted]")
                    if rid:
                        owner.journal.finish(rid, {"http_status": exc.code, "status": "provider_error", "usage": None, "cost": None,
                            "detail": detail, "retry_after_s": retry_after_seconds((exc.headers or {}).get('Retry-After'))})
                    self.reply(exc.code, {"error": {"message": detail, "type": "provider_error"}})
                except (AdmissionError, KeyError, ValueError, TypeError) as exc:
                    if rid:
                        owner.journal.finish(rid, {"status": "failed", "usage": None, "cost": None, "error": type(exc).__name__})
                    try:
                        owner.journal.event(owner.scope, owner.generation, "admission.denied", {"reason": str(exc)[:400]})
                    except AdmissionError:
                        pass
                    self.reply(409, {"error": {"message": str(exc)[:400], "type": "admission_error"}})
                except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError) as exc:
                    rid = getattr(exc, 'provider_reservation_id', rid)
                    if rid:
                        owner.journal.finish(rid, {"status": "transport_failure", "usage": None, "cost": None})
                    self.close_connection = True
                except Exception as exc:
                    if rid:
                        owner.journal.finish(rid, {"status": "failed", "usage": None, "cost": None, "error": type(exc).__name__})
                    self.reply(400, {"error": {"message": owner.redact(str(exc)[:400]), "type": "tool_error", "class": type(exc).__name__}})

        class Server(ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                pass  # Broken local probes are not provider receipts or runtime events.

        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def open_model(self, request, reservation):
        """Retry only before receiving headers; every attempt has its own billable reservation."""
        if not self.provider_retry_delays:
            return self.opener.open(request, timeout=60), reservation
        for attempt in range(len(self.provider_retry_delays) + 1):
            try:
                return self.opener.open(request, timeout=60), reservation
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                    ConnectionResetError, http.client.RemoteDisconnected) as exc:
                code = exc.code if isinstance(exc, urllib.error.HTTPError) else None
                reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                transient = code in (408, 502, 503, 504) or isinstance(reason,
                    (TimeoutError, ConnectionResetError, ConnectionRefusedError, http.client.RemoteDisconnected))
                if isinstance(reason, socket.gaierror) and reason.errno == socket.EAI_AGAIN:
                    transient = True
                if not transient or attempt == len(self.provider_retry_delays):
                    # The handler owns final failure accounting, including the
                    # HTTP body and Retry-After. Finishing here loses those details.
                    exc.provider_reservation_id = reservation
                    raise
                # No model/tool mutation is replayed. Unknown spend stays unknown.
                self.journal.finish(reservation, {'http_status': code, 'status': 'provider_error',
                    'usage': None, 'cost': None})
                if isinstance(exc, urllib.error.HTTPError):
                    exc.close()
                delay = self.provider_retry_delays[attempt] + random.uniform(0, 1)
                self.journal.event(self.scope, self.generation, 'provider.retry',
                    {'attempt': attempt + 1, 'http_status': code, 'delay_s': round(delay, 2)})
                deadline = time.monotonic() + delay
                while time.monotonic() < deadline:
                    state = self.journal.inspect(self.scope)  # product checks cancellation/lease
                    cap = state['limits'].get('active_s')
                    if (state['generation'] != self.generation or state['status'] != 'running' or
                            (cap is not None and state['active_s'] >= cap)):
                        raise AdmissionError('inactive/stale/duration exhausted during provider retry')
                    time.sleep(min(0.1, max(0, deadline - time.monotonic())))
                reservation = self.journal.reserve(self.scope, self.generation, 'model', self.model)
                self.journal.event(self.scope, self.generation, 'model.started',
                    {'reservation': reservation, 'model': self.model, 'retry': attempt + 1})
        raise RuntimeError('provider retry loop exhausted')

    def redact(self, text):
        return text.replace(self.key, "[redacted]").replace(self.token, "[redacted]")

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
