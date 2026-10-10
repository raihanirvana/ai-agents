"""Relay accounting backed exclusively by the product JobQueue, not the spike journal."""
import threading
import uuid
import math
import time
from app.workers.telemetry import measure, record_phase
from app.persistence.models import Job
from app.runtime_spike.journal import AdmissionError
from app.workers.queue import BudgetExhausted, StaleLease, QuotaWait
from .contracts import digest_of
from app.persistence.transactions import bind_service

UNKNOWN = {'input_tokens': None, 'output_tokens': None, 'total_tokens': None, 'cost_usd': None}


class ProductAdmission:
    def __init__(self, ctx, redactor):
        self.ctx, self.redactor = ctx, redactor
        self.pending, self.error = {}, None
        self.started = {}
        self.provider_failure = None
        self.relay_failure = None
        self.lock = threading.RLock()

    def inspect(self, scope):
        if self.ctx.cancelled.is_set():
            raise AdmissionError('run cancelled')
        identity = self.ctx.queue.verify(self.ctx.lease)
        return {'limits': self.ctx.job['limits'], 'generation': identity['generation'],
                'status': 'running', 'active_s': 0, 'reservations': []}

    def reserve(self, scope, generation, kind, name):
        acquired = False
        try:
            if generation != self.ctx.lease.generation or scope != self.ctx.lease.job_id:
                raise StaleLease('relay identity mismatch')
            if kind == 'model':
                with measure(self.ctx, 'provider_slot_wait'):
                    self.ctx.limiter.acquire(self.ctx.lane)
                acquired = True
            rid = uuid.uuid4().hex
            exhausted = None
            with self.ctx.queue.db.write() as s:
                try:
                    bind_service(self.ctx.queue, s).reserve(self.ctx.lease, kind)
                except BudgetExhausted as exc:
                    exhausted = exc
                if exhausted is None:
                    job = s.get(Job, self.ctx.lease.job_id)
                    job.runtime_ref = {**job.runtime_ref, 'pipeline_reservations': {
                        **job.runtime_ref.get('pipeline_reservations', {}), rid: {'kind': kind, 'generation': generation}}}
            if exhausted:
                raise exhausted
            with self.lock:
                self.pending[rid] = kind
                self.started[rid] = time.monotonic()
            return rid
        except (BudgetExhausted, StaleLease, QuotaWait) as exc:
            self.error = exc
            if acquired:
                self.ctx.limiter.release(self.ctx.lane)
            raise AdmissionError(str(exc)) from exc
        except BaseException:
            if acquired:
                self.ctx.limiter.release(self.ctx.lane)
            raise

    def finish(self, rid, result):
        with self.lock:
            kind = self.pending.pop(rid, None)
            began = self.started.pop(rid, None)
            if kind == 'model':
                self.provider_failure = ({'http_status': result.get('http_status'), 'status': result.get('status'),
                    'detail': self.redactor.redact(str(result.get('detail') or ''))[:1000]}
                    if result.get('status') in ('provider_error', 'transport_failure') else None)
        if kind is None:
            return  # a transport error after accounting cannot bill the same call twice
        response = None
        try:
            with self.ctx.queue.db.write() as s:
                job = s.get(Job, self.ctx.lease.job_id)
                saved = dict(job.runtime_ref.get('pipeline_reservations', {}))
                saved.pop(rid, None)
                job.runtime_ref = {**job.runtime_ref, 'pipeline_reservations': saved}
                if kind == 'model':
                    actual = result.get('model')
                    if isinstance(actual, str) and actual.strip():
                        response = {'generation': self.ctx.lease.generation, 'reservation': rid,
                            'model': self.redactor.redact(actual)[:200],
                            'requested_model': self.redactor.redact(str(result.get('requested_model') or ''))[:200],
                            'status': result.get('status')}
                        job.runtime_ref = {**job.runtime_ref, 'pipeline_model_responses': [
                            *job.runtime_ref.get('pipeline_model_responses', []), response]}
                    usage = result.get('usage') if isinstance(result.get('usage'), dict) else {}
                    def amount(value):
                        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
                    counters = {**UNKNOWN, 'input_tokens': amount(usage.get('prompt_tokens')),
                        'output_tokens': amount(usage.get('completion_tokens')), 'total_tokens': amount(usage.get('total_tokens')),
                        'cost_usd': amount(result.get('cost'))}
                    details = usage.get('prompt_tokens_details')
                    counters['cached_tokens'] = amount(details.get('cached_tokens')) if isinstance(details, dict) else None
                    if result.get('status') == 'local_rejected':
                        # Admission was reserved, but no request reached a provider.
                        counters = dict.fromkeys((*UNKNOWN, 'cached_tokens'), 0)
                    if counters['input_tokens'] is not None and counters['output_tokens'] is not None:
                        counters['total_tokens'] = max(counters['total_tokens'] or 0, counters['input_tokens'] + counters['output_tokens'])
                    bound = bind_service(self.ctx.queue, s)
                    bound.finalize_usage(job.id, self.ctx.lease.generation, counters)
                    output = counters['output_tokens']
                    cap = self.ctx.job['limits'].get('output_tokens')
                    if output is not None and cap is not None and output > cap:
                        bound.cancel(job.id, reason='provider exceeded output token cap', actor='service:relay')
                    if result.get('http_status') == 429:
                        detail = self.redactor.redact(str(result.get('detail') or ''))[:1000]
                        delay = result.get('retry_after_s', 30)
                        if type(delay) not in (int, float) or not math.isfinite(delay):
                            delay = 30
                        self.error = self.ctx.provider_quota(min(3600, max(1, delay)),
                            'provider HTTP 429' + (': ' + detail if detail else ' rate limit'))
            # Durable logging uses its own transaction, after accounting commits.
            # One line per finished call: response identity rides on the relay line.
            line = f'relay {kind}: {result.get("status")} usage={result.get("usage")}'
            if response is not None:
                line += f' model.response requested={response["requested_model"]} actual={response["model"]}'
            if kind == 'model' and result.get('status') in ('provider_error', 'transport_failure'):
                line += (f' provider.error http_status={result.get("http_status")} '
                         f'detail={str(result.get("detail") or "transport or upstream error")[:1000]}')
            self.ctx.log(self.redactor.redact(line))
        finally:
            if kind == 'model':
                self.ctx.limiter.release(self.ctx.lane)
            if began is not None:
                record_phase(self.ctx, kind, time.monotonic()-began,
                    status='passed' if result.get('status') in ('complete', 'completed', 'succeeded') else 'failed')

    def event(self, scope, generation, kind, payload):
        if kind == 'relay.rejected':
            self.relay_failure = dict(payload)
        # Persist hashes/audit metadata, never tool content or provider secrets.
        # Details for the same event share one audit line, not extra writes.
        line = f'{kind}: {digest_of(payload)}'
        if kind in ('relay.rejected', 'context.size'):
            line += ' ' + str(payload)
        if kind == 'provider.retry':
            line += ' ' + str({k: payload.get(k) for k in ('attempt', 'http_status', 'delay_s')})
        suffix = ''
        if kind in ('tool.started', 'tool.completed'):
            name = payload.get('name')
            if isinstance(name, str) and name.replace('_', '').isalnum():
                suffix = f' tool.metric name={name}'
        self.ctx.log(self.redactor.redact(line) + suffix)

    def close(self):
        for rid in list(self.pending):
            self.finish(rid, {'status': 'interrupted', 'usage': None, 'cost': None})


def reconcile_accounting(queue, snapshot):
    """Crash between reservation and response: spending is visibly unknown, not zero."""
    with queue.db.write() as s:
        job = s.get(Job, snapshot['id'])
        pending = job.runtime_ref.get('pipeline_reservations', {})
        for reservation in pending.values():
            if reservation['kind'] == 'model':
                bind_service(queue, s).finalize_usage(job.id, reservation['generation'], UNKNOWN)
        job.runtime_ref = {**job.runtime_ref, 'pipeline_reservations': {}}
