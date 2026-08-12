---
name: rancher-rke2-runbook
description: Generate or audit a human-executable Rancher/RKE2 installation manual through the rancher-rke2 MCP server. Use when the user asks for a reproducible runbook, offline handoff, operator checklist, command-by-command manual, approved-plan documentation, evidence map, or an audit of manual steps. This skill does not execute infrastructure changes.
---

# Rancher RKE2 Runbook

Generate documentation from an immutable MCP plan; do not reconstruct commands from
memory or copy implementation from legacy skills.

Call `get_capabilities` first and use only tools it reports. If `render_runbook` is
unavailable, return the plan and explain that the current server cannot yet render
an audited runbook.

## Workflow

1. Use an existing `plan_id`, or validate configuration and call `build_plan`
   without starting a run.
2. Call `get_plan` and confirm its component scope, mode, versions, prerequisites,
   risks, and expiry.
3. Call `render_runbook` with `format: "markdown"` and
   `output_profile: "human-step-by-step"`.
4. Audit the result for ordered steps, expected outputs, verification commands,
   rollback boundaries, offline files, artifact hashes, and credential redaction.
5. Return the artifact location and unresolved audit findings.

Subagents may independently review correctness and operator usability when the
manual spans several components. They remain read-only and may not execute commands.

Configuration contains only Secret references. A generated runbook may identify
the required Secret names but must never resolve or embed their values. Do not call
mutation tools from this skill.
