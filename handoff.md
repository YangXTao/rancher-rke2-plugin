# Rancher / RKE2 MCP 自动化项目交接文档（handoff）

生成时间：2026-08-21（Asia/Shanghai）
用途：在另一台电脑/新会话中，`git pull` 当前可用版本代码后继续工作。

> 安全说明：本文不含 SSH/vCenter/Harbor/Rancher 密码、MCP Bearer Token 或证书私钥。
> 所有凭据只存在于 MCP Server 主机的 Docker Secrets 中，以 `docker-secret://<名称>` 引用。

## 1. 项目目标和背景

把"VMware vSphere 上手工搭建 Rancher + RKE2"的整套流程自动化，形成：

```text
Codex Desktop（插件 rancher-rke2-codex）
        │ HTTPS + Bearer Token（RANCHER_RKE2_MCP_TOKEN）
        ▼
远端 MCP Server（192.168.10.184:8443，Docker Compose）
        │ SSH / SFTP
        ▼
自动化控制机（192.168.2.179，rancher-rke2-control 容器）
        │
        ├─ Terraform：vSphere VM（vm）
        ├─ Ansible：node-init / local-rke2 / rancher / downstream
        └─ 可选：audit 通过的纯人工安装手册（installation_manual）
```

目标是一次批准即可串行执行完整五阶段：
`vm → node-init → local-rke2 → rancher → downstream`，并输出可离线交付、
无自动化依赖的纯人工操作手册。

## 2. 已完成的工作

### 2.1 架构与基础能力（v0.9.x）
- 插件 + 远端 MCP Server 架构打通（HTTPS + 私有 CA + Bearer Token）。
- 5 组件执行器（vm/node-init/local-rke2/rancher/downstream）全部在隔离的
  `rancher-rke2-control` Docker 容器内执行（host 网络、挂载 /software 与工作区、不挂 Docker socket）。
- 配置校验、不可变计划、preflight（Secret 可用性 + TCP 可达性）、审批文本、幂等键。
- 凭据只接受 `docker-secret://` 引用，不持久化、不回显。

### 2.2 本会话（v0.11.x）完成项
- **vm 阶段 apt 走代理**（v0.11.10）：`install-control-dependencies.sh` 的 apt 增加
  `Acquire::http::Proxy`/`https::Proxy`，terraform zip 下载显式 `--proxy`。
- **纯人工操作手册渲染**（v0.11.11）：重写 `runbook.py`，按
  `rancher-rke2-automation` skill 的 15 章规范生成纯人工手册
  （vSphere UI / SSH 原生命令 / Rancher UI，禁止 Terraform/Ansible/脚本/playbook），
  内置审计 + skill `audit-installation-manual.py` 双重验证。
- **node-init 兼容 RHEL 8.10**：
  - v0.11.12：Ansible facts 前用 `raw` 引导安装 python3（dnf/yum/apt 分支）。
  - v0.11.13：RHEL 8 默认 python3.6 太旧（不支持 `from __future__ import annotations`），
    改为优先安装 python3.11（回退 python39）并软链 `/usr/local/bin/python3`。
- **downstream 按发行版判断最小内核**（v0.11.14）：redhat≥4.18 / ubuntu≥5.4 /
  kylin-v11≥4.19 / 其它回退 4.18。
- **组件产物可从失败 workflow 复用**（v0.11.15）：`latest_succeeded_component_run`
  由"整体 run SUCCEEDED"放宽为"组件级 SUCCEEDED"，允许 downstream 复用
  整体失败 workflow 中已成功的 rancher 产物。
- **只读运行时诊断**（v0.11.16）：新增 `collect_diagnostics`，按
  `summary|standard|deep` 深度采集固定白名单日志尾部、控制机/控制容器相关进程和
  包数据库审计；不接受任意命令或路径，返回前按 Docker Secret 实值与凭据模式脱敏。
- **插件发布为 git marketplace**：新增 `.agents/plugins/marketplace.json`
  （marketplace 名 `rancher-rke2`），插件源码迁至
  `.agents/plugins/plugins/rancher-rke2-codex/`，当前 cachebuster 版本
  `0.12.0+codex.20260821`。
- **RHEL 8.10 全流程跑通**（2026-08-11）：RKE2 v1.35.6+rke2r1 + Rancher
  2.14.3-ent + downstream 全部 SUCCEEDED，并渲染新配置手册。

