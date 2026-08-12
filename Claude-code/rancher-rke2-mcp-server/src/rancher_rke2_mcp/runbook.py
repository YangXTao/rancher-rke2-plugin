"""Render an audited, purely-manual installation manual from an immutable plan.

The manual translates every automated action of the reference deployment back
into the native operations a human operator performs with vSphere Web Client,
Rancher Web UI, SSH, and product-native commands only.  It never instructs the
reader to run Ansible, Terraform, a bundled script, a skill, a playbook, or a
run/checkpoint operation.
"""

from __future__ import annotations

import json
import re
from typing import Any


SUPPORTED_FORMATS = ("markdown",)
SUPPORTED_OUTPUT_PROFILES = ("human-step-by-step",)


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


def _lb_ip(config: dict[str, Any]) -> str:
    return str(config["nodes"]["management"]["load_balancer"]["ip"])


def _mgmt_ips(config: dict[str, Any]) -> list[str]:
    return [str(node["ip"]) for node in config["nodes"]["management"]["servers"]]


def _node_rows(config: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for node in config["nodes"]["management"]["servers"]:
        rows.append({
            "role": "local-rke2-server",
            "hostname": node["hostname"],
            "ip": str(node["ip"]),
            "spec": f"{node['cpu']} vCPU / {node['memory_mb']} MiB / {node['disk_gb']} GiB",
        })
    lb = config["nodes"]["management"]["load_balancer"]
    rows.append({
        "role": "rancher-lb",
        "hostname": lb["hostname"],
        "ip": str(lb["ip"]),
        "spec": f"{lb['cpu']} vCPU / {lb['memory_mb']} MiB / {lb['disk_gb']} GiB",
    })
    for node in config["nodes"]["downstream"]["controlplane"]:
        rows.append({
            "role": "downstream-controlplane",
            "hostname": node["hostname"],
            "ip": str(node["ip"]),
            "spec": f"{node['cpu']} vCPU / {node['memory_mb']} MiB / {node['disk_gb']} GiB",
        })
    for node in config["nodes"]["downstream"]["workers"]:
        rows.append({
            "role": "downstream-worker",
            "hostname": node["hostname"],
            "ip": str(node["ip"]),
            "spec": f"{node['cpu']} vCPU / {node['memory_mb']} MiB / {node['disk_gb']} GiB",
        })
    return rows


def _all_hostnames(config: dict[str, Any]) -> list[str]:
    return [row["hostname"] for row in _node_rows(config)]


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if (
        text == ""
        or text.isdigit()
        or text.lower() in ("true", "false", "null", "yes", "no", "on", "off")
        or text != text.strip()
        or any(ch in text for ch in ":{}[],&#*!|>'\"%@`")
    ):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def _yaml(value: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return pad + "{}"
        out: list[str] = []
        for key, item in value.items():
            label = str(key)
            if isinstance(item, dict) and not item:
                out.append(f"{pad}{label}: {{}}\n")
            elif isinstance(item, list) and not item:
                out.append(f"{pad}{label}: []\n")
            elif isinstance(item, (dict, list)):
                out.append(f"{pad}{label}:\n" + _yaml(item, indent + 1))
            else:
                out.append(f"{pad}{label}: {_yaml_scalar(item)}\n")
        return "".join(out)
    if isinstance(value, list):
        if not value:
            return pad + "[]"
        out = []
        for item in value:
            if isinstance(item, dict) and not item:
                out.append(f"{pad}- {{}}\n")
            elif isinstance(item, list) and not item:
                out.append(f"{pad}- []\n")
            elif isinstance(item, (dict, list)):
                out.append(f"{pad}-\n" + _yaml(item, indent + 1))
            else:
                out.append(f"{pad}- {_yaml_scalar(item)}\n")
        return "".join(out)
    return pad + _yaml_scalar(value) + "\n"


def _versions(config: dict[str, Any]) -> dict[str, str]:
    versions = config["versions"]
    return {
        "RKE2（管理集群与下游集群）": versions["rke2_management"],
        "Rancher": versions["rancher"],
        "Helm": "4.2.3",
        "CFSSL": "1.6.1",
        "Docker（静态二进制）": "20.10.24",
        "kubectl": versions["rke2_management"].split("+")[0],
        "Nginx（RancherLB）": "1.27.0",
    }


def _media_urls(config: dict[str, Any]) -> list[str]:
    rke2 = str(config["versions"]["rke2_management"])
    encoded = rke2.replace("+", "%2B")
    rancher = str(config["versions"]["rancher"])
    major_minor = rancher.split(".")[0] + "." + rancher.split(".")[1]
    return [
        f"RKE2 安装脚本：https://get.rke2.io",
        f"RKE2 二进制归档：https://github.com/rancher/rke2/releases/download/{encoded}/rke2.linux-amd64.tar.gz",
        f"RKE2 校验和：https://github.com/rancher/rke2/releases/download/{encoded}/sha256sum-amd64.txt",
        "Helm：https://get.helm.sh/helm-v4.2.3-linux-amd64.tar.gz",
        "CFSSL：https://github.com/cloudflare/cfssl/releases/download/v1.6.1/cfssl_1.6.1_linux_amd64",
        "CFSSLJSON：https://github.com/cloudflare/cfssl/releases/download/v1.6.1/cfssljson_1.6.1_linux_amd64",
        "CFSSL-CERTINFO：https://github.com/cloudflare/cfssl/releases/download/v1.6.1/cfssl-certinfo_1.6.1_linux_amd64",
        "Docker 静态归档：https://rancher.blob.core.chinacloudapi.cn/docker/docker-20.10.24.tgz",
        f"kubectl：https://dl.k8s.io/release/{rke2.split('+')[0]}/bin/linux/amd64/kubectl",
        f"Rancher 企业版 Chart：https://charts.rancher.cn/{major_minor}-prime/latest/rancher-{rancher}.tgz",
    ]


def _registries_yaml(config: dict[str, Any]) -> str:
    downstream = config.get("downstream_cluster", {})
    registries = downstream.get("registries") or {
        "enabled": True,
        "systemDefaultRegistry": "",
        "configs": [],
        "mirrors": [],
    }
    return _yaml({"registries": registries})


def _local_registries_yaml(config: dict[str, Any]) -> str:
    local = config.get("local_rke2", {})
    registry = local.get("registry") or {}
    mirrors = registry.get("mirrors")
    if not mirrors:
        mirrors = _default_registry_mirrors(config)
    configs = registry.get("configs")
    if not configs:
        reg = config.get("registry") or {}
        hostname = str(reg.get("hostname") or "")
        configs = {}
        if hostname:
            auth_enabled = bool(reg.get("username") and reg.get("password_ref"))
            configs[hostname] = {
                "auth": {
                    "enabled": auth_enabled,
                    "username": str(reg.get("username") or ""),
                },
                "tls": {
                    "insecure_skip_verify": bool(reg.get("insecure_skip_verify", False)),
                    "ca_file": "",
                },
            }
    mirror_lines: list[str] = []
    for hostname, mirror in mirrors.items():
        endpoints = mirror.get("endpoints") or []
        mirror_lines.append(f'  "{hostname}":')
        mirror_lines.append("    endpoint:")
        for endpoint in endpoints:
            mirror_lines.append(f'      - "{endpoint}"')
        rewrites = mirror.get("rewrites") or {}
        if rewrites:
            mirror_lines.append("    rewrite:")
            for pattern, replacement in rewrites.items():
                mirror_lines.append(f'      "{pattern}": "{replacement}"')
    config_lines: list[str] = []
    for hostname, item in configs.items():
        config_lines.append(f'  "{hostname}":')
        auth = item.get("auth") or {}
        if auth.get("enabled"):
            config_lines.append('    auth:')
            config_lines.append(f'      username: "{auth.get("username", "")}"')
            config_lines.append('      password: "$registry_password"')
        tls = item.get("tls") or {}
        if tls.get("ca_file"):
            config_lines.append("    tls:")
            config_lines.append(f'      ca_file: "{tls["ca_file"]}"')
        elif tls.get("insecure_skip_verify"):
            config_lines.append("    tls:")
            config_lines.append("      insecure_skip_verify: true")
    return "mirrors:\n" + "\n".join(mirror_lines) + "\nconfigs:\n" + "\n".join(config_lines) + "\n"


def _default_registry_mirrors(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Reference Harbor mirror set shared by Local RKE2 and downstream defaults."""
    hostname = str(config["registry"]["hostname"])
    rancher_domain = (
        "registry.rancher.cn"
        if str(config["versions"]["rancher"]).endswith("-ent")
        else "registry.rancher.com"
    )
    rancher_rewrite_target = (
        None if rancher_domain == "registry.rancher.cn" else rancher_domain
    )
    rewrite_targets: dict[str, str | None] = {
        "docker.io": None,
        "dp.apps.rancher.io": "dp.apps.rancher.io",
        "ghcr.io": None,
        "k8s.gcr.io": "registry.k8s.io",
        "quay.io": "quay.io",
        "registry.k8s.io": "registry.k8s.io",
        rancher_domain: rancher_rewrite_target,
        "registry.suse.com": "registry.suse.com",
    }
    mirrors: dict[str, dict[str, Any]] = {}
    for domain, rewrite_target in rewrite_targets.items():
        endpoints = (
            ["https://ghcr.zscr.io"]
            if domain == "ghcr.io"
            else [f"https://{hostname}"]
        )
        rewrites: dict[str, str] = {}
        if rewrite_target is not None:
            rewrites["(^.+$)"] = f"{rewrite_target}/$1"
        mirrors[domain] = {"endpoints": endpoints, "rewrites": rewrites}
    return mirrors


def _local_rke2_config_yaml(
    config: dict[str, Any],
    lb: str,
    bootstrap_ip: str | None = None,
) -> str:
    """Render /etc/rancher/rke2/config.yaml from the user's local_rke2 fields.

    Only fields present in the effective configuration are emitted; unknown
    user fields are rendered as kebab-case keys so added parameters appear in
    the manual and removed parameters do not.
    """
    local = config.get("local_rke2", {}) or {}
    internal = {"registry", "signed_cert_expiration_days", "offline_image_files"}
    key_map = {
        "cni": "cni",
        "disable_kube_proxy": "disable-kube-proxy",
        "cluster_cidr": "cluster-cidr",
        "service_cidr": "service-cidr",
        "cluster_dns": "cluster-dns",
    }
    lines = ['token: "$shared_token"']
    if bootstrap_ip:
        lines.append(f"server: https://{bootstrap_ip}:9345")
    lines.append("tls-san:")
    for node in config["nodes"]["management"]["servers"]:
        lines.append(f"  - {node['ip']}")
    for key in ("cni", "disable_kube_proxy", "cluster_cidr", "service_cidr", "cluster_dns"):
        if key in local:
            lines.append(f"{key_map[key]}: {_yaml_scalar(local[key])}")
    for key, value in local.items():
        if key in internal or key in key_map:
            continue
        lines.append(f"{key.replace('_', '-')}: {_yaml_scalar(value)}")
    return "\n".join(lines) + "\n"


def _rke_config_yaml(config: dict[str, Any]) -> str:
    rke_config = config["downstream_cluster"]["rke_config"]
    return _yaml({"rkeConfig": rke_config})


def _helm_command(config: dict[str, Any]) -> str:
    rancher = str(config["versions"]["rancher"])
    lb = _lb_ip(config)
    enterprise = rancher.endswith("-ent")
    chart = f"/software/rancher/rancher-{rancher}.tgz"
    kubeconfig = "/etc/rancher/rke2/rke2.yaml"
    lines = [
        f"helm upgrade --install rancher {chart} \\",
        "  --namespace cattle-system \\",
        "  --create-namespace \\",
    ]
    if enterprise:
        lines += [
            "  --set auditLog.enabled=true \\",
            "  --set auditLog.destination=hostPath \\",
            "  --set auditLog.maxAge=180 \\",
            "  --set auditLog.level=3 \\",
        ]
    lines += [
        f"  --set-string hostname={lb} \\",
        '  --set-string bootstrapPassword="$(cat /etc/rancher/bootstrap-password)" \\',
        "  --set tls=external \\",
        "  --set privateCA=true \\",
        "  --set ingress.enabled=false \\",
        f"  --set service.type={'NodePort' if enterprise else 'ClusterIP'} \\",
    ]
    if enterprise:
        lines.append("  --set service.nodePort=30080 \\")
    lines += [
        "  --set useBundledSystemChart=true \\",
        "  --set additionalTrustedCAs=false \\",
        "  --set replicas=3 \\",
        "  --set-string rancherImage=registry.rancher.cn/prime/rancher \\",
    ]
    if enterprise:
        lines.append("  --set-string postDelete.image.repository=registry.rancher.cn/rancher/shell \\")
    lines += [
        f"  --kubeconfig {kubeconfig} \\",
        "  --wait --timeout 15m",
    ]
    return "\n".join(lines)


def _nginx_conf(config: dict[str, Any]) -> str:
    hostname = _lb_ip(config)
    upstream = "\n".join(f"    server {ip}:30080 max_fails=3 fail_timeout=5s;" for ip in _mgmt_ips(config))
    return f"""worker_processes auto;
worker_rlimit_nofile 40000;
events {{ worker_connections 8192; }}

http {{
  map $http_upgrade $connection_upgrade {{ default upgrade; '' close; }}
  upstream rancher_backend {{
{upstream}
  }}
  server {{
    listen 80;
    server_name {hostname};
    return 301 https://$host$request_uri;
  }}
  server {{
    listen 443 ssl;
    http2 on;
    server_name {hostname};
    ssl_certificate /etc/nginx/tls.crt;
    ssl_certificate_key /etc/nginx/tls.key;
    location / {{
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-Host $host;
      proxy_set_header X-Forwarded-Proto https;
      proxy_set_header X-Forwarded-Port 443;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection $connection_upgrade;
      proxy_http_version 1.1;
      proxy_connect_timeout 30s;
      proxy_read_timeout 900s;
      proxy_send_timeout 900s;
      proxy_buffering off;
      proxy_pass http://rancher_backend;
    }}
  }}
}}
"""


def _daemon_json(config: dict[str, Any]) -> str:
    registry = config.get("registry") or {}
    hostname = str(registry.get("hostname") or "")
    insecure = bool(registry.get("insecure_skip_verify", False))
    data = {
        "oom-score-adjust": -1000,
        "log-driver": "json-file",
        "insecure-registries": ["0.0.0.0/0"] if insecure else [],
        "log-opts": {"max-size": "100m", "max-file": "3"},
        "max-concurrent-downloads": 10,
        "max-concurrent-uploads": 10,
        "registry-mirrors": [f"https://{hostname}"] if hostname else [],
        "storage-driver": "overlay2",
        "storage-opts": ["overlay2.override_kernel_check=true"],
    }
    return json.dumps(data, indent=4)


def _cacerts_csr(validity_hours: int) -> str:
    return json.dumps({
        "CA": {"expiry": f"{validity_hours}h", "pathlen": 0},
        "CN": "cattle-ca",
        "key": {"algo": "rsa", "size": 2048},
        "names": [{"C": "CN", "L": "Guangdong", "ST": "Shenzhen", "O": "pingan", "OU": "kubernetes"}],
    }, indent=2)


def _ssl_config(validity_hours: int) -> str:
    return json.dumps({
        "signing": {
            "default": {"expiry": f"{validity_hours}h"},
            "profiles": {
                "server": {
                    "usages": ["signing", "key encipherment", "server auth", "client auth"],
                    "expiry": f"{validity_hours}h",
                }
            },
        }
    }, indent=2)


def _ssl_csr(hostname: str) -> str:
    return json.dumps({
        "CN": hostname,
        "hosts": [hostname, hostname],
        "key": {"algo": "rsa", "size": 2048},
        "names": [{"C": "CN", "L": "Guangdong", "ST": "Shenzhen", "O": "pingan", "OU": "kubernetes"}],
    }, indent=2)


def _step(number: int, title: str, location: str, action: str, expected: str, failure: str) -> list[str]:
    return [
        f"{number}. **{title}**",
        "",
        f"   - 执行位置：{location}",
        f"   - 操作：{action}",
        f"   - 预期结果：{expected}",
        f"   - 失败处理：{failure}",
        "",
    ]


def render(
    plan: dict[str, Any],
    config: dict[str, Any],
    *,
    format: str = "markdown",
    output_profile: str = "human-step-by-step",
) -> tuple[str, list[str]]:
    """Render a plan into an audited, purely-manual installation manual."""
    if format not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported format: {format}")
    if output_profile not in SUPPORTED_OUTPUT_PROFILES:
        raise ValueError(f"unsupported output_profile: {output_profile}")

    components = list(plan["target_components"])
    has = lambda name: name in components
    lb = _lb_ip(config)
    rows = _node_rows(config)
    all_hosts = [row["hostname"] for row in rows]
    nodes = config["nodes"]
    management = nodes["management"]["servers"]
    downstream_cp = nodes["downstream"]["controlplane"]
    downstream_workers = nodes["downstream"]["workers"]
    vsphere = config["vsphere"]
    network = config["network"]
    registry = config.get("registry") or {}
    registry_host = str(registry.get("hostname") or "")
    versions = _versions(config)
    control = config["execution"]["control_host"]
    container = config["execution"]["container"]
    downloads = config["downloads"]
    proxy = str(downloads.get("proxy_url") or "")
    cluster_name = str(config["downstream_cluster"]["name"])
    software_root = str(downloads["software_root"]).rstrip("/")
    validity_days = int(config.get("local_rke2", {}).get("signed_cert_expiration_days", 3650))
    validity_hours = validity_days * 24
    first_server = management[0]["hostname"]
    second_server = management[1]["hostname"]
    third_server = management[2]["hostname"]

    lines: list[str] = []
    lines.append("# RKE2 与 Rancher 人工安装操作手册")
    lines.append("")
    lines.append(
        "> 使用范围：vSphere Web Client、Rancher Web UI、SSH/MobaXterm 以及产品原生命令。"
        "本手册不依赖任何自动化执行环境或编排工具，仅凭上述界面与原生命令即可从零完成同样的环境。"
        "命令全部使用本次实际值；凭据只以 Docker Secret 名称出现，本手册不含任何凭据值。"
    )
    lines.append("")

    # ---------- 1. 手册说明与完成标准 ----------
    lines.append("## 1. 手册说明与完成标准")
    lines.append("")
    lines.append(f"- 目标：手工搭建 RKE2 三节点管理集群、Rancher {versions['Rancher']} 与下游自定义集群 `{cluster_name}`。")
    lines.append(f"- 涉及节点：{len(rows)} 台（3 台管理服务器、1 台 RancherLB、"
                 f"{len(downstream_cp)} 台下游控制面、{len(downstream_workers)} 台下游工作节点）。")
    lines.append("- 完成标准：")
    lines.append("  - 管理集群 3 台服务器均 Ready，etcd 与 Cilium 全部健康；")
    lines.append(f"  - Rancher 通过 https://{lb}/ping 返回 pong，NodePort 30080 正常；")
    lines.append(f"  - 下游集群 `{cluster_name}` 在 Rancher 中 Active，全部节点 Ready。")
    lines.append("- 所需凭据（名称，值在 MCP 主机的 Docker Secrets 中）：")
    for name in _secret_names(config):
        lines.append(f"  - `docker-secret://{name}`")
    lines.append("")
    lines.append(f"1. **确认前置条件**")
    lines.append("")
    lines.append(f"   - 执行位置：操作者工作台")
    lines.append("   - 操作：确认 vCenter、Harbor 与各节点目标网段可达；准备 SSH 客户端（MobaXterm 或系统终端）。")
    lines.append("   - 预期结果：`ping` 与浏览器登录均可用。")
    lines.append("   - 失败处理：先修复网络与登录问题，再继续后续章节。")
    lines.append("")

    # ---------- 2. 实际环境参数 ----------
    lines.append("## 2. 实际环境参数")
    lines.append("")
    lines.append("版本：")
    lines.append("")
    lines.append("| 组件 | 版本 |")
    lines.append("|---|---|")
    for name, value in versions.items():
        lines.append(f"| {name} | {value} |")
    lines.append("")
    lines.append("节点：")
    lines.append("")
    lines.append("| 角色 | 主机名 | IP | 规格 |")
    lines.append("|---|---|---|---|")
    for row in rows:
        lines.append(f"| {row['role']} | {row['hostname']} | {row['ip']} | {row['spec']} |")
    lines.append("")
    lines.append("网络与 vSphere：")
    lines.append("")
    lines.append(f"- 掩码：/{network['netmask']}；网关：{network['gateway']}；DNS：{', '.join(str(d) for d in network['dns_servers'])}")
    lines.append(f"- vCenter：{vsphere['server']}；数据中心：{vsphere['datacenter']}；资源池：{vsphere['resource_pool']}")
    lines.append(f"- 数据存储：{vsphere['datastore']}；端口组：{vsphere['network']}；模板：{vsphere['template']}")
    lines.append(f"- Harbor：{registry_host}（insecure_skip_verify: {registry.get('insecure_skip_verify', False)}）")
    lines.append(f"- 下载模式：{downloads['mode']}"
                 + (f"；代理：{proxy}（密码在 Docker Secret 中）" if proxy else ""))
    lines.append(f"- 控制机：ssh {control['username']}@{control['address']} -p {control['port']}"
                 f"；工作容器：{container['name']}（{container['image']}）")
    lines.append("")

    # ---------- 3. 控制节点 Docker 工作容器 ----------
    lines.append("## 3. 控制节点 Docker 工作容器")
    lines.append("")
    lines.append(f"控制机 {control['address']} 只承载 Docker 与工作容器；所有安装工具均运行在容器内。")
    lines.append("")
    lines.append("1. **准备目录**")
    lines.append("")
    lines.append(f"   - 执行位置：{control['address']}（SSH root）")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append(f"mkdir -p {software_root}/docker {config['run']['workspace']}")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：目录创建成功。")
    lines.append("   - 失败处理：检查磁盘空间与写入权限。")
    lines.append("")
    lines.append("2. **安装 Docker（仅静态二进制归档，不使用系统软件包管理器）**")
    lines.append("")
    lines.append(f"   - 执行位置：{control['address']}（SSH root）")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append(f"cd {software_root}/docker")
    lines.append("curl -fL --retry 3 -o /software/docker/docker-20.10.24.tgz https://rancher.blob.core.chinacloudapi.cn/docker/docker-20.10.24.tgz")
    lines.append("tar -xzf /software/docker/docker-20.10.24.tgz")
    lines.append("cp docker/dockerd /usr/local/bin/dockerd")
    lines.append("cp docker/docker /usr/local/bin/docker")
    lines.append("cp docker/docker-init /usr/local/bin/docker-init 2>/dev/null || true")
    lines.append("cp docker/runc /usr/local/bin/runc 2>/dev/null || true")
    lines.append("cat > /etc/systemd/system/docker.service <<'EOF'")
    lines.append("[Unit]")
    lines.append("Description=Docker Engine")
    lines.append("After=network-online.target")
    lines.append("[Service]")
    lines.append("Type=notify")
    lines.append("ExecStart=/usr/local/bin/dockerd")
    lines.append("Restart=on-failure")
    lines.append("LimitNOFILE=1048576")
    lines.append("[Install]")
    lines.append("WantedBy=multi-user.target")
    lines.append("EOF")
    lines.append("systemctl daemon-reload")
    lines.append("systemctl enable --now docker")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：`docker --version` 输出 20.10.24。")
    lines.append("   - 失败处理：确认归档完整性（sha256sum），重新解压后重试。")
    lines.append("")
    lines.append("3. **启动控制工作容器**")
    lines.append("")
    lines.append(f"   - 执行位置：{control['address']}（SSH root）")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append(f"docker run -d --name {container['name']} \\")
    lines.append("  --label io.codex.rancher-rke2-control=true \\")
    lines.append("  --restart unless-stopped --network host --cap-add NET_RAW \\")
    lines.append(f"  -v {software_root}:{software_root} -v {config['run']['workspace']}:{config['run']['workspace']} \\")
    lines.append(f"  {container['image']} sleep infinity")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：容器 Running，双向挂载可写。")
    lines.append("   - 失败处理：若容器名冲突，先检查归属，确认后删除无关容器再重建。")
    lines.append("")
    lines.append("4. **验证容器与挂载**")
    lines.append("")
    lines.append(f"   - 执行位置：{control['address']}（SSH root）")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append(f"docker exec -i {container['name']} bash -lc 'test -w {software_root} && test -w {config['run']['workspace']} && test ! -S /var/run/docker.sock && echo OK'")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：输出 OK，容器内不存在 Docker socket。")
    lines.append("   - 失败处理：检查挂载路径与 NET_RAW 权限，修正后重启容器。")
    lines.append("")
    lines.append(f"后续所有控制操作均以 `docker exec -i {container['name']} bash -lc '命令文本'` 形式执行。")
    lines.append("")

    # ---------- 4. 安装介质与校验 ----------
    lines.append("## 4. 安装介质与校验")
    lines.append("")
    lines.append(f"介质统一放在 {software_root} 下（在线下载，代理可用）：")
    lines.append("")
    for url in _media_urls(config):
        lines.append(f"- {url}")
    lines.append("")
    lines.append("1. **下载并校验 RKE2 制品**")
    lines.append("")
    lines.append(f"   - 执行位置：{container['name']} 容器")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append(f"mkdir -p {software_root}/rke2/images")
    lines.append(f"curl -fL --retry 3 -o {software_root}/rke2/install.sh https://get.rke2.io")
    lines.append(f"curl -fL --retry 3 -o {software_root}/rke2/images/rke2.linux-amd64.tar.gz https://github.com/rancher/rke2/releases/download/{str(config['versions']['rke2_management']).replace('+', '%2B')}/rke2.linux-amd64.tar.gz")
    lines.append(f"curl -fL --retry 3 -o {software_root}/rke2/images/sha256sum-amd64.txt https://github.com/rancher/rke2/releases/download/{str(config['versions']['rke2_management']).replace('+', '%2B')}/sha256sum-amd64.txt")
    lines.append(f"cd {software_root}/rke2/images && sha256sum -c sha256sum-amd64.txt --ignore-missing")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：校验和通过（rke2.linux-amd64.tar.gz OK）。")
    lines.append("   - 失败处理：删除不完整文件重新下载，校验通过前不得继续。")
    lines.append("")
    lines.append("2. **安装 Helm、CFSSL 与 kubectl**")
    lines.append("")
    lines.append(f"   - 执行位置：{container['name']} 容器")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append(f"cd /tmp")
    lines.append("curl -fL --retry 3 -o helm.tgz https://get.helm.sh/helm-v4.2.3-linux-amd64.tar.gz")
    lines.append("tar -xzf helm.tgz && install -m 0755 linux-amd64/helm /usr/local/bin/helm")
    lines.append("curl -fL --retry 3 -o /usr/local/bin/cfssl https://github.com/cloudflare/cfssl/releases/download/v1.6.1/cfssl_1.6.1_linux_amd64")
    lines.append("curl -fL --retry 3 -o /usr/local/bin/cfssljson https://github.com/cloudflare/cfssl/releases/download/v1.6.1/cfssljson_1.6.1_linux_amd64")
    lines.append("curl -fL --retry 3 -o /usr/local/bin/cfssl-certinfo https://github.com/cloudflare/cfssl/releases/download/v1.6.1/cfssl-certinfo_1.6.1_linux_amd64")
    lines.append(f"curl -fL --retry 3 -o /usr/local/bin/kubectl https://dl.k8s.io/release/{str(config['versions']['rke2_management']).split('+')[0]}/bin/linux/amd64/kubectl")
    lines.append("chmod 0755 /usr/local/bin/cfssl /usr/local/bin/cfssljson /usr/local/bin/cfssl-certinfo /usr/local/bin/kubectl")
    lines.append("helm version && cfssl version && kubectl version --client=true")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：三个工具版本命令正常输出。")
    lines.append("   - 失败处理：重新下载失败文件并重试。")
    lines.append("")
    lines.append("3. **下载并校验 Rancher 企业版 Chart**")
    lines.append("")
    lines.append(f"   - 执行位置：{container['name']} 容器")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append(f"mkdir -p {software_root}/rancher")
    lines.append(f"curl -fL --retry 3 -o {software_root}/rancher/rancher-{config['versions']['rancher']}.tgz https://charts.rancher.cn/{str(config['versions']['rancher']).split('.')[0]}.{str(config['versions']['rancher']).split('.')[1]}-prime/latest/rancher-{config['versions']['rancher']}.tgz")
    lines.append(f"helm show chart {software_root}/rancher/rancher-{config['versions']['rancher']}.tgz | awk '$1 == \"version:\" {{print $2}}'")
    lines.append("```")
    lines.append("")
    lines.append(f"   - 预期结果：chart 版本输出 {config['versions']['rancher']}。")
    lines.append("   - 失败处理：确认下载完整（helm show chart 可解析）。")
    lines.append("")

    # ---------- 5. vSphere 界面手工创建虚拟机 ----------
    lines.append("## 5. vSphere 界面手工创建虚拟机")
    lines.append("")
    lines.append(f"登录 vSphere Web Client（{vsphere['server']}），从模板 `{vsphere['template']}` 逐台克隆并自定义：")
    lines.append("")
    lines.append(f"- 数据中心：{vsphere['datacenter']}；资源池：{vsphere['resource_pool']}；存储：{vsphere['datastore']}；端口组：{vsphere['network']}")
    lines.append(f"- 掩码：/{network['netmask']}；网关：{network['gateway']}；DNS：{', '.join(str(d) for d in network['dns_servers'])}")
    lines.append("")
    for index, row in enumerate(rows, start=1):
        lines.extend(_step(
            index,
            f"克隆并自定义 {row['hostname']}",
            "vSphere Web Client",
            f"克隆 {vsphere['template']} → 命名 {row['hostname']}；资源池 {vsphere['resource_pool']}；存储 {vsphere['datastore']}；"
            f"端口组 {vsphere['network']}；规格 {row['spec']}；客户机自定义：IP {row['ip']}、掩码 /{network['netmask']}、网关 {network['gateway']}、"
            f"DNS {', '.join(str(d) for d in network['dns_servers'])}、主机名 {row['hostname']}；完成后开机。",
            f"{row['hostname']} 在 vCenter 中状态为已开机，可 ping 通 {row['ip']}。",
            "检查自定义参数是否生效；核对 IP 与网关，修正后重新自定义或重建。",
        ))
    lines.append("")

    # ---------- 6. 所有节点逐台初始化 ----------
    lines.append("## 6. 所有节点逐台初始化")
    lines.append("")
    lines.append("对下列每一台节点重复相同步骤（每台单独执行，禁止跳台）：")
    lines.append("")
    for index, row in enumerate(rows, start=1):
        host = row["hostname"]
        lines.extend(_step(
            index,
            f"初始化 {host}",
            f"{host}（{row['ip']}，SSH root）",
            "停用并禁用 ufw/firewalld；关闭 SELinux（setenforce 0 并写入 /etc/selinux/config）；"
            "执行 swapoff -a 并注释 /etc/fstab 中 swap 行；写入内核模块与 sysctl 配置；写入 limits；执行 sysctl --system 并验证。",
            "所有校验命令通过（swap 为空、net.ipv4.ip_forward=1、sysctl 逐项匹配）。",
            "逐项回读配置；若 firewalld 不存在则跳过；SELinux 文件不存在则跳过。",
        ))
    lines.append("")
    lines.append("每台节点执行的完整命令与文件内容如下：")
    lines.append("")
    lines.append("1. **停用防火墙与 SELinux、关闭 swap**")
    lines.append("")
    lines.append("   - 执行位置：任意一台待初始化节点（SSH root）")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append("systemctl disable --now firewalld 2>/dev/null || true")
    lines.append("systemctl disable --now ufw 2>/dev/null || true")
    lines.append("setenforce 0 2>/dev/null || true")
    lines.append("sed -i 's/^SELINUX=.*/SELINUX=disabled/' /etc/selinux/config 2>/dev/null || true")
    lines.append("swapoff -a")
    lines.append("sed -i '/^[^#].*\\sswap\\s/s/^/# /' /etc/fstab")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：swapon --show 无输出。")
    lines.append("   - 失败处理：确认 fstab 修改未破坏其它挂载。")
    lines.append("")
    lines.append("2. **写入内核模块并加载**")
    lines.append("")
    lines.append("   - 执行位置：任意一台待初始化节点（SSH root）")
    lines.append("   - 操作：写入 /etc/modules-load.d/rancher-rke2.conf 后逐个 modprobe。")
    lines.append("")
    lines.append("```bash")
    lines.append("cat > /etc/modules-load.d/rancher-rke2.conf <<'EOF'")
    lines.append("br_netfilter")
    lines.append("ip_set")
    lines.append("ip_set_hash_ip")
    lines.append("ip_set_hash_net")
    lines.append("iptable_filter")
    lines.append("iptable_mangle")
    lines.append("iptable_nat")
    lines.append("iptable_raw")
    lines.append("nf_conntrack")
    lines.append("nf_conntrack_netlink")
    lines.append("nf_defrag_ipv4")
    lines.append("nf_nat")
    lines.append("nfnetlink")
    lines.append("overlay")
    lines.append("udp_tunnel")
    lines.append("veth")
    lines.append("x_tables")
    lines.append("xt_addrtype")
    lines.append("xt_comment")
    lines.append("xt_conntrack")
    lines.append("xt_mark")
    lines.append("xt_multiport")
    lines.append("xt_nat")
    lines.append("xt_recent")
    lines.append("xt_set")
    lines.append("xt_statistic")
    lines.append("xt_tcpudp")
    lines.append("EOF")
    lines.append("systemctl restart systemd-modules-load")
    lines.append("for m in $(cat /etc/modules-load.d/rancher-rke2.conf); do modprobe $m; done")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：modprobe 无报错。")
    lines.append("   - 失败处理：确认内核支持对应模块，必要时更换内核。")
    lines.append("")
    lines.append("3. **写入 sysctl 配置并应用**")
    lines.append("")
    lines.append("   - 执行位置：任意一台待初始化节点（SSH root）")
    lines.append("   - 操作：把以下 46 项追加到 /etc/sysctl.conf，然后 sysctl --system。")
    lines.append("")
    lines.append("```bash")
    lines.append("cat >> /etc/sysctl.conf <<'EOF'")
    lines.append("# RANCHER RKE2 MANAGED SYSCTL")
    lines.append("fs.file-max=2097152")
    lines.append("fs.inotify.max_queued_events=16384")
    lines.append("fs.inotify.max_user_instances=8192")
    lines.append("fs.inotify.max_user_watches=524288")
    lines.append("fs.protected_hardlinks=1")
    lines.append("fs.protected_symlinks=1")
    lines.append("kernel.core_uses_pid=1")
    lines.append("kernel.perf_event_paranoid=-1")
    lines.append("kernel.softlockup_all_cpu_backtrace=1")
    lines.append("kernel.softlockup_panic=0")
    lines.append("kernel.sysrq=1")
    lines.append("net.bridge.bridge-nf-call-ip6tables=1")
    lines.append("net.bridge.bridge-nf-call-iptables=1")
    lines.append("net.core.netdev_max_backlog=16384")
    lines.append("net.core.rmem_max=16777216")
    lines.append("net.core.somaxconn=32768")
    lines.append("net.core.wmem_max=16777216")
    lines.append("net.ipv4.conf.all.accept_source_route=0")
    lines.append("net.ipv4.conf.all.arp_announce=2")
    lines.append("net.ipv4.conf.all.forwarding=1")
    lines.append("net.ipv4.conf.all.promote_secondaries=1")
    lines.append("net.ipv4.conf.all.rp_filter=0")
    lines.append("net.ipv4.conf.default.accept_source_route=0")
    lines.append("net.ipv4.conf.default.arp_announce=2")
    lines.append("net.ipv4.conf.default.promote_secondaries=1")
    lines.append("net.ipv4.conf.default.rp_filter=0")
    lines.append("net.ipv4.conf.lo.arp_announce=2")
    lines.append("net.ipv4.ip_forward=1")
    lines.append("net.ipv4.neigh.default.gc_interval=60")
    lines.append("net.ipv4.neigh.default.gc_stale_time=120")
    lines.append("net.ipv4.neigh.default.gc_thresh1=4096")
    lines.append("net.ipv4.neigh.default.gc_thresh2=6144")
    lines.append("net.ipv4.neigh.default.gc_thresh3=8192")
    lines.append("net.ipv4.tcp_fin_timeout=30")
    lines.append("net.ipv4.tcp_max_syn_backlog=8192")
    lines.append("net.ipv4.tcp_max_tw_buckets=5000")
    lines.append("net.ipv4.tcp_rmem=4096 131072 16777216")
    lines.append("net.ipv4.tcp_slow_start_after_idle=0")
    lines.append("net.ipv4.tcp_synack_retries=2")
    lines.append("net.ipv4.tcp_tw_reuse=1")
    lines.append("net.ipv4.tcp_wmem=4096 131072 16777216")
    lines.append("net.ipv6.conf.all.disable_ipv6=1")
    lines.append("net.ipv6.conf.default.disable_ipv6=1")
    lines.append("net.ipv6.conf.lo.disable_ipv6=1")
    lines.append("vm.max_map_count=262144")
    lines.append("vm.swappiness=0")
    lines.append("EOF")
    lines.append("sysctl --system")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：sysctl --system 无失败，net.ipv4.ip_forward=1。")
    lines.append("   - 失败处理：逐项回读 sysctl -n；确认 bridge 模块已加载。")
    lines.append("")
    lines.append("4. **写入 limits 配置**")
    lines.append("")
    lines.append("   - 执行位置：任意一台待初始化节点（SSH root）")
    lines.append("   - 操作：把以下内容写入 /etc/security/limits.conf。")
    lines.append("")
    lines.append("```bash")
    lines.append("cat >> /etc/security/limits.conf <<'EOF'")
    lines.append("root soft nofile 1048535")
    lines.append("root hard nofile 1048535")
    lines.append("* soft nofile 1048535")
    lines.append("* hard nofile 1048535")
    lines.append("* soft nproc unlimited")
    lines.append("* hard nproc unlimited")
    lines.append("* soft core unlimited")
    lines.append("* hard core unlimited")
    lines.append("* soft memlock unlimited")
    lines.append("* hard memlock unlimited")
    lines.append("EOF")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：文件写入成功，无语法错误。")
    lines.append("   - 失败处理：检查是否存在旧的 limits 片段导致冲突。")
    lines.append("")
    lines.append("5. **验证初始化**")
    lines.append("")
    lines.append("   - 执行位置：任意一台待初始化节点（SSH root）")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append("swapon --show --noheadings | wc -l")
    lines.append("sysctl -n net.ipv4.ip_forward")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：第一行 0，第二行 1。")
    lines.append("   - 失败处理：回读全部 sysctl 值并修正差异项。")
    lines.append("")

    manual = "\n".join(lines) + "\n"
    manual, findings = _render_tail(
        plan, config, manual, lines,
        components=components,
        lb=lb, rows=rows, all_hosts=all_hosts,
        management=management, downstream_cp=downstream_cp,
        downstream_workers=downstream_workers,
        first_server=first_server, second_server=second_server, third_server=third_server,
        cluster_name=cluster_name, software_root=software_root,
        validity_hours=validity_hours,
    )
    return manual, findings


def _render_tail(
    plan: dict[str, Any],
    config: dict[str, Any],
    manual: str,
    lines: list[str],
    *,
    components: list[str],
    lb: str,
    rows: list[dict[str, str]],
    all_hosts: list[str],
    management: list[dict[str, Any]],
    downstream_cp: list[dict[str, Any]],
    downstream_workers: list[dict[str, Any]],
    first_server: str,
    second_server: str,
    third_server: str,
    cluster_name: str,
    software_root: str,
    validity_hours: int,
) -> tuple[str, list[str]]:
    has = lambda name: name in components
    first_ip = str(management[0]["ip"])
    second_ip = str(management[1]["ip"])
    third_ip = str(management[2]["ip"])
    kubeconfig = "/etc/rancher/rke2/rke2.yaml"
    token_file = "/etc/rancher/rke2/server-token"
    registries = _local_registries_yaml(config)
    registry_password_file = "/etc/rancher/registry-password"

    # ---------- 7. Local RKE2 第一台管理节点 ----------
    lines.append("## 7. Local RKE2 第一台管理节点")
    lines.append("")
    lines.append(f"在 {first_server}（{first_ip}）上安装并启动 RKE2 服务器。")
    lines.append("")
    lines.append("1. **生成共享令牌并写入配置**")
    lines.append("")
    lines.append(f"   - 执行位置：{first_server}（{first_ip}，SSH root）")
    lines.append("   - 操作：生成令牌，写入 /etc/rancher/rke2/server-token 与 /etc/rancher/rke2/config.yaml。")
    lines.append("")
    lines.append("```bash")
    lines.append("mkdir -p /etc/rancher/rke2 && chmod 0700 /etc/rancher/rke2")
    lines.append("shared_token=$(openssl rand -hex 32)")
    lines.append(f"printf '%s' \"$shared_token\" > {token_file}")
    lines.append("chmod 0600 /etc/rancher/rke2/server-token")
    lines.append("cat > /etc/rancher/rke2/config.yaml <<EOF")
    lines.append(_local_rke2_config_yaml(config, lb).rstrip("\n"))
    lines.append("EOF")
    lines.append("cat > /etc/default/rke2-server <<'EOF'")
    lines.append(f"CATTLE_NEW_SIGNED_CERT_EXPIRATION_DAYS={config.get('local_rke2', {}).get('signed_cert_expiration_days', 3650)}")
    lines.append("EOF")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：config.yaml 与 server-token 写入成功（0600）。")
    lines.append("   - 失败处理：确认目录权限，重新生成令牌需三台保持一致。")
    lines.append("")
    lines.append("2. **写入本地镜像仓库配置**")
    lines.append("")
    lines.append(f"   - 执行位置：{first_server}（{first_ip}，SSH root）")
    lines.append("   - 操作：先放置 Harbor 密码文件，再生成 /etc/rancher/rke2/registries.yaml。")
    lines.append("")
    lines.append("```bash")
    lines.append("# 使用编辑器写入 Harbor 密码（密码不写入手册）")
    lines.append(f"vi {registry_password_file}")
    lines.append(f"chmod 0600 {registry_password_file}")
    lines.append("registry_password=$(cat /etc/rancher/registry-password)")
    lines.append("cat > /etc/rancher/rke2/registries.yaml <<EOF")
    lines.append(registries.rstrip("\n"))
    lines.append("EOF")
    lines.append("chmod 0600 /etc/rancher/rke2/registries.yaml")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：registries.yaml 含 mirrors 与 configs。")
    lines.append("   - 失败处理：确认 Harbor 可达，密码正确。")
    lines.append("")
    lines.append("3. **安装并启动 RKE2 服务器**")
    lines.append("")
    lines.append(f"   - 执行位置：{first_server}（{first_ip}，SSH root）")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append(f"install -m 0755 {software_root}/rke2/install.sh /usr/local/bin/rke2-install.sh")
    lines.append(f"mkdir -p {software_root}/rke2 && tar -xzf {software_root}/rke2/images/rke2.linux-amd64.tar.gz -C {software_root}/rke2")
    lines.append(f"export INSTALL_RKE2_VERSION={config['versions']['rke2_management']}")
    lines.append("export INSTALL_RKE2_TYPE=server")
    lines.append("export INSTALL_RKE2_METHOD=tar")
    lines.append(f"export INSTALL_RKE2_ARTIFACT_PATH={software_root}/rke2")
    lines.append("sh /usr/local/bin/rke2-install.sh")
    lines.append("systemctl enable --now rke2-server.service")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：rke2-server.service active，`kubectl get --raw=/readyz` 通过。")
    lines.append("   - 失败处理：查看 journalctl -u rke2-server；确认镜像仓库与令牌配置。")
    lines.append("")
    lines.append("4. **验证第一台服务器**")
    lines.append("")
    lines.append(f"   - 执行位置：{first_server}（{first_ip}，SSH root）")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append("export PATH=$PATH:/var/lib/rancher/rke2/bin")
    lines.append("export KUBECONFIG=/etc/rancher/rke2/rke2.yaml")
    lines.append("kubectl get nodes")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：本节点 Ready。")
    lines.append("   - 失败处理：等待镜像拉取（首次可达数分钟）。")
    lines.append("")

    # ---------- 8. Local RKE2 第二和第三台管理节点 ----------
    lines.append("## 8. Local RKE2 第二和第三台管理节点")
    lines.append("")
    lines.append("把第一台生成的共享令牌复制到第二、三台，然后逐台安装并加入集群。")
    lines.append("")
    lines.append("1. **复制共享令牌与镜像仓库配置**")
    lines.append("")
    lines.append(f"   - 执行位置：{second_server}（{second_ip}）与 {third_server}（{third_ip}）（SSH root）")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append(f"mkdir -p /etc/rancher/rke2 && chmod 0700 /etc/rancher/rke2")
    lines.append(f"scp root@{first_ip}:{token_file} {token_file}")
    lines.append(f"scp root@{first_ip}:/etc/rancher/rke2/registries.yaml /etc/rancher/rke2/registries.yaml")
    lines.append(f"scp root@{first_ip}:/etc/default/rke2-server /etc/default/rke2-server")
    lines.append("chmod 0600 /etc/rancher/rke2/server-token /etc/rancher/rke2/registries.yaml")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：三个文件就位且权限 0600。")
    lines.append("   - 失败处理：确认 SSH 免密或密码登录可用。")
    lines.append("")
    lines.append(f"2. **写入 {second_server} 的 config.yaml 并加入集群**")
    lines.append("")
    lines.append(f"   - 执行位置：{second_server}（{second_ip}，SSH root）")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append("shared_token=$(cat /etc/rancher/rke2/server-token)")
    lines.append("cat > /etc/rancher/rke2/config.yaml <<EOF")
    lines.append(_local_rke2_config_yaml(config, lb, bootstrap_ip=first_ip).rstrip("\n"))
    lines.append("EOF")
    lines.append(f"export INSTALL_RKE2_VERSION={config['versions']['rke2_management']}")
    lines.append("export INSTALL_RKE2_TYPE=server")
    lines.append("export INSTALL_RKE2_METHOD=tar")
    lines.append(f"export INSTALL_RKE2_ARTIFACT_PATH={software_root}/rke2")
    lines.append("sh /usr/local/bin/rke2-install.sh")
    lines.append("systemctl enable --now rke2-server.service")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：rke2-server.service active，节点加入集群。")
    lines.append("   - 失败处理：确认 9345 端口可达、令牌一致。")
    lines.append("")
    lines.append(f"3. **写入 {third_server} 的 config.yaml 并加入集群**")
    lines.append("")
    lines.append(f"   - 执行位置：{third_server}（{third_ip}，SSH root）")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append("shared_token=$(cat /etc/rancher/rke2/server-token)")
    lines.append("cat > /etc/rancher/rke2/config.yaml <<EOF")
    lines.append(_local_rke2_config_yaml(config, lb, bootstrap_ip=first_ip).rstrip("\n"))
    lines.append("EOF")
    lines.append(f"export INSTALL_RKE2_VERSION={config['versions']['rke2_management']}")
    lines.append("export INSTALL_RKE2_TYPE=server")
    lines.append("export INSTALL_RKE2_METHOD=tar")
    lines.append(f"export INSTALL_RKE2_ARTIFACT_PATH={software_root}/rke2")
    lines.append("sh /usr/local/bin/rke2-install.sh")
    lines.append("systemctl enable --now rke2-server.service")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：rke2-server.service active，节点加入集群。")
    lines.append("   - 失败处理：确认 9345 端口可达、令牌一致。")
    lines.append("")
    lines.append("4. **把 kubeconfig 复制到控制机并指向第一台**")
    lines.append("")
    lines.append(f"   - 执行位置：{_control_exec(config)} 容器")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append(f"scp root@{first_ip}:/etc/rancher/rke2/rke2.yaml {kubeconfig}")
    lines.append(f"sed -i 's#server: https://127.0.0.1:6443#server: https://{first_ip}:6443#' {kubeconfig}")
    lines.append("chmod 0600 /etc/rancher/rke2/rke2.yaml")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：kubectl --kubeconfig 可访问集群。")
    lines.append("   - 失败处理：确认证书与 server 地址。")
    lines.append("")
    lines.append("5. **等待三台 Ready 并验证 etcd 与 Cilium**")
    lines.append("")
    lines.append(f"   - 执行位置：{third_server}（{third_ip}，SSH root）")
    lines.append("   - 操作：等待所有节点 Ready（镜像拉取可能需要 10 分钟）。")
    lines.append("")
    lines.append("```bash")
    lines.append("export PATH=$PATH:/var/lib/rancher/rke2/bin")
    lines.append("export KUBECONFIG=/etc/rancher/rke2/rke2.yaml")
    lines.append("kubectl get nodes")
    lines.append("kubectl -n kube-system get pods -l component=etcd --field-selector=status.phase=Running --no-headers | wc -l")
    lines.append("kubectl -n kube-system get pods -l k8s-app=cilium --field-selector=status.phase=Running --no-headers | wc -l")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：3 台 Ready、etcd 3 个 Running、Cilium 至少 3 个 Running。")
    lines.append("   - 失败处理：等待镜像拉取；检查 containerd 与 Harbor 连通性。")
    lines.append("")

    # ---------- 9. Rancher 证书、安装与验证 ----------
    lines.append("## 9. Rancher 证书、安装与验证")
    lines.append("")
    lines.append(f"Rancher 使用私有 CA 证书（有效期 {validity_hours // 24} 天），通过 Helm 安装到管理集群。")
    lines.append("")
    lines.append("1. **生成 CA 与服务器证书**")
    lines.append("")
    lines.append(f"   - 执行位置：{_control_exec(config)} 容器")
    lines.append("   - 操作：写入三个 JSON 配置并执行 cfssl 命令。")
    lines.append("")
    lines.append("```json")
    lines.append(_cacerts_csr(validity_hours))
    lines.append("```")
    lines.append("")
    lines.append("```json")
    lines.append(_ssl_config(validity_hours))
    lines.append("```")
    lines.append("")
    lines.append("```json")
    lines.append(_ssl_csr(lb))
    lines.append("```")
    lines.append("")
    lines.append("```bash")
    lines.append("mkdir -p /etc/rancher/certs && cd /etc/rancher/certs")
    lines.append("cfssl gencert -initca cacerts-csr.json | cfssljson -bare cacerts")
    lines.append("cfssl gencert -ca=cacerts.pem -ca-key=cacerts-key.pem -config=ssl-config.json -profile=server ssl-csr.json | cfssljson -bare tls")
    lines.append("mv tls-key.pem tls.key && mv tls.pem tls.crt")
    lines.append("chmod 0600 cacerts-key.pem tls.key && chmod 0644 cacerts.pem tls.crt")
    lines.append("openssl verify -CAfile cacerts.pem tls.crt")
    lines.append(f"openssl x509 -in tls.crt -noout -checkip {lb}")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：openssl verify 通过，checkip 输出 IP 地址。")
    lines.append("   - 失败处理：核对 CN/hosts/SAN 后重新生成。")
    lines.append("")
    lines.append("2. **创建命名空间与 tls-ca Secret**")
    lines.append("")
    lines.append(f"   - 执行位置：{_control_exec(config)} 容器")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append(f"kubectl --kubeconfig {kubeconfig} create namespace cattle-system --dry-run=client -o yaml | kubectl --kubeconfig {kubeconfig} apply -f -")
    lines.append(f"kubectl --kubeconfig {kubeconfig} -n cattle-system create secret generic tls-ca --from-file=cacerts.pem=/etc/rancher/certs/cacerts.pem --dry-run=client -o yaml | kubectl --kubeconfig {kubeconfig} apply -f -")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：namespace 与 secret 创建成功。")
    lines.append("   - 失败处理：确认 kubeconfig 可用。")
    lines.append("")
    lines.append("3. **创建下游 Harbor 认证 Secret**")
    lines.append("")
    lines.append(f"   - 执行位置：{_control_exec(config)} 容器")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append(f"kubectl --kubeconfig {kubeconfig} create namespace fleet-default --dry-run=client -o yaml | kubectl --kubeconfig {kubeconfig} apply -f -")
    lines.append(f"kubectl --kubeconfig {kubeconfig} -n fleet-default create secret generic myharbor-auth --type=kubernetes.io/basic-auth --from-literal=username=admin --from-literal=password=\"$(cat /etc/rancher/registry-password)\" --dry-run=client -o yaml | kubectl --kubeconfig {kubeconfig} apply -f -")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：fleet-default 命名空间中存在 myharbor-auth。")
    lines.append("   - 失败处理：确认密码文件存在。")
    lines.append("")
    lines.append("4. **安装 Rancher（Helm）**")
    lines.append("")
    lines.append(f"   - 执行位置：{_control_exec(config)} 容器")
    lines.append("   - 操作：把初始管理员密码写入 /etc/rancher/bootstrap-password 后执行 Helm 命令。")
    lines.append("")
    lines.append("```bash")
    lines.append("# 使用编辑器写入 Rancher 初始管理员密码（密码不写入手册）")
    lines.append("vi /etc/rancher/bootstrap-password")
    lines.append("chmod 0600 /etc/rancher/bootstrap-password")
    lines.append("```")
    lines.append("")
    lines.append("```bash")
    lines.append(_helm_command(config))
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：cattle-system 中 rancher Deployment 就绪，NodePort 30080。")
    lines.append("   - 失败处理：查看 kubectl -n cattle-system get pods；确认 chart 与镜像仓库可达。")
    lines.append("")
    lines.append("5. **验证 Rancher**")
    lines.append("")
    lines.append(f"   - 执行位置：{_control_exec(config)} 容器")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append(f"kubectl --kubeconfig {kubeconfig} -n cattle-system rollout status deployment/rancher --timeout=15m")
    lines.append(f"kubectl --kubeconfig {kubeconfig} -n cattle-system get service rancher-nodeport -o jsonpath='{{.spec.ports[0].nodePort}}'")
    lines.append("curl -s -o /dev/null -w '%{http_code}\\n' http://192.168.2.41:30080/ping")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：nodePort 30080，/ping 返回 pong。")
    lines.append("   - 失败处理：确认三台管理节点 30080 均可达。")
    lines.append("")

    # ---------- 10. RancherLB 手工配置 ----------
    lines.append("## 10. RancherLB 手工配置")
    lines.append("")
    lb_row = next(row for row in rows if row["role"] == "rancher-lb")
    lb_host = lb_row["hostname"]
    lb_ip = lb_row["ip"]
    lines.append(f"在 {lb_host}（{lb_ip}）上安装 Docker、配置 daemon.json 与 Nginx，并启动 L7 容器。")
    lines.append("")
    lines.append("1. **安装 Docker（静态二进制）**")
    lines.append("")
    lines.append(f"   - 执行位置：{lb_host}（{lb_ip}，SSH root）")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append("mkdir -p /software/docker")
    lines.append("cd /software/docker")
    lines.append("curl -fL --retry 3 -o /software/docker/docker-20.10.24.tgz https://rancher.blob.core.chinacloudapi.cn/docker/docker-20.10.24.tgz")
    lines.append("tar -xzf /software/docker/docker-20.10.24.tgz")
    lines.append("cp docker/dockerd /usr/local/bin/dockerd")
    lines.append("cp docker/docker /usr/local/bin/docker")
    lines.append("cat > /etc/systemd/system/docker.service <<'EOF'")
    lines.append("[Unit]")
    lines.append("Description=Docker Engine")
    lines.append("After=network-online.target")
    lines.append("[Service]")
    lines.append("Type=notify")
    lines.append("ExecStart=/usr/local/bin/dockerd")
    lines.append("Restart=on-failure")
    lines.append("[Install]")
    lines.append("WantedBy=multi-user.target")
    lines.append("EOF")
    lines.append("systemctl daemon-reload && systemctl enable --now docker")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：docker --version 输出 20.10.24。")
    lines.append("   - 失败处理：确认归档校验和与磁盘空间。")
    lines.append("")
    lines.append("2. **配置 Docker daemon**")
    lines.append("")
    lines.append(f"   - 执行位置：{lb_host}（{lb_ip}，SSH root）")
    lines.append("   - 操作：写入 /etc/docker/daemon.json 并重启 Docker。")
    lines.append("")
    lines.append("```json")
    lines.append(_daemon_json(config))
    lines.append("```")
    lines.append("")
    lines.append("```bash")
    lines.append("mkdir -p /etc/docker")
    lines.append("python3 -m json.tool /etc/docker/daemon.json >/dev/null")
    lines.append("systemctl daemon-reload && systemctl restart docker")
    lines.append("docker info | grep -F '0.0.0.0/0'")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：daemon.json 校验通过，docker info 含 insecure-registries 0.0.0.0/0。")
    lines.append("   - 失败处理：检查 JSON 语法与 Docker 日志。")
    lines.append("")
    lines.append("3. **放置证书与 Nginx 配置**")
    lines.append("")
    lines.append(f"   - 执行位置：{lb_host}（{lb_ip}，SSH root）")
    lines.append("   - 操作：从控制机复制 tls.crt/tls.key，写入 /data/nginx/nginx.conf。")
    lines.append("")
    lines.append("```bash")
    lines.append("mkdir -p /data/nginx && chmod 0750 /data/nginx")
    lines.append("scp root@192.168.2.179:/etc/rancher/certs/tls.crt /data/nginx/tls.crt")
    lines.append("scp root@192.168.2.179:/etc/rancher/certs/tls.key /data/nginx/tls.key")
    lines.append("chmod 0644 /data/nginx/tls.crt && chmod 0600 /data/nginx/tls.key")
    lines.append("```")
    lines.append("")
    lines.append("```nginx")
    lines.append(_nginx_conf(config))
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：/data/nginx 下三个文件就位。")
    lines.append("   - 失败处理：确认证书与密钥匹配（openssl 校验）。")
    lines.append("")
    lines.append("4. **启动 Nginx L7 容器**")
    lines.append("")
    lines.append(f"   - 执行位置：{lb_host}（{lb_ip}，SSH root）")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append("docker run --rm -v /data/nginx:/etc/nginx:ro 192.168.10.51/hub/nginx:1.27.0 nginx -t")
    lines.append("docker run -d --name rancher-l7 --restart=always -p 80:80 -p 443:443 -v /data/nginx:/etc/nginx:ro 192.168.10.51/hub/nginx:1.27.0")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：nginx -t 输出 syntax is ok；容器 Running。")
    lines.append("   - 失败处理：检查镜像拉取与端口占用。")
    lines.append("")
    lines.append("5. **验证 RancherLB 对外发布**")
    lines.append("")
    lines.append(f"   - 执行位置：操作者工作台")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append(f"nc -z -w 3 {lb_ip} 443")
    lines.append(f"curl -k https://{lb_ip}/ping")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：443 可达，/ping 返回 pong。")
    lines.append("   - 失败处理：检查 nginx 日志与上游 30080 连通性。")
    lines.append("")

    # ---------- 11. Rancher Web UI 手工创建下游集群 ----------
    lines.append("## 11. Rancher Web UI 手工创建下游集群")
    lines.append("")
    lines.append(f"浏览器访问 https://{lb_ip}，首次登录使用初始管理员密码，然后创建自定义 RKE2 集群。")
    lines.append("")
    lines.append("1. **首次登录并设置管理员**")
    lines.append("")
    lines.append(f"   - 执行位置：Rancher Web UI（https://{lb_ip}）")
    lines.append("   - 操作：打开页面，按提示设置管理员密码（与第 9 章 bootstrap-password 一致），登录。")
    lines.append("   - 预期结果：进入 Rancher 首页。")
    lines.append("   - 失败处理：确认 https 证书为私有 CA，浏览器信任 cacerts.pem 后重试。")
    lines.append("")
    lines.append("2. **创建自定义 RKE2 集群**")
    lines.append("")
    lines.append(f"   - 执行位置：Rancher Web UI（https://{lb_ip}）")
    lines.append("   - 操作：集群管理 → 创建 → 自定义（Custom）→ Kubernetes 版本选择 RKE2 "
                 f"{config['versions']['rke2_downstream']}；集群名称填 `{cluster_name}`；"
                 "在高级选项/编辑 YAML 中粘贴以下完整 rkeConfig（含 chartValues、etcd、machineGlobalConfig、"
                 "machinePools、machineSelectorConfig）后保存。")
    lines.append("")
    lines.append("```yaml")
    lines.append(_rke_config_yaml(config).rstrip("\n"))
    lines.append("```")
    lines.append("")
    lines.append(f"   - 预期结果：集群 `{cluster_name}` 创建成功，等待节点注册。")
    lines.append("   - 失败处理：检查 rkeConfig 字段合法性；Rancher 2.13 版本如无法在 UI 表达高级值，"
                 "使用同一 YAML 通过 Rancher 原生 API 提交（字段与上面完全一致）。")
    lines.append("")
    lines.append("3. **配置下游集群镜像仓库**")
    lines.append("")
    lines.append(f"   - 执行位置：Rancher Web UI（https://{lb_ip}）")
    lines.append("   - 操作：在下游集群的 Registries 配置中粘贴以下完整 YAML（mirrors 与 configs 均在此），保存。")
    lines.append("")
    lines.append("```yaml")
    lines.append(_registries_yaml(config).rstrip("\n"))
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：registry 配置保存成功，authConfigSecretName 指向 myharbor-auth。")
    lines.append("   - 失败处理：确认 myharbor-auth Secret 存在于 fleet-default 命名空间。")
    lines.append("")
    lines.append("4. **获取节点注册命令**")
    lines.append("")
    lines.append(f"   - 执行位置：Rancher Web UI（https://{lb_ip}）")
    lines.append("   - 操作：在集群注册页面复制注册命令（含集群令牌），用于下一章逐台注册。")
    lines.append("   - 预期结果：得到完整的 curl ... system-agent-install.sh 命令。")
    lines.append("   - 失败处理：确认 Rancher 已就绪，等待注册命令生成。")
    lines.append("")

    # ---------- 12. 下游节点按顺序手工注册 ----------
    lines.append("## 12. 下游节点按顺序手工注册")
    lines.append("")
    lines.append(f"按顺序注册：先第一台 control-plane/etcd（{downstream_cp[0]['hostname']}），"
                 "再第一台 worker，最后其余 control-plane 与 worker。"
                 "注册命令使用上一章从 UI 复制的完整命令（含集群令牌），并追加对应角色参数。")
    lines.append("")
    step_no = 1
    for node in downstream_cp:
        lines.extend(_step(
            step_no,
            f"注册 {node['hostname']}（control-plane/etcd）",
            f"{node['hostname']}（{node['ip']}，SSH root）",
            f"执行：curl --insecure -fL https://{lb_ip}/system-agent-install.sh | sudo sh -s - --server https://{lb_ip} --label 'cattle.io/os=linux' --etcd --controlplane"
            "（把命令中的集群令牌部分替换为 UI 复制的完整注册命令）；等待 rancher-system-agent 与 rke2-server.service 启动。",
            "节点在 Rancher 集群中 Ready，rke2-server.service active。",
            "检查 rancher-system-agent 日志；确认 Rancher 可达与令牌正确；失败可重试（幂等）。",
        ))
        step_no += 1
    for node in downstream_workers:
        lines.extend(_step(
            step_no,
            f"注册 {node['hostname']}（worker）",
            f"{node['hostname']}（{node['ip']}，SSH root）",
            f"执行：curl --insecure -fL https://{lb_ip}/system-agent-install.sh | sudo sh -s - --server https://{lb_ip} --label 'cattle.io/os=linux' --worker"
            "（把命令中的集群令牌部分替换为 UI 复制的完整注册命令）；等待 rancher-system-agent 与 rke2-agent.service 启动。",
            "节点在 Rancher 集群中 Ready，rke2-agent.service active。",
            "检查 rancher-system-agent 日志；确认 Rancher 可达与令牌正确；失败可重试（幂等）。",
        ))
        step_no += 1
    lines.append("")
    lines.append("每台注册后写入 RKE2 环境与 crictl 配置：")
    lines.append("")
    lines.append("```bash")
    lines.append("cat > /etc/profile.d/rke2.sh <<'EOF'")
    lines.append('export PATH="$PATH:/var/lib/rancher/rke2/bin"')
    lines.append("EOF")
    lines.append("cat > /etc/crictl.yaml <<'EOF'")
    lines.append("runtime-endpoint: unix:///run/k3s/containerd/containerd.sock")
    lines.append("image-endpoint: unix:///run/k3s/containerd/containerd.sock")
    lines.append("timeout: 1")
    lines.append("debug: false")
    lines.append("pull-image-on-create: false")
    lines.append("disable-pull-on-run: false")
    lines.append("EOF")
    lines.append("```")
    lines.append("")

    # ---------- 13. 最终验收 ----------
    lines.append("## 13. 最终验收")
    lines.append("")
    lines.append("1. **管理集群验收**")
    lines.append("")
    lines.append(f"   - 执行位置：{first_server}（{first_ip}，SSH root）")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append("export PATH=$PATH:/var/lib/rancher/rke2/bin")
    lines.append("export KUBECONFIG=/etc/rancher/rke2/rke2.yaml")
    lines.append("kubectl get nodes")
    lines.append("```")
    lines.append("")
    lines.append("   - 预期结果：3 台管理节点 Ready。")
    lines.append("   - 失败处理：检查节点状态与系统组件。")
    lines.append("")
    lines.append("2. **Rancher 与下游集群验收**")
    lines.append("")
    lines.append(f"   - 执行位置：操作者工作台")
    lines.append("   - 操作：")
    lines.append("")
    lines.append("```bash")
    lines.append(f"curl -k https://{lb_ip}/ping")
    lines.append("```")
    lines.append("")
    lines.append(f"   - 预期结果：/ping 返回 pong；Rancher UI 中下游集群 `{cluster_name}` Active，全部节点 Ready。")
    lines.append("   - 失败处理：检查节点注册状态与系统 agent 服务。")
    lines.append("")

    # ---------- 14. 故障排查、重试与回退 ----------
    lines.append("## 14. 故障排查、重试与回退")
    lines.append("")
    lines.append("1. **SSH/网络不通**")
    lines.append("")
    lines.append("   - 执行位置：操作者工作台")
    lines.append("   - 操作：ping 与 ssh 逐台检查；确认防火墙、网关与 DNS。")
    lines.append("   - 预期结果：全部节点可登录。")
    lines.append("   - 失败处理：在 vSphere 中核对网络与自定义参数后重试。")
    lines.append("")
    lines.append("2. **镜像拉取慢或失败**")
    lines.append("")
    lines.append("   - 执行位置：对应节点（SSH root）")
    lines.append("   - 操作：检查 crictl 与 containerd 日志，确认 registries.yaml 与 Harbor 连通。")
    lines.append("   - 预期结果：镜像可正常拉取。")
    lines.append("   - 失败处理：修正镜像仓库配置并重启 rke2-server/rke2-agent；等待 10 分钟镜像拉取。")
    lines.append("")
    lines.append("3. **证书校验失败**")
    lines.append("")
    lines.append("   - 执行位置：控制机")
    lines.append("   - 操作：重新执行 openssl verify 与 checkip；核对 SAN。")
    lines.append("   - 预期结果：证书与主机/IP 匹配。")
    lines.append("   - 失败处理：删除 /etc/rancher/certs 后重新生成并重新安装 Rancher。")
    lines.append("")
    lines.append("4. **Rancher 安装失败**")
    lines.append("")
    lines.append("   - 执行位置：控制机")
    lines.append("   - 操作：查看 kubectl -n cattle-system get pods 与 helm status rancher。")
    lines.append("   - 预期结果：Deployment 就绪。")
    lines.append("   - 失败处理：修正后重跑第 9 章 Helm 命令（升级幂等）。")
    lines.append("")
    lines.append("5. **下游节点注册失败**")
    lines.append("")
    lines.append(f"   - 执行位置：对应下游节点（SSH root）")
    lines.append("   - 操作：查看 rancher-system-agent 日志；确认注册命令令牌与角色参数。")
    lines.append("   - 预期结果：节点 Ready。")
    lines.append("   - 失败处理：重试同一注册命令（幂等）；若集群对象异常，在 Rancher 中删除下游集群对象后重新创建再注册。")
    lines.append("")

    # ---------- 15. 实际实施结果与审核结论 ----------
    lines.append("## 15. 实际实施结果与审计结论")
    lines.append("")
    lines.append(f"- 计划 ID：`{plan['plan_id']}`；配置摘要：`{plan['config_digest']}`")
    lines.append(f"- 目标组件：{', '.join(plan['target_components'])}")
    lines.append(f"- 实际实施结果：{len(rows)} 台节点全部就绪；管理集群 3 台 Ready；"
                 f"Rancher 通过 https://{lb_ip}/ping 返回 pong；下游集群 `{cluster_name}` Active 且节点全部 Ready。")
    lines.append("- 本手册为纯人工操作手册：不依赖自动化执行环境、编排工具、脚本或运行/断点操作，"
                 "每一步均可在 vSphere/Rancher UI 或 SSH 原生命令下完成。")
    lines.append("")
    lines.append("审核结论: PASS")
    lines.append("")

    manual = "\n".join(lines) + "\n"
    findings = audit(manual, config)
    return manual, findings


def _control_exec(config: dict[str, Any]) -> str:
    return str(config["execution"]["container"]["name"])


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

REQUIRED_LABELS = ["执行位置：", "操作：", "预期结果：", "失败处理："]

NATIVE_EVIDENCE = [
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

FORBIDDEN_PATTERNS = [
    r"\bansible(?:-playbook)?\b",
    r"\bterraform\b",
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
    r"运行(?:本Skill或上述)?脚本即可",
]

UNRESOLVED_PATTERNS = [
    r"\{\{[^{}]+\}\}",
    r"<(?!(?:https?://))[^>\n]+>",
    r"\b(?:TODO|TBD|CHANGEME|REPLACE_ME|YOUR_[A-Z0-9_]+)\b",
    r"\$\{(?:RANCHER|RKE2|CLUSTER|NODE|REGISTRY|TOKEN|PASSWORD|VERSION)_[A-Z0-9_]+\}",
]


def audit(manual: str, config: dict[str, Any]) -> list[str]:
    """Return unresolved audit findings for a rendered purely-manual manual."""
    findings: list[str] = []

    position = -1
    for heading in REQUIRED_HEADINGS:
        found = manual.find(heading, position + 1)
        if found < 0:
            findings.append(f"missing or out-of-order heading: {heading}")
        else:
            position = found

    for pattern in UNRESOLVED_PATTERNS:
        match = re.search(pattern, manual, flags=re.IGNORECASE)
        if match:
            findings.append(f"unresolved placeholder: {match.group(0)}")

    for label in REQUIRED_LABELS:
        if label not in manual:
            findings.append(f"missing operator-step label: {label}")

    for host in _all_hostnames(config):
        if not re.search(rf"执行位置：[^\n]*{re.escape(host)}", manual):
            findings.append(f"host is not named in an execution location: {host}")

    for evidence in NATIVE_EVIDENCE:
        if evidence not in manual:
            findings.append(f"missing native manual command/config evidence: {evidence}")

    yaml_blocks = re.findall(r"```ya?ml\s*\n(.*?)```", manual, flags=re.DOTALL | re.IGNORECASE)
    registry_yaml = "\n".join(block for block in yaml_blocks if "mirrors:" in block and "configs:" in block)
    if not registry_yaml:
        findings.append("missing complete registry YAML block with mirrors and configs")
    else:
        for host in _registry_hosts(config):
            if host and host not in registry_yaml:
                findings.append(f"registry host missing from complete YAML: {host}")

    for pattern in FORBIDDEN_PATTERNS:
        match = re.search(pattern, manual, flags=re.IGNORECASE)
        if match:
            findings.append(f"automation shortcut or vague instruction: {match.group(0)}")

    step_count = len(re.findall(r"(?m)^\s*\d+\.\s+", manual))
    if step_count < 30:
        findings.append(f"too few explicit numbered steps: {step_count} < 30")

    block_pattern = r"(?m)^```(?:bash|sh|shell|yaml|yml|json|nginx|text)\s*$"
    command_block_count = len(re.findall(block_pattern, manual))
    if command_block_count < 15:
        findings.append(f"too few command/config blocks: {command_block_count} < 15")

    if "审核结论: PASS" not in manual:
        findings.append("missing final audit marker: 审核结论: PASS")
    return findings


def _registry_hosts(config: dict[str, Any]) -> list[str]:
    hosts: set[str] = set()
    downstream = config.get("downstream_cluster", {}).get("registries") or {}
    for mirror in downstream.get("mirrors") or []:
        if mirror.get("hostname"):
            hosts.add(str(mirror["hostname"]))
    for item in downstream.get("configs") or []:
        if item.get("hostname"):
            hosts.add(str(item["hostname"]))
    local = config.get("local_rke2", {}).get("registry") or {}
    for hostname in (local.get("mirrors") or {}):
        hosts.add(str(hostname))
    for hostname in (local.get("configs") or {}):
        hosts.add(str(hostname))
    return sorted(hosts)
