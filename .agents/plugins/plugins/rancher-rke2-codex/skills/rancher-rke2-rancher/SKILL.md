---
name: rancher-rke2-rancher
description: Plan, inspect, execute, resume, or diagnose Rancher installation and publication through the rancher-rke2 MCP server. Use for private CFSSL certificates, standard or Enterprise artifacts, Helm installation, NodePort 30080, Harbor authentication, Rancher bootstrap, or the independent RancherLB Docker and Nginx L7 proxy.
---

# Rancher RKE2 Rancher

Operate only component `rancher` through the domain MCP server. Never invoke
CFSSL, Helm, kubectl, Docker, Nginx, curl, or SSH directly.

Call `get_capabilities` first. Treat its tool list as authoritative. If
`preflight_plan` is available, call it after `get_plan`; stop and report failed
Secret or TCP checks. If `start_run` is unavailable, stop after the plan and
preflight result and state that the current server is preflight-only. Later execution steps apply only when their
named tools are available.

## Workflow

1. Validate configuration and build a plan with
   `target_components: ["rancher"]`.
2. Present Rancher edition/version, artifact mode, certificate source and IP SAN,
   replica count, NodePort, registry authentication, and RancherLB publication.
3. Obtain exact plan approval before calling `start_run`.
4. Track with `get_run` and `get_run_events`. A successful Local RKE2 run using
   the same configuration is an enforced prerequisite; the server reuses its
   kubeconfig artifact without requiring a YAML path.
5. Require server verification for certificate chain, Rancher workloads, service
   on NodePort 30080, Rancher API readiness, and RancherLB HTTPS access.

If Local RKE2 is not verified, report `BLOCKED`; do not install or repair Local
RKE2 from this component skill. Bootstrap, registry, proxy, and SSH credentials
must be `docker-secret://` references; never ask for or expose their values.
