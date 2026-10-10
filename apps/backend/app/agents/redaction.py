"""Separate source-safe credential masking from generic prose/log redaction.

Known secret values (provider keys read from the environment) are masked exactly, and common
secret shapes are masked by pattern in conversational text, logs and errors.
Source and exact source witnesses use known values only: variable names or
assignments involving token/password/secret are programming syntax, not credentials.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

MASK = "[REDACTED]"
# (pattern, replacement). The key=value form keeps the key name so the text stays readable.
_PATTERNS = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), MASK),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"), MASK),
    (re.compile(r"(?i)(\b(?:api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*)(['\"]?)[^\s'\",;]{6,}"),
     r"\1\2" + MASK),
    (re.compile(r"\b(?:ghp|gho|ghs|glpat)_[A-Za-z0-9]{16,}"), MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), MASK),
)


class Redactor:
    def __init__(self, secrets: Iterable[str] = ()):
        # Very short values would mask ordinary words; real keys are far longer.
        self._secrets = sorted({s for s in secrets if isinstance(s, str) and len(s) >= 8}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        text = self.redact_source(text)
        for pattern, replacement in _PATTERNS:
            text = pattern.sub(replacement, text)
        return text

    def redact_source(self, text: str) -> str:
        """Mask actual configured credentials without interpreting programming syntax."""
        for secret in self._secrets:
            text = text.replace(secret, MASK)
        return text

    def redact_value(self, value: Any, *, source: bool = False) -> Any:
        """Redact JSON-like strings/keys; source=True uses exact configured values only."""
        redact = self.redact_source if source else self.redact
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, dict):
            return {redact(k) if isinstance(k, str) else k: self.redact_value(v, source=source)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact_value(v, source=source) for v in value]
        return value

    def redact_source_fields(self, value: Any) -> Any:
        """Mixed message metadata retains exact source witnesses; prose stays scrubbed."""
        if isinstance(value, dict):
            return {self.redact(k) if isinstance(k, str) else k:
                    (self.redact_value(v, source=True) if k in ('source_excerpt', 'ui_contract')
                     else self.redact_source_fields(v)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact_source_fields(v) for v in value]
        return self.redact_value(value)

    def contains_secret(self, text: str) -> bool:
        return self.redact(text) != text

    def with_secrets(self, *secrets: str) -> "Redactor":
        return Redactor([*self._secrets, *secrets])
