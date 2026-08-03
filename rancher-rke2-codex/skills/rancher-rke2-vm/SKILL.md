---
name: rancher-rke2-vm
description: Plan, inspect, execute, resume, or destroy the VMware vSphere VM component through the rancher-rke2 MCP server. Use for management, RancherLB, or downstream VM creation; vSphere configuration; IP conflict checks; Terraform Provider cache behavior; VM-stage status; or the explicitly confirmed destruction of the complete VM set owned by one run.
---

# Rancher RKE2 VM

Operate only component `vm` through the domain MCP server. Never invoke Terraform,
vSphere, ping, SSH, or Docker directly.

Call `get_capabilities` first. Treat its tool list as authoritative. If
`preflight_plan` is available, call it after `get_plan`; stop and report failed
Secret or TCP checks. If `start_run` is unavailable, stop after the plan and
preflight result and state that the current server is preflight-only. The execution and destruction steps below
apply only when their named tools are available.

## Create or Resume

1. Validate configuration with `validate_config`.
2. Build a plan with `target_components: ["vm"]`.
3. Present the server's redacted VM inventory, IP-conflict findings, Provider/cache
   requirements, and planned creates or updates.
4. Obtain the exact plan approval, then call `start_run` with a stable idempotency
   key. Use `resume_run` only when the user identifies an existing `run_id`.
5. Verify with `get_run`; do not infer success from Terraform text alone.

Planning, validation, inventory, and status requests are read-only.

## Destroy

Destruction is a separate workflow:

1. Read the target run with `get_run`.
2. Build or retrieve a destruction plan that identifies the selected Terraform
   State and its complete managed VM set.
3. Show the exact VM list and consequences.
4. Require this exact confirmation:
   `CONFIRM DESTROY run_id=<run_id> ALL TERRAFORM-STATE VMS`
5. Call `destroy_vm_set` with `run_id`, `plan_id`, the exact confirmation, and a
   stable idempotency key.

Do not support partial per-VM destruction, guessed State paths, or confirmations
copied from another run.

## Credentials

Use only `docker-secret://` references for vSphere, control-host, node, proxy, and
registry credentials. Do not ask for secret values in chat or send plaintext
credential fields to `validate_config`.
