from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.persistence.columns import utcnow
from app.persistence.models import LocalSession, RuntimeCredential
from app.workers.queue import Lease, StaleLease


class ApiError(Exception):
    def __init__(self, status, code, message, details=None):
        self.status, self.code, self.message, self.details = status, code, message, details or {}


def error_response(error):
    return JSONResponse({"error": {"code": error.code, "message": error.message,
                                   "details": error.details}}, status_code=error.status)


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Settings:
    port: int = 8000
    origins: tuple[str, ...] = ("http://127.0.0.1:5173", "http://127.0.0.1:5174")
    session_s: int = 28800
    replay_events: int = 10000
    poll_s: float = .25
    cookie: str = "ai_team_session"
    preview_port: int = 5180

    def __post_init__(self):
        if not 1 <= self.port <= 65535 or not 1 <= self.session_s <= 86400:
            raise ValueError("local API port and session lifetime must be bounded")
        if not 1024 <= self.preview_port <= 65535 or self.preview_port == self.port:
            raise ValueError("preview port must be an unprivileged port different from the API port")
        if self.replay_events < 1 or not .01 <= self.poll_s <= 5:
            raise ValueError("event window and poll interval must be bounded")
        for origin in self.origins:
            p = urlsplit(origin)
            if (p.scheme != "http" or p.hostname != "127.0.0.1" or p.username or p.password
                    or p.path or p.query or p.fragment or p.port is None or origin != f"http://127.0.0.1:{p.port}"):
                raise ValueError("control origins must be exact http://127.0.0.1:<port> origins")


@dataclass(frozen=True)
class Principal:
    kind: str
    token_hash: str
    user_id: str | None = None
    lease: Lease | None = None


class Auth:
    def __init__(self, db, queue, settings, login_code):
        if not isinstance(login_code, str) or not 16 <= len(login_code) <= 256 or not login_code.isascii():
            raise ValueError("local login code must be 16..256 ASCII characters")
        self.db, self.queue, self.settings, self.login_code = db, queue, settings, login_code

    def csrf(self, raw):
        return hmac.new(self.login_code.encode(), raw.encode(), hashlib.sha256).hexdigest()

    def login(self, code):
        if not code.isascii() or not hmac.compare_digest(code, self.login_code):
            raise ApiError(401, "invalid_login", "Invalid local login code")
        raw = secrets.token_urlsafe(32)
        with self.db.write() as s:
            s.add(LocalSession(token_hash=token_hash(raw), user_id="user:local",
                               expires_at=utcnow() + timedelta(seconds=self.settings.session_s)))
        return raw, self.csrf(raw)

    def check(self, s, principal):
        if principal.kind == "user":
            row = s.get(LocalSession, principal.token_hash)
            if row is None or row.revoked_at or row.expires_at <= utcnow() or row.user_id != principal.user_id:
                raise ApiError(401, "session_expired", "Session is expired or revoked")
        else:
            row = s.get(RuntimeCredential, principal.token_hash)
            if row is None or row.expires_at <= utcnow():
                raise ApiError(401, "runtime_expired", "Runtime credential is expired")
            if Lease(row.job_id, row.owner, row.generation) != principal.lease:
                raise ApiError(401, "runtime_invalid", "Runtime binding differs")
            self.queue.identity(s, principal.lease)

    def authenticate(self, request: Request, *, runtime=False):
        authorization = request.headers.get("authorization")
        raw_cookie = request.cookies.get(self.settings.cookie)
        if runtime:
            if raw_cookie or not authorization or not authorization.startswith("Bearer "):
                raise ApiError(401, "runtime_required", "A distinct runtime bearer credential is required")
            with self.db.read() as s:
                row = s.get(RuntimeCredential, token_hash(authorization[7:]))
                if row is None:
                    raise ApiError(401, "runtime_invalid", "Invalid runtime credential")
                principal = Principal("runtime", row.token_hash, lease=Lease(row.job_id, row.owner, row.generation))
                self.check(s, principal)
        else:
            if authorization or not raw_cookie:
                raise ApiError(401, "session_required", "A local browser session is required")
            principal = Principal("user", token_hash(raw_cookie), "user:local")
            with self.db.read() as s:
                self.check(s, principal)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                csrf = request.headers.get("x-csrf-token", "")
                if not csrf.isascii() or not hmac.compare_digest(csrf, self.csrf(raw_cookie)):
                    raise ApiError(403, "csrf_invalid", "CSRF token is missing or invalid")
        return principal

    def issue_runtime(self, lease, *, ttl_s=300):
        """Trusted supervisor API only; never exposed as a browser command."""
        if not 1 <= ttl_s <= 3600:
            raise ValueError("runtime credential lifetime must be bounded")
        raw = secrets.token_urlsafe(32)
        with self.db.write() as s:
            self.queue.identity(s, lease)
            s.add(RuntimeCredential(token_hash=token_hash(raw), job_id=lease.job_id,
                                    owner=lease.owner, generation=lease.generation,
                                    expires_at=utcnow() + timedelta(seconds=ttl_s)))
        return raw


def local_login_code(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        code = path.read_text(encoding="utf-8").strip()
        if len(code) < 32 or not code.isascii():
            raise ValueError("invalid local login-code file; refusing to start")
        return code
    code = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(code + "\n")
    return code


class LocalPolicy:
    """Outer ASGI guard: protects preflight, docs, HEAD, errors and all routes alike."""
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        req = Request(scope)

        async def protected_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message["headers"], (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer")]
            await send(message)
        try:
            if len(req.headers.getlist("host")) != 1 or req.headers.get("host") != f"127.0.0.1:{self.settings.port}":
                raise ApiError(403, "host_rejected", "Use the configured 127.0.0.1 control host")
            if any(len(req.headers.getlist(key)) > 1 for key in ("origin", "authorization", "x-csrf-token", "idempotency-key", "last-event-id")):
                raise ApiError(400, "ambiguous_header", "Repeated security headers are forbidden")
            origin = req.headers.get("origin")
            if origin is not None and origin not in self.settings.origins:
                raise ApiError(403, "origin_rejected", "Origin is outside the control allowlist")
            if req.method not in ("GET", "HEAD", "OPTIONS") and not req.url.path.startswith("/runtime/") and origin is None:
                raise ApiError(403, "origin_required", "An exact control Origin is required")
            if req.headers.get("sec-fetch-site") == "cross-site":
                raise ApiError(403, "cross_site", "Cross-site control requests are forbidden")
            if req.method not in ("GET", "HEAD", "OPTIONS"):
                body = bytearray()
                while True:
                    part = await receive()
                    if part["type"] == "http.disconnect":
                        return
                    body.extend(part.get("body", b""))
                    if len(body) > 1024 * 1024:
                        raise ApiError(413, "body_too_large", "Request body exceeds 1 MiB")
                    if not part.get("more_body"):
                        break
                original = receive
                pending = True
                async def buffered():
                    nonlocal pending
                    if pending:
                        pending = False
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await original()
                receive = buffered
        except ApiError as exc:
            return await error_response(exc)(scope, receive, protected_send)
        return await self.app(scope, receive, protected_send)
