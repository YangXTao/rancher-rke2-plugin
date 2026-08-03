# Runtime secrets

Create these files on the dedicated MCP host; never commit or archive their values:

- `mcp_bearer_token`
- `control_host_password`
- `node_password`
- `vsphere_password`
- `rancher_bootstrap_password`
Each file contains only the secret value with an optional trailing newline. Set ownership so
container UID/GID `10001:10001` can read the files and remove access for other users.

Optional proxy or registry authentication can use additional Docker Secrets and corresponding
`proxy_password_ref` or `password_ref` fields.
