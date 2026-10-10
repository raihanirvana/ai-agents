"""Observational phase durations, separate from usage/budgets and approvals."""
import json
import math
import time
from contextlib import contextmanager
from datetime import datetime

PHASES = frozenset({'model', 'tool', 'provider_slot_wait', 'install', 'test', 'build',
    'browser', 'baseline_browser', 'baseline_build', 'baseline_browser_restore', 'checks',
    'queue_wait', 'user_wait', 'provider_quota_wait'})


def accumulate(job, metric, generation):
    phase, seconds = metric.get('phase'), metric.get('duration_s')
    if (phase not in PHASES or type(seconds) not in (int, float) or
            not math.isfinite(seconds) or seconds < 0):
        raise ValueError('phase telemetry requires a known phase and finite nonnegative duration')
    ref = dict(job.runtime_ref or {})
    telemetry = dict(ref.get('telemetry', {}))
    phases = dict(telemetry.get('phases', {}))
    previous = phases.get(phase, {})
    phases[phase] = {'count': previous.get('count', 0) + 1,
        'total_s': round(previous.get('total_s', 0) + seconds, 6),
        'max_s': max(previous.get('max_s', 0), round(seconds, 6)),
        'failed': previous.get('failed', 0) + int(metric.get('status') == 'failed'),
        'cache_hits': previous.get('cache_hits', 0) + int(metric.get('cache_hit') is True),
        'last_generation': generation}
    job.runtime_ref = {**ref, 'telemetry': {**telemetry, 'schema': 1, 'phases': phases}}


def wait_transition(job, event, now):
    """Called inside the transition transaction; no polling or extra write lock."""
    phase = {'enqueued': 'queue_wait', 'retry_scheduled': 'queue_wait', 'resumed': 'queue_wait',
        'quota_retry': 'queue_wait', 'released': 'queue_wait', 'operator_retry': 'queue_wait',
        'budget_extended': 'queue_wait', 'waiting_input': 'user_wait',
        'waiting_quota': 'provider_quota_wait'}.get(event)
    terminal = {'claimed', 'cancelled', 'cancellation_requested', 'budget_exhausted', 'failed', 'succeeded'}
    if phase is None and event not in terminal:
        return
    previous = (job.runtime_ref or {}).get('telemetry_wait')
    if previous and previous.get('phase') == phase:
        return  # Idempotent transition/retry metadata cannot restart a wait timer.
    if previous:
        began = datetime.fromisoformat(previous['started_at'])
        accumulate(job, {'phase': previous['phase'], 'duration_s': max(0, (now-began).total_seconds()),
                         'status': 'passed'}, previous['generation'])
    ref = dict(job.runtime_ref or {})
    ref.pop('telemetry_wait', None)
    if phase:
        ref['telemetry_wait'] = {'phase': phase, 'started_at': now.isoformat(),
                                'generation': job.lease_generation}
    job.runtime_ref = ref


def record_phase(ctx, phase, seconds, *, status='passed', cache_hit=False):
    metric = {'phase': phase, 'duration_s': round(seconds, 6), 'status': status, 'cache_hit': cache_hit}
    # Metrics never supply a result/approval, nor replace the original exception.
    try:
        if hasattr(ctx, 'record_phase'):
            ctx.record_phase(metric)
        else:
            ctx.log('phase.metric ' + json.dumps(metric))
    except Exception:
        pass  # The operation's authoritative evidence/error path remains unchanged.


@contextmanager
def measure(ctx, phase):
    began = time.monotonic()
    metric = {'status': 'passed'}
    try:
        yield metric
    except BaseException:
        metric['status'] = 'failed'
        raise
    finally:
        record_phase(ctx, phase, time.monotonic()-began, status=metric['status'])
