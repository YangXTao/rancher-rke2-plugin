# Runtime secrets

Create these files on the dedicated MCP host; never commit or archive their values:

- `mcp_bearer_token`
- `control_host_password`
- `node_password`
- `vsphere_password`
- `rancher_bootstrap_password`
- `proxy_password`
- `registry_password`
Each file contains only the secret value with an optional trailing newline. Set ownership so
container UID/GID `10001:10001` can read the files and remove access for other users.

`proxy_password` and `registry_password` must exist because they are mounted by the
0.3.1 Compose service. They may be empty only when the configuration does not
reference them. A referenced empty file is reported as a failed preflight check.
