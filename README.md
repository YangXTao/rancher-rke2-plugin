# Rancher/RKE2 MCP Automation

This repository contains the 0.3.1 release for a remote, TLS-protected
Rancher/RKE2 MCP planning and non-mutating preflight service and its Codex Plugin.

## Contents

- `rancher-rke2-mcp-server/`: remote Docker-deployed MCP Server. It validates
  reference-only configuration and creates non-executable plans.
- `rancher-rke2-codex/`: Codex Plugin with orchestration skills, MCP contract,
  and client-side plaintext-credential blocking hooks.

## Security boundary

Runtime secrets, TLS private keys, SQLite state, and local caches are excluded
from source control. Configuration uses `docker-secret://<name>` references;
never commit secret values.

## Baseline behavior

Version 0.3.1 exposes non-mutating planning and preflight tools:
`get_capabilities`, `get_config_schema`, `validate_config`, `build_plan`,
`get_plan`, `preflight_plan`, and `get_preflight`.

When a plan includes `vm`, its target nodes are treated as not yet created and
their SSH checks are skipped. vCenter, registry, control-host, and optional proxy
TCP checks remain required.
