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


def ask(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
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
        "start_workflow": [
            "plan_id",
            "config_digest",
            "preflight_id",
            "approval_text",
            "idempotency_key",
        ],
    }
    missing = [key for key in required.get(operation, []) if not args.get(key)]
    if missing:
        deny(f"{operation} is missing required arguments: {', '.join(missing)}")
        return 0

    if operation == "start_workflow":
        expected = f"APPROVE WORKFLOW {args.get('plan_id')}"
        if args.get("approval_text") != expected:
            deny(f"Workflow approval must exactly equal: {expected}")
            return 0

    ask(
        f"{operation} can change real infrastructure. Confirm this exact MCP "
        "tool call in addition to the server-side approval gate."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
