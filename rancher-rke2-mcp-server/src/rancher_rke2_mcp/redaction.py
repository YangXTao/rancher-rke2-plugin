from __future__ import annotations

import re
from typing import Any

REDACTED = "***REDACTED***"
SECRET_KEY_PARTS = (
    "password",
    "passwd",
    "token",
    "secret",
    "credential",
    "private_key",
)
URL_CREDENTIALS = re.compile(r"(://)[^/@:\s]+:[^/@\s]+@")
TEXT_SECRET_PATTERNS = (
    (re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"), r"\1***REDACTED***"),
    (
        re.compile(
            r"(?i)([\w.-]*(?:token|password|passwd|api[_-]?key|private[_-]?key|secret)"
            r"[\w.-]*\s*[=:]\s*)\S+"
        ),
        r"\1***REDACTED***",
    ),
    (
        re.compile(r"(?i)(--(?:password|token|api[_-]?key|private[_-]?key)\s+)\S+"),
        r"\1***REDACTED***",
    ),
)


def redact(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if lowered.endswith("_ref"):
        return value
    if any(part in lowered for part in SECRET_KEY_PARTS):
        return REDACTED
    if lowered in {"proxy_url", "proxy"} and value:
        return REDACTED
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return URL_CREDENTIALS.sub(r"\1***:***@", value)
    return value


def redact_text(text: str) -> str:
    """Redact credential fragments in free-text log output."""
    for pattern, replacement in TEXT_SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
