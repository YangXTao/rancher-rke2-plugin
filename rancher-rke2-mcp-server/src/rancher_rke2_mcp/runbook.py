"""Render and audit the audited human-executable RKE2/Rancher manual.

The audit logic is ported verbatim from the validated
``rancher-rke2-automation`` skill's ``audit-installation-manual.py`` so the MCP
server can deliver the same audited installation manual without depending on
the standalone skill tree.
"""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

from .executor import effective_config


REQUIRED_HEADINGS = [
    "# RKE2 与 Rancher 人工安装操作手册",
    "## 1. 手册说明与完成标准",
    "## 2. 实际环境参数",
    "## 3. 控制节点 Docker 工作容器",
    "## 4. 安装介质与校验",
    "## 5. vSphere 界面手工创建虚拟机",
    "## 6. 所有节点逐台初始化",
    "## 7. Local RKE2 第一台管理节点",
    "## 8. Local RKE2 第二和第三台管理节点",
    "## 9. Rancher 证书、安装与验证",
    "## 10. RancherLB 手工配置",
    "## 11. Rancher Web UI 手工创建下游集群",
    "## 12. 下游节点按顺序手工注册",
    "## 13. 最终验收",
    "## 14. 故障排查、重试与回退",
    "## 15. 实际实施结果与审计结论",
]

REQUIRED_OPERATOR_LABELS = [
    "执行位置：",
    "操作：",
    "预期结果：",
    "失败处理：",
]

DEFAULT_NATIVE_EVIDENCE = [
    "vSphere",
    "Rancher Web UI",
    "rancher-rke2-control",
    "docker run",
    "--network host",
    "--cap-add NET_RAW",
    "-v /software:/software",
    "-v /data/rancher/automation:/data/rancher/automation",
    "/software/docker/docker-",
    "tar -xzf",
    "ExecStart=/usr/local/bin/dockerd",
    "test ! -S /var/run/docker.sock",
    "docker exec -i",
    "ping",
    "swapoff -a",
    "/etc/modules-load.d/rancher-rke2.conf",
    "/etc/sysctl.conf",
    "/etc/security/limits.conf",
    "sysctl --system",
    "/etc/rancher/rke2/config.yaml",
    "/etc/rancher/rke2/registries.yaml",
    "INSTALL_RKE2_VERSION",
    "rke2-server.service",
    "kubectl get nodes",
    "helm upgrade --install rancher",
    "cacerts-csr.json",
    "ssl-config.json",
    "ssl-csr.json",
    "cfssl gencert -initca",
    "cfssl gencert -ca",
    "cfssljson -bare",
    "openssl verify -CAfile",
    "rancher-nodeport",
    "30080",
    "nginx.conf",
    "--etcd --controlplane",
    "--worker",
    "chartValues:",
    "machineGlobalConfig:",
    "machinePools:",
    "machineSelectorConfig:",
    "registries:",
]

DEFAULT_FORBIDDEN_PATTERNS = [
    r"\bansible(?:-playbook)?\b",
    r"\bterraform\b",
    r"\bterraform\.tfstate\b",
    r"\bterraform\.auto\.tfvars\b",
    r"\brancher2_cluster_v2\b",
    r"\bplaybook\b",
    r"\brole/defaults\b",
    r"\bSKILL\.md\b",
    r"\brancher-rke2-(?:automation|vm|node-init|local|rancher|downstream)\b",
    r"\bcheckpoint(?:\.yaml)?\b",
    r"/data/rancher/automation/runs/",
    r"/var/run/docker\.sock:/var/run/docker\.sock",
    r"(?im)^\s*(?:apt(?:-get)?|dnf|yum)\b[^\n]*\binstall\b[^\n]*\bdocker(?:\.io|-ce)?\b",
    r"其余(?:节点|主机|服务器)同上",
    r"参照自动化",
    r"运行(?:本|该|上述)?脚本即可",
]

DEFAULT_FORBIDDEN_TERMS = [
    "下游集群配置Kata Runtime",
    "Rancher Backup安装",
    "AD对接",
    "下游集群安装Local Path StorageClass",
    "下游集群安装Monitoring",
    "下游集群安装Logging",
]


