# Runtime secrets

Create these files on the dedicated MCP host; never commit or archive their values:

- `mcp_bearer_token`
- `control_host_password`
- `node_password`
- `vsphere_password`
- `rancher_bootstrap_password`
- `proxy_password`
- `registry_password`
- `control_host_known_hosts`
Each file contains only the secret value with an optional trailing newline. Set ownership so
container UID/GID `10001:10001` can read the files and remove access for other users.

`proxy_password` and `registry_password` must exist because they are mounted by the
0.4.0 Compose service. They may be empty only when the configuration does not
reference them. A referenced empty file is reported as a failed preflight check.

`control_host_known_hosts` is not a credential. It must contain the SSH public host
key entry for `execution.control_host.address` (use `[address]:port` form for a
non-22 port). Version 0.5.3 refuses to execute a VM plan without this file and does
not use trust-on-first-use host-key acceptance.
