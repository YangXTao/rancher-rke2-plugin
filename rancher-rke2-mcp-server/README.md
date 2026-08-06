# Rancher/RKE2 MCP Server 0.10.13

## 0.10.13 remote run log reading

`read_run_log(run_id, path, lines)` reads a run log or artifact from the control
host workspace over SFTP and returns the tail with credential fragments
redacted, so failures can be diagnosed without the user pasting log files. The
contract version is now 0.10.10.

## 0.10.12 RancherLB preflight deferral for VM plans

When a plan includes the `vm` component, the RancherLB HTTPS (443) preflight
check is reported as `SKIPPED` instead of failing, because the LB is an intended
resource that does not exist yet. Full-pipeline workflows can therefore pass
preflight before VM creation.

## 0.10.11 standard Rancher service type default

The reference standard (non-`-ent`) Helm command no longer sets
`service.type=ClusterIP`, relying on the chart default (ClusterIP) instead;
`ingress.enabled=false` remains required because the chart default is true.

## 0.10.10 standard Rancher NodePort ownership fix

The reference Helm command and the rendered runbook now use
`service.type=ClusterIP` for standard (non-`-ent`) Rancher and create the
NodePort `rancher-nodeport` Service (30080) separately through kubectl, matching
the validated skill assets. Enterprise keeps the Helm-managed NodePort Service.

## 0.10.9 one-approval full pipeline workflow

`start_workflow` now accepts the full pipeline
`vm`, `node-init`, `local-rke2`, `rancher`, `downstream` with a single
`APPROVE WORKFLOW <plan-id>` text. After VM creation it waits for every node
TCP/22 readiness, then runs node-init, Local RKE2, Rancher (reusing the workflow
kubeconfig), and downstream (reusing the workflow Rancher private-CA artifact)
in the fixed order, stopping on the first failure. Single-component `start_run`
plans remain available. `render_runbook` accepts an optional `run_id` so the
manual can be rendered from an actual run when `installation_manual: ask`.
The contract version is now 0.10.9.

## 0.10.8 audited installation manual delivery

`render_runbook(plan_id, format, output_profile)` renders the 15-chapter
human-executable installation manual from an immutable plan and the expanded
effective configuration, runs the strict audit (ported from the validated
standalone skill), and writes the audited deliverable to the control host
workspace only when the audit passes. After a successful run, when
`deliverables.installation_manual: true` the same audited manual is generated
automatically under `runs/<run_id>/deliverables/` and announced with a
`RUNBOOK_DELIVERED` run event. The contract version is now 0.10.8.

## 0.10.7 effective configuration in validate_config

`validate_config` now returns an `effective_config` document alongside the
redacted preview: the submitted configuration with execution defaults expanded
(downstream rkeConfig, downstream registries, and Local RKE2 registry defaults,
including the edition-aware Rancher mirror), redacted and free of resolved
secrets. Clients present this complete configuration for explicit confirmation
before building a plan. `downstream_cluster.rkeConfig` is accepted as an alias
for `rke_config` so the user-facing camelCase form matches the reference.

## 0.10.6 downstream registration group variables

The downstream registration playbook now loads the generated
`../group_vars/all.yml` explicitly through `vars_files`, matching the Local RKE2
and Rancher playbooks. Without it, Ansible resolved role defaults before the
run-generated `automation_run_id` was available and registration stopped before
any node was registered.

## 0.10.5 edition-aware Rancher mirror for both clusters

The Local RKE2 registry defaults now use the same edition-based Rancher mirror
as downstream: `registry.rancher.cn` for `-ent` versions and
`registry.rancher.com` otherwise, with only the selected entry rendered. The
Docker Hub default mirror no longer adds a `hub/$1` rewrite in either cluster.

## 0.10.4 edition-aware Rancher mirror default

The reference downstream registries no longer add a Docker Hub `hub/$1`
rewrite, and the Rancher mirror hostname follows the Rancher edition:
`registry.rancher.cn` for `-ent` versions and `registry.rancher.com` otherwise.
The Rancher mirror entry carries no rewrites, matching the validated reference.

## 0.10.3 versions.tf quoting fix

The downstream `versions.tf` renderer wrapped the already-quoted template
placeholder in another set of quotes (`""13.1.4""`), which is invalid HCL.
The rendered file now contains exactly `version = "13.1.4"`.

## 0.10.2 reference downstream registries by default

