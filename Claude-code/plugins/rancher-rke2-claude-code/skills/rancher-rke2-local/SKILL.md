---
name: rancher-rke2-local
description: Plan, inspect, execute, or diagnose the three-server Local RKE2 management cluster through the rancher-rke2 MCP server. Use for RKE2 artifact preparation, registry configuration, Cilium with kube-proxy retained, first-server bootstrap, second or third server joins, control-container prerequisites, or Local RKE2 status.
---

# Rancher RKE2 Local

Operate only component `local-rke2` through the domain MCP server. Never use SSH,
Ansible, curl, systemctl, kubectl, or legacy scripts as an alternate execution path.

Call `get_capabilities` first. Treat its tool list as authoritative. If
`preflight_plan` is available, call it after `get_plan`; stop and report failed
Secret or TCP checks. If `start_run` is unavailable, stop after the plan and
preflight result and state that the current server is preflight-only. Later execution steps apply only when their
named tools are available.

## Workflow

1. Validate configuration and build a plan with
   `target_components: ["local-rke2"]`.
2. Present online/offline mode, artifact readiness, registry settings, three server
   roles, cluster CIDRs, Cilium settings, and the fact that kube-proxy is retained.
3. Obtain exact plan approval before calling `start_run`.
4. Observe bootstrap and joins with `get_run` and `get_run_events`; do not retry
   automatically after a failure.
5. Require server-side checks for three ready servers, RKE2 service health, node
   readiness, Cilium readiness, registry configuration, and kube-proxy presence.

If `vm` or `node-init` dependencies are unsatisfied, stop at the server's blocked
result. Do not repair those components from this skill.

SSH, registry, and proxy credentials must be `docker-secret://` references
provisioned on the MCP host. Never request their values or include them in replies.
