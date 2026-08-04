# Rancher RKE2 Codex Plugin 0.4.0

## 0.4.0 approval-gated run workflow

After `build_plan` and `get_plan`, the orchestration Skill calls
`preflight_plan`. A failed result stops the workflow before future execution tools
could be considered. A passed TCP check proves reachability only; it is not an SSH,
vSphere, registry, or proxy authentication result.

If a plan includes `vm`, node SSH checks are intentionally skipped because the
nodes are expected to be created by that plan. Control-host, vCenter, registry,
and optional proxy checks still run.

For a single-component plan, `start_run` requires the exact plan approval text, matching
configuration digest, matching passed preflight, and an idempotency key. In 0.4.0
it persists a `BLOCKED` run for audit and does not execute infrastructure.

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
