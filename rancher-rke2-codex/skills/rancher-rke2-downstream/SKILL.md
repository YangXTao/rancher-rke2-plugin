---
name: rancher-rke2-downstream
description: Plan, inspect, execute, resume, or diagnose a Rancher-managed downstream custom RKE2 cluster through the rancher-rke2 MCP server. Use for Rancher API token handling, rancher2 Provider caching, rancher2_cluster_v2 creation, Harbor authentication, role-specific registration, ordered node registration, or downstream readiness.
---

# Rancher RKE2 Downstream

Operate only component `downstream` through the domain MCP server. Never invoke
Terraform, curl, SSH, registration commands, kubectl, or Docker directly.

Call `get_capabilities` first. Treat its tool list as authoritative. If
`preflight_plan` is available, call it after `get_plan`; stop and report failed
Secret or TCP checks. If `start_run` is unavailable, stop after the plan and
preflight result and state that the current server is preflight-only. Later execution steps apply only when their
named tools are available.

## Workflow

1. Validate configuration and build a plan with
   `target_components: ["downstream"]`.
2. Present the cluster name, Provider version/cache, registry settings, control
   plane and worker inventory, and exact registration order:
   `first-controlplane`, `first-worker`, `remaining-controlplanes`,
   `remaining-workers`.
3. Obtain exact plan approval before calling `start_run`.
4. Track Rancher resource creation and node registration with `get_run` and
   `get_run_events`. Use `resume_run` only for the same persisted run.
5. Require server verification for the Rancher cluster state, expected node roles,
   Kubernetes readiness, and registry configuration.

If Rancher or prerequisite nodes are not verified, report the blocked dependency.
Never register nodes out of order to bypass it.

Rancher, registry, proxy, and SSH credentials must be `docker-secret://`
references provisioned on the MCP host. Never request or reproduce their values.
