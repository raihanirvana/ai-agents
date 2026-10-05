from __future__ import annotations
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from fastapi import FastAPI, Request, Response, Query, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.agents import Redactor, Threads
from app.agents.tools import NotWired
from app.domain import Actor, Attempt, ApprovalItem, Workflow, Conflict, Forbidden, Invalid
from app.persistence import (Database, ArtifactStore, migrate, NotFound, RevisionConflict,
    AlreadyAnswered, IdempotencyConflict, ArtifactUnavailable, EventSpec, append_event, append_message,
    latest_cursor)
from app.persistence.columns import new_id, utcnow
from app.persistence.models import Project, Ticket, Message, Job, Candidate, Artifact, Release, LocalSession, Preview
from app.preview import requests as previews
from app.workers import JobQueue, ProviderLimiter
from app.workers.queue import StaleLease, BudgetExhausted, QueueError
from app.workers.runtime import RunContext, WaitingForInput
from . import schemas as b, queries as q, events
from .security import Auth, Settings, LocalPolicy, ApiError, error_response, local_login_code
from .service import execute, user_actor
from .contract import describe

DEFAULT_LIMITS = {"model_calls": 8, "tool_calls": 24, "active_s": 120,
                  "output_tokens": 2048, "total_tokens": 32000}


def create_app(*, db=None, store=None, settings=None, login_code=None, redactor=None, runtime="structured"):
    settings = settings or Settings()
    if runtime not in ("structured", "structured:fake"):
        raise ValueError("PO API runtime must be structured or explicitly structured:fake")
    redactor = redactor or Redactor([v for k, v in os.environ.items() if any(
        marker in k.upper() for marker in ("API_KEY", "SECRET", "TOKEN", "PASSWORD"))])
    def initialise(database, artifacts, code):
        nonlocal redactor
        redactor = redactor.with_secrets(code)
        wf = Workflow(database, artifacts)
        queue = JobQueue(database, startable=wf.startable)
        return SimpleNamespace(db=database, store=artifacts, queue=queue, redactor=redactor,
            settings=settings, auth=Auth(database, queue, settings, code),
            threads=Threads(database, queue, redactor=redactor), limiter=ProviderLimiter(), runtime=runtime)
    @asynccontextmanager
    async def lifespan(app):
        if db is None:
            from app.config import DATABASE_PATH, ARTIFACT_DIR, DATA_DIR
            migrate.upgrade(DATABASE_PATH)
            database = Database(DATABASE_PATH)
            app.state.api = initialise(database, ArtifactStore(ARTIFACT_DIR),
                                       local_login_code(DATA_DIR / "auth" / "login-code"))
            try: yield
            finally: database.dispose()
        else: yield
    def require_identity(request: Request):
        if request.url.path == "/health" or request.url.path == "/auth/login" and request.method == "POST":
            return
        request.app.state.api.auth.authenticate(request, runtime=request.url.path.startswith("/runtime/"))
    app = FastAPI(title="AI Software Development Team API", version="0.2.0", lifespan=lifespan,
                  dependencies=[Depends(require_identity)],
                  docs_url=None, redoc_url=None, openapi_url=None)
    if db is not None:
        if store is None or login_code is None: raise ValueError("injected API requires store and login code")
        app.state.api = initialise(db, store, login_code)
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.origins), allow_credentials=True,
        allow_methods=["GET", "HEAD", "POST"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "Last-Event-ID", "Authorization"])
    app.add_middleware(LocalPolicy, settings=settings)
    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return error_response(ApiError(500, "internal_error", "Command failed; no success receipt was committed"))
    async def domain_error(request, exc):
        if isinstance(exc, ApiError): return error_response(exc)
        code, status, details = "invalid_command", 422, {}
        if isinstance(exc, NotFound): code, status = "not_found", 404
        elif isinstance(exc, Forbidden): code, status = "forbidden", 403
        elif isinstance(exc, ArtifactUnavailable): code, status = "artifact_unavailable", 409
        elif isinstance(exc, RevisionConflict):
            code, status, details = "revision_conflict", 409, {"expected": exc.expected, "actual": exc.actual}
        elif isinstance(exc, StaleLease): code, status = "stale_lease", 409
        elif isinstance(exc, BudgetExhausted): code, status = "budget_exhausted", 409
        elif isinstance(exc, IdempotencyConflict): code, status = "idempotency_conflict", 409
        elif isinstance(exc, (Conflict, AlreadyAnswered)): code, status = "conflict", 409
        elif isinstance(exc, NotWired): code, status = "not_wired", 501
        elif isinstance(exc, IntegrityError): code, status = "constraint_conflict", 409
        safe = "Mutation violates a database constraint" if isinstance(exc, IntegrityError) else redactor.redact(str(exc))[:500]
        return error_response(ApiError(status, code, safe, details))
    for cls in (ApiError, NotFound, Forbidden, Invalid, Conflict, RevisionConflict, StaleLease,
                BudgetExhausted, QueueError, IdempotencyConflict, AlreadyAnswered, ArtifactUnavailable, NotWired, IntegrityError):
        app.add_exception_handler(cls, domain_error)
    @app.exception_handler(RequestValidationError)
    async def validation(request, exc):
        return error_response(ApiError(422, "invalid_request", "Request does not match command contract",
            redactor.redact_value({"fields": [{"location": [str(v) for v in e["loc"]], "type": e["type"]} for e in exc.errors()]})))
    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(ApiError(exc.status_code, "http_error", "Route or method unavailable"))
    def auth(request, runtime=False): return request.app.state.api.auth.authenticate(request, runtime=runtime)
    def clean(request, value): return jsonable_encoder(request.app.state.api.redactor.redact_value(value))
    def command(request, body, action, runtime=False):
        principal = auth(request, runtime)
        data = body.model_dump() if body else {}
        result = clean(request, execute(request, principal, data, lambda s, svc, key: action(s, svc, key, principal)))
        status = result.pop("_status", 200)
        return JSONResponse(result, status_code=status)
    def ticket_actor(s, principal, tid): return user_actor(principal, q.row(s, Ticket, tid).project_id)
    def revision(value, expected):
        if value.revision != expected: raise RevisionConflict(value.__tablename__, value.id, expected, value.revision)
    @app.get("/health")
    def health(): return {"status": "ok"}
    @app.post("/auth/login")
    def login(request: Request, body: b.Login, response: Response):
        raw, csrf = request.app.state.api.auth.login(body.code)
        response.set_cookie(settings.cookie, raw, max_age=settings.session_s, httponly=True,
                            samesite="strict", path="/", secure=False)
        return {"user_id": "user:local", "csrf_token": csrf}
    @app.get("/auth/session")
    def session(request: Request):
        principal = auth(request)
        return {"user_id": principal.user_id, "csrf_token": request.app.state.api.auth.csrf(request.cookies[settings.cookie])}
    @app.post("/auth/logout")
    def logout(request: Request, response: Response):
        principal = auth(request)
        with request.app.state.api.db.write() as s:
            request.app.state.api.auth.check(s, principal)
            s.get(LocalSession, principal.token_hash).revoked_at = utcnow()
        response.delete_cookie(settings.cookie, path="/", httponly=True, samesite="strict")
        return {"logged_out": True}
    @app.get("/openapi.json")
    def openapi(request: Request):
        auth(request)
        return app.openapi()
    @app.get("/projects")
    def projects(request: Request):
        auth(request)
        with request.app.state.api.db.read() as s:
            return clean(request, {"projects": [q.project(p) for p in s.scalars(select(Project).order_by(Project.created_at))]})
    @app.post("/projects")
    def create_project(request: Request, body: b.ProjectCreate):
        def action(s, svc, key, principal):
            p = svc.workflow.create_project(user_actor(principal, new_id()), **request.app.state.api.redactor.redact_value(body.model_dump()))
            return {"project": q.project(p)}
        return command(request, body, action)
    @app.get("/projects/{project_id}")
    def project(request: Request, project_id: str):
        auth(request)
        with request.app.state.api.db.read() as s: return clean(request, {"project": q.project(q.row(s, Project, project_id))})
    @app.post("/projects/{project_id}/brief")
    def brief(request: Request, project_id: str, body: b.Brief):
        return command(request, body, lambda s, svc, key, p: {"project": q.project(svc.workflow.update_brief(
            user_actor(p, project_id), body.expected_revision, request.app.state.api.redactor.redact(body.brief)))})
    @app.post("/projects/{project_id}/onboarding")
    def onboarding(request: Request, project_id: str, body: b.Onboarding):
        def action(s, svc, key, principal):
            from app.onboarding.requests import request as request_onboarding
            project, job = request_onboarding(s, svc, request.app.state.api.store,
                user_actor(principal, project_id), body.expected_revision, body.manifest, key,
                patch=body.patch, source_sha=body.source_sha)
            return {"project": q.project(project), "job_id": job.id}
        return command(request, body, action)
    @app.get("/projects/{project_id}/tickets")
    def board(request: Request, project_id: str):
        auth(request)
        with request.app.state.api.db.read() as s: return clean(request, q.board(s, project_id))
    @app.post("/projects/{project_id}/tickets")
    def new_ticket(request: Request, project_id: str, body: b.Scope):
        return command(request, body, lambda s, svc, key, p: {"ticket": q.ticket(svc.workflow.create_ticket(
            user_actor(p, project_id), request.app.state.api.redactor.redact_value(body.model_dump())))})
    @app.get("/tickets/{ticket_id}")
    def detail(request: Request, ticket_id: str):
        auth(request)
        with request.app.state.api.db.read() as s: return clean(request, q.detail(s, ticket_id, request.app.state.api.threads))
    @app.post("/tickets/{ticket_id}/scope-versions")
    def edit_scope(request: Request, ticket_id: str, body: b.ScopeEdit):
        return command(request, body, lambda s, svc, key, p: {"ticket": q.ticket(svc.workflow.edit_scope(
            ticket_actor(s, p, ticket_id), ticket_id, body.expected_revision,
            request.app.state.api.redactor.redact_value(body.document.model_dump())))})
    @app.post("/projects/{project_id}/scope-approvals")
    def scope_approval(request: Request, project_id: str, body: b.ApprovalBatch):
        def action(s, svc, key, p):
            batch = svc.workflow.approve_scope(user_actor(p, project_id), [ApprovalItem(i.ticket_id, i.scope_version, i.expected_revision) for i in body.items])
            return {"batch_id": batch, "tickets": [q.ticket(q.row(s, Ticket, i.ticket_id, project_id)) for i in body.items]}
        return command(request, body, action)
    @app.post("/tickets/{ticket_id}/proposals/{proposal_id}/decisions")
    def proposal_decision(request: Request, ticket_id: str, proposal_id: str, body: b.ProposalDecision):
        return command(request, body, lambda s, svc, key, p: {"ticket": q.ticket(svc.workflow.decide_proposal(
            ticket_actor(s, p, ticket_id), ticket_id, body.expected_revision, proposal_id, body.accept))})
    @app.post("/projects/{project_id}/decisions/{proposal_id}")
    def decision(request: Request, project_id: str, proposal_id: str, body: b.Decision):
        return command(request, body, lambda s, svc, key, p: {"message_id": svc.threads.decide(user_actor(p, project_id), proposal_id, body.accept)})
    @app.post("/tickets/{ticket_id}/priority")
    def priority(request: Request, ticket_id: str, body: b.Priority):
        return command(request, body, lambda s, svc, key, p: {"ticket": q.ticket(svc.workflow.set_priority(
            ticket_actor(s, p, ticket_id), ticket_id, body.expected_revision, body.priority))})
    @app.post("/tickets/{ticket_id}/cancel")
    def cancel(request: Request, ticket_id: str, body: b.Revision):
        return command(request, body, lambda s, svc, key, p: {"ticket": q.ticket(svc.workflow.cancel(ticket_actor(s, p, ticket_id), ticket_id, body.expected_revision))})
    @app.post("/tickets/{ticket_id}/request-changes")
    def changes(request: Request, ticket_id: str, body: b.Changes):
        return command(request, body, lambda s, svc, key, p: {"ticket": q.ticket(svc.workflow.request_changes(
            ticket_actor(s, p, ticket_id), ticket_id, body.expected_revision, body.candidate_id, request.app.state.api.redactor.redact(body.reason)))})
    @app.post("/tickets/{ticket_id}/repair-authorizations")
    def repair(request: Request, ticket_id: str, body: b.Repair):
        return command(request, body, lambda s, svc, key, p: {"ticket": q.ticket(svc.workflow.authorize_repair(
            ticket_actor(s, p, ticket_id), ticket_id, body.expected_revision, body.additional_cycles))})
    @app.post("/tickets/{ticket_id}/uat-decisions")
    def uat(request: Request, ticket_id: str, body: b.Uat):
        return command(request, body, lambda s, svc, key, p: {"integration": svc.workflow.accept_uat(
            ticket_actor(s, p, ticket_id), ticket_id, body.expected_revision, body.candidate_id, body.scope_version,
            body.target_artifact_id, body.target_digest, body.verification_id, body.evidence_ids, body.manual_uac_ids)})
    @app.post("/projects/{project_id}/baseline-waivers")
    def waiver(request: Request, project_id: str, body: b.Waiver):
        return command(request, body, lambda s, svc, key, p: {"approval_id": svc.workflow.waive_baseline(
            user_actor(p, project_id), body.ticket_id, body.expected_revision, body.fingerprint_artifact_id, request.app.state.api.redactor.redact(body.reason)).id})
    @app.post("/releases/{release_id}/decisions")
    def release(request: Request, release_id: str, body: b.ReleaseDecision):
        def action(s, svc, key, p):
            r = q.row(s, Release, release_id)
            r = svc.workflow.approve_release(user_actor(p, r.project_id), release_id, body.expected_revision, body.target_artifact_id, body.target_digest, body.evidence_ids)
            return {"release_id": r.id, "status": r.status, "revision": r.revision}
        return command(request, body, action)
    @app.post("/projects/{project_id}/messages")
    def post_message(request: Request, project_id: str, body: b.MessageCreate):
        def action(s, svc, key, p):
            project = q.row(s, Project, project_id)
            revision(project, body.expected_revision)
            if body.task == "revise" and not body.ticket_id or body.task == "breakdown" and body.ticket_id:
                raise Invalid("revise requires a ticket; breakdown is project scoped")
            if body.ticket_id: q.row(s, Ticket, body.ticket_id, project_id)
            text = request.app.state.api.redactor.redact(body.body)
            m, _ = append_message(s, project_id=project_id, thread_id=f"chat:{project_id}", ticket_id=body.ticket_id,
                                   sender=p.user_id, recipient="role:po", body=text)
            job_id = None
            if body.task != "note":
                j = svc.queue.enqueue(project_id=project_id, role="po", stage="chat", lane="interactive", runtime=runtime,
                    limits=DEFAULT_LIMITS, ticket_id=body.ticket_id, idempotency_key=f"api-chat:{m.id}",
                    payload={"task": body.task, "request": text, "message_id": m.id})
                job_id = j.id
            append_event(s, project_id, EventSpec("message.created", p.user_id, {"message_id": m.id, "job_id": job_id}, entity_type="messages", entity_id=m.id))
            return {"message": q.message(s, m, svc.threads), "job_id": job_id}
        return command(request, body, action)
    @app.get("/projects/{project_id}/messages")
    def messages(request: Request, project_id: str, thread_id: str | None = None, limit: int = Query(100, ge=1, le=500)):
        auth(request)
        with request.app.state.api.db.read() as s:
            q.row(s, Project, project_id)
            # Runtime log lines are messages too, but they belong to /runs/{id}/logs, not to the conversation.
            query = select(Message).where(Message.project_id == project_id, q.not_runtime_log())
            if thread_id: query = query.where(Message.thread_id == thread_id)
            rows = list(s.scalars(query.order_by(Message.created_at.desc(), Message.thread_id.desc(), Message.seq.desc()).limit(limit)))
            return clean(request, {"messages": [q.message(s, m, request.app.state.api.threads) for m in reversed(rows)], "cursor": latest_cursor(s, project_id=project_id)})
    @app.get("/runs/{run_id}")
    def run(request: Request, run_id: str):
        auth(request)
        with request.app.state.api.db.read() as s:
            j = q.row(s, Job, run_id)
            return clean(request, {"run": q.run(j, s), "input": q.message(s, q.row(s, Message, j.waiting_request_id), request.app.state.api.threads) if j.waiting_request_id else None})
    @app.get("/runs/{run_id}/logs")
    def logs(request: Request, run_id: str, generation: int = Query(ge=1), after_seq: int = Query(0, ge=0)):
        auth(request)
        with request.app.state.api.db.read() as s:
            j = q.row(s, Job, run_id)
            if generation > j.lease_generation: raise Invalid("unknown run generation")
            rows = s.scalars(select(Message).where(Message.project_id == j.project_id, Message.thread_id == f"job:{run_id}:g{generation}", Message.seq > after_seq).order_by(Message.seq).limit(500))
            return clean(request, {"messages": [q.message(s, m, request.app.state.api.threads) for m in rows]})
    @app.post("/runs/{run_id}/input")
    def input_answer(request: Request, run_id: str, body: b.InputAnswer):
        def action(s, svc, key, p):
            j = q.row(s, Job, run_id)
            revision(j, body.expected_revision)
            m = q.row(s, Message, body.request_id, j.project_id)
            view = svc.threads.input_request(s, m.id)
            if (j.waiting_request_id != m.id or view.status != "open" or view.scope_version != body.scope_version
                    or view.generation != body.generation or j.scope_version != body.scope_version or j.lease_generation != body.generation):
                raise Conflict("request/run scope or generation changed")
            answer_id, resumed = svc.threads.answer_request(user_actor(p, j.project_id), m.id, body=body.answer, answer_key=f"api-answer:{j.id}:{key}")
            append_event(s, j.project_id, EventSpec("message.created", p.user_id, {"message_id": answer_id}, entity_type="messages", entity_id=answer_id))
            return {"answer_id": answer_id, "resumed": resumed}
        return command(request, body, action)
    @app.post("/projects/{project_id}/inputs/{request_id}")
    def nonblocking_answer(request: Request, project_id: str, request_id: str, body: b.NonblockingAnswer):
        def action(s, svc, key, p):
            m = q.row(s, Message, request_id, project_id)
            view = svc.threads.input_request(s, m.id)
            waiting = s.scalar(select(Job).where(Job.waiting_request_id == m.id))
            if (waiting is not None or view.status != "open" or m.recipient != "user"
                    or m.meta.get("intent") != "user_escalation" or m.meta.get("source_request_id") is not None
                    or view.scope_version != body.scope_version or view.generation != body.generation):
                raise Conflict("only the current nonblocking user question may be answered here")
            answer_id, resumed = svc.threads.answer_request(user_actor(p, project_id), m.id,
                body=body.answer, answer_key=f"api-answer:{m.id}:{key}")
            append_event(s, project_id, EventSpec("message.created", p.user_id,
                {"message_id": answer_id}, entity_type="messages", entity_id=answer_id))
            return {"answer_id": answer_id, "resumed": resumed}
        return command(request, body, action)
    @app.post("/runs/{run_id}/stop")
    def stop(request: Request, run_id: str, body: b.Empty):
        def action(s, svc, key, p):
            j = q.row(s, Job, run_id)
            status = j.status
            # Revoke first; a run that already ended cannot be "stopped", so say so instead of reporting success.
            if not svc.queue.cancel(j.id, reason="user stop", actor=p.user_id):
                raise Conflict(f"run is {status}; only an active run can be stopped")
            return {"run": q.run(j, s), "cleanup": "supervisor_pending"}
        return command(request, body, action)
    @app.post("/runs/{run_id}/budget-authorizations")
    def budget(request: Request, run_id: str, body: b.Budget):
        def action(s, svc, key, p):
            j = q.row(s, Job, run_id)
            revision(j, body.expected_revision)
            retry = svc.queue.extend_budget(j.id, user=p.user_id, additions=body.additions, authorization_id=key)
            return {"run": q.run(q.row(s, Job, retry), s)}
        return command(request, body, action)
    @app.get("/tickets/{ticket_id}/candidates/{candidate_id}")
    def candidate(request: Request, ticket_id: str, candidate_id: str):
        auth(request)
        with request.app.state.api.db.read() as s:
            t = q.row(s, Ticket, ticket_id)
            c = q.row(s, Candidate, candidate_id, t.project_id)
            if c.ticket_id != t.id: raise NotFound("candidate", candidate_id)
            return clean(request, {"candidate": q.candidate(s, c)})
    @app.post("/tickets/{ticket_id}/candidates/{candidate_id}/previews")
    def start_preview(request: Request, ticket_id: str, candidate_id: str, body: b.Empty):
        """Open (or reopen) the single local preview of this verified target. The supervisor starts it; poll
        the candidate/preview or follow `preview.*` events. Approval never depends on this process staying up."""
        def action(s, svc, key, p):
            ticket_actor(s, p, ticket_id)
            preview = previews.request_preview(s, request.app.state.api.store, ticket_id=ticket_id,
                candidate_id=candidate_id, user_id=p.user_id, port=request.app.state.api.settings.preview_port)
            return {"preview": previews.public(preview)}
        return command(request, body, action)
    @app.post("/previews/{preview_id}/stop")
    def stop_preview(request: Request, preview_id: str, body: b.Empty):
        def action(s, svc, key, p):
            row = q.row(s, Preview, preview_id)
            user_actor(p, row.project_id)
            return {"preview": previews.public(previews.request_stop(s, preview_id, p.user_id))}
        return command(request, body, action)
    @app.get("/previews/{preview_id}")
    def preview(request: Request, preview_id: str):
        auth(request)
        with request.app.state.api.db.read() as s:
            return clean(request, {"preview": previews.public(q.row(s, Preview, preview_id))})
    @app.get("/artifacts/{artifact_id}")
    def artifact(request: Request, artifact_id: str):
        auth(request)
        api = request.app.state.api
        with api.db.write() as s:
            a = q.row(s, Artifact, artifact_id)
            api.store.verify(s, a.id)
            return clean(request, {"artifact": q.artifact(a)})
    @app.get("/artifacts/{artifact_id}/content")
    def content(request: Request, artifact_id: str):
        auth(request)
        api = request.app.state.api
        with api.db.write() as s:
            a = q.row(s, Artifact, artifact_id)
            api.store.verify(s, a.id)
            info = q.artifact(a)
        if info["availability"] != "available": raise ArtifactUnavailable(artifact_id, info["unavailable_reason"])
        if info["storage"] != "file": raise NotWired("Git export is supplied by DEV-012/014")
        try:
            with api.db.read() as s: data = api.store.read_bytes(s, artifact_id)
        except ArtifactUnavailable:
            # A file can disappear between the first verification and the download read.
            # Commit availability/event separately before returning the failed download.
            with api.db.write() as s: api.store.verify(s, artifact_id)
            raise
        return Response(data, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{artifact_id}.bin"', "Content-Security-Policy": "sandbox; default-src 'none'", "X-Artifact-Digest": info["checksum"]})
    @app.get("/projects/{project_id}/events", response_class=Response)
    async def sse(request: Request, project_id: str, cursor: int | None = Query(None, ge=0), follow: bool = True):
        principal = auth(request)
        header = request.headers.get("last-event-id")
        if header is not None:
            if not header.isascii() or not header.isdigit() or len(header) > 18: raise ApiError(422, "invalid_cursor", "Last-Event-ID must be a nonnegative cursor")
            parsed = int(header)
            # EventSource retains the original URL on reconnect; its newer header wins.
            cursor = parsed
        return await events.response(request, principal, project_id, cursor or 0, follow)
    @app.post("/runtime/tools")
    def runtime_tool(request: Request, body: b.Tool):
        def action(s, svc, key, p):
            identity = svc.queue.identity(s, p.lease)
            j = q.row(s, Job, identity["job_id"])
            ctx = RunContext(queue=svc.queue, limiter=request.app.state.api.limiter, lease=p.lease,
                job={"id": j.id, "project_id": j.project_id, "ticket_id": j.ticket_id, "scope_version": j.scope_version,
                     "lane": j.lane, "stage": j.stage, "runtime_ref": dict(j.runtime_ref), "limits": dict(j.limits)})
            try:
                # Admission stays committed even when the tool's own effects are rolled back.
                svc.queue.reserve(p.lease, "tool")
                with s.begin_nested():
                    try:
                        return {"result": svc.tools._execute(ctx, body.name, body.args)}
                    except WaitingForInput as waiting:
                        return {"waiting_input": True, "request_id": str(waiting)}
            except Exception as exc:
                status, code = 422, "tool_error"
                if isinstance(exc, Forbidden): status, code = 403, "forbidden"
                elif isinstance(exc, NotWired): status, code = 501, "not_wired"
                elif isinstance(exc, BudgetExhausted): status, code = 409, "budget_exhausted"
                elif isinstance(exc, (Conflict, IdempotencyConflict, AlreadyAnswered)): status, code = 409, "conflict"
                elif isinstance(exc, StaleLease): status, code = 409, "stale_lease"
                elif isinstance(exc, NotFound): status, code = 404, "not_found"
                elif not isinstance(exc, (Invalid, QueueError, KeyError, TypeError, ValueError)):
                    status, code = 500, "tool_error"
                message = "Tool execution failed" if status == 500 else redactor.redact(str(exc))[:500]
                return {"_status": status, "error": {"code": code, "message": message, "details": {}}}
        return command(request, body, action, runtime=True)
    @app.post("/runtime/tickets/{ticket_id}/candidates")
    def submit(request: Request, ticket_id: str, body: b.CandidateSubmit):
        def action(s, svc, key, p):
            identity = svc.queue.identity(s, p.lease)
            actor = Actor(p.lease.owner, identity["role"], identity["project_id"], job_id=identity["job_id"], generation=identity["generation"])
            c = svc.workflow.submit_candidate(actor, ticket_id, body.expected_revision, Attempt(identity["job_id"], identity["generation"], identity["scope_version"]),
                commit_artifact_id=body.commit_artifact_id, commit_receipt_id=body.commit_receipt_id, base_sha=body.base_sha, submission_key=f"api:{key}")
            return {"candidate": q.candidate(s, c)}
        return command(request, body, action, runtime=True)
    @app.post("/runtime/candidates/{candidate_id}/reviews")
    def review(request: Request, candidate_id: str, body: b.CandidateReview):
        def action(s, svc, key, p):
            identity = svc.queue.identity(s, p.lease)
            actor = Actor(p.lease.owner, identity["role"], identity["project_id"], job_id=identity["job_id"], generation=identity["generation"])
            attempt = Attempt(identity["job_id"], identity["generation"], identity["scope_version"])
            if body.accept: t = svc.workflow.approve_review(actor, body.ticket_id, body.expected_revision, attempt, candidate_id)
            else: t = svc.workflow.request_changes(actor, body.ticket_id, body.expected_revision, candidate_id, request.app.state.api.redactor.redact(body.reason), attempt=attempt)
            return {"ticket": q.ticket(t)}
        return command(request, body, action, runtime=True)
    app.openapi = lambda: describe(app, settings)
    return app
