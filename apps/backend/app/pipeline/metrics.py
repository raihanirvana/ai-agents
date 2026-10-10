"""Read-only phase/usage report: python -m app.pipeline.metrics --project-id ID."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select


def report(db, project_id):
    from app.persistence.models import Event, Job, Project
    from app.workers.telemetry import PHASES
    with db.read() as s:
        if s.get(Project, project_id) is None:
            raise ValueError('project not found')
        jobs = list(s.scalars(select(Job).where(Job.project_id == project_id).order_by(Job.created_at, Job.id)))
        rows, by_role = [], {}
        now = datetime.now(timezone.utc)
        for job in jobs:
            ref = job.runtime_ref or {}
            phases = ref.get('telemetry', {}).get('phases', {})
            role = ref.get('role', 'unknown')
            totals = by_role.setdefault(role, {'phases': {}, 'usage': {}, 'unknown_usage_jobs': 0})
            for name, value in phases.items():
                if name not in PHASES:
                    continue
                bucket = totals['phases'].setdefault(name, dict.fromkeys(('count', 'total_s', 'failed', 'cache_hits'), 0))
                for key in bucket:
                    bucket[key] += value.get(key, 0)
            usage = job.usage or {}
            for name, value in usage.items():
                if name != '_unknown' and type(value) in (int, float):
                    totals['usage'][name] = totals['usage'].get(name, 0) + value
            totals['unknown_usage_jobs'] += int(bool(usage.get('_unknown')))
            waiting = ref.get('telemetry_wait')
            pending = None
            if waiting:
                end = job.finished_at or now
                pending = {'phase': waiting['phase'], 'elapsed_s': max(0,
                    (end-datetime.fromisoformat(waiting['started_at'])).total_seconds()), 'ongoing':
                    job.status in ('queued', 'waiting_input', 'waiting_quota')}
            rows.append({'job_id': job.id, 'ticket_id': job.ticket_id, 'scope_version': job.scope_version,
                'role': role, 'stage': job.stage, 'task': ref.get('payload', {}).get('task'),
                'attempt': job.attempt, 'status': job.status, 'phases': phases, 'pending_wait': pending,
                'telemetry_available': bool(phases or waiting), 'usage': usage})
        # UAT/scope approval waits do not have an active worker job. Derive
        # them from real ticket phase events without adding scheduler polling.
        last_phase, approvals = {}, []
        events = s.scalars(select(Event).where(Event.project_id == project_id,
            Event.entity_type == 'tickets').order_by(Event.cursor))
        for event in events:
            phase = (event.payload or {}).get('phase')
            if not phase:
                continue
            prior = last_phase.get(event.entity_id)
            if prior and prior[0] == phase:
                continue
            if prior and prior[0] in ('scope_review', 'uat'):
                approvals.append({'ticket_id': event.entity_id, 'phase': prior[0],
                    'duration_s': max(0, (event.created_at-prior[1]).total_seconds()), 'ongoing': False})
            last_phase[event.entity_id] = (phase, event.created_at)
        for ticket_id, (phase, began) in last_phase.items():
            if phase in ('scope_review', 'uat'):
                approvals.append({'ticket_id': ticket_id, 'phase': phase,
                    'duration_s': max(0, (now-began).total_seconds()), 'ongoing': True})
    return {'schema': 1, 'project_id': project_id, 'by_role': by_role, 'jobs': rows,
        'user_approval_waits': approvals,
        'note': 'Inclusive/nested durations overlap: never sum model/tool/checks/browser as wall time. '
                'Pending waits are shown separately, not added to closed totals. Historical jobs without '
                'telemetry are unavailable, not zero. Usage is recorded per job, not per phase; '
                'unknown usage stays unknown. Compare identical cold/warm workloads separately.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--db', type=Path)
    args = parser.parse_args()
    from app.config import DATABASE_PATH
    from app.persistence import Database
    path = args.db or DATABASE_PATH
    if not path.is_file():
        parser.error('database does not exist; this command never creates or migrates it')
    try:
        result = report(Database(path), args.project_id)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
