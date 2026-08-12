#!/usr/bin/env python3
"""Write a credential-free local audit index for MCP calls."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys


def find_value(value, names):
    if isinstance(value, dict):
        for key in names:
            if key in value and value[key] not in (None, ""):
                return value[key]
        for child in value.values():
            found = find_value(child, names)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_value(child, names)
            if found not in (None, ""):
                return found
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0

    raw_result = (
        event.get("tool_response")
        or event.get("tool_result")
        or event.get("tool_output")
        or {}
    )
    if isinstance(raw_result, str):
        try:
            result = json.loads(raw_result)
        except Exception:
            result = {"text": raw_result[:1000]}
    else:
        result = raw_result

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": event.get("session_id"),
        "tool_name": event.get("tool_name"),
        "run_id": find_value(result, {"run_id"}),
        "state": find_value(result, {"state", "run_state"}),
        "ok": find_value(result, {"ok", "success"}),
    }
    operation = str(record["tool_name"] or "").rsplit("__", 1)[-1]

    plugin_data = Path(
        os.environ.get("CLAUDE_PLUGIN_DATA", Path.home() / ".claude-plugin-data")
    )
    audit_dir = plugin_data / "rancher-rke2"
    try:
        audit_dir.mkdir(parents=True, exist_ok=True)
        with (audit_dir / "audit.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        if operation == "get_run" and (record["run_id"] or record["state"]):
            target = audit_dir / "latest-run-status.json"
            temp = audit_dir / "latest-run-status.json.tmp"
            temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(target)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