When `downstream_cluster.registries` is omitted, the executor now uses the
validated reference defaults: Harbor registry enabled with the `myharbor-auth`
secret created by the Rancher phase, plus mirrors for Docker Hub,
dp.apps.rancher.io, GHCR, Kubernetes registries, Quay, Rancher, and SUSE.
An explicitly submitted registries object remains authoritative as-is, including
an explicit `enabled: false`.

## 0.10.1 legacy config digest compatibility

0.10.0 added optional `downstream_cluster.rke_config` and `registries` fields.
Default values for unset fields no longer enter the normalized configuration, so
an unchanged YAML produces the same config digest as 0.9.x and the
Rancher-before-downstream artifact lookup still finds runs validated before the
upgrade. Explicitly set values remain preserved in the digest and execution.

## 0.10.0 downstream custom cluster execution

`start_run` now supports the single `downstream` component after a successful
`rancher` run using the same validated configuration. The executor reuses the
durable Rancher private-CA certificate artifact from that prior run, then runs
the validated downstream skill assets inside the existing control container:
exact `rancher/rancher2` Provider mirror, Rancher `/ping` verification and admin
API token, `rancher2_cluster_v2` creation with the user-customizable
`downstream_cluster.rke_config` and `registries`, and role-ordered registration
of every configured control-plane and worker node. Registration starts the first
control-plane and first worker together, then processes remaining control-planes
and remaining workers serially, and requires every node to report Ready.
`downstream_cluster.rke_config` and `registries` are new optional configuration
fields; omitted rkeConfig keys fall back to the validated reference defaults.
Preflight for a downstream plan adds a non-mutating TCP check against the
RancherLB HTTPS endpoint (443).

## 0.9.9 NodePort verification target

Both editions verify `rancher-nodeport` on port 30080. Enterprise Helm owns
that Service, while standard automation owns it.

## 0.9.8 Enterprise NodePort field ownership migration

For an existing Helm-owned Enterprise `rancher-nodeport` Service, the server
uses server-side apply with field manager `helm` and force-conflicts only for
the expected `80/TCP` port name (`http-80`). This transfers that one legacy
field without deleting, recreating, or changing either NodePort.

## 0.9.7 Enterprise NodePort ownership migration

Before an Enterprise Helm reconciliation, the server inspects any existing
`rancher-nodeport` Service and removes only its legacy client-side apply
annotation when that Service is confirmed to belong to the `rancher` Helm
release. It never deletes or recreates the Service.

## 0.9.6 edition-specific NodePort ownership

Enterprise Rancher uses Helm to manage the release-owned `rancher` NodePort
Service on port 30080. Standard Rancher keeps its Helm Service as ClusterIP and
uses the separately rendered, Ansible-managed `rancher-nodeport` Service.

## 0.9.5 RancherLB private-CA verification

The RancherLB HTTPS verification defaults to private-CA mode when the generated
inventory does not explicitly carry `rancher_private_ca`, matching the supported
certificate flow without changing any installation resources.

## 0.9.4 direct Kubernetes API access after downloads

Rancher artifact preparation retains `DOWNLOAD_PROXY_URL`, while the Rancher
Ansible/kubectl phase now clears HTTP(S) proxy variables. Internal Kubernetes
API calls therefore connect directly to the management server instead of being
routed through an external download proxy.

## 0.9.3 Local RKE2 kubeconfig artifact path

Rancher now reuses the successful Local RKE2 kubeconfig from the durable
`runs/<local-run>/kubeconfig/rke2.yaml` path. Failed Rancher runs also return
their safe control-host log locations in the run state and event stream.

## 0.9.2 versioned Rancher Chart manifest

The Rancher executor now supplies the exact versioned chart checkpoint path
`/software/rancher/rancher-<version>.tgz.manifest`, matching both the standard
and Enterprise artifact preparation scripts.

## 0.9.1 Local RKE2 artifact lookup fix

The Rancher prerequisite lookup now reads the persisted `state` inside each
run record, matching the existing SQLite schema. A valid successful Local RKE2
run is therefore found correctly before Rancher execution is queued.

## 0.9.0 approval-gated Rancher component

`start_run` now supports the single `rancher` component after a successful
`local-rke2` run using the same validated configuration. The executor reuses
the durable Local RKE2 kubeconfig from that prior run, then runs the validated
Rancher skill assets in the existing control container: Enterprise/standard
artifacts, private-CA certificate, Helm install, NodePort `30080`, registry
auth and the independent RancherLB Nginx endpoint. The YAML remains unchanged;
no secret values or mutable artifact paths enter the MCP SQLite database.

