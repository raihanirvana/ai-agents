"""Relay accounting backed exclusively by the product JobQueue, not the spike journal."""
import threading
import uuid
import math
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
        self.lock = threading.RLock()

    def inspect(self, scope):
        identity = self.ctx.queue.verify(self.ctx.lease)
        return {'limits': self.ctx.job['limits'], 'generation': identity['generation'],
                'status': 'running', 'active_s': 0, 'reservations': []}

    def reserve(self, scope, generation, kind, name):
        acquired = False
        try:
            if generation != self.ctx.lease.generation or scope != self.ctx.lease.job_id:
                raise StaleLease('relay identity mismatch')
            if kind == 'model':
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
        if kind is None:
            return  # a transport error after accounting cannot bill the same call twice
        try:
            with self.ctx.queue.db.write() as s:
                job = s.get(Job, self.ctx.lease.job_id)
                saved = dict(job.runtime_ref.get('pipeline_reservations', {}))
                saved.pop(rid, None)
                job.runtime_ref = {**job.runtime_ref, 'pipeline_reservations': saved}
                if kind == 'model':
                    usage = result.get('usage') if isinstance(result.get('usage'), dict) else {}
                    def amount(value):
                        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
                    counters = {**UNKNOWN, 'input_tokens': amount(usage.get('prompt_tokens')),
                        'output_tokens': amount(usage.get('completion_tokens')), 'total_tokens': amount(usage.get('total_tokens')),
                        'cost_usd': amount(result.get('cost'))}
                    if counters['input_tokens'] is not None and counters['output_tokens'] is not None:
                        counters['total_tokens'] = max(counters['total_tokens'] or 0, counters['input_tokens'] + counters['output_tokens'])
                    bound = bind_service(self.ctx.queue, s)
                    bound.finalize_usage(job.id, self.ctx.lease.generation, counters)
                    output = counters['output_tokens']
                    if output is not None and output > self.ctx.job['limits']['output_tokens']:
                        bound.cancel(job.id, reason='provider exceeded output token cap', actor='service:relay')
                    if result.get('http_status') == 429:
                        self.error = self.ctx.provider_quota(30, 'provider rate limit')
            self.ctx.log(self.redactor.redact(f'relay {kind}: {result.get("status")} usage={result.get("usage")}'))
        finally:
            if kind == 'model':
                self.ctx.limiter.release(self.ctx.lane)

    def event(self, scope, generation, kind, payload):
        # Persist hashes/audit metadata, never tool content or provider secrets.
        self.ctx.log(self.redactor.redact(f'{kind}: {digest_of(payload)}'))

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
