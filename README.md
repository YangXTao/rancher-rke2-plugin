# Rancher/RKE2 MCP Automation

This repository contains the 0.4.0 release for a remote, TLS-protected
Rancher/RKE2 planning, preflight, and approval-gated run-state service and its Codex Plugin.

## Contents

- `rancher-rke2-mcp-server/`: remote Docker-deployed MCP Server. It validates
  reference-only configuration and creates non-executable plans.
- `.agents/plugins/plugins/rancher-rke2-codex/`: Codex Plugin with orchestration
  skills, MCP contract, and client-side plaintext-credential blocking hooks,
  published through the repository marketplace (`.agents/plugins/marketplace.json`).

## Security boundary

Runtime secrets, TLS private keys, SQLite state, and local caches are excluded
from source control. Configuration uses `docker-secret://<name>` references;
never commit secret values.

## Baseline behavior

Version 0.4.0 exposes planning, preflight, and approval-gated run-state tools:
`get_capabilities`, `get_config_schema`, `validate_config`, `build_plan`,
`get_plan`, `preflight_plan`, `get_preflight`, `start_run`, `get_run`, and
`get_run_events`.

When a plan includes `vm`, its target nodes are treated as not yet created and
their SSH checks are skipped. vCenter, registry, control-host, and optional proxy
TCP checks remain required.

`start_run` accepts only a VM-only plan with an exact approval and matching passed
preflight. It records a durable blocked run in 0.4.0; no remote executor is present.
