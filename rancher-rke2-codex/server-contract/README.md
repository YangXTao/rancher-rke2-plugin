# Rancher/RKE2 MCP Server Contract 0.4.0

## Implemented preflight boundary

Server 0.4.0 provides `preflight_plan`, `get_preflight`, `start_run`, `get_run`,
and `get_run_events`. If a preflight plan
includes `vm`, node SSH checks are skipped because those nodes are intended to be
created; vCenter and registry checks remain required.
Preflight can resolve mounted Secret availability and test TCP reachability only.
It does not authenticate against SSH, vCenter, registry, or proxy services, and it
does not make any infrastructure change.

`start_run` is a server-side mutation gate: it accepts one supported component plan, exact
plan approval text, matching digest, and a current `PASSED` preflight. It records
durable state with idempotency protection. In 0.4.0 the result is intentionally
`BLOCKED`, because no infrastructure execution backend has been introduced.

## 服务职责

MCP Server是唯一基础设施执行面，负责：

1. 校验配置并生成不可变摘要；
2. 产生包含风险、前置条件和变更范围的计划；
3. 以后用 `run_id` 和 `idempotency_key` 驱动可恢复状态机；
4. 保存结构化事件、产物索引和最终验证结果；
5. 在服务端重新检查所有变更、重试、取消和销毁授权。

Skill只决定调用顺序和用户解释，不得绕过MCP Server直接执行基础设施命令。

## Secret规则

- MCP远程传输必须使用HTTPS。
- 配置不得包含明文密码、Token、私钥或带用户信息的代理URL。
- Secret字段使用 `docker-secret://<name>`。
- Secret值在专属服务器带外配置，规划阶段不解析。
- SQLite只保存引用，不保存Secret值。
- 日志、事件、计划、Hook审计和操作手册不得包含Secret值。

## 固定组件顺序

1. `vm`
2. `node-init`
3. `local-rke2`
4. `rancher`
5. `downstream`

全量运行必须按该顺序推进；单组件运行也必须检查依赖。

## 关键约束

- `validate_config` 返回 `config_digest` 和安全预览。
- `build_plan` 绑定摘要、组件范围和过期时间。
- `start_run` 必须验证计划未过期、摘要一致、批准文本和幂等键。
- 相同幂等键与相同请求返回同一 `run_id`；相同键不同请求必须拒绝。
- `resume_run` 只能从服务端持久化检查点继续。
- `retry_component` 不得跳过依赖。
- `destroy_vm_set` 只能销毁指定运行Terraform State管理的完整VM集合。
- 所有破坏性工具必须在服务端再次鉴权。

## 当前实现边界

Server 0.4.0已实现：

```text
get_capabilities
get_config_schema
validate_config
build_plan
get_plan
preflight_plan
get_preflight
start_run
get_run
get_run_events
```

这些工具不会探测或修改vSphere、Linux、Kubernetes、Rancher或Harbor。
