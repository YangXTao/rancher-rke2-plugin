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
# Kubernetes Secret *names* referenced by Rancher2 registry entries are not
# credential values; keep them visible in redacted previews.
SECRET_NAME_ONLY_KEYS = (
    "authconfigsecretname",
    "tlssecretname",
)
URL_CREDENTIALS = re.compile(r"(://)[^/@:\s]+:[^/@\s]+@")
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)([\"']?\b(?:password|passwd|token|credential|private[_-]?key)\b"
    r"[\"']?\s*[:=]\s*)([\"']?[^\s,;}\]]+)"
)
SENSITIVE_FLAG = re.compile(
    r"(?i)(--(?:password|passwd|token|secret|credential|private-key|proxy-url)"
    r"(?:=|\s+))([^\s]+)"
)
BEARER_TOKEN = re.compile(r"(?i)(\bauthorization\s*:\s*bearer\s+)([^\s]+)")


def redact(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if lowered.endswith("_ref") or lowered in SECRET_NAME_ONLY_KEYS:
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


def redact_text(value: str, secret_values: tuple[str, ...] = ()) -> str:
    """Redact credentials from unstructured diagnostic text."""

    result = value
    for secret in sorted({item for item in secret_values if item}, key=len, reverse=True):
        result = result.replace(secret, REDACTED)
    result = URL_CREDENTIALS.sub(r"\1***:***@", result)
    result = SENSITIVE_FLAG.sub(r"\1" + REDACTED, result)
    result = SENSITIVE_ASSIGNMENT.sub(r"\1" + REDACTED, result)
    result = BEARER_TOKEN.sub(r"\1" + REDACTED, result)
    return result


def redact_diagnostic_payload(value: Any, secret_values: tuple[str, ...] = ()) -> Any:
    """Apply structured and unstructured redaction to diagnostic evidence."""

    structured = redact(value)

    def scrub(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(key): scrub(child) for key, child in item.items()}
        if isinstance(item, list):
            return [scrub(child) for child in item]
        if isinstance(item, tuple):
            return [scrub(child) for child in item]
        if isinstance(item, str):
            return redact_text(item, secret_values)
        return item

    return scrub(structured)
