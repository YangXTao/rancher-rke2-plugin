"""Render an audited human-executable installation manual from a plan."""

from __future__ import annotations

import re
from typing import Any


SUPPORTED_FORMATS = ("markdown",)
SUPPORTED_OUTPUT_PROFILES = ("human-step-by-step",)

PLACEHOLDER_PATTERN = re.compile(
    r"\b(?:TODO|TBD|CHANGEME|REPLACE_ME|YOUR_[A-Z0-9_]+)\b"
)
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


def _component_steps(
    component: str,
    plan_component: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, str]]:
    details = plan_component.get("details", {})
    if component == "vm":
        return [
            {
                "title": "Prepare the vSphere Terraform provider cache",
                "command": "prepare-terraform-provider-cache.sh --mode <online|offline> "
                "--source vmware/vsphere --version <vsphere_provider> --software-root /software",
                "expected": "TERRAFORM_PROVIDER_CACHE_READY; TF_CLI_CONFIG_FILE under /software/terraform",
                "verify": "terraform providers mirror is populated under /software/terraform/providers",
            },
            {
                "title": "Create the VMs with Terraform",
                "command": "terraform init && terraform apply -auto-approve",
                "expected": f"{details.get('intended_vm_count', 'N')} vsphere_virtual_machine resources created",
                "verify": "terraform state list | count vsphere_virtual_machine.vm",
            },
            {
                "title": "Check target IP conflicts",
                "command": "python3 check-ip-conflicts.py <ips>",
                "expected": "no conflicting addresses",
                "verify": "runner exits 0 and VM_EXECUTION_SUCCEEDED is reported",
            },
        ]
    if component == "node-init":
        return [
            {
                "title": "Initialize every node",
                "command": "ansible-playbook playbooks/node-init.yml",
                "expected": "sysctl profile, kernel modules, limits, SELinux, swap, firewall applied",
                "verify": "nodes remain reachable on TCP/22; NODE_INIT_SUCCEEDED is reported",
            },
            {
                "title": "Node readiness gate",
                "command": "TCP/22 probe on every intended node",
                "expected": "all nodes accept TCP/22",
                "verify": "WORKFLOW_NODE_READINESS_PASSED event when part of a workflow",
            },
        ]
    if component == "local-rke2":
        return [
            {
                "title": "Prepare RKE2 artifacts",
                "command": "prepare-rke2-artifacts.sh --mode <online|offline> --software-root /software",
                "expected": "pinned rke2 binaries and install script under /software/rke2",
                "verify": "artifact manifest exists and matches the pinned version",
            },
            {
                "title": "Install the three-server cluster",
                "command": "ansible-playbook playbooks/local-rke2.yml",
                "expected": "exactly 3 management servers run rke2-server; kubeconfig fetched",
                "verify": "kubectl get nodes --kubeconfig <runs>/<run>/kubeconfig/rke2.yaml shows 3 Ready",
            },
            {
                "title": "Final health assertion",
                "command": "kubectl get nodes; count Ready; etcd and Cilium pods Running",
                "expected": "3 Ready servers, 3 etcd pods, >=3 Cilium pods",
                "verify": "waits up to 10 minutes for Ready before asserting",
            },
        ]
    if component == "rancher":
        return [
            {
                "title": "Prepare Rancher artifacts and private CA",
                "command": "prepare-rancher-enterprise-artifacts.sh and generate-rancher-certs-cfssl.sh",
                "expected": "chart manifest and CA/cert under <run>/rancher/cert/output",
                "verify": "openssl verify -CAfile cacerts.pem tls.crt",
            },
            {
                "title": "Install Rancher with Helm",
                "command": "helm upgrade --install rancher <chart> --namespace cattle-system "
                "--set-string hostname=<lb-ip> --set service.type=NodePort "
                "--set service.nodePort=30080 --kubeconfig <kubeconfig> --wait --timeout 15m",
                "expected": "cattle-system pods Running; rancher-nodeport Service on 30080",
                "verify": "curl -k https://<lb-ip>/ping returns pong",
            },
            {
                "title": "Publish through RancherLB",
                "command": "ansible-playbook playbooks/rancher-lb.yml",
                "expected": "nginx L7 container listens on 80/443 with the private CA",
                "verify": "TCP 443 to the load balancer succeeds",
            },
        ]
    if component == "downstream":
        return [
            {
                "title": "Prepare the rancher2 Provider cache",
                "command": "prepare-terraform-provider-cache.sh --source rancher/rancher2 "
                "--version <rancher2_provider> --software-root /software",
                "expected": "exact provider version cached under /software/terraform",
                "verify": "provider-cache.log reports cache ready or hit",
            },
            {
                "title": "Obtain a Rancher API token",
                "command": "prepare-rancher-api-token.py --api-url https://<lb-ip> "
                "--admin-password-file <run>/secrets/rancher-bootstrap-password "
                "--ca-file <run>/secrets/rancher-ca.pem",
                "expected": "token-key written under <run>/secrets/rancher-api-token",
                "verify": "token verifies against the Rancher API",
            },
            {
                "title": "Create rancher2_cluster_v2",
                "command": "terraform init && terraform apply -auto-approve",
                "expected": "cluster <name> created with the configured rke_config and registries",
                "verify": "cluster_id output equals fleet-default/<name>",
            },
            {
                "title": "Register nodes in role order",
                "command": "ansible-playbook playbooks/downstream-register.yml",
                "expected": "first control-plane and first worker together, then remaining "
                "control-planes, then remaining workers",
                "verify": "kubectl get nodes reports every configured node Ready",
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
    """Render a plan into an audited human-executable manual."""
    if format not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported format: {format}")
    if output_profile not in SUPPORTED_OUTPUT_PROFILES:
        raise ValueError(f"unsupported output_profile: {output_profile}")

    lines: list[str] = []
    lines.append(f"# Rancher / RKE2 部署手册（{output_profile}）")
    lines.append("")
    lines.append("> 由 rancher-rke2 MCP 服务器从不可变计划渲染；配置只含 "
                 "`docker-secret://` 引用，手册不含任何凭据值。")
    lines.append("")
    lines.append("## 计划信息")
    lines.append("")
    lines.append(f"- 计划 ID：`{plan['plan_id']}`")
    lines.append(f"- 配置摘要：`{plan['config_digest']}`")
    lines.append(f"- 执行模式：{plan.get('execution_mode')}；可执行：{plan.get('executable')}")
    lines.append(f"- 目标组件：{', '.join(plan['target_components'])}")
    lines.append(f"- 计划创建：{plan.get('created_at')}；过期：{plan.get('expires_at')}")
    lines.append(f"- 批准文本：`{plan.get('approval_text')}`")
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
        lines.append(f"- 变更基础设施：{plan_component.get('would_change_infrastructure')}")
        details = plan_component.get("details", {})
        if details:
            lines.append("- 计划细节：")
            for key, value in details.items():
                lines.append(f"  - `{key}`: `{value}`")
        lines.append("")
        lines.append("步骤：")
        lines.append("")
        for index, step in enumerate(_component_steps(component, plan_component, config), 1):
            lines.append(f"{index}. **{step['title']}**")
            lines.append(f"   - 命令：`{step['command']}`")
            lines.append(f"   - 预期输出：{step['expected']}")
            lines.append(f"   - 验证：{step['verify']}")
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
    for match in PLAINTEXT_SECRET_PATTERN.finditer(manual):
        findings.append(f"[PLAINTEXT] line: {match.group(0).strip()}")
    required = [
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
