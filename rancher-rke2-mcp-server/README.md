# Rancher/RKE2 MCP Server 0.3.1

## 0.3.1 non-mutating preflight

`preflight_plan(plan_id)` verifies that referenced Docker Secret files are mounted
and non-empty, then opens short-lived TCP connections to the control host, vCenter,
registry, and optional proxy. When the plan includes the `vm` component, its target
nodes are still intended resources, so their SSH checks are reported as `SKIPPED`.
For plans that do not include `vm`, target node SSH TCP checks are performed. It does **not** authenticate, run
SSH commands, call vSphere APIs, or change any infrastructure. Results are saved
without Secret values and can be read with `get_preflight(preflight_id)`.

The Compose service now mounts `proxy_password` and `registry_password` too. Create
both files before starting the 0.3.1 container; use an empty file only when that
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
