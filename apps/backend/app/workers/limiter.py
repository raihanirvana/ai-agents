"""Provider request limiter shared by both lanes of one supervisor.

Execution may use at most `max_concurrent - reserved_interactive` in-flight model requests,
so interactive work (PO chat, clarification) always has capacity. A provider quota signal
blocks new requests for every job until the retry time; affected jobs become waiting_quota.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Callable

from app.persistence.columns import utcnow

from .queue import QuotaWait


class ProviderLimiter:
    def __init__(self, *, max_concurrent: int = 3, reserved_interactive: int = 1,
                 clock: Callable[[], datetime] = utcnow):
        if max_concurrent < 1 or not 0 <= reserved_interactive < max_concurrent:
            raise ValueError("need max_concurrent >= 1 and 0 <= reserved_interactive < max_concurrent")
        self.max_concurrent, self.reserved_interactive, self.clock = max_concurrent, reserved_interactive, clock
        self._lock = threading.Condition()
        self._in_flight = {"interactive": 0, "execution": 0}
        self._blocked_until: datetime | None = None
        self._reason = ""

    def _check_quota(self) -> None:
        if self._blocked_until is not None and self.clock() < self._blocked_until:
            raise QuotaWait(self._blocked_until, self._reason)

    def acquire(self, lane: str, *, timeout_s: float = 30.0) -> None:
        """Wait for a request slot; QuotaWait if the provider quota is exhausted."""
        if lane not in self._in_flight:
            raise ValueError("unknown lane")
        with self._lock:
            def free() -> bool:
                self._check_quota()
                total = sum(self._in_flight.values())
                cap = self.max_concurrent if lane == "interactive" else self.max_concurrent - self.reserved_interactive
                return total < self.max_concurrent and self._in_flight[lane] < cap
            self._check_quota()
            if not self._lock.wait_for(free, timeout=timeout_s):
                raise QuotaWait(self.clock() + timedelta(seconds=timeout_s), "local request capacity busy")
            self._check_quota()
            self._in_flight[lane] += 1

    def release(self, lane: str) -> None:
        with self._lock:
            self._in_flight[lane] = max(0, self._in_flight[lane] - 1)
            self._lock.notify_all()

    def exhausted(self, retry_after_s: float, reason: str) -> QuotaWait:
        """Record a provider quota signal (e.g. HTTP 429). Shared by all jobs of this supervisor."""
        with self._lock:
            until = self.clock() + timedelta(seconds=max(1.0, retry_after_s))
            if self._blocked_until is None or until > self._blocked_until:
                self._blocked_until, self._reason = until, reason
            self._lock.notify_all()
            return QuotaWait(self._blocked_until, self._reason)

    def status(self) -> dict:
        with self._lock:
            blocked = self._blocked_until if self._blocked_until and self.clock() < self._blocked_until else None
            return {"in_flight": dict(self._in_flight), "blocked_until": blocked and blocked.isoformat(),
                    "reason": self._reason if blocked else None}
