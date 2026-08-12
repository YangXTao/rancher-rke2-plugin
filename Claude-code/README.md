# Rancher/RKE2 Claude Code 0.1.0

This directory is an independent Claude Code edition of the Rancher/RKE2
automation package. The existing Codex plugin and MCP Server outside this
directory are unchanged.

## Contents

- `plugins/rancher-rke2-claude-code/`: Claude Code plugin with eight skills,
  a remote HTTP MCP definition, client-side safety hooks, and the 0.11.16
  server contract.
- `rancher-rke2-mcp-server/`: a source copy of MCP Server 0.11.16 for deploying
  the same TLS-protected remote service.
- `.claude-plugin/marketplace.json`: marketplace catalog pinned to the
  `Claude-code0.1.0` Git tag.

## Install from GitHub

Prerequisites:

1. Install a current Claude Code release with plugin support.
2. Install Python 3 for the plugin's local safety hooks.
3. Trust the MCP Server CA in the operating system used by Claude Code. On
   Node.js-based installations, set `NODE_EXTRA_CA_CERTS` to the CA bundle when
   the private CA is not available through the platform trust store.
4. Keep the MCP bearer token out of the repository and chat history.

Add the tagged marketplace and install the plugin:

```text
/plugin marketplace add https://raw.githubusercontent.com/YangXTao/rancher-rke2-plugin/Claude-code0.1.0/Claude-code/.claude-plugin/marketplace.json
/plugin install rancher-rke2-claude-code@rancher-rke2-claude
```

Claude Code prompts for the HTTPS MCP URL and the bearer token. The token is a
sensitive `userConfig` value stored in Claude Code secure storage and is sent as
the `Authorization: Bearer ...` header. Run `/reload-plugins` if requested, then
check `/mcp` and invoke:

```text
/rancher-rke2-claude-code:rancher-rke2-orchestrate
```

For local development:

```powershell
claude --plugin-dir .\Claude-code\plugins\rancher-rke2-claude-code
```

## Safety model

- The MCP Server remains the only infrastructure execution path.
- YAML credentials must use `docker-secret://<name>` references.
- Preflight is non-mutating and checks only mounted Secret availability plus
  TCP reachability.
- `start_run` and `start_workflow` require the server-generated exact approval,
  a matching passed preflight, and an idempotency key.
- Claude Code hooks reject obvious plaintext credential fields, validate
  mutation call shape, persist a credential-free audit index under
  `${CLAUDE_PLUGIN_DATA}`, and block an unsupported success claim.
- Server-side validation is authoritative; hooks are an additional client-side
  guard and never replace server authorization.

## Validation

```powershell
claude plugin validate .\Claude-code
claude plugin validate .\Claude-code\plugins\rancher-rke2-claude-code
cd .\Claude-code\rancher-rke2-mcp-server
python -m pytest
```
