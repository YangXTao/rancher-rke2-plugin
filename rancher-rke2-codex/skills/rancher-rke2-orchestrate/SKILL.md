---
name: rancher-rke2-orchestrate
description: Orchestrate the complete VMware-to-Rancher/RKE2 workflow through the rancher-rke2 MCP server. Use for help, configuration discovery, full or multi-component plans, approval, execution, resume, status, cross-component coordination, shared run IDs, online or offline preparation, or an end-to-end result summary. This is the primary entry point when more than one component is involved.
---

# Rancher RKE2 Orchestrate

Use the domain MCP server as the only infrastructure execution path. Do not run
Terraform, Ansible, SSH, Docker, Helm, kubectl, or legacy skill scripts directly.

## Workflow

1. Call `get_capabilities` when server compatibility is unknown.
   Treat its tool list as authoritative. Never call an unavailable tool.
2. Obtain the user's configuration. Call `get_config_schema` if fields are missing.
3. Require `https`, `plaintext_credentials_compatible: false`, and
   `docker-secret` reference support. Do not send a configuration containing
   plaintext credential fields.
4. Call `validate_config`. Show validation errors and the server's safe preview;
   never request, resolve, or reproduce secret values.
5. Resolve `target_components`. For a full run use this fixed order:
   `vm`, `node-init`, `local-rke2`, `rancher`, `downstream`.
6. Call `build_plan` with the returned `config_digest`.
7. Call `get_plan` and present scope, prerequisites, warnings, destructive effects,
   run mode, and the exact approval text. Planning is read-only.
8. If `preflight_plan` is available, call it with the returned `plan_id`, then call
   `get_preflight` when a durable readback is needed. Present every failed check.
   Preflight checks Docker Secret availability and TCP reachability only; it never
   authenticates, executes commands, or changes infrastructure. If a plan includes
   `vm`, node SSH checks must be `SKIPPED` because those nodes are intended to be
   created; control-host, vCenter, registry, and optional proxy checks remain in scope.
9. If preflight is `FAILED`, stop. The user must repair the reported prerequisite,
   then validate and build a fresh plan before running preflight again.
10. If `start_run` is unavailable, stop after returning the plan and preflight result
   and state that this server version is preflight-only. Do not ask for execution approval.
11. In server 0.5.4, `start_run` accepts only a VM-only plan. It validates a matching
   current `PASSED` preflight, exact plan approval text, stable idempotency key, and
   a server-mounted `control_host_known_hosts` file. It then queues Terraform on the
   declared SSH control host, inside the configured control container. This is a real
   infrastructure mutation; obtain explicit approval before calling it.
12. Call `start_run` with `plan_id`, `config_digest`, `preflight_id`, the exact `approval_text`, and
   a stable `idempotency_key`.
13. Track progress only through available tools such as `get_run` and
   `get_run_events`. Use `resume_run` only for the same persisted `run_id`.
14. Before declaring success, call `get_run` again and require run state
    `SUCCEEDED` plus successful required component verification.

When the user asks for help, a schema, an explanation, a plan, or status, remain
read-only. Do not start a run unless the user explicitly authorizes execution.

## Rules

- Treat the MCP Server's state, policy checks, and artifacts as authoritative.
- A component cannot be silently skipped or reordered.
- Never invent a `run_id`, `plan_id`, digest, event, artifact, or success result.
- Accept only `*_ref` fields using `docker-secret://<name>`. If a user supplies a
  plaintext password, token, private key, or credential-bearing proxy URL, do not
  call the server. Explain how to create the Docker Secret and replace the field.
- Secret values are provisioned out of band on the MCP host. Never ask the user to
  paste them into chat or include them in tool arguments, plans, events, or logs.
- If a plan expires or the configuration changes, validate and build a new plan.
- Never reinterpret a TCP pass as successful SSH, vSphere, Registry, or proxy
  authentication. Those authenticated checks belong to a later server version.
- For a failed run, report the failed component and checkpoint. Do not auto-retry.
- For VM destruction, use `$rancher-rke2-vm`; never reinterpret a normal approval
  as destruction approval.
- If the final verified run state is `SUCCEEDED`, include the exact line
  `RANCHER_RKE2_FINAL: SUCCEEDED`. Never emit it for partial or inferred success.

## Result

Return the `run_id`, final state, component-state table, important warnings, and
artifact locations. Keep operational evidence concise and credential-free.
