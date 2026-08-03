# Rancher/RKE2 MCP Server 0.5.6

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

`start_run` is now available only for a VM-only plan. The server requires the
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
