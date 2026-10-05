import asyncio
import json
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse
from app.persistence import read_events
from app.persistence.models import Event, Project
from .queries import row
from .security import ApiError


def page(api, principal, project_id, cursor):
    with api.db.read() as s:
        api.auth.check(s, principal)
        row(s, Project, project_id)
        tail = list(s.scalars(select(Event.cursor).where(Event.project_id == project_id)
                              .order_by(Event.cursor.desc()).limit(api.settings.replay_events + 1)))
        newest = max(tail, default=0)
        floor = tail[api.settings.replay_events - 1] - 1 if len(tail) > api.settings.replay_events else 0
        if cursor < floor or cursor > newest:
            return [], {"reason": "cursor_expired" if cursor < floor else "cursor_ahead",
                        "snapshot_url": f"/projects/{project_id}/tickets", "cursor": newest}
        return [{"cursor": e.cursor, "project_id": e.project_id, "type": e.type,
                 "entity_type": e.entity_type, "entity_id": e.entity_id, "run_id": e.run_id,
                 "payload": e.payload, "created_at": e.created_at} for e in
                read_events(s, after=cursor, project_id=project_id, limit=100)], None


def frame(name, data, cursor=None):
    prefix = f"id: {cursor}\n" if cursor is not None else ""
    return prefix + f"event: {name}\ndata: " + json.dumps(jsonable_encoder(data), ensure_ascii=True) + "\n\n"


async def response(request, principal, project_id, cursor, follow):
    api = request.app.state.api
    initial = await run_in_threadpool(page, api, principal, project_id, cursor)
    async def stream():
        nonlocal cursor
        result, idle = initial, 0
        while True:
            rows, reset = result
            if reset:
                yield frame("snapshot_required", reset)
                return
            for event in rows:
                cursor = event["cursor"]
                yield frame("state", api.redactor.redact_value(event), cursor)
            if not follow and len(rows) < 100: return
            if await request.is_disconnected(): return
            if not rows:
                idle += 1
                if idle >= max(1, int(15 / api.settings.poll_s)):
                    yield ": keepalive\n\n"
                    idle = 0
                await asyncio.sleep(api.settings.poll_s)
            try:
                result = await run_in_threadpool(page, api, principal, project_id, cursor)
            except ApiError:
                yield frame("session_expired", {"message": "Authenticate again"})
                return
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"})
