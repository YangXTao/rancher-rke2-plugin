# Rancher/RKE2 MCP Automation

This repository contains the 0.12.0 release for a remote, TLS-protected
Rancher/RKE2 planning, preflight, and approval-gated run-state service and its Codex Plugin.

## Contents

- `rancher-rke2-mcp-server/`: remote Docker-deployed MCP Server. It validates
  reference-only configuration, creates immutable plans, executes approved
  workflows, and collects bounded read-only runtime diagnostics.
- `.agents/plugins/plugins/rancher-rke2-codex/`: Codex Plugin with orchestration
  skills, MCP contract, and client-side plaintext-credential blocking hooks,
  published through the repository marketplace (`.agents/plugins/marketplace.json`).

## Security boundary

Runtime secrets, TLS private keys, SQLite state, and local caches are excluded
from source control. Configuration uses `docker-secret://<name>` references;
never commit secret values.

## Baseline behavior

Version 0.12.0 exposes planning, preflight, approval-gated execution, run-state,
runbook, and read-only diagnostic tools:
`get_capabilities`, `get_config_schema`, `validate_config`, `build_plan`,
`get_plan`, `preflight_plan`, `get_preflight`, `render_runbook`, `start_run`,
`start_workflow`, `get_run`, `get_run_events`, and `collect_diagnostics`.

When a plan includes `vm`, its target nodes are treated as not yet created and
their SSH checks are skipped. vCenter, registry, control-host, and optional proxy
TCP checks remain required.

`start_run` and `start_workflow` require exact approval text and a matching passed
preflight. `collect_diagnostics` authenticates to the declared control host but
runs only fixed read-only evidence commands and redacts returned text.

Every component now invokes a shared `ControlContainerManager` before execution.
It installs the pinned static Docker distribution on the SSH control host when
needed, pulls or imports the configured immutable control image, creates or
validates the persistent container, and records phase logs below the run's
`control-container/` artifact directory. Offline mode may pull only from the
configured internal registry when explicitly enabled; it never falls back to a
public registry. Component bundles continue to be uploaded automatically.
