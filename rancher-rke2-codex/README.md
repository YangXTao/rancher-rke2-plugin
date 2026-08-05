# Rancher RKE2 Codex Plugin 0.8.4

## 0.8.4 whitespace-safe Local RKE2 health verification

The final Local RKE2 health check counts the whitespace-delimited `Ready` status
column rather than relying on a fragile table regular expression.

## 0.8.3 Local RKE2 explicit runtime variables

The Local RKE2 playbook explicitly loads the generated runtime configuration, so
role defaults cannot mask the configured RKE2 version after inventory parsing.

## 0.8.2 Local RKE2 group-variable safety guard

The server verifies that the configured RKE2 version is present for all three
management hosts before starting the playbook. Missing group variables are a
failed run, never a default-valued installation attempt.

## 0.8.1 Local RKE2 inventory safety guard

Local RKE2 execution fails before the playbook starts unless its generated
inventory contains exactly three management servers. A no-host Ansible run can
therefore never be reported as a successful Local RKE2 installation.

## 0.8.0 VM-to-Local-RKE2 workflow

The primary orchestration Skill uses the Rancher/RKE2 MCP Server as the only
infrastructure execution path. After `validate_config`, `build_plan`, and a
passing `preflight_plan`, an exact `APPROVE WORKFLOW <plan-id>` can start either
of the immutable ordered workflows:

- `vm` → `node-init`
- `vm` → `node-init` → `local-rke2`

The three-stage workflow creates the VMs, waits for every node TCP/22 endpoint,
initializes the nodes, and installs the fixed three-server Local RKE2 cluster from
the validated control-container assets. A failed component stops downstream work;
there is no automatic retry.

`start_run` remains available for one component at a time (`vm`, `node-init`, or
`local-rke2`). A TCP preflight pass is reachability only, not SSH, vSphere,
registry, or proxy authentication.

这是 Rancher/RKE2 自动化体系的 Codex 客户端Plugin。它通过一个领域 MCP
Server完成规划、未来执行、状态跟踪、诊断和审计。

现有 `C:\Users\tg\.codex\skills\rancher-rke2-*` 不属于本Plugin，也不会被修改。

## 内容

- 1个主编排Skill；
- 5个薄组件Skill；
- 1个只读诊断Skill；
- 1个操作手册Skill；
- MCP连接配置、工具契约和客户端Hooks。

## 安全约束

- MCP URL必须使用HTTPS。
- 配置只接受 `docker-secret://<name>` 引用。
- Plugin不得请求用户把密码、Token或私钥粘贴到对话中。
- 发现旧版明文凭据字段时，停止工具调用并指导用户创建Docker Secret。
- Secret值由MCP专属服务器带外配置，不进入工具参数、SQLite、计划或日志。

## 接入

1. 部署 `rancher-rke2-mcp-server` 0.4.0。
2. 用受Windows信任的证书启用HTTPS。
3. 确认 `.mcp.json` 地址与证书SAN一致。
4. 在Codex客户端设置 `RANCHER_RKE2_MCP_TOKEN`。
5. 安装或重新安装Plugin后，新建任务以加载更新后的Skills和MCP工具。
6. 从 `$rancher-rke2-orchestrate` 开始校验配置并生成计划。

## 当前边界

当前MCP Server只提供5个无破坏性规划工具，不包含实际Terraform、Ansible、
SSH、Docker、Helm或kubectl执行。真正的授权、状态机、幂等、取消、重试和销毁
保护必须继续由服务端实现，客户端Hooks只是辅助防线。
