"""Trusted, loopback-only provider/tool relay for the DEV-006 scoped harness."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .journal import AdmissionError


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Relay:
    ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, journal, scope, generation, model, key, tools, *, transport=None, foreign_markers=(), interval_s=0):
        self.journal, self.scope, self.generation = journal, scope, generation
        self.model, self.key, self.tools = model, key, tools
        self.foreign_markers = tuple(foreign_markers)
        self.token = secrets.token_urlsafe(32)
        self.opener = transport or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
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
                    if not 0 < size <= 1024 * 1024:
                        self.reply(413, {"error": "request too large"})
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
                    limits = owner.journal.inspect(owner.scope)["limits"]
                    if limits.get('output_tokens') is not None:
                        body["max_tokens"] = limits["output_tokens"]
                    body.pop("max_completion_tokens", None)
                    body["usage"] = {"include": True}
                    body["provider"] = {"allow_fallbacks": False}
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
                    owner.journal.event(owner.scope, owner.generation, "model.started", {"reservation": rid, "model": owner.model})
                    req = urllib.request.Request(owner.ENDPOINT, data=json.dumps(body).encode(), headers={
                        "Authorization": "Bearer " + owner.key, "Content-Type": "application/json",
                        "X-Title": "DEV-006 scoped compatibility experiment"})
                    # Quota pacing never resets journal counters and remains part
                    # of active duration. Admission is rechecked during the wait.
                    with owner.pacing_lock:
                        while time.time() < owner.next_request_at:
                            state = owner.journal.inspect(owner.scope)
                            if state["generation"] != owner.generation or state["status"] != "running" or state["active_s"] >= state["limits"]["active_s"]:
                                raise AdmissionError("inactive, stale or duration exhausted during quota wait")
                            time.sleep(max(0, min(0.1, owner.next_request_at - time.time())))
                        owner.next_request_at = time.time() + owner.interval_s
                    with owner.opener.open(req, timeout=60) as upstream:
                        usage, count, error = None, 0, False
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
                                self.wfile.write(line)
                                self.wfile.flush()
                        else:
                            raw = upstream.read(8 * 1024 * 1024 + 1)
                            if len(raw) > 8 * 1024 * 1024:
                                raise ValueError("provider output too large")
                            frame = json.loads(raw)
                            usage, error = frame.get("usage"), bool(frame.get("error"))
                            self.wfile.write(raw)
                        owner.journal.finish(rid, {"http_status": upstream.status,
                            "status": "provider_error" if error else "complete", "usage": usage,
                            "cost": usage.get("cost") if isinstance(usage, dict) else None})
                        owner.journal.event(owner.scope, owner.generation, "model.completed", {"reservation": rid, "usage": usage})
                except urllib.error.HTTPError as exc:
                    detail = exc.read(2000).decode(errors="replace").replace(owner.key, "[redacted]")
                    if rid:
                        owner.journal.finish(rid, {"http_status": exc.code, "status": "provider_error", "usage": None, "cost": None, "detail": detail})
                    self.reply(exc.code, {"error": {"message": detail, "type": "provider_error"}})
                except (AdmissionError, KeyError, ValueError, TypeError) as exc:
                    if rid:
                        owner.journal.finish(rid, {"status": "failed", "usage": None, "cost": None, "error": type(exc).__name__})
                    try:
                        owner.journal.event(owner.scope, owner.generation, "admission.denied", {"reason": str(exc)[:400]})
                    except AdmissionError:
                        pass
                    self.reply(409, {"error": {"message": str(exc)[:400], "type": "admission_error"}})
                except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
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