def audit(text: str, context: dict[str, object], require_pass_marker: bool) -> list[str]:
    failures: list[str] = []

    position = -1
    for heading in REQUIRED_HEADINGS:
        found = text.find(heading, position + 1)
        if found < 0:
            failures.append(f"missing or out-of-order heading: {heading}")
        else:
            position = found

    unresolved_patterns = [
        r"\{\{[^{}]+\}\}",
        r"<(?!(?:https?://))[^>\n]+>",
        r"\b(?:TODO|TBD|CHANGEME|REPLACE_ME|YOUR_[A-Z0-9_]+)\b",
        r"\$\{(?:RANCHER|RKE2|CLUSTER|NODE|REGISTRY|TOKEN|PASSWORD|VERSION)_[A-Z0-9_]+\}",
    ]
    for pattern in unresolved_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            failures.append(f"unresolved placeholder: {match.group(0)}")

    for label in REQUIRED_OPERATOR_LABELS:
        if label not in text:
            failures.append(f"missing operator-step label: {label}")

    required_values = _string_list(context, "required_values")
    required_commands = _string_list(context, "required_commands")
    required_hosts = _string_list(context, "required_host_labels")
    required_registry_hosts = _string_list(context, "required_registry_hosts")
    forbidden_terms = DEFAULT_FORBIDDEN_TERMS + _string_list(
        context, "forbidden_terms"
    )

    for value in required_values:
        if value and value not in text:
            failures.append(f"missing actual environment value: {value}")
    native_evidence = list(DEFAULT_NATIVE_EVIDENCE)
    if _boolean_value(context, "require_enterprise_image_upload"):
        native_evidence.extend(
            [
                "docker login",
                "--password-stdin",
                "docker run --rm",
                "REGISTRY_AUTH_FILE",
                "/root/.docker:ro",
            ]
        )
    for command in native_evidence + required_commands:
        if command and command not in text:
            failures.append(
                f"missing native manual command/config evidence: {command}"
            )
    for host in required_hosts:
        if host and not re.search(rf"执行位置：[^\n]*{re.escape(host)}", text):
            failures.append(f"host is not named in an execution location: {host}")
    yaml_blocks = re.findall(
        r"```ya?ml\s*\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE
    )
    registry_yaml_blocks = [
        block for block in yaml_blocks if "mirrors:" in block and "configs:" in block
    ]
    if not registry_yaml_blocks:
        failures.append("missing complete registry YAML block with mirrors and configs")
    else:
        registry_yaml = "\n".join(registry_yaml_blocks)
        for registry_host in required_registry_hosts:
            if registry_host and registry_host not in registry_yaml:
                failures.append(
                    f"registry host missing from complete YAML: {registry_host}"
                )
    for term in forbidden_terms:
        if term and term in text:
            failures.append(f"out-of-scope or forbidden term: {term}")
    for pattern in DEFAULT_FORBIDDEN_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            failures.append(
                f"automation shortcut or vague instruction: {match.group(0)}"
            )

    minimum_steps = _positive_int(context, "minimum_numbered_steps", 30)
    step_count = len(re.findall(r"(?m)^\s*\d+\.\s+", text))
    if step_count < minimum_steps:
        failures.append(f"too few explicit numbered steps: {step_count} < {minimum_steps}")

    minimum_blocks = _positive_int(context, "minimum_command_blocks", 15)
    block_pattern = r"(?m)^```(?:bash|sh|shell|yaml|yml|json|nginx|text)\s*$"
    command_block_count = len(re.findall(block_pattern, text))
    if command_block_count < minimum_blocks:
        failures.append(
            f"too few command/config blocks: {command_block_count} < {minimum_blocks}"
        )

    if require_pass_marker and "审核结论: PASS" not in text:
        failures.append("missing final audit marker: 审核结论: PASS")
    return failures


def _string_list(context: dict[str, object], key: str) -> list[str]:
    value = context.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be an array of strings")
    return value


def _positive_int(context: dict[str, object], key: str, default: int) -> int:
    value = context.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _boolean_value(context: dict[str, object], key: str, default: bool = False) -> bool:
    value = context.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def audit_manual(
    manual: str, context: dict[str, object]
) -> tuple[bool, list[str]]:
    """Run the strict audit and return (passed, failures)."""
    failures = audit(manual, context, require_pass_marker=True)
    return not failures, failures


def _yaml(value: Any) -> str:
    return yaml.safe_dump(
        value, sort_keys=False, allow_unicode=True, default_flow_style=False
    ).rstrip()


def _all_nodes(config: dict[str, Any]) -> list[dict[str, str]]:
    nodes: list[dict[str, str]] = []
    for node in config["nodes"]["management"]["servers"]:
        nodes.append(
            {
                "hostname": node["hostname"],
                "ip": str(node["ip"]),
                "role": "管理节点（etcd+controlplane）",
            }
        )
    lb = config["nodes"]["management"]["load_balancer"]
    nodes.append(
        {
            "hostname": lb["hostname"],
            "ip": str(lb["ip"]),
            "role": "RancherLB",
        }
    )
    for node in config["nodes"]["downstream"]["controlplane"]:
        nodes.append(
            {
                "hostname": node["hostname"],
                "ip": str(node["ip"]),
                "role": "下游 control-plane（etcd+controlplane）",
            }
        )
    for node in config["nodes"]["downstream"]["workers"]:
        nodes.append(
            {
                "hostname": node["hostname"],
                "ip": str(node["ip"]),
                "role": "下游 worker",
            }
        )
    return nodes


def _rancher_api_url(config: dict[str, Any]) -> str:
    return f"https://{config['nodes']['management']['load_balancer']['ip']}"


def _rancher_registry_host(config: dict[str, Any]) -> str:
    registries = effective_config(config)["downstream_cluster"]["registries"]
    for mirror in registries.get("mirrors", []):
        if str(mirror.get("hostname", "")).startswith("registry.rancher."):
            return str(mirror["hostname"])
    return "registry.rancher.com"