## 0.8.4 whitespace-safe Local RKE2 health verification

The final Local RKE2 node health check now tokenizes the `kubectl get nodes`
table with `awk` and counts rows whose second column is exactly `Ready`. This
accepts tabs and variable-width spaces without weakening the three-node health
requirement.

## 0.8.3 Local RKE2 explicit runtime variables

The Local RKE2 playbook now explicitly loads its generated runtime configuration
through `vars_files`. This prevents role defaults from masking the configured RKE2
version or other generated settings after inventory parsing.

## 0.8.2 Local RKE2 group-variable guard

The generated Local RKE2 group variables are now written to the exact Ansible
`group_vars/all.yml` path. The pre-play inventory guard also verifies that the
configured RKE2 management version reaches every management host, preventing a
default-empty variable from reaching the playbook.

## 0.8.1 Local RKE2 inventory guard

The Local RKE2 executor now writes the generated Ansible inventory to the exact
configured `inventory/hosts.yml` path. Before the playbook starts, it runs
`ansible-inventory --list` and requires exactly three `management_servers` hosts.
An empty or unparsable inventory is therefore a failed run, never a successful
no-op.

## 0.8.0 one-approval VM-to-Local-RKE2 workflow

`start_workflow` accepts only immutable ordered `vm` → `node-init` or `vm` →
`node-init` → `local-rke2` plans and the exact `APPROVE WORKFLOW <plan-id>` text.
It creates the VMs, waits up to five minutes for every node TCP/22 endpoint to
become reachable, runs node-init, and — for the three-stage plan — installs the
three-server Local RKE2 cluster from the validated control-container assets. Any
component or readiness failure stops the workflow without an automatic retry.
Existing `start_run` remains available for one supported component at a time.

## 0.5.6 timestamped run IDs

New runs are assigned an operator-readable UTC timestamp and a random suffix, for
example `run-20260803T085142Z-a1b2c3d4e5f6`. Existing run IDs remain readable and
unchanged.

## 0.5.5 persistent vSphere Provider mirror

The VM runner now follows the validated VM Skill provider-cache workflow. It
downloads the exact configured `vmware/vsphere` archive and SHA256SUMS through
the download proxy only, verifies them, and persists the archive, checksum,
unpacked filesystem mirror, manifest, plugin cache, and Terraform CLI config under
`/software/terraform`. Terraform then uses that filesystem mirror with proxy
variables cleared. Offline mode requires the same archive and SHA256SUMS paths to
be pre-positioned and never accesses the network.

## 0.5.4 vCenter connection proxy isolation

The VM executor now writes two separate protected environment files.  The
dependency installer receives proxy variables only for `apt-get` and Terraform
archive downloads; Terraform itself receives only the vSphere credentials and
explicitly clears proxy environment variables before contacting vCenter.  This
prevents a configured download proxy from intercepting the vSphere SOAP client.

## 0.5.3 VM Terraform workspace fix

Terraform source assets are now uploaded directly into the VM run directory where
Terraform executes; nested asset directories are not Terraform module directories.
After `terraform apply`, the runner verifies that state contains exactly the intended
number of `vsphere_virtual_machine.vm` instances. An empty apply is therefore a
failed VM run, not a successful deployment.

## 0.5.2 control-container package fix

The VM control container continues to bind-mount `/etc/localtime` read-only. On a
pristine Ubuntu image, installing Python can install `tzdata` for the first time;
its post-install action attempts to replace that mount and fails. The dependency
installer now completes only that interrupted package configuration with the
vendor post-install action temporarily suppressed, restores the action immediately,
then holds `tzdata`. Terraform is started only after this succeeds.

## 0.5.1 VM execution

`start_run` now executes a VM-only plan after the existing digest, preflight,
approval-text, and idempotency checks. It connects to `execution.control_host` with
password authentication only after validating its SSH host key against the mounted
`secrets/control_host_known_hosts` file. It uploads a Terraform VM bundle to the
configured persistent workspace, creates or reuses the configured control container
without mounting the host Docker socket, runs the ping-only IP conflict check, then
runs `terraform init` and `terraform apply` inside that container.

