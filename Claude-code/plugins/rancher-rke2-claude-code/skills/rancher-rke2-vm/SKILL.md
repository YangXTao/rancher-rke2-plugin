---
name: rancher-rke2-vm
description: Plan, inspect, execute, or diagnose the VMware vSphere VM component through the rancher-rke2 MCP server. Use for management, RancherLB, or downstream VM creation; vSphere configuration; IP conflict checks; Terraform Provider cache behavior; or VM-stage status.
---

# Rancher RKE2 VM

Operate only component `vm` through the domain MCP server. Never invoke Terraform,
vSphere, ping, SSH, or Docker directly.

Call `get_capabilities` first. Treat its tool list as authoritative. If
`preflight_plan` is available, call it after `get_plan`; stop and report failed
Secret or TCP checks. If `start_run` is unavailable, stop after the plan and
preflight result and state that the current server is preflight-only. The execution and destruction steps below
apply only when their named tools are available.

## Create and inspect

1. Validate configuration with `validate_config`.
2. Build a plan with `target_components: ["vm"]`.
3. Present the server's redacted VM inventory, IP-conflict findings, Provider/cache
   requirements, and planned creates or updates.
4. Show the exact plan approval text and explain that this invokes
   Terraform against vCenter. Only after explicit user approval, call `start_run`
   with the matching `preflight_id` and a stable idempotency key.
5. Track `QUEUED`, then `SUCCEEDED` or `FAILED` with `get_run` and `get_run_events`.
   Do not infer VM creation merely from a queued run; require `SUCCEEDED`.

Planning, validation, inventory, and status requests are read-only.

Server 0.11.16 does not expose a VM destruction tool. Never reinterpret a normal
plan approval as destruction approval and never run Terraform directly to destroy
resources.

## Credentials

Use only `docker-secret://` references for vSphere, control-host, node, proxy, and
registry credentials. Do not ask for secret values in chat or send plaintext
credential fields to `validate_config`.