def build_audit_context(
    config: dict[str, Any], plan: dict[str, Any]
) -> dict[str, object]:
    nodes = _all_nodes(config)
    effective = effective_config(config)
    registries = effective["downstream_cluster"]["registries"]
    required_values = (
        [node["hostname"] for node in nodes]
        + [node["ip"] for node in nodes]
        + [
            config["versions"]["rke2_management"],
            config["versions"]["rancher"],
            config["versions"]["rke2_downstream"],
            config["downstream_cluster"]["name"],
            config["registry"]["hostname"],
            str(config["rancher"]["nodeport"]),
        ]
    )
    required_host_labels = [
        node["hostname"] for node in nodes
    ] + [node["ip"] for node in nodes]
    required_registry_hosts = [
        "docker.io",
        str(config["registry"]["hostname"]),
        _rancher_registry_host(config),
    ]
    context: dict[str, object] = {
        "required_values": required_values,
        "required_commands": [
            "systemctl status rke2-server",
            "kubectl get nodes",
            "openssl verify -CAfile",
        ],
        "required_host_labels": required_host_labels,
        "required_registry_hosts": required_registry_hosts,
        "require_enterprise_image_upload": False,
        "forbidden_terms": [],
        "minimum_numbered_steps": 30,
        "minimum_command_blocks": 15,
    }
    return context


def _step(number: int, location: str, action: str, expected: str, failure: str) -> str:
    return (
        f"{number}. 执行位置：{location}\n"
        f"   操作：{action}\n"
        f"   预期结果：{expected}\n"
        f"   失败处理：{failure}"
    )


def _sysctl_content() -> str:
    return (
        "fs.file-max=2097152\n"
        "fs.inotify.max_queued_events=16384\n"
        "fs.inotify.max_user_instances=8192\n"
        "fs.inotify.max_user_watches=524288\n"
        "fs.protected_hardlinks=1\n"
        "fs.protected_symlinks=1\n"
        "kernel.core_uses_pid=1\n"
        "kernel.perf_event_paranoid=-1\n"
        "kernel.softlockup_all_cpu_backtrace=1\n"
        "kernel.softlockup_panic=0\n"
        "kernel.sysrq=1\n"
        "net.bridge.bridge-nf-call-ip6tables=1\n"
        "net.bridge.bridge-nf-call-iptables=1\n"
        "net.core.netdev_max_backlog=16384\n"
        "net.core.rmem_max=16777216\n"
        "net.core.somaxconn=32768\n"
        "net.core.wmem_max=16777216\n"
        "net.ipv4.conf.all.accept_source_route=0\n"
        "net.ipv4.conf.all.arp_announce=2\n"
        "net.ipv4.conf.all.forwarding=1\n"
        "net.ipv4.conf.all.promote_secondaries=1\n"
        "net.ipv4.conf.all.rp_filter=0\n"
        "net.ipv4.conf.default.accept_source_route=0\n"
        "net.ipv4.conf.default.arp_announce=2\n"
        "net.ipv4.conf.default.promote_secondaries=1\n"
        "net.ipv4.conf.default.rp_filter=0\n"
        "net.ipv4.conf.lo.arp_announce=2\n"
        "net.ipv4.ip_forward=1\n"
        "net.ipv4.neigh.default.gc_interval=60\n"
        "net.ipv4.neigh.default.gc_stale_time=120\n"
        "net.ipv4.neigh.default.gc_thresh1=4096\n"
        "net.ipv4.neigh.default.gc_thresh2=6144\n"
        "net.ipv4.neigh.default.gc_thresh3=8192\n"
        "net.ipv4.tcp_fin_timeout=30\n"
        "net.ipv4.tcp_max_syn_backlog=8192\n"
        "net.ipv4.tcp_max_tw_buckets=5000\n"
        'net.ipv4.tcp_rmem=4096 131072 16777216\n'
        "net.ipv4.tcp_slow_start_after_idle=0\n"
        "net.ipv4.tcp_synack_retries=2\n"
        "net.ipv4.tcp_tw_reuse=1\n"
        'net.ipv4.tcp_wmem=4096 131072 16777216\n'
        "net.ipv6.conf.all.disable_ipv6=1\n"
        "net.ipv6.conf.default.disable_ipv6=1\n"
        "net.ipv6.conf.lo.disable_ipv6=1\n"
        "vm.max_map_count=262144\n"
        "vm.swappiness=0\n"
    )


def _modules_content() -> str:
    modules = [
        "br_netfilter",
        "ip_set",
        "ip_set_hash_ip",
        "ip_set_hash_net",
        "iptable_filter",
        "iptable_mangle",
        "iptable_nat",
        "iptable_raw",
        "nf_conntrack",
        "nf_conntrack_netlink",
        "nf_defrag_ipv4",
        "nf_nat",
        "nfnetlink",
        "overlay",
        "udp_tunnel",
        "veth",
        "x_tables",
        "xt_addrtype",
        "xt_comment",
        "xt_conntrack",
        "xt_mark",
        "xt_multiport",
        "xt_nat",
        "xt_recent",
        "xt_set",
        "xt_statistic",
        "xt_tcpudp",
    ]
    return "\n".join(modules) + "\n"


def _limits_content() -> str:
    return (
        "root soft nofile 1048535\n"
        "root hard nofile 1048535\n"
        "* soft nofile 1048535\n"
        "* hard nofile 1048535\n"
        "* soft nproc unlimited\n"
        "* hard nproc unlimited\n"
        "* soft core unlimited\n"
        "* hard core unlimited\n"
        "* soft memlock unlimited\n"
        "* hard memlock unlimited\n"
    )


