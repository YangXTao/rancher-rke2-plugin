#!/usr/bin/env python3
"""Prevent a success claim when the latest audited run is not successful."""

import json
import os
from pathlib import Path
import sys


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0

    if event.get("stop_hook_active"):
        return 0

    message = str(
        event.get("last_assistant_message")
        or event.get("assistant_message")
        or ""
    )
    if "RANCHER_RKE2_FINAL: SUCCEEDED" not in message:
        return 0

    plugin_data = Path(os.environ.get("PLUGIN_DATA", Path.home() / ".codex-plugin-data"))
    status_path = plugin_data / "rancher-rke2" / "latest-run-status.json"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        status = {}

    if status.get("state") != "SUCCEEDED":
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": (
                        "The final response claims deployment success, but the latest "
                        "MCP audit state is not SUCCEEDED. Call get_run again and "
                        "correct the conclusion."
                    ),
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
