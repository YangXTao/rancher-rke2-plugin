---
name: rancher-rke2-diagnose
description: Diagnose Rancher/RKE2 runs and component failures through read-only MCP tools. Use for failed or blocked runs, event timelines, checkpoint analysis, artifact inspection, dependency failures, health-check evidence, root-cause hypotheses, or a proposed remediation plan. Do not use this skill to execute fixes.
---

# Rancher RKE2 Diagnose

This skill is read-only. Use only `get_run`, `get_run_events`,
`collect_diagnostics`, `get_plan`, and capability/schema reads.

Call `get_capabilities` first and use only tools it reports. If runtime diagnostic
tools are unavailable, explain that the current server supports planning only and
do not invent run state or evidence.

## Workflow

1. Require a `run_id`; never guess one.
2. Call `get_run` to identify the first failed or blocked component and its last
   successful checkpoint.
3. Read only the relevant event window with `get_run_events`.
4. Call `read_run_log` for the failed component's mandatory logs (for example
   `local-rke2/ansible-playbook.log`) to inspect redacted evidence directly from
   the control host; increase `lines` only when the first window is insufficient.
5. Call `collect_diagnostics` when the server reports it as available; otherwise
   `read_run_log` is the evidence path.
6. Separate facts, server-reported findings, hypotheses, and missing evidence.
7. Return the smallest remediation plan and identify which component skill would
   own a later fix.

When three or more evidence areas are independent, subagents may analyze them in
parallel. Give them read-only evidence and require concise findings; do not let a
subagent call mutation tools.

Do not call `start_run`, `resume_run`, `retry_component`, `cancel_run`, or
`destroy_vm_set`. Do not implement the remediation unless the user separately asks
for a change. Redact any plaintext credential fragments that appear in legacy logs.