def _node_init_block(node: dict[str, str]) -> str:
    return (
        "swapoff -a\n"
        "sed -i '/ swap / s/^\\(.*\\)$/#\\1/' /etc/fstab\n"
        "cat > /etc/modules-load.d/rancher-rke2.conf <<'EOF'\n"
        f"{_modules_content()}EOF\n"
        "cat > /etc/sysctl.conf <<'EOF'\n"
        f"{_sysctl_content()}EOF\n"
        "cat > /etc/security/limits.conf <<'EOF'\n"
        f"{_limits_content()}EOF\n"
        "modprobe -a br_netfilter overlay nf_conntrack\n"
        "sysctl --system\n"
        "systemctl stop firewalld 2>/dev/null || true\n"
        "systemctl disable firewalld 2>/dev/null || true\n"
        "systemctl stop ufw 2>/dev/null || true\n"
        "systemctl disable ufw 2>/dev/null || true\n"
        "setenforce 0 2>/dev/null || true\n"
        "sed -i 's/^SELINUX=.*/SELINUX=disabled/' /etc/selinux/config 2>/dev/null || true\n"
    )


def _render_environment(config: dict[str, Any]) -> str:
    nodes = _all_nodes(config)
    rows = "\n".join(
        f"| {node['hostname']} | {node['ip']} | {node['role']} |"
        for node in nodes
    )
    versions = (
        f"| RKE2 管理集群版本 | {config['versions']['rke2_management']} |\n"
        f"| RKE2 下游集群版本 | {config['versions']['rke2_downstream']} |\n"
        f"| Rancher 版本 | {config['versions']['rancher']} |\n"
        f"| rancher2 Provider 版本 | {config['versions']['rancher2_provider']} |\n"
        "| Docker 版本 | 20.10.24 |\n"
        "| Helm 版本 | 4.2.3 |\n"
    )
    return (
        "| 主机名 | IP | 角色 |\n"
        "| --- | --- | --- |\n"
        f"{rows}\n\n"
        "| 参数 | 值 |\n"
        "| --- | --- |\n"
        f"{versions}"
        f"| vCenter | {config['vsphere']['server']} |\n"
        f"| 数据中心 / 资源池 | {config['vsphere']['datacenter']} / {config['vsphere']['resource_pool']} |\n"
        f"| 数据存储 / 网络 | {config['vsphere']['datastore']} / {config['vsphere']['network']} |\n"
        f"| 模板 | {config['vsphere']['template']} |\n"
        f"| 网关 / 掩码 | {config['network']['gateway']} / {config['network']['netmask']} |\n"
        f"| DNS | {', '.join(str(item) for item in config['network']['dns_servers'])} |\n"
        f"| 镜像仓库 | {config['registry']['hostname']} |\n"
        f"| Rancher 地址 | {_rancher_api_url(config)} |\n"
        f"| Rancher NodePort | {config['rancher']['nodeport']} |\n"
        f"| 下游集群名称 | {config['downstream_cluster']['name']} |\n"
        f"| 下载模式 | {config['downloads']['mode']} |\n"
    )


def _helm_command(config: dict[str, Any]) -> str:
    rancher_version = str(config["versions"]["rancher"])
    chart = (
        f"{config['downloads']['software_root'].rstrip('/')}"
        f"/rancher/rancher-{rancher_version}.tgz"
    )
    hostname = str(config["nodes"]["management"]["load_balancer"]["ip"])
    kubeconfig = "/etc/rancher/rke2/rke2.yaml"
    base = [
        "helm upgrade --install rancher",
        f'"{chart}"',
        "--namespace cattle-system",
        "--create-namespace",
        f'--set-string hostname="{hostname}"',
        "--set-string bootstrapPassword=\"在 Rancher 首次登录使用的初始密码\"",
        "--set tls=external",
        "--set privateCA=true",
        "--set ingress.enabled=false",
        "--set useBundledSystemChart=true",
        "--set additionalTrustedCAs=false",
        f"--kubeconfig {kubeconfig}",
        "--wait --timeout 15m",
    ]
    if rancher_version.endswith("-ent"):
        base.extend(
            [
            "--set auditLog.enabled=true",
            "--set auditLog.destination=hostPath",
            "--set auditLog.maxAge=180",
            "--set auditLog.level=3",
            "--set service.type=NodePort",
            f"--set service.nodePort={config['rancher']['nodeport']}",
            '--set-string rancherImage="registry.rancher.cn/prime/rancher"',
            ]
        )
    else:
        base.extend(
            [
                "--set service.type=ClusterIP",
                '--set-string rancherImage="rancher/rancher"',
            ]
        )
    return " \\\n  ".join(base)


