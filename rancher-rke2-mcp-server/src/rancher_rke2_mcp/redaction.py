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
