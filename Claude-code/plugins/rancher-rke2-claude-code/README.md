# Rancher RKE2 Claude Code Plugin 0.1.0

This plugin connects Claude Code to the remote Rancher/RKE2 MCP Server 0.11.16.
It provides one orchestration skill, five component skills, one diagnostic
skill, one runbook skill, and Claude Code-native safety hooks.

## MCP connection

The plugin declares a streamable HTTP server in `.mcp.json`. During installation
Claude Code asks for:

- `mcp_url`: HTTPS URL ending in `/mcp`;
- `mcp_token`: sensitive bearer token stored by Claude Code.

The MCP TLS certificate must be trusted by the Claude Code host. Never disable
certificate verification and never commit the bearer token.

## Usage

Start with the namespaced orchestration skill:

```text
/rancher-rke2-claude-code:rancher-rke2-orchestrate
```

Example request:

```text
Use /rancher-rke2-claude-code:rancher-rke2-orchestrate to call
get_capabilities and return only the server version and tool list. Do not make
changes.
```

For a full workflow, the skill validates configuration, shows the effective
configuration, creates an immutable plan, runs non-mutating preflight, and waits
for the exact approval text returned by the server before invoking a mutation.

## Security boundary

- Only the MCP Server may invoke Terraform, Ansible, SSH, Docker, Helm, kubectl,
  vSphere, or Rancher operations.
- Configuration accepts only `docker-secret://` references for credentials.
- The plugin never asks users to paste infrastructure passwords or private keys
  into the conversation.
- A TCP preflight pass is not authenticated vSphere, SSH, registry, or proxy
  success.
- Failed workflows are never retried automatically.
- Final success requires a fresh `get_run` result with state `SUCCEEDED`.

The authoritative tool inventory is returned by `get_capabilities`; do not call
tools absent from that response.