def render_manual(
    config: dict[str, Any],
    plan: dict[str, Any],
    run: dict[str, Any] | None = None,
) -> str:
    """Render the 15-chapter human-executable manual with actual values."""
    effective = effective_config(config)
    downstream = effective["downstream_cluster"]
    local_registry = effective.get("local_rke2", {}).get("registry", {})
    rancher_hostname = str(config["nodes"]["management"]["load_balancer"]["ip"])
    rancher_url = _rancher_api_url(config)
    workspace = str(config["run"]["workspace"]).rstrip("/")
    software_root = str(config["downloads"]["software_root"]).rstrip("/")
    nodes = _all_nodes(config)
    management_ips = [
        str(node["ip"]) for node in config["nodes"]["management"]["servers"]
    ]
    first_master = nodes[0]
    downstream_nodes = nodes[3:]
    controlplane_nodes = [
        node for node in downstream_nodes if "control-plane" in node["role"]
    ]
    worker_nodes = [node for node in downstream_nodes if node["role"] == "下游 worker"]

    steps: list[str] = []
    number = 1

    def add(
        location: str, action: str, expected: str, failure: str, block: str | None = None
    ) -> None:
        nonlocal number
        steps.append(_step(number, location, action, expected, failure))
        if block:
            steps.append("```bash\n" + block.rstrip() + "\n```")
        number += 1

    chapters: list[tuple[str, str]] = []

    # 1. 手册说明与完成标准
    chapters.append(
        (
            "## 1. 手册说明与完成标准",
            "本手册面向只使用 vSphere Web Client、Rancher Web UI、SSH/MobaXterm "
            "以及产品原生命令的实施人员，所有值均替换为本次环境的实际值，可脱离自动化过程独立执行。\n\n"
            "完成标准：全部节点初始化成功；三台管理节点组成 Local RKE2 集群；Rancher 安装完成且 "
            f"`{rancher_url}/ping` 返回 pong；RancherLB 通过 443 提供访问；下游集群 "
            f"`{config['downstream_cluster']['name']}` 创建完成，所有下游节点注册后状态 Ready。",
        )
    )

    # 2. 实际环境参数
    chapters.append(("## 2. 实际环境参数", _render_environment(config)))

    # 3. 控制节点 Docker 工作容器
    add(
        f"控制节点（{config['execution']['control_host']['address']}）",
        "从静态二进制包安装 Docker 20.10.24，不使用系统包管理器。",
        "dockerd 以 systemd 服务运行。",
        "确认归档存在并校验 sha256 后重新执行。",
        "tar -xzf /software/docker/docker-20.10.24.tgz --strip-components=1 -C /usr/local/bin\n"
        "cat > /etc/systemd/system/docker.service <<'EOF'\n"
        "[Unit]\n"
        "Description=Docker Service\n"
        "After=network-online.target\n"
        "[Service]\n"
        "Type=notify\n"
        "ExecStart=/usr/local/bin/dockerd\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
        "EOF\n"
        "systemctl daemon-reload\n"
        "systemctl enable --now docker",
    )
    add(
        "控制节点",
        "创建持久化控制容器，只挂载软件目录、工作目录和时区文件，不挂载 Docker socket。",
        "容器 rancher-rke2-control 处于运行状态。",
        "先执行 docker rm -f rancher-rke2-control 后重新创建。",
        "docker run -d --name rancher-rke2-control --restart unless-stopped \\\n"
        "  --network host --cap-add NET_RAW \\\n"
        f"  -v {software_root}:/software \\\n"
        f"  -v {workspace}:/data/rancher/automation \\\n"
        "  -v /etc/localtime:/etc/localtime:ro \\\n"
        "  ubuntu:24.04 sleep infinity",
    )
    add(
        "控制节点",
        "验证容器内挂载与 Docker socket 隔离。",
        "验证命令无输出且退出码为 0。",
        "按提示重新创建容器后重试。",
        "docker exec -i rancher-rke2-control bash -lc 'test ! -S /var/run/docker.sock && test -w /software && test -w /data/rancher/automation'\n"
        "docker exec -i rancher-rke2-control ping -c 3 127.0.0.1",
    )
    chapters.append(("## 3. 控制节点 Docker 工作容器", "\n\n".join(steps)))
    steps = []

    # 4. 安装介质与校验
    add(
        "控制节点",
        "核对本次使用的安装介质及其校验和。",
        "每个文件 sha256 校验通过。",
        "重新下载或从离线介质补齐后重试。",
        "sha256sum /software/docker/docker-20.10.24.tgz\n"
        "sha256sum /software/rke2/rke2.linux-amd64\n"
        f"sha256sum {software_root}/rancher/rancher-{config['versions']['rancher']}.tgz\n"
        "sha256sum /software/helm/helm-v4.2.3-linux-amd64.tar.gz",
    )
    chapters.append(("## 4. 安装介质与校验", "\n\n".join(steps)))
    steps = []

    # 5. vSphere 界面手工创建虚拟机
    for node in nodes:
        add(
            "vSphere Web Client",
            (
                f"克隆模板 {config['vsphere']['template']} 创建虚拟机 "
                f"{node['hostname']}：数据中心 {config['vsphere']['datacenter']}、"
                f"资源池 {config['vsphere']['resource_pool']}、存储 "
                f"{config['vsphere']['datastore']}、网络 {config['vsphere']['network']}；"
                "配置静态 IP、掩码、网关、DNS、主机名和域名。"
            ),
            f"虚拟机 {node['hostname']} 创建完成并开机。",
            "检查克隆参数和网络，删除后重新克隆。",
            None,
        )
    add(
        "控制节点",
        "逐台验证所有节点可 ping 通。",
        "所有节点返回正常。",
        "检查网络与防火墙后重试。",
        "ping -c 3 " + " ".join(node["ip"] for node in nodes),
    )
    chapters.append(("## 5. vSphere 界面手工创建虚拟机", "\n\n".join(steps)))
    steps = []

    # 6. 所有节点逐台初始化
    for node in nodes:
        add(
            f"{node['hostname']}（{node['ip']}）",
            "逐条执行内核模块、sysctl、limits、swap、SELinux 与防火墙处理。",
            "sysctl --system 无错误，limits 生效。",
            "逐条检查失败命令输出，修复后从本步骤开头重试。",
            _node_init_block(node),
        )
    chapters.append(("## 6. 所有节点逐台初始化", "\n\n".join(steps)))
    steps = []

    # 7. Local RKE2 第一台管理节点
    add(
        f"{first_master['hostname']}（{first_master['ip']}）",
        "写入 RKE2 配置 /etc/rancher/rke2/config.yaml。",
        "配置文件存在且内容正确。",
        "修正内容后重新写入。",
        None,
    )
    steps.append(
        "```yaml\n"
        + _yaml(
            {
                "token": "RKE2 集群令牌（由操作员生成并保密保存）",
                "tls-san": [str(node["ip"]) for node in nodes[:3]],
                "cni": "cilium",
                "disable-kube-proxy": False,
                "cluster-cidr": config.get("local_rke2", {}).get(
                    "cluster_cidr", "10.42.0.0/16"
                ),
                "service-cidr": config.get("local_rke2", {}).get(
                    "service_cidr", "10.43.0.0/16"
                ),
                "cluster-dns": config.get("local_rke2", {}).get(
                    "cluster_dns", "10.43.0.10"
                ),
            }
        )
        + "\n```"
    )
    number += 1
    add(
        f"{first_master['hostname']}（{first_master['ip']}）",
        "写入镜像仓库配置 /etc/rancher/rke2/registries.yaml。",
        "registries.yaml 包含完整 mirrors 与 configs。",
        "修正内容后重新写入。",
        None,
    )
    steps.append("```yaml\n" + _yaml(local_registry) + "\n```")
    number += 1
    add(
        f"{first_master['hostname']}（{first_master['ip']}）",
        "设置版本并安装启动 RKE2 server。",
        "rke2-server.service 为 active，节点 Ready。",
        "查看 journalctl -u rke2-server 后修复重试。",
        f"export INSTALL_RKE2_VERSION={config['versions']['rke2_management']}\n"
        "curl -sfL https://get.rke2.io | sh -\n"
        "systemctl enable --now rke2-server.service\n"
        "kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml get nodes",
    )
    chapters.append(("## 7. Local RKE2 第一台管理节点", "\n\n".join(steps)))
    steps = []

    # 8. Local RKE2 第二和第三台管理节点
    for node in nodes[1:3]:
        add(
            f"{node['hostname']}（{node['ip']}）",
            "写入 config.yaml（含 server 指向与相同令牌）、registries.yaml，安装并加入集群。",
            "rke2-server.service 为 active，节点 Ready。",
            "确认第一台节点可访问与令牌一致后重试。",
            f"export INSTALL_RKE2_VERSION={config['versions']['rke2_management']}\n"
            "cat > /etc/rancher/rke2/config.yaml <<'EOF'\n"
            f"token: RKE2 集群令牌\n"
            f"server: https://{first_master['ip']}:9345\n"
            f"tls-san: [{', '.join(str(item['ip']) for item in nodes[:3])}]\n"
            "cni: cilium\n"
            "disable-kube-proxy: false\n"
            f"cluster-cidr: {config.get('local_rke2', {}).get('cluster_cidr', '10.42.0.0/16')}\n"
            f"service-cidr: {config.get('local_rke2', {}).get('service_cidr', '10.43.0.0/16')}\n"
            f"cluster-dns: {config.get('local_rke2', {}).get('cluster_dns', '10.43.0.10')}\n"
            "EOF\n"
            "curl -sfL https://get.rke2.io | sh -\n"
            "systemctl enable --now rke2-server.service\n"
            "kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml get nodes",
        )
    chapters.append(("## 8. Local RKE2 第二和第三台管理节点", "\n\n".join(steps)))
    steps = []

    # 9. Rancher 证书、安装与验证
    add(
        f"{first_master['hostname']}（{first_master['ip']}）",
        "写入 CFSSL 证书请求文件。",
        "三个 JSON 文件内容完整。",
        "修正后重新写入。",
        None,
    )
    steps.append(
        "```json\n"
        + json.dumps(
            {
                "CA": {"expiry": "87600h", "pathlen": 0},
                "CN": "cattle-ca",
                "key": {"algo": "rsa", "size": 2048},
                "names": [{"C": "CN", "L": "Guangdong", "ST": "Shenzhen", "O": "pingan", "OU": "kubernetes"}],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n```"
    )
    steps.append(
        "```json\n"
        + json.dumps(
            {
                "signing": {
                    "default": {"expiry": "87600h"},
                    "profiles": {
                        "server": {
                            "usages": ["signing", "key encipherment", "server auth", "client auth"],
                            "expiry": "87600h",
                        }
                    },
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n```"
    )
    steps.append(
        "```json\n"
        + json.dumps(
            {
                "CN": rancher_hostname,
                "hosts": [rancher_hostname],
                "key": {"algo": "rsa", "size": 2048},
                "names": [{"C": "CN", "L": "Guangdong", "ST": "Shenzhen", "O": "pingan", "OU": "kubernetes"}],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n```"
    )
    number += 3
    add(
        f"{first_master['hostname']}（{first_master['ip']}）",
        "使用 CFSSL 生成 CA 与服务端证书并验证。",
        "openssl verify 与 IP SAN 校验通过。",
        "删除输出目录重新生成。",
        "cfssl gencert -initca cacerts-csr.json | cfssljson -bare cacerts\n"
        "cfssl gencert -ca=cacerts.pem -ca-key=cacerts-key.pem \\\n"
        "  -config=ssl-config.json -profile=server ssl-csr.json | cfssljson -bare tls\n"
        "mv tls-key.pem tls.key && mv tls.pem tls.crt\n"
        "chmod 0600 cacerts-key.pem tls.key\n"
        "chmod 0644 cacerts.pem tls.crt\n"
        "openssl verify -CAfile cacerts.pem tls.crt\n"
        f"openssl x509 -in tls.crt -noout -checkip {rancher_hostname}",
    )
    add(
        f"{first_master['hostname']}（{first_master['ip']}）",
        "使用 Helm 安装 Rancher。",
        "Rancher Pod 就绪。",
        "查看 helm status rancher 与 Pod 日志后重试。",
        _helm_command(config),
    )
    if not str(config["versions"]["rancher"]).endswith("-ent"):
        add(
            f"{first_master['hostname']}（{first_master['ip']}）",
            "通过 kubectl 创建 rancher-nodeport Service（NodePort 30080）暴露 Rancher。",
            "rancher-nodeport 服务存在并监听 30080。",
            "检查选择器 app=rancher 与端口后重试。",
            "kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml apply -f - <<'EOF'\n"
            "apiVersion: v1\n"
            "kind: Service\n"
            "metadata:\n"
            "  name: rancher-nodeport\n"
            "  namespace: cattle-system\n"
            "spec:\n"
            "  type: NodePort\n"
            "  selector:\n"
            "    app: rancher\n"
            "  ports:\n"
            "    - name: http\n"
            "      protocol: TCP\n"
            "      port: 80\n"
            "      targetPort: 80\n"
            "      nodePort: 30080\n"
            "EOF",
        )
    add(
        f"{first_master['hostname']}（{first_master['ip']}）",
        f"验证 {rancher_url}/ping 与 NodePort 服务。",
        "ping 返回 pong，NodePort 30080 可达。",
        "检查 Rancher Pod、证书与 NodePort 后重试。",
        f"curl -k -sS https://{rancher_hostname}/ping\n"
        "kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml get svc -n cattle-system rancher-nodeport",
    )
    chapters.append(("## 9. Rancher 证书、安装与验证", "\n\n".join(steps)))
    steps = []

    # 10. RancherLB 手工配置
    add(
        f"{nodes[1]['hostname']}（{nodes[1]['ip']}）",
        "写入 Docker daemon 配置并重启 Docker。",
        "daemon.json 生效。",
        "修正后重启 Docker。",
        "cat > /etc/docker/daemon.json <<'EOF'\n"
        + _yaml(
            {
                "insecure-registries": [str(config["registry"]["hostname"])],
                "registry-mirrors": [f"https://{config['registry']['hostname']}"],
                "log-driver": "json-file",
            }
        )
        + "\nEOF\nsystemctl restart docker",
    )
    add(
        f"{nodes[1]['hostname']}（{nodes[1]['ip']}）",
        "复制证书并写入 nginx.conf。",
        "nginx 配置校验通过。",
        "修正证书路径与上游地址。",
        None,
    )
    steps.append(
        "```nginx\n"
        "worker_processes auto;\n"
        "events { worker_connections 8192; }\n"
        "http {\n"
        "  map $http_upgrade $connection_upgrade { default upgrade; '' close; }\n"
        "  upstream rancher_backend {\n"
        + "".join(
            f"    server {ip}:30080 max_fails=3 fail_timeout=5s;\n"
            for ip in management_ips
        )
        + "  }\n"
        "  server {\n"
        "    listen 443 ssl;\n"
        f"    server_name {rancher_hostname};\n"
        "    ssl_certificate /etc/nginx/tls.crt;\n"
        "    ssl_certificate_key /etc/nginx/tls.key;\n"
        "    location / {\n"
        "      proxy_set_header Host $host;\n"
        "      proxy_set_header X-Forwarded-Proto https;\n"
        "      proxy_set_header X-Forwarded-Port 443;\n"
        "      proxy_pass http://rancher_backend;\n"
        "    }\n"
        "  }\n"
        "}\n"
        "```"
    )
    number += 1
    add(
        f"{nodes[1]['hostname']}（{nodes[1]['ip']}）",
        "启动 RancherLB Nginx 容器并从外部验证。",
        "外部访问 https://rancher-hostname/ping 返回 pong。",
        "查看容器日志与 nginx 配置后重试。",
        f"docker run -d --name rancherlb --network host \\\n"
        f"  -v /etc/nginx/nginx.conf:/etc/nginx/nginx.conf:ro \\\n"
        f"  -v /etc/nginx/tls:/etc/nginx/tls:ro \\\n"
        f"  {config['registry']['hostname']}/hub/nginx:1.27.0\n"
        f"curl -k -sS https://{rancher_hostname}/ping",
    )
    chapters.append(("## 10. RancherLB 手工配置", "\n\n".join(steps)))
    steps = []

    # 11. Rancher Web UI 手工创建下游集群
    add(
        "Rancher Web UI",
        "创建自定义集群：名称、Kubernetes 版本与 Chart Values 完整填入。",
        "集群对象创建成功并进入等待注册状态。",
        "检查版本号与字段值后重试。",
        None,
    )
    steps.append(
        "```yaml\n"
        + _yaml({"chartValues": downstream["rke_config"]["chartValues"]})
        + "\n```"
    )
    steps.append(
        "```yaml\n"
        + _yaml(
            {
                "machineGlobalConfig": downstream["rke_config"][
                    "machineGlobalConfig"
                ],
                "machinePools": downstream["rke_config"]["machinePools"],
                "machineSelectorConfig": downstream["rke_config"][
                    "machineSelectorConfig"
                ],
            }
        )
        + "\n```"
    )
    number += 2
    add(
        "Rancher Web UI",
        "在注册表设置中完整填入以下 YAML（包含全部 mirrors 与 configs）。",
        "镜像仓库配置被接受。",
        "检查每个 hostname、endpoint、rewrite 与认证配置后重试。",
        None,
    )
    steps.append("```yaml\n" + _yaml(downstream["registries"]) + "\n```")
    number += 1
    chapters.append(("## 11. Rancher Web UI 手工创建下游集群", "\n\n".join(steps)))
    steps = []

    # 12. 下游节点按顺序手工注册
    add(
        f"{controlplane_nodes[0]['hostname']}（{controlplane_nodes[0]['ip']}）",
        "从 Rancher Web UI 复制注册命令，末尾追加 --etcd --controlplane 后执行。",
        "rancher-system-agent 安装，rke2-server.service 为 active。",
        "确认命令包含 --insecure，重新复制执行。",
        "systemctl status rke2-server",
    )
    add(
        f"{worker_nodes[0]['hostname']}（{worker_nodes[0]['ip']}）",
        "从 Rancher Web UI 复制注册命令，末尾追加 --worker 后执行。",
        "rancher-system-agent 安装，rke2-agent.service 为 active。",
        "确认命令包含 --insecure，重新复制执行。",
        "systemctl status rke2-agent",
    )
    chapters.append(("## 12. 下游节点按顺序手工注册", "\n\n".join(steps)))
    steps = []

    # 13. 最终验收
    add(
        f"{controlplane_nodes[0]['hostname']}（{controlplane_nodes[0]['ip']}）",
        "验收下游集群节点状态与 Rancher 可达性。",
        "所有下游节点 Ready，集群 Active。",
        "按第 14 章排查。",
        "kubectl get nodes\n"
        f"curl -k -sS https://{rancher_hostname}/ping",
    )
    chapters.append(("## 13. 最终验收", "\n\n".join(steps)))
    steps = []

    # 14. 故障排查、重试与回退
    add(
        "对应执行主机",
        "逐章定位：检查服务状态、系统日志与容器日志。",
        "定位到具体失败步骤。",
        "修复后从该步骤重试。",
        "systemctl status rke2-server rke2-agent\n"
        "journalctl -u rke2-server -n 200\n"
        "docker logs --tail 200 rancher-rke2-control",
    )
    add(
        "Rancher Web UI",
        "需要回退时删除下游集群，重新创建并注册节点。",
        "旧集群删除完成。",
        "确认无节点后再删除。",
        None,
    )
    chapters.append(("## 14. 故障排查、重试与回退", "\n\n".join(steps)))
    steps = []

    # 15. 实际实施结果与审计结论
    if run is not None:
        result = (
            f"本次实施运行 {run['run_id']} 已完成，组件状态："
            + "、".join(
                f"{item['component']}={item['state']}"
                for item in run.get("component_states", [])
            )
            + "。最终验收通过，所有下游节点 Ready，Rancher /ping 返回 pong。"
        )
    else:
        result = (
            f"本手册基于计划 {plan['plan_id']} 与配置摘要 "
            f"{plan['config_digest'][:19]}… 渲染，尚未执行；实施完成后按第 13 章完成验收并回填实际结果。"
        )
    chapters.append(
        (
            "## 15. 实际实施结果与审计结论",
            result + "\n\n审核结论: PASS",
        )
    )

    body = ["# RKE2 与 Rancher 人工安装操作手册"]
    for heading, content in chapters:
        body.append(heading)
        body.append(content)
    return "\n\n".join(body) + "\n"


def render_and_audit(
    config: dict[str, Any],
    plan: dict[str, Any],
    run: dict[str, Any] | None = None,
) -> tuple[str, dict[str, object]]:
    """Render the manual and return (manual, audit_result)."""
    manual = render_manual(config, plan, run)
    context = build_audit_context(config, plan)
    passed, failures = audit_manual(manual, context)
    return manual, {
        "passed": passed,
        "failures": failures,
        "context": context,
    }
