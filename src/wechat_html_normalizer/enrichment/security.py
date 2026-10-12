from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS = [
    re.compile(r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+[^\s,;]+"),
    re.compile(r"(?i)data:image/[a-z0-9.+-]+;base64,[a-z0-9+/=]+"),
]


def redact_sensitive_text(value: str) -> tuple[str, bool]:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED_CREDENTIAL]", redacted)
    return redacted, redacted != value


def redact_sensitive_object(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        text, changed = redact_sensitive_text(value)
        return text, int(changed)
    if isinstance(value, list):
        result, count = [], 0
        for item in value:
            safe, changes = redact_sensitive_object(item)
            result.append(safe)
            count += changes
        return result, count
    if isinstance(value, dict):
        result, count = {}, 0
        for key, item in value.items():
            safe, changes = redact_sensitive_object(item)
            result[key] = safe
            count += changes
        return result, count
    return value, 0
