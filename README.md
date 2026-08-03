# Rancher/RKE2 MCP Automation

This repository contains the 0.2.0 baseline for a remote, TLS-protected
Rancher/RKE2 MCP planning service and its Codex Plugin.

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

Version 0.2.0 exposes only read-only planning tools:
`get_capabilities`, `get_config_schema`, `validate_config`, `build_plan`, and
`get_plan`.
