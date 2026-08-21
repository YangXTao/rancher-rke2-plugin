# Control container image

This immutable image contains the stable OS and Ansible dependencies used by all
five workflow components. Terraform, Helm, kubectl, providers, charts, and RKE2
artifacts remain version-driven and are cached under the persistent `/software`
mount.

Release tags build and publish the image to
`ghcr.io/yangxtao/rancher-rke2-control:<version>`. Production and disconnected
sites should mirror that digest into their internal Harbor and set
`execution.container.image` to the Harbor reference. The MCP server will log in
with `registry.password_ref` and may pull that internal image even when
`downloads.mode: offline`; public registry pulls remain blocked in offline mode.

Users do not need to build the image per run. Building it locally is only a
developer fallback:

```sh
docker build -t harbor.example.internal/automation/rancher-rke2-control:0.12.0 .
docker push harbor.example.internal/automation/rancher-rke2-control:0.12.0
```
