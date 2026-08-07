"""Render an audited, directly executable installation manual from a plan."""

from __future__ import annotations

import re
from typing import Any


SUPPORTED_FORMATS = ("markdown",)
SUPPORTED_OUTPUT_PROFILES = ("human-step-by-step",)

PLACEHOLDER_PATTERN = re.compile(
    r"\b(?:TODO|TBD|CHANGEME|REPLACE_ME|YOUR_[A-Z0-9_]+)\b"
)
ANGLE_PLACEHOLDER_PATTERN = re.compile(r"<[A-Za-z0-9_.\-/]+>")
PLAINTEXT_SECRET_PATTERN = re.compile(
    r"(?im)^\s*(?:password|passwd|token|api_key|private_key|secret)\s*:\s*"
    r"['\"]?[^'\"\s][^'\"]*['\"]?\s*$"
)


def _secret_names(config: dict[str, Any]) -> list[str]:
    """Collect docker-secret references from the configuration, never values."""
    names: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str) and item.startswith("docker-secret://"):
                    names.add(item.removeprefix("docker-secret://"))
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(config)
    return sorted(names)


def _node_table(config: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    nodes = config["nodes"]
    for node in nodes["management"]["servers"]:
        rows.append({"role": "local-rke2-server", "hostname": node["hostname"], "ip": str(node["ip"])})
    lb = nodes["management"]["load_balancer"]
    rows.append({"role": "rancher-lb", "hostname": lb["hostname"], "ip": str(lb["ip"])})
    for node in nodes["downstream"]["controlplane"]:
        rows.append({"role": "downstream-controlplane", "hostname": node["hostname"], "ip": str(node["ip"])})
    for node in nodes["downstream"]["workers"]:
        rows.append({"role": "downstream-worker", "hostname": node["hostname"], "ip": str(node["ip"])})
    return rows


def _all_ips(config: dict[str, Any]) -> list[str]:
    return [row["ip"] for row in _node_table(config)]


def _lb_ip(config: dict[str, Any]) -> str:
    return str(config["nodes"]["management"]["load_balancer"]["ip"])


def _run_dir(config: dict[str, Any], component: str) -> str:
    workspace = str(config["run"]["workspace"]).rstrip("/")
    return f"{workspace}/runs/$RUN_ID/{component}"


def _docker_exec(config: dict[str, Any]) -> str:
    container = config["execution"]["container"]["name"]
    return f"docker exec -i {container} bash -lc"


def _component_steps(
    component: str,
    plan_component: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, str]]:
    versions = config["versions"]
    lb = _lb_ip(config)
    ips = " ".join(_all_ips(config))
    exec_prefix = _docker_exec(config)
    workspace = str(config["run"]["workspace"]).rstrip("/")
    proxy = str(config["downloads"].get("proxy_url") or "")
    proxy_arg = f" --proxy-url {proxy}" if proxy else ""
    software_root = str(config["downloads"]["software_root"]).rstrip("/")
    details = plan_component.get("details", {})

    if component == "vm":
        return [
            {
                "title": "Prepare the vSphere Terraform provider cache",
                "command": (
                    f"{exec_prefix} \"prepare-terraform-provider-cache.sh --mode "
                    f"{config['downloads']['mode']} --source vmware/vsphere "
                    f"--version {versions['vsphere_provider']} --software-root {software_root}{proxy_arg}\""
                ),
                "expected": (
                    "TERRAFORM_PROVIDER_CACHE_READY; TF_CLI_CONFIG_FILE at "
                    f"{software_root}/terraform/provider-cache-config/vmware-vsphere.tfrc"
                ),
                "verify": (
                    f"test -x {software_root}/terraform/providers/registry.terraform.io/"
                    f"vmware/vsphere/{versions['vsphere_provider']}/linux_amd64/terraform-provider-vsphere_*"
                ),
            },
            {
                "title": "Create the VMs with Terraform",
                "command": (
                    f"cd {_run_dir(config, 'vm')} && export TF_CLI_CONFIG_FILE="
                    f"{software_root}/terraform/provider-cache-config/vmware-vsphere.tfrc "
                    "&& unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy "
                    "&& terraform init && terraform apply -auto-approve"
                ),
                "expected": f"{details.get('intended_vm_count', '6')} vsphere_virtual_machine resources created",
                "verify": (
                    f"terraform state list | grep -c '^vsphere_virtual_machine.vm\\[' "
                    f"# 期望 {details.get('intended_vm_count', 6)}"
                ),
            },
            {
                "title": "Check target IP conflicts",
                "command": f"python3 {_run_dir(config, 'vm')}/check-ip-conflicts.py {ips}",
                "expected": "no conflicting addresses",
                "verify": "exit 0; VM_EXECUTION_SUCCEEDED is reported",
            },
        ]
    if component == "node-init":
        return [
            {
                "title": "Initialize every node",
                "command": (
                    f"cd {_run_dir(config, 'node-init')}/ansible && "
                    "ansible-playbook playbooks/node-init.yml"
                ),
                "expected": (
                    "sysctl profile (46 entries), kernel modules, limits, SELinux, "
                    "swap, firewall applied on all 6 nodes"
                ),
                "verify": "all nodes reachable on TCP/22; play recap has 0 failed",
            },
            {
                "title": "Node readiness gate",
                "command": f"for ip in {ips}; do nc -z -w 3 $ip 22 || exit 1; done",
                "expected": "all nodes accept TCP/22",
                "verify": "WORKFLOW_NODE_READINESS_PASSED event when part of a workflow",
            },
        ]
    if component == "local-rke2":
        return [
            {
                "title": "Prepare RKE2 artifacts",
                "command": (
                    f"{exec_prefix} \"prepare-rke2-artifacts.sh --mode "
                    f"{config['downloads']['mode']} --software-root {software_root} "
                    f"--rke2-version {versions['rke2_management']}\""
                ),
                "expected": f"pinned RKE2 binaries and install script under {software_root}/rke2",
                "verify": f"test -d {software_root}/rke2 && ls {software_root}/rke2",
            },
            {
                "title": "Install the three-server cluster",
                "command": (
                    f"cd {_run_dir(config, 'local-rke2')}/ansible && "
                    "ansible-playbook playbooks/local-rke2.yml"
                ),
                "expected": (
                    "exactly 3 management servers run rke2-server; kubeconfig fetched to "
                    f"{workspace}/runs/$RUN_ID/kubeconfig/rke2.yaml"
                ),
                "verify": (
                    f"kubectl --kubeconfig {workspace}/runs/$RUN_ID/kubeconfig/rke2.yaml "
                    "get nodes # 期望 3 行 Ready"
                ),
            },
            {
                "title": "Final health assertion",
                "command": (
                    f"kubectl --kubeconfig {workspace}/runs/$RUN_ID/kubeconfig/rke2.yaml get nodes --no-headers "
                    "| awk '$2==\"Ready\"{n++} END{exit !(n==3)}'"
                ),
                "expected": "3 Ready servers, 3 etcd pods, >=3 Cilium pods Running",
                "verify": (
                    "kubectl --kubeconfig " + f"{workspace}/runs/$RUN_ID/kubeconfig/rke2.yaml "
                    "-n kube-system get pods -l component=etcd -l k8s-app=cilium --no-headers"
                ),
            },
        ]
    if component == "rancher":
        return [
            {
                "title": "Prepare Rancher artifacts and private CA",
                "command": (
                    f"{exec_prefix} \"prepare-rancher-enterprise-artifacts.sh --mode "
                    f"{config['downloads']['mode']} --rancher-version {versions['rancher']} "
                    f"--rke2-version {versions['rke2_management']} --software-root {software_root}\" && "
                    f"{exec_prefix} \"generate-rancher-certs-cfssl.sh "
                    f"{_run_dir(config, 'rancher')}/cert/output {lb} {lb}\""
                ),
                "expected": (
                    f"chart manifest at {software_root}/rancher/rancher-{versions['rancher']}.tgz.manifest; "
                    "CA/cert under " + f"{_run_dir(config, 'rancher')}/cert/output"
                ),
                "verify": (
                    f"openssl verify -CAfile {_run_dir(config, 'rancher')}/cert/output/cacerts.pem "
                    f"{_run_dir(config, 'rancher')}/cert/output/tls.crt"
                ),
            },
            {
                "title": "Install Rancher with Helm",
                "command": (
                    f"helm upgrade --install rancher {software_root}/rancher/rancher-{versions['rancher']}.tgz "
                    "--namespace cattle-system --create-namespace "
                    f"--set-string hostname={lb} "
                    "--set-string bootstrapPassword=\"$(cat "
                    f"{_run_dir(config, 'rancher')}/secrets/rancher-bootstrap-password)\" "
                    "--set tls=external --set privateCA=true --set ingress.enabled=false "
                    "--set service.type=NodePort --set service.nodePort=30080 "
                    "--set useBundledSystemChart=true --set replicas=3 "
                    f"--kubeconfig {workspace}/runs/$RUN_ID/kubeconfig/rke2.yaml "
                    "--wait --timeout 15m"
                ),
                "expected": "cattle-system pods Running; rancher-nodeport Service on 30080",
                "verify": f"curl -k https://{lb}/ping # 期望 pong",
            },
            {
                "title": "Publish through RancherLB",
                "command": (
                    f"cd {_run_dir(config, 'rancher')}/ansible && "
                    "ansible-playbook playbooks/rancher-lb.yml"
                ),
                "expected": "nginx L7 container listens on 80/443 with the private CA",
                "verify": f"nc -z -w 3 {lb} 443 && curl -k https://{lb}/ping",
            },
        ]
    if component == "downstream":
        cluster_name = str(config["downstream_cluster"]["name"])
        return [
            {
                "title": "Prepare the rancher2 Provider cache",
                "command": (
                    f"{exec_prefix} \"prepare-terraform-provider-cache.sh --mode "
                    f"{config['downloads']['mode']} --source rancher/rancher2 "
                    f"--version {versions['rancher2_provider']} --software-root {software_root}{proxy_arg}\""
                ),
                "expected": (
                    "TERRAFORM_PROVIDER_CACHE_READY; TF_CLI_CONFIG_FILE at "
                    f"{software_root}/terraform/provider-cache-config/rancher-rancher2.tfrc"
                ),
                "verify": (
                    f"test -x {software_root}/terraform/providers/registry.terraform.io/"
                    f"rancher/rancher2/{versions['rancher2_provider']}/linux_amd64/terraform-provider-rancher2_*"
                ),
            },
            {
                "title": "Obtain a Rancher API token",
                "command": (
                    f"python3 {_run_dir(config, 'downstream')}/scripts/prepare-rancher-api-token.py "
                    f"--api-url https://{lb} --rancher-version {versions['rancher']} "
                    f"--admin-password-file {_run_dir(config, 'downstream')}/secrets/rancher-bootstrap-password "
                    f"--ca-file {_run_dir(config, 'downstream')}/secrets/rancher-ca.pem "
                    f"--output-directory {_run_dir(config, 'downstream')}/secrets/rancher-api-token "
                    f"--desired-server-url https://{lb}"
                ),
                "expected": f"token-key written under {_run_dir(config, 'downstream')}/secrets/rancher-api-token",
                "verify": "RANCHER_API_TOKEN_CREATED or RANCHER_API_TOKEN_REUSED is printed",
            },
            {
                "title": "Create rancher2_cluster_v2",
                "command": (
                    f"cd {_run_dir(config, 'downstream')}/terraform && export TF_CLI_CONFIG_FILE="
                    f"{software_root}/terraform/provider-cache-config/rancher-rancher2.tfrc "
                    "&& unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy "
                    "&& terraform init && "
                    f"TF_VAR_rancher_token_key=\"$(cat ../secrets/rancher-api-token/token-key)\" "
                    "terraform apply -auto-approve"
                ),
                "expected": (
                    f"cluster {cluster_name} created with the configured rke_config and registries"
                ),
                "verify": f"terraform output -raw cluster_id # 期望 fleet-default/{cluster_name}",
            },
            {
                "title": "Register nodes in role order",
                "command": (
                    f"cd {_run_dir(config, 'downstream')}/ansible && "
                    "ansible-playbook playbooks/downstream-register.yml"
                ),
                "expected": (
                    "first control-plane and first worker together, then remaining "
                    "control-planes, then remaining workers"
                ),
                "verify": "every configured downstream node reports Ready",
            },
        ]
    return [
        {
            "title": f"Run the {component} component",
            "command": "start_run (or the enclosing workflow stage)",
            "expected": f"{component} completes and reports SUCCEEDED",
            "verify": "get_run state SUCCEEDED with a durable artifact path",
        }
    ]


