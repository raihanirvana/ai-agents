"""Atomically commit HTTP receipts with the existing domain/queue transactions."""
from contextlib import nullcontext
from dataclasses import dataclass
import hashlib

from sqlalchemy import select
from fastapi.encoders import jsonable_encoder

from app.agents import Threads, ToolFacade
from app.domain import Actor, Workflow
from app.persistence.artifacts import canonical_json
from app.persistence.models import ApiCommand
from app.workers import JobQueue
from .security import ApiError


class TransactionDatabase:
    """Services keep their transaction API; the outer command owns commit/rollback."""
    def __init__(self, session):
        self.session = session

    def read(self):
        return nullcontext(self.session)

    def write(self):
        return nullcontext(self.session)


@dataclass
class Services:
    workflow: Workflow
    queue: JobQueue
    threads: Threads
    tools: ToolFacade


def bind(session, store, redactor):
    db = TransactionDatabase(session)
    workflow = Workflow(db, store)
    queue = JobQueue(db, startable=workflow.startable)
    threads = Threads(db, queue, redactor=redactor)
    return Services(workflow, queue, threads, ToolFacade(db, workflow, threads))


def execute(request, principal, data, action):
    api = request.app.state.api
    key = request.headers.get("idempotency-key", "")
    if not key or len(key) > 160 or not key.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ApiError(422, "idempotency_required", "A bounded printable Idempotency-Key is required")
    digest = hashlib.sha256(canonical_json({"method": request.method, "path": request.url.path, "body": data})).hexdigest()
    actor_key = principal.user_id if principal.kind == "user" else f"runtime:{principal.lease.job_id}:{principal.lease.generation}"
    with api.db.write() as s:
        api.auth.check(s, principal)  # same lock as admission, effect and receipt
        existing = s.scalar(select(ApiCommand).where(ApiCommand.actor_key == actor_key, ApiCommand.idempotency_key == key))
        if existing:
            if existing.request_hash != digest:
                raise ApiError(409, "idempotency_conflict", "This key identifies different work")
            return existing.response
        services = bind(s, api.store, api.redactor)
        result = jsonable_encoder(api.redactor.redact_value(action(s, services, key)))
        s.flush()
        s.add(ApiCommand(actor_key=actor_key, idempotency_key=key, request_hash=digest, response=result))
        return result


def user_actor(principal, project_id):
    if principal.kind != "user":
        raise ApiError(403, "user_required", "Only the authenticated user may perform this command")
    return Actor(principal.user_id, "user", project_id)
