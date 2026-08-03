#!/usr/bin/env python3
"""Block plaintext credentials before validate_config reaches the MCP server."""

from __future__ import annotations

import json
import re
import sys
from typing import Any


SECRET_KEY_PARTS = (
    "password",
    "passwd",
    "token",
    "secret",
    "credential",
    "private_key",
    "ssh_key",
    "api_key",
)
YAML_FIELD = re.compile(r"(?im)^\s*(?P<key>[A-Za-z0-9_.-]+)\s*:")
URL_USERINFO = re.compile(r"://[^/@\s]+:[^/@\s]+@")


def deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            ensure_ascii=False,
        )
    )


def find_plaintext(value: Any, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            item_path = f"{path}.{key_text}"
            if (
                any(part in lowered for part in SECRET_KEY_PARTS)
                and not lowered.endswith("_ref")
            ):
                return item_path
            found = find_plaintext(item, item_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = find_plaintext(item, f"{path}[{index}]")
            if found:
                return found
    elif isinstance(value, str):
        if URL_USERINFO.search(value):
            return path
        if path == "$":
            for match in YAML_FIELD.finditer(value):
                key = match.group("key").lower()
                if any(part in key for part in SECRET_KEY_PARTS) and not key.endswith(
                    "_ref"
                ):
                    return f"{path}.{match.group('key')}"
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        deny("Unable to parse the validate_config call; request blocked.")
        return 0

    args = event.get("tool_input") or event.get("tool_args") or {}
    config = args.get("config") if isinstance(args, dict) else None
    if config is None:
        deny("validate_config is missing the required config argument.")
        return 0

    unsafe_path = find_plaintext(config)
    if unsafe_path:
        deny(
            "Plaintext credentials or a credential-bearing URL were found; "
            "request blocked. Create a Docker Secret on the MCP host and replace "
            f"{unsafe_path} with a docker-secret:// reference."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
