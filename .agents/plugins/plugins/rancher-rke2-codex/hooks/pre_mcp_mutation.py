#!/usr/bin/env python3
"""Client-side shape checks for Rancher/RKE2 mutation tool calls."""

import json
import sys


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


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        deny("Unable to parse the MCP tool call; mutation blocked.")
        return 0

    tool_name = str(event.get("tool_name", ""))
    args = event.get("tool_input") or event.get("tool_args") or {}
    if not isinstance(args, dict):
        deny("Mutation tool arguments must be an object.")
        return 0

    operation = tool_name.rsplit("__", 1)[-1]
    required = {
        "start_run": [
            "plan_id",
            "config_digest",
            "preflight_id",
            "approval_text",
            "idempotency_key",
        ],
        "resume_run": ["run_id", "idempotency_key"],
        "retry_component": ["run_id", "component", "idempotency_key"],
        "cancel_run": ["run_id", "approval_text", "idempotency_key"],
        "destroy_vm_set": [
            "run_id",
            "plan_id",
            "approval_text",
            "idempotency_key",
        ],
    }
    missing = [key for key in required.get(operation, []) if not args.get(key)]
    if missing:
        deny(f"{operation} is missing required arguments: {', '.join(missing)}")
        return 0

    if operation == "destroy_vm_set":
        expected = f"CONFIRM DESTROY run_id={args.get('run_id')} ALL TERRAFORM-STATE VMS"
        if args.get("approval_text") != expected:
            deny(f"Destruction confirmation must exactly equal: {expected}")
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
