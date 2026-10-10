"""Redact before model calls, logs and default UI serialization."""

from __future__ import annotations

import re
from typing import Any

SECRET_KEY = re.compile(
    r"(?:api[_-]?key|password|secret|token(?:$|[_-])|authorization|cookie|private[_-]?key)", re.I
)
PATTERNS = [
    re.compile(
        r"""(?i)((?:["']?)\b[A-Z0-9_]*(?:TOKEN|API[_-]?KEY|PASSWORD|SECRET|AUTHORIZATION|COOKIE|PRIVATE[_-]?KEY)["']?\s*[:=]\s*)(?:"(?:\\[\s\S]|[^"\\])*(?:"|\\?$)|'(?:\\[\s\S]|[^'\\])*(?:'|\\?$)|[^"'\s,;}\]]+)"""
    ),
    re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/-]+"),
    re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    ),
]


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if SECRET_KEY.search(str(k)) else redact(v) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        return value
    result = value
    for pattern in PATTERNS:
        result = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", result)
    return result
