---
name: rancher-rke2-node-init
description: Plan, inspect, execute, or diagnose Linux node initialization through the rancher-rke2 MCP server. Use for the approved 46-entry sysctl profile, kernel modules, limits, SELinux, swap, firewall handling, removal of obsolete Rancher drop-ins, control-container prerequisites, or node-init component status.
---

# Rancher RKE2 Node Init

Operate only component `node-init` through the domain MCP server. Never run Ansible,
SSH, sysctl, package managers, or legacy scripts directly.

Call `get_capabilities` first. Treat its tool list as authoritative. If
`preflight_plan` is available, call it after `get_plan`; stop and report failed
Secret or TCP checks. If `start_run` is unavailable, stop after the plan and
preflight result and state that the current server is preflight-only. Later execution steps apply only when their
named tools are available.

## Workflow

1. Call `validate_config`.
2. Call `build_plan` with `target_components: ["node-init"]`.
3. Present affected nodes, dependency checks, approved sysctl profile, modules,
   limits, SELinux, swap, firewall, and obsolete drop-in changes.
4. For a change, obtain the exact plan approval and call `start_run` with the
   returned plan identifiers and a stable idempotency key.
5. Use `get_run` and `get_run_events` for progress. Do not retry automatically
   after a failure.
6. Require server verification for all selected nodes before reporting success.

If VM prerequisites are missing, report the server's `BLOCKED` reason rather than
trying to create or repair VMs from this skill. Status and explanation requests
remain read-only.

Control-host and node credentials must be `docker-secret://` references provisioned
on the MCP host. Never request their values or include them in an event summary.