The server stores only run state and artifact paths. Secret values are resolved from
Docker Secrets at execution time and are never returned through MCP, stored in
SQLite, or added to run events. Terraform state and protected execution logs remain
on the control host under `<workspace>/runs/<run_id>/vm`.

## 0.4.0 approval-gated run state

`preflight_plan(plan_id)` verifies that referenced Docker Secret files are mounted
and non-empty, then opens short-lived TCP connections to the control host, vCenter,
registry, and optional proxy. When the plan includes the `vm` component, its target
nodes are still intended resources, so their SSH checks are reported as `SKIPPED`.
For plans that do not include `vm`, target node SSH TCP checks are performed. It does **not** authenticate, run
SSH commands, call vSphere APIs, or change any infrastructure. Results are saved
without Secret values and can be read with `get_preflight(preflight_id)`.

`start_run` is now available only for an approved single-component plan. The server requires the
matching configuration digest, an unexpired `PASSED` preflight, the exact plan
approval text, and an idempotency key. It then persists a `BLOCKED` run with
structured events. Version 0.4.0 intentionally has **no execution backend**: it
does not SSH, call Terraform, authenticate to vSphere, or change infrastructure.

The Compose service now mounts `proxy_password` and `registry_password` too. Create
both files before starting the 0.4.0 container; use an empty file only when that
Secret is not referenced by the configuration.

这是一个远程 Docker 部署的无破坏性 MCP Server，用于校验 Rancher/RKE2
配置并生成不可执行的静态计划。

## 工具

| 工具 | 作用 | 基础设施副作用 |
|---|---|---|
| `get_capabilities` | 返回版本、组件和安全边界 | 无 |
| `get_config_schema` | 返回配置 JSON Schema | 无 |
| `validate_config` | 校验配置并保存 Secret 引用 | 无 |
| `build_plan` | 生成不可执行的部署计划 | 无 |
| `get_plan` | 读取已生成的计划 | 无 |

## 凭据边界

- 配置只接受 `docker-secret://<name>` 引用。
- `password`、`token`、`private_key` 等明文字段会在写入 SQLite 前被拒绝。
- SQLite 只保存规范化配置、Secret 引用、摘要和计划，不保存 Secret 值。
- 规划工具不会解析 `/run/secrets`；Secret 解析器留给后续预检或执行工具使用。
- Bearer Token 从 `/run/secrets/mcp_bearer_token` 读取。
- MCP容器不直接发布8787端口；远程访问仅通过HTTPS代理。

## 准备Secret

在专属服务器进入项目目录：

```bash
install -d -m 700 secrets
umask 077
```

逐个静默输入：

```bash
read -rsp "MCP bearer token: " VALUE
printf '%s' "$VALUE" > secrets/mcp_bearer_token
unset VALUE
echo

read -rsp "Control host password: " VALUE
printf '%s' "$VALUE" > secrets/control_host_password
unset VALUE
echo

read -rsp "Node password: " VALUE
printf '%s' "$VALUE" > secrets/node_password
unset VALUE
echo

read -rsp "vSphere password: " VALUE
printf '%s' "$VALUE" > secrets/vsphere_password
unset VALUE
echo

read -rsp "Rancher bootstrap password: " VALUE
printf '%s' "$VALUE" > secrets/rancher_bootstrap_password
unset VALUE
echo
```

容器以 `10001:10001` 运行：

```bash
chown -R 10001:10001 secrets
chmod 700 secrets
chmod 600 secrets/*
```

## 准备TLS

将受Codex客户端信任的证书放到：

```text
tls/server.crt
tls/server.key
```

证书SAN必须包含插件URL使用的DNS名称或IP。不要关闭客户端证书校验。

## 启动

复制环境文件并按需调整：

```bash
cp .env.example .env
export DOCKER_API_VERSION=1.41
docker-compose --profile https up -d --build
```

默认HTTPS地址：

```text
https://<服务器地址>:8443/mcp
```

检查：

```bash
docker-compose ps
docker-compose logs --tail=100 rancher-rke2-mcp
docker-compose logs --tail=100 rancher-rke2-mcp-proxy
```

## 配置示例

参见 `examples/config.example.yaml`。核心形式：

```yaml
execution:
  control_host:
    address: "192.0.2.10"
    username: root
    password_ref: "docker-secret://control_host_password"

vsphere:
  username: "administrator@vsphere.local"
  password_ref: "docker-secret://vsphere_password"
```

不要把Secret值写进YAML、聊天消息、`.env`、日志或工具参数。