### 2.3 v0.12.0 控制容器生命周期改造

- 新增服务端 `ControlContainerManager`，由五个组件执行器共享。任何单组件或完整
  workflow 在执行前都会自动准备控制环境，不再隐式依赖 vm 阶段先创建容器。
- 控制机仅安装配置中固定版本的 Docker 静态二进制；Terraform、Ansible、Helm、
  kubectl 等仍只存在于控制容器内或 `/software` 持久缓存中。
- 自动通过 SFTP 下发 manager、runner、playbook 和 Terraform 资产；用户不再需要
  预先把插件文件复制到控制机固定目录。
- 控制镜像支持本地 archive，也支持在线拉取；`downloads.mode: offline` 时，仅在
  `allow_offline_registry_pull: true` 且镜像主机等于配置的 Harbor 主机时允许拉取，
  不会回退到公网仓库。
- 按配置校验容器镜像、host 网络、`NET_RAW`、`/software`、工作区、只读
  `/etc/localtime` 及“不挂载 Docker socket”边界；不兼容容器按 strategy 失败或重建。
- 每个 run 新增 `control-container/install-docker.log`、`prepare-container.log`、
  `validate-container.log`。完整 workflow 只准备一次。
- 新增 `control-image/Dockerfile` 和 tag 触发的 GitHub Actions 发布。运维侧将该镜像
  按 digest 镜像同步进内网 Harbor 即可，不需要每次运行自行构建。

## 3. 关键技术决策及原因

| 决策 | 原因 |
|---|---|
| MCP Server 作为唯一基础设施执行路径 | 所有变更必须经过审批文本 + 幂等键；客户端不直接执行 Terraform/Ansible/SSH |
| 凭据只用 `docker-secret://` 引用 | 明文凭据禁止入库/入日志/入聊天；值只在 MCP 主机 Secret 中 |
| workflow 一次批准串行 5 阶段 | 用户要求"一次回复即可执行全部"，阶段间自动复用产物（kubeconfig、Rancher CA） |
| preflight 只做 TCP/Secret 检查，不认证 | 明确边界：可提前发现连通性问题，但不改变基础设施 |
| 手册必须是纯人工操作手册 | 按已实现 skill 规范：把自动化动作还原为 vSphere/Rancher UI + 原生命令，禁止自动化捷径 |
| node-init 用 raw 引导 python3 | RHEL 8.10 模板无 python3，Ansible 需要目标 Python 才能收集 facts（鸡生蛋问题） |
| RHEL8 装 python3.11 并软链 | RHEL8 自带 python3.6，无法解析 Ansible 2.20 模块语法（3.7+） |
| 内核阈值按发行版判断 | 后续会用到不同 OS（RHEL/Rocky/Ubuntu/Kylin），不能一个固定值 |
| 组件产物复用放宽到组件级 | 全流程中前置阶段成功后置阶段失败时，已成功阶段产物应可复用 |
| marketplace 发布到 git 仓库 | 新电脑可用 `codex plugin marketplace add` + `plugin add` 正式安装（HTTPS 免 SSH key） |
| 控制容器由服务端统一管理 | 单组件和完整 workflow 语义一致，消除手工建容器、重复装依赖及 vm 阶段耦合 |
| 离线模式允许受限 Harbor 拉取 | “离线”表示不能访问公网，不等于不能访问内网镜像仓库；镜像主机必须与 registry.hostname 一致 |

## 4. 已修改的文件

本会话涉及的主要文件（均在 `rancher-rke2-mcp-server/` 下，除非注明）：

