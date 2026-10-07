"""Trusted OpenRouter routing configuration; the runtime cannot select its own backups."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def routing_fields(model: str, fallbacks: tuple[str, ...], allow_provider_fallbacks: bool) -> dict:
    fields = {"provider": {"allow_fallbacks": allow_provider_fallbacks}}
    if fallbacks:
        fields["models"] = [model, *fallbacks]
    else:
        fields["model"] = model
    return fields


def retry_after_seconds(value: str | None, default: float = 30.0) -> float:
    try:
        delay = float(value) if value else default
    except (TypeError, ValueError):
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            delay = (when - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            delay = default
    return min(3600.0, max(1.0, delay if math.isfinite(delay) else default))