def _rollback_boundary(component: str) -> str:
    return {
        "vm": "Destroy the complete VM set only through the explicitly confirmed "
        "$rancher-rke2-vm destruction flow.",
        "node-init": "Re-run node-init; initialization is idempotent and does not destroy nodes.",
        "local-rke2": "Re-run local-rke2; install is idempotent and the final health check "
        "waits up to 10 minutes for Ready nodes.",
        "rancher": "Re-run the rancher component (Helm upgrade) or remove the release manually; "
        "a normal plan approval never authorizes destruction.",
        "downstream": "Before a rerun, delete the existing downstream cluster object in Rancher; "
        "node registration is role-ordered and idempotent per node.",
    }.get(component, "Re-run the component; it is idempotent within the approved scope.")


def _offline_files(config: dict[str, Any]) -> list[str]:
    root = str(config["downloads"]["software_root"]).rstrip("/")
    return [
        f"{root}/rke2",
        f"{root}/terraform",
        f"{root}/rancher",
        f"{root}/control-dependencies",
        f"{root}/docker",
    ]


def render(
    plan: dict[str, Any],
    config: dict[str, Any],
    *,
    format: str = "markdown",
    output_profile: str = "human-step-by-step",
) -> tuple[str, list[str]]:
    """Render a plan into an audited, directly executable manual."""
    if format not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported format: {format}")
    if output_profile not in SUPPORTED_OUTPUT_PROFILES:
        raise ValueError(f"unsupported output_profile: {output_profile}")

    versions = config["versions"]
    control = config["execution"]["control_host"]
    workspace = str(config["run"]["workspace"]).rstrip("/")
    proxy = str(config["downloads"].get("proxy_url") or "")

    lines: list[str] = []
    lines.append(f"# Rancher / RKE2 部署手册（{output_profile}）")
    lines.append("")
    lines.append("> 由 rancher-rke2 MCP 服务器从不可变计划渲染；命令可直接执行，"
                 "凭据只以 `docker-secret://` 名称出现，本手册不含任何凭据值。")
    lines.append("")
    lines.append("## 计划信息")
    lines.append("")
    lines.append(f"- 计划 ID：`{plan['plan_id']}`")
    lines.append(f"- 配置摘要：`{plan['config_digest']}`")
    lines.append(f"- 执行模式：{plan.get('execution_mode')}；可执行：{plan.get('executable')}")
    lines.append(f"- 目标组件：{', '.join(plan['target_components'])}")
    lines.append(f"- 计划创建：{plan.get('created_at')}；过期：{plan.get('expires_at')}")
    lines.append("")
    lines.append("## 执行环境")
    lines.append("")
    lines.append("开始前，把本次运行 ID 写入环境变量（形如 `run-20260807T060840Z-9f94f759d82f`）：")
    lines.append("")
    lines.append("```bash")
    lines.append("export RUN_ID='本次运行的 run_id（例如 run-20260807T060840Z-9f94f759d82f）'")
    lines.append("```")
    lines.append("")
    lines.append(f"- 控制机：`ssh {control['username']}@{control['address']} -p {control['port']}`")
    lines.append(f"- 控制容器：`{config['execution']['container']['name']}`"
                 f"（镜像 {config['execution']['container']['image']}，host 网络）")
    lines.append(f"- 运行目录：`{workspace}/runs/$RUN_ID/`")
    lines.append(f"- 软件根目录：`{config['downloads']['software_root']}`")
    lines.append(f"- 下载模式：`{config['downloads']['mode']}`"
                 + (f"；代理：`{proxy}`（密码在 Docker Secret 中）" if proxy else ""))
    lines.append("")
    lines.append("版本：")
    lines.append("")
    lines.append("| 组件 | 版本 |")
    lines.append("|---|---|")
    lines.append(f"| terraform | {versions['terraform']} |")
    lines.append(f"| vsphere_provider | {versions['vsphere_provider']} |")
    lines.append(f"| rke2_management | {versions['rke2_management']} |")
    lines.append(f"| rancher | {versions['rancher']} |")
    lines.append(f"| rancher2_provider | {versions['rancher2_provider']} |")
    lines.append(f"| rke2_downstream | {versions['rke2_downstream']} |")
    lines.append("")
    lines.append("## 拓扑")
    lines.append("")
    lines.append("| 角色 | 主机名 | IP |")
    lines.append("|---|---|---|")
    for row in _node_table(config):
        lines.append(f"| {row['role']} | {row['hostname']} | {row['ip']} |")
    lines.append("")
    lines.append("## 凭据（仅名称，值在 MCP 主机的 Docker Secrets 中）")
    lines.append("")
    for name in _secret_names(config):
        lines.append(f"- `docker-secret://{name}`")
    lines.append("")
    lines.append("## 前置条件与风险")
    lines.append("")
    for item in plan.get("global_prerequisites", []):
        lines.append(f"- {item}")
    for item in plan.get("warnings", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 分组件步骤")
    lines.append("")
    for plan_component in plan.get("component_plans", []):
        component = plan_component["component"]
        lines.append(f"### {component}")
        lines.append("")
        lines.append(f"- 顺序：{plan_component.get('sequence')}；依赖："
                     f"{', '.join(plan_component.get('dependencies', [])) or '无'}")
        details = plan_component.get("details", {})
        if details:
            lines.append("- 计划细节：")
            for key, value in details.items():
                lines.append(f"  - `{key}`: `{value}`")
        lines.append("")
        lines.append("可直接执行的步骤：")
        lines.append("")
        for index, step in enumerate(_component_steps(component, plan_component, config), 1):
            lines.append(f"{index}. **{step['title']}**")
            lines.append("")
            lines.append("```bash")
            lines.append(step["command"])
            lines.append("```")
            lines.append("")
            lines.append(f"   - 预期输出：{step['expected']}")
            lines.append(f"   - 验证：`{step['verify']}`")
            lines.append("")
        lines.append(f"回滚边界：{_rollback_boundary(component)}")
        lines.append("")
    lines.append("## 离线文件")
    lines.append("")
    for path in _offline_files(config):
        lines.append(f"- `{path}`")
    lines.append("")
    lines.append("## 审计结果")
    lines.append("")

    manual = "\n".join(lines) + "\n"
    findings = audit(manual)
    lines.append(f"- 占位符检查：{'通过' if not any(f.startswith('[PLACEHOLDER]') for f in findings) else '失败'}")
    lines.append(f"- 明文凭据检查：{'通过' if not any(f.startswith('[PLAINTEXT]') for f in findings) else '失败'}")
    lines.append(f"- 结构检查：{len(findings)} 项发现（0 为通过）")
    manual = "\n".join(lines) + "\n"
    return manual, findings


def audit(manual: str) -> list[str]:
    """Return unresolved audit findings for a rendered manual."""
    findings: list[str] = []
    for match in PLACEHOLDER_PATTERN.finditer(manual):
        findings.append(f"[PLACEHOLDER] {match.group(0)}")
    for match in ANGLE_PLACEHOLDER_PATTERN.finditer(manual):
        findings.append(f"[PLACEHOLDER] {match.group(0)}")
    for match in PLAINTEXT_SECRET_PATTERN.finditer(manual):
        findings.append(f"[PLAINTEXT] line: {match.group(0).strip()}")
    required = [
        "执行环境",
        "计划信息",
        "拓扑",
        "凭据",
        "前置条件与风险",
        "分组件步骤",
        "离线文件",
        "回滚边界",
        "审计结果",
    ]
    for section in required:
        if section not in manual:
            findings.append(f"[STRUCTURE] missing section: {section}")
    if "验证" not in manual:
        findings.append("[STRUCTURE] missing verification commands")
    return findings