- `src/rancher_rke2_mcp/constants.py`：版本常量（当前 0.12.0）
- `src/rancher_rke2_mcp/control_container.py`：控制机 Docker 与持久控制容器统一管理
- `src/rancher_rke2_mcp/assets/control-container/`：三阶段准备、校验与独立日志脚本
- `control-image/`：预构建控制镜像定义与 Harbor 镜像同步说明
- `src/rancher_rke2_mcp/diagnostics.py`：只读运行时诊断采集、日志活性判定与固定命令白名单
- `src/rancher_rke2_mcp/runbook.py`：纯人工 15 章手册渲染 + 内置审计
- `src/rancher_rke2_mcp/storage.py`：`latest_succeeded_component_run` 组件级复用
- `src/rancher_rke2_mcp/executor.py`：`downstream_minimum_kernel`（fallback 4.18）、vm apt proxy 相关环境
- `src/rancher_rke2_mcp/assets/vm/install-control-dependencies.sh`：apt/curl 走代理
- `src/rancher_rke2_mcp/assets/node-init/ansible/playbooks/node-init.yml`：python3 引导
- `src/rancher_rke2_mcp/assets/downstream/ansible/roles/downstream_register/{defaults,tasks}/main.yml`：按发行版内核判断
- `tests/test_read_only_server.py`：runbook/内核/产物复用/运行时诊断相关测试（51/51）
- `.agents/plugins/marketplace.json`：git marketplace 清单
- `.agents/plugins/plugins/rancher-rke2-codex/`：插件源码（原 `rancher-rke2-codex/` 迁入，git mv 保留历史）
- `README.md`：目录说明更新

## 5. 已执行的重要命令

### 5.1 MCP Server 部署（服务器 192.168.10.184，按 tag）
```bash
cd ~/rancher-rke2-mcp-server/rancher-rke2-plugin
git fetch origin --tags
git switch --detach v0.12.0
cd rancher-rke2-mcp-server
export DOCKER_API_VERSION=1.41
docker-compose stop rancher-rke2-mcp rancher-rke2-mcp-proxy
docker-compose rm -f rancher-rke2-mcp rancher-rke2-mcp-proxy
docker-compose --profile https up -d --build
```
注意：tag 强制更新过时，服务器本地同名 tag 需先 `git tag -d <tag>` 再 fetch。

### 5.2 客户端标准调用链（通过插件/MCP）
```
get_capabilities → validate_config → build_plan → get_plan
→ preflight_plan → get_preflight → APPROVE WORKFLOW <plan-id> → start_workflow
→ get_run / get_run_events（轮询）→ render_runbook（可选）
```
单组件：`build_plan(config_digest, ["downstream"])` → `APPROVE PLAN <plan-id>` → `start_run`。

### 5.3 发布流程（本机）
```bash
git -C <repo> add ... && git -C <repo> commit -m "..."
git -C <repo> tag -a v0.12.0 -m "Release v0.12.0"
git -C <repo> push origin refs/tags/v0.12.0
```
本次发布只推 tag，不推送或合并 `dev/rancher-rke2-automation`。版本发布前必须同步
`constants.py` 的 SCHEMA/CONTRACT/SERVER_VERSION。

### 5.4 插件安装（新电脑）
```powershell
codex plugin marketplace add https://github.com/YangXTao/rancher-rke2-plugin.git --ref v0.12.0
codex plugin add rancher-rke2-codex@rancher-rke2
```
（SSH 地址 `git@github.com:...` 亦可，但需要 GitHub key；HTTPS 走 Git 凭据。）

## 6. 当前代码和环境状态

### 6.1 代码
- 本地发布分支：`release/v0.12.0`（不推送、不合并 dev）
- 发布基线：`v0.11.16`；目标 tag：`v0.12.0`（MCP Server 版本常量 0.12.0）
- 自动化测试：53/53 通过（仓库内忽略的 `rancher-rke2-mcp-server/.venv`）

### 6.2 基础设施（2026-08-11 验证成功）
- 模板：`Redhat8.10-cgroupv2_Teamplate-abc@123`（RHEL 8.10 / cgroup v2）
- 版本：RKE2 `v1.35.6+rke2r1`（管理+下游）、Rancher `2.14.3-ent`、Terraform 1.14.7、
  vsphere provider 2.15.0、rancher2 provider 13.1.4
- 节点：auto-rancher01/02/03（192.168.2.41/42/43）、auto-rancherlb（.40）、
  auto-master01（.44）、auto-worker01（.45），均为 RHEL 8.10
- 成功 run：
  - `run-20260807T095937Z-1e1e04da4aca`（vm/node-init/local-rke2/rancher SUCCEEDED，downstream 失败）
  - `run-20260811T014157Z-a35f0eece515`（downstream SUCCEEDED）
- Rancher：https://192.168.2.40（NodePort 30080），下游集群 `downstream` Active

