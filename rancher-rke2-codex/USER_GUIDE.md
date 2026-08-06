# Rancher RKE2 Automation - User Guide

This guide is for a new user who wants to run the full VMware-to-Rancher/RKE2
workflow (vm -> node-init -> local-rke2 -> rancher -> downstream -> audited
installation manual) from Codex through this plugin.

## Architecture

```text
Codex (plugin skills + hooks)
        |  HTTPS + Bearer token (streamable HTTP MCP)
        v
MCP Server (rancher-rke2-mcp-server, deployed by you)
        |  SSH (strict known_hosts, password from Docker Secret)
        v
Automation control host (only Docker; persistent rancher-rke2-control container)
        |  Terraform / Ansible / Helm / kubectl / scripts inside the container
        v
vSphere VMs, RKE2 clusters, Rancher, downstream nodes
```

The plugin is the client (skills + hooks + contract). The MCP Server is the only
infrastructure execution path. The plugin version tracks client changes only;
the compatibility contract is `contract_version` reported by
`get_capabilities`.

## Prerequisites

1. One Linux host (e.g. 192.168.2.179) with only Docker installed - the
   automation control host. All tooling is installed inside its control
   container; nothing else is installed on the host.
2. vCenter with a cloned VM template, a resource pool, datastore, and network.
3. A container image registry (Harbor) reachable from the VMs.
4. A machine to run the MCP Server (any host with Docker Compose; e.g.
   192.168.10.184) plus a TLS certificate (private CA is fine) and a bearer
   token.

## Deploy the MCP Server

```bash
git clone git@github.com:YangXTao/rancher-rke2-plugin.git
cd rancher-rke2-plugin/rancher-rke2-mcp-server
git switch --detach v0.10.16   # or the latest release tag
cp .env.example .env
```

Create the required Docker Secrets under `secrets/` (names match
`docker-secret://<name>` references):

```bash
install -d -m 700 secrets
umask 077
read -rsp "MCP bearer token: " V; printf '%s' "$V" > secrets/mcp_bearer_token; unset V
read -rsp "Control host password: " V; printf '%s' "$V" > secrets/control_host_password; unset V
read -rsp "Node password: " V; printf '%s' "$V" > secrets/node_password; unset V
read -rsp "vSphere password: " V; printf '%s' "$V" > secrets/vsphere_password; unset V
read -rsp "Rancher bootstrap password: " V; printf '%s' "$V" > secrets/rancher_bootstrap_password; unset V
read -rsp "Proxy password: " V; printf '%s' "$V" > secrets/proxy_password; unset V
read -rsp "Registry password: " V; printf '%s' "$V" > secrets/registry_password; unset V
```

Put the TLS certificate in `tls/server.crt` and `tls/server.key`, then start:

```bash
export DOCKER_API_VERSION=1.41
docker-compose --profile https up -d --build
```

Health: `curl -k https://<server>:8443/mcp` with the token returns 401 without
credentials (expected).

## Install the plugin

The plugin is distributed as a personal-marketplace plugin. After the
marketplace entry points at your local `rancher-rke2-codex` directory, install
from the snapshot:

```bash
codex plugin add rancher-rke2-codex@personal
```

Then restart Codex / open a new task so the updated skills and MCP tools load.

## Configure the connection

`rancher-rke2-codex/.mcp.json` points at the server:

```json
{
  "mcpServers": {
    "rancher-rke2": {
      "type": "http",
      "url": "https://<your-server>:8443/mcp",
      "bearer_token_env_var": "RANCHER_RKE2_MCP_TOKEN"
    }
  }
}
```

Set `RANCHER_RKE2_MCP_TOKEN` to the same value as
`secrets/mcp_bearer_token`, and trust the server's CA in the client. The
plugin's `pre_mcp_config` hook rejects plaintext credentials before they reach
the server.

## Run the full workflow

1. Open a new task and invoke `$rancher-rke2-orchestrate`.
2. Provide the YAML configuration (start from
   `assets/config.example.yaml`; replace addresses/resources; keep all
   `docker-secret://` references).
3. The skill calls `validate_config` and presents the `effective_config`
   (defaults expanded, including downstream registries) for your confirmation.
4. It builds the plan (`components: all` = the full pipeline), runs
   `preflight_plan`, and asks for the exact `APPROVE WORKFLOW <plan-id>` text.
5. It calls `start_workflow` once; the server runs vm -> node-init ->
   local-rke2 -> rancher -> downstream in fixed order and stops on the first
   failure. Single components remain available through `start_run`.
6. Progress is tracked with `get_run` / `get_run_events`. On failure the skill
   reads the component logs itself with `read_run_log`.
7. With `deliverables.installation_manual: true`, the audited 15-chapter
   installation manual is written to
   `runs/<run_id>/deliverables/RKE2_Rancher_Install_<run_id>.md` after success.

## Troubleshooting

- `preflight_plan` failed: the response lists the failing Secret/TCP check;
  repair and re-run validate/plan/preflight.
- A run failed: call `read_run_log(run_id, "local-rke2/ansible-playbook.log")`
  (or the component's log) - the server returns the redacted tail without
  asking anyone to paste files.
- `RANCHER_ARTIFACT_REQUIRED`: no successful Rancher run exists for the current
  config digest; run `rancher` first.

## Claude Code (future)

The MCP Server speaks standard streamable HTTP MCP, so a Claude Code user can
connect the same server with:

```bash
claude mcp add rancher-rke2 --transport http https://<your-server>:8443/mcp
export RANCHER_RKE2_MCP_TOKEN=...
```

The Codex-specific parts (skills, hooks, marketplace packaging) do not carry
over; a Claude-side operating guide would be published separately. The server,
contract, and tools are the shared execution core.