### 6.3 关键资产
- MCP 端点：`https://192.168.10.184:8443/mcp`；Token 环境变量 `RANCHER_RKE2_MCP_TOKEN`
- 控制机：`root@192.168.2.179`；控制容器：`rancher-rke2-control`
- Harbor：`192.168.10.51`（admin / docker-secret://registry_password）
- 手册：`/data/manuals/plan-9fa1b48f85ff4cd9aee2dc642d73c103.md`
  （本地副本 `outputs/runbook-rhel8-full-deployment.md`）

## 7. 尚未解决的问题

- **运行时诊断是采样而非持续监控**：`collect_diagnostics` 可读固定白名单日志和进程，
  但单次进程快照不能证明持续前进；必要时应间隔调用比较日志大小与修改时间。
- **重跑全流程没有自动清理**：需先在 vCenter 删除既有 VM（IP 冲突），
  并在 Rancher 删除同名 downstream 集群（否则 terraform 409 AlreadyExists）。
- **手册渲染需要有效 plan**：plan 有效期 24h，过期后需重新 validate→build_plan→render。
- **RHEL 8.10 模板无 python3**：已在 playbook 自动引导，但属于模板预置差异，后续新模板需回归。

## 8. 下一步任务

1. 验证 Rancher UI 登录（初始管理员密码在服务器 Secret）与下游集群节点 Ready。
2. 发布后在客户端重装新 cachebuster，并新建任务加载 `collect_diagnostics`。
3. 可选：新增 OS/版本支持时回归 node-init 引导、内核阈值、镜像策略。
4. 如需全流程再验证：清 VM → 删 Rancher downstream 集群 → 重新批准跑 workflow。

## 9. 风险和注意事项

- 重跑前必须清理旧 VM 与旧 downstream 集群对象，否则 vm 阶段 IP 冲突、downstream 阶段 409。
- 计划 24 小时过期；跨天继续需重新 validate/plan/preflight/批准。
- 每次发布版本必须同步 `constants.py` 三个版本号，否则 get_capabilities 显示旧版本。
- 不推送 main；只在 `dev/rancher-rke2-automation` 与 tag 上工作。
- 凭据绝不写入配置/日志/聊天/工具参数；只允许 `docker-secret://` 引用。
- 服务器部署按 tag detach，改 tag 时注意本地旧 tag 需要先删除再 fetch。
- 下游/rancher 的注册命令含 token，只在执行时从 Secret/UI 取得，不回显。

## 10. 恢复工作所需的关键上下文

### 10.1 本机（开发/控制端）
- 仓库：`C:\Users\tg\Documents\Codex\2026-07-22\gei\work\rancher-rke2-plugin`
- Git 远程：`git@github.com:YangXTao/rancher-rke2-plugin.git`（默认 HTTPS 也可）
- MCP 调用临时 payload：`C:\Users\tg\Documents\Codex\2026-08-06\rancher-rke2-codex-plugin-rancher-rke2\work\`（JSON-RPC 文件）
- 本机插件源码（personal marketplace）：`C:\Users\tg\.agents\plugins\plugins\rancher-rke2-codex\`

### 10.2 服务器（192.168.10.184）
- 仓库路径：`~/rancher-rke2-mcp-server/rancher-rke2-plugin`
- Secrets：`rancher-rke2-mcp-server/secrets/*`（token、各密码、known_hosts）
- 数据卷：`rancher-rke2-mcp-data:/data`（SQLite 状态在 `/data/state.db`）

### 10.3 控制机（192.168.2.179）
- 运行目录：`/data/rancher/automation/runs/<run_id>/<component>/`
- 软件根：`/software`（terraform provider 缓存、rke2、rancher chart、docker 等持久化）

### 10.4 配置模板
- 参考配置：`rancher-rke2-mcp-server/examples/config.example.yaml`
- 插件示例：`.agents/plugins/plugins/rancher-rke2-codex/assets/config.example.yaml`

### 10.5 快速恢复动作
1. `git fetch origin --tags`（或 clone）到目标机器，切到 detached `v0.12.0`。
2. 确认服务器 `get_capabilities` 返回 v0.12.0，并包含 `collect_diagnostics`。
3. 需要执行时：validate → build_plan → preflight → 用户发批准文本 → start_workflow。
4. 需要手册时：build_plan 全流程 → render_runbook → 同步 outputs。
