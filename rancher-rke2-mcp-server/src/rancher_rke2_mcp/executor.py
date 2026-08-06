from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import shlex
from threading import Thread
from typing import Any, Callable
from urllib.parse import quote, urlsplit, urlunsplit

from .secrets import DockerSecretResolver


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionResult:
    run_id: str
    succeeded: bool
    code: str
    artifact_path: str
    message: str = ""


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge user rkeConfig/registries values over reference defaults."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


_DEFAULT_DOWNSTREAM_RKE_CONFIG: dict[str, Any] = {
    "chartValues": {
        "rke2-cilium": {
            "hubble": {
                "enabled": True,
                "metrics": {
                    "enableOpenMetrics": True,
                    "enabled": [
                        "dns:query;ignoreAAAA",
                        "drop",
                        "tcp",
                        "flow",
                        "icmp",
                        "http",
                        "port-distribution",
                    ],
                },
                "relay": {"enabled": True},
                "ui": {"enabled": True},
            },
            "k8sServiceHost": "127.0.0.1",
            "k8sServicePort": "6443",
            "kubeProxyReplacement": True,
        }
    },
    "etcd": {
        "disableSnapshots": False,
        "snapshotRetention": 5,
        "snapshotScheduleCron": "0 */5 * * *",
    },
    "machineGlobalConfig": {
        "cluster-cidr": "10.42.0.0/16",
        "cluster-dns": "10.43.0.10",
        "cni": "cilium",
        "disable-kube-proxy": True,
        "etcd-arg": [
            "--auto-compaction-mode=periodic",
            "--auto-compaction-retention=1h0m0s",
            "--quota-backend-bytes=6442450944",
        ],
        "kube-apiserver-arg": [
            "--watch-cache=true",
            "--default-watch-cache-size=200",
            "--max-requests-inflight=400",
            "--max-mutating-requests-inflight=200",
        ],
        "kube-controller-manager-arg": [
            "--node-monitor-grace-period=20s",
            "--node-startup-grace-period=30s",
        ],
        "service-cidr": "10.43.0.0/16",
    },
    "machinePools": None,
    "machineSelectorConfig": [
        {
            "config": {
                "kubelet-arg": [
                    "kube-reserved=cpu=1,memory=2048Mi",
                    "system-reserved=cpu=1,memory=2048Mi",
                    "container-log-max-files=5",
                    "container-log-max-size=100Mi",
                    "cgroups-per-qos=true",
                    "enforce-node-allocatable=pods",
                    "eviction-hard=memory.available<256Mi,nodefs.available<10%,imagefs.available<15%,nodefs.inodesFree<5%",
                    "eviction-soft=memory.available<512Mi,nodefs.available<15%,imagefs.available<20%,nodefs.inodesFree<10%",
                    "eviction-soft-grace-period=memory.available=1m30s,nodefs.available=1m30s,imagefs.available=1m30s,nodefs.inodesFree=1m30s",
                    "eviction-max-pod-grace-period=30",
                    "eviction-pressure-transition-period=30s",
                    "max-open-files=1000000",
                    "registry-burst=10",
                    "registry-qps=0",
                    "serialize-image-pulls=false",
                    "sync-frequency=30s",
                    "max-pods=999",
                ]
            },
            "protect-kernel-defaults": False,
        }
    ],
}

class VmExecutor:
    """Run the VM-only Terraform bundle on the declared SSH control host.

    The executor deliberately uses a pre-provisioned known_hosts file and Paramiko
    password authentication.  It never accepts a first-seen host key and never
    puts a secret in a command line, an MCP result, or the server SQLite database.
    """

    def __init__(
        self,
        *,
        secret_root: str,
        known_hosts_path: str = "/run/secrets/control_host_known_hosts",
        assets_root: Path | None = None,
    ) -> None:
        self.secret_root = secret_root
        self.known_hosts_path = Path(known_hosts_path)
        self.assets_root = assets_root or Path(__file__).parent / "assets" / "vm"

    def ready(self) -> bool:
        return self.known_hosts_path.is_file() and self.assets_root.is_dir()

    def submit(
        self,
        *,
        config: dict[str, Any],
        run_id: str,
        on_started: Callable[[str], None],
        on_complete: Callable[[ExecutionResult], None],
    ) -> None:
        Thread(
            target=self._run_and_report,
            args=(config, run_id, on_started, on_complete),
            daemon=True,
            name=f"rancher-rke2-vm-{run_id[-8:]}",
        ).start()

    def _run_and_report(
        self,
        config: dict[str, Any],
        run_id: str,
        on_started: Callable[[str], None],
        on_complete: Callable[[ExecutionResult], None],
    ) -> None:
        workspace = str(config["run"]["workspace"]).rstrip("/")
        artifact_path = f"{workspace}/runs/{run_id}/vm"
        try:
            on_started(run_id)
            self.execute(config, artifact_path)
        except Exception:
            LOGGER.exception("VM execution failed for run %s", run_id)
            on_complete(ExecutionResult(run_id, False, "VM_EXECUTION_FAILED", artifact_path))
            return
        on_complete(ExecutionResult(run_id, True, "VM_EXECUTION_SUCCEEDED", artifact_path))

    def execute(self, config: dict[str, Any], artifact_path: str) -> None:
        """Run this component synchronously for a workflow coordinator."""
        self._run(config, artifact_path)

    def _run(self, config: dict[str, Any], artifact_path: str) -> None:
        import paramiko

        control = config["execution"]["control_host"]
        client = paramiko.SSHClient()
        client.load_host_keys(str(self.known_hosts_path))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        password = DockerSecretResolver(self.secret_root).resolve(control["password_ref"])
        try:
            client.connect(
                hostname=str(control["address"]),
                port=int(control["port"]),
                username=str(control["username"]),
                password=password,
                allow_agent=False,
                look_for_keys=False,
                timeout=15,
                banner_timeout=15,
                auth_timeout=15,
            )
            self._upload_bundle(client, config, artifact_path)
            container = config["execution"]["container"]
            command = " ".join(
                shlex.quote(value)
                for value in (
                    "bash",
                    f"{artifact_path}/vm-runner.sh",
                    artifact_path,
                    str(container["name"]),
                    str(container["image"]),
                    str(container["strategy"]),
                    str(config["downloads"]["software_root"]),
                    str(config["run"]["workspace"]),
                    str(config["downloads"]["mode"]),
                    str(config["versions"]["terraform"]),
                    str(config["versions"]["vsphere_provider"]),
                )
            )
            _, stdout, stderr = client.exec_command(command, timeout=1800)
            exit_status = stdout.channel.recv_exit_status()
            # Consume channels to avoid leaving SSH channel buffers open, but never
            # return command output because tools may print sensitive provider data.
            stdout.read()
            stderr.read()
            if exit_status != 0:
                raise RuntimeError("remote VM runner failed")
        finally:
            client.close()

    def _upload_bundle(
        self, client: paramiko.SSHClient, config: dict[str, Any], run_dir: str
    ) -> None:
        sftp = client.open_sftp()
        try:
            self._mkdirs(sftp, run_dir)
            for source in self.assets_root.rglob("*"):
                if source.is_file():
                    remote = self._asset_destination(source, run_dir)
                    self._mkdirs(sftp, remote.rsplit("/", 1)[0])
                    sftp.put(str(source), remote)
                    if source.suffix in {".sh", ".py"}:
                        sftp.chmod(remote, 0o700)
            self._put_text(sftp, f"{run_dir}/versions.tf", self._versions_tf(config))
            self._put_text(sftp, f"{run_dir}/terraform.auto.tfvars.json", self._tfvars(config))
            self._put_text(sftp, f"{run_dir}/vm-input.json", self._vm_input(config))
            self._put_text(
                sftp,
                f"{run_dir}/.runtime.env",
                self._runtime_env(config),
                mode=0o600,
            )
            self._put_text(
                sftp,
                f"{run_dir}/.dependency.env",
                self._dependency_env(config),
                mode=0o600,
            )
        finally:
            sftp.close()

    def _asset_destination(self, source: Path, run_dir: str) -> str:
        """Place Terraform source files in the directory Terraform executes in.

        Component assets may be packaged below ``assets/vm/terraform`` for source
        organization. Terraform intentionally does not recurse into child
        directories, so those files must be flattened into the component run
        directory before ``terraform init`` and ``terraform apply``.
        """
        relative = source.relative_to(self.assets_root)
        if relative.parts[0] == "terraform":
            if len(relative.parts) != 2 or source.suffix != ".tf":
                raise ValueError(f"unsupported Terraform asset path: {relative}")
            return f"{run_dir}/{source.name}"
        return f"{run_dir}/{relative.as_posix()}"

    @staticmethod
    def _mkdirs(sftp: paramiko.SFTPClient, path: str) -> None:
        current = ""
        for part in path.split("/"):
            if not part:
                current = "/"
                continue
            current = f"{current.rstrip('/')}/{part}"
            try:
                sftp.stat(current)
            except OSError:
                sftp.mkdir(current, mode=0o700)

    @staticmethod
    def _put_text(
        sftp: paramiko.SFTPClient, path: str, content: str, *, mode: int = 0o600
    ) -> None:
        with sftp.file(path, "w") as remote:
            remote.write(content)
        sftp.chmod(path, mode)

    @staticmethod
    def _all_nodes(config: dict[str, Any]) -> list[dict[str, Any]]:
        nodes = config["nodes"]
        return [
            *nodes["management"]["servers"],
            nodes["management"]["load_balancer"],
            *nodes["downstream"]["controlplane"],
            *nodes["downstream"]["workers"],
        ]

    def _tfvars(self, config: dict[str, Any]) -> str:
        vsphere = config["vsphere"]
        network = config["network"]
        nodes = {
            node["hostname"]: {
                "host_name": node["hostname"],
                "ip": str(node["ip"]),
                "netmask": int(network["netmask"]),
                "gateway": str(network["gateway"]),
                "cpu": int(node["cpu"]),
                "memory": int(node["memory_mb"]),
                "disk_gb": int(node["disk_gb"]),
            }
            for node in self._all_nodes(config)
        }
        payload = {
            "vsphere_server": vsphere["server"],
            "vsphere_insecure": vsphere["allow_unverified_ssl"],
            "datacenter": vsphere["datacenter"],
            "resource_pool_name": vsphere["resource_pool"],
            "datastore": vsphere["datastore"],
            "network_name": vsphere["network"],
            "template_name": vsphere["template"],
            "vm_folder": vsphere["vm_folder"],
            "vm_domain": network["vm_domain"],
            "dns_servers": [str(item) for item in network["dns_servers"]],
            "nodes": nodes,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    def _vm_input(self, config: dict[str, Any]) -> str:
        return json.dumps({"ips": [str(node["ip"]) for node in self._all_nodes(config)]})

    @staticmethod
    def _versions_tf(config: dict[str, Any]) -> str:
        version = str(config["versions"]["vsphere_provider"])
        return f'''terraform {{
  required_version = ">= 1.6.0"
  backend "local" {{}}
  required_providers {{
    vsphere = {{
      source  = "vmware/vsphere"
      version = {json.dumps(version)}
    }}
  }}
}}
'''

    def _runtime_env(self, config: dict[str, Any]) -> str:
        resolver = DockerSecretResolver(self.secret_root)
        values = {
            "TF_VAR_vsphere_user": str(config["vsphere"]["username"]),
            "TF_VAR_vsphere_password": resolver.resolve(config["vsphere"]["password_ref"]),
        }
        return "".join(f"export {key}={shlex.quote(value)}\n" for key, value in values.items())

    def _dependency_env(self, config: dict[str, Any]) -> str:
        """Return proxy variables for package/archive downloads only.

        Terraform talks directly to vCenter and must never inherit this file.
        """
        resolver = DockerSecretResolver(self.secret_root)
        downloads = config["downloads"]
        values: dict[str, str] = {}
        if downloads.get("proxy_username"):
            parsed = urlsplit(str(downloads["proxy_url"]))
            password = quote(resolver.resolve(downloads["proxy_password_ref"]), safe="")
            username = quote(str(downloads["proxy_username"]), safe="")
            endpoint = urlunsplit(
                (parsed.scheme, f"{username}:{password}@{parsed.netloc}", parsed.path, parsed.query, parsed.fragment)
            )
            values.update({"HTTP_PROXY": endpoint, "HTTPS_PROXY": endpoint, "http_proxy": endpoint, "https_proxy": endpoint})
        return "".join(f"export {key}={shlex.quote(value)}\n" for key, value in values.items())


class NodeInitExecutor(VmExecutor):
    """Execute the packaged node-initialization Ansible project remotely."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.assets_root = Path(__file__).parent / "assets" / "node-init"

    def submit(
        self, *, config: dict[str, Any], run_id: str,
        on_started: Callable[[str], None],
        on_complete: Callable[[ExecutionResult], None],
    ) -> None:
        Thread(target=self._run_and_report, args=(config, run_id, on_started, on_complete), daemon=True,
               name=f"rancher-rke2-node-init-{run_id[-8:]}").start()

    def _run_and_report(self, config: dict[str, Any], run_id: str,
                        on_started: Callable[[str], None],
                        on_complete: Callable[[ExecutionResult], None]) -> None:
        artifact_path = f"{str(config['run']['workspace']).rstrip('/')}/runs/{run_id}/node-init"
        try:
            on_started(run_id)
            self.execute(config, artifact_path)
        except Exception:
            LOGGER.exception("Node initialization failed for run %s", run_id)
            on_complete(ExecutionResult(run_id, False, "NODE_INIT_EXECUTION_FAILED", artifact_path))
            return
        on_complete(ExecutionResult(run_id, True, "NODE_INIT_SUCCEEDED", artifact_path))

    def execute(self, config: dict[str, Any], artifact_path: str) -> None:
        """Run this component synchronously for a workflow coordinator."""
        self._run_node_init(config, artifact_path)

    def _run_node_init(self, config: dict[str, Any], artifact_path: str) -> None:
        import paramiko
        control = config["execution"]["control_host"]
        client = paramiko.SSHClient()
        client.load_host_keys(str(self.known_hosts_path))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        password = DockerSecretResolver(self.secret_root).resolve(control["password_ref"])
        try:
            client.connect(hostname=str(control["address"]), port=int(control["port"]),
                           username=str(control["username"]), password=password,
                           allow_agent=False, look_for_keys=False, timeout=15,
                           banner_timeout=15, auth_timeout=15)
            self._upload_node_bundle(client, config, artifact_path)
            container = config["execution"]["container"]
            command = " ".join(shlex.quote(value) for value in (
                "bash", f"{artifact_path}/node-init-runner.sh", artifact_path,
                str(container["name"]), str(container["image"]), str(container["strategy"]),
                str(config["downloads"]["software_root"]), str(config["run"]["workspace"]),
                str(config["downloads"]["mode"])))
            _, stdout, stderr = client.exec_command(command, timeout=3600)
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError("remote node-init runner failed")
            stdout.read(); stderr.read()
        finally:
            client.close()

    def _upload_node_bundle(self, client: Any, config: dict[str, Any], run_dir: str) -> None:
        sftp = client.open_sftp()
        try:
            self._mkdirs(sftp, run_dir)
            for source in self.assets_root.rglob("*"):
                if source.is_file():
                    remote = f"{run_dir}/{source.relative_to(self.assets_root).as_posix()}"
                    self._mkdirs(sftp, remote.rsplit("/", 1)[0]); sftp.put(str(source), remote)
                    if source.suffix in {".sh", ".py"}: sftp.chmod(remote, 0o700)
            self._put_text(sftp, f"{run_dir}/.dependency.env", self._dependency_env(config), mode=0o600)
            resolver = DockerSecretResolver(self.secret_root)
            inventory = {"all": {"vars": {"ansible_connection": "paramiko", "ansible_user": config["node_access"]["username"], "ansible_password": resolver.resolve(config["node_access"]["password_ref"]), "ansible_become_password": resolver.resolve(config["node_access"]["password_ref"])}, "hosts": {node["hostname"]: {"ansible_host": str(node["ip"])} for node in self._all_nodes(config)}}}
            self._mkdirs(sftp, f"{run_dir}/ansible/inventory")
            self._put_text(sftp, f"{run_dir}/ansible/inventory/hosts.yml", json.dumps(inventory), mode=0o600)
        finally:
            sftp.close()


class LocalRke2Executor(VmExecutor):
    """Execute the packaged three-server Local RKE2 component remotely.

    The assets are copied from the validated ``rancher-rke2-local`` skill.  As
    with the earlier stages, only the SSH control host is contacted directly;
    Ansible and all artifact preparation execute inside the persistent control
    container.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.assets_root = Path(__file__).parent / "assets" / "local-rke2"

    def submit(
        self,
        *,
        config: dict[str, Any],
        run_id: str,
        on_started: Callable[[str], None],
        on_complete: Callable[[ExecutionResult], None],
    ) -> None:
        Thread(
            target=self._run_and_report,
            args=(config, run_id, on_started, on_complete),
            daemon=True,
            name=f"rancher-rke2-local-{run_id[-8:]}",
        ).start()

    def _run_and_report(
        self,
        config: dict[str, Any],
        run_id: str,
        on_started: Callable[[str], None],
        on_complete: Callable[[ExecutionResult], None],
    ) -> None:
        artifact_path = f"{str(config['run']['workspace']).rstrip('/')}/runs/{run_id}/local-rke2"
        try:
            on_started(run_id)
            self.execute(config, artifact_path)
        except Exception:
            LOGGER.exception("Local RKE2 execution failed for run %s", run_id)
            on_complete(ExecutionResult(run_id, False, "LOCAL_RKE2_EXECUTION_FAILED", artifact_path))
            return
        on_complete(ExecutionResult(run_id, True, "LOCAL_RKE2_SUCCEEDED", artifact_path))

    def execute(self, config: dict[str, Any], artifact_path: str) -> None:
        local = config.get("local_rke2", {})
        if local.get("cni", "cilium") != "cilium" or bool(local.get("disable_kube_proxy", False)):
            raise ValueError("Local RKE2 requires cilium with kube-proxy retained")
        self._run_local_rke2(config, artifact_path)

    def _run_local_rke2(self, config: dict[str, Any], artifact_path: str) -> None:
        import paramiko

        control = config["execution"]["control_host"]
        client = paramiko.SSHClient()
        client.load_host_keys(str(self.known_hosts_path))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        password = DockerSecretResolver(self.secret_root).resolve(control["password_ref"])
        try:
            client.connect(
                hostname=str(control["address"]),
                port=int(control["port"]),
                username=str(control["username"]),
                password=password,
                allow_agent=False,
                look_for_keys=False,
                timeout=15,
                banner_timeout=15,
                auth_timeout=15,
            )
            self._upload_local_bundle(client, config, artifact_path)
            container = config["execution"]["container"]
            offline_images = [str(item) for item in config.get("local_rke2", {}).get("offline_image_files", [])]
            command = " ".join(
                shlex.quote(value)
                for value in (
                    "bash",
                    f"{artifact_path}/local-rke2-runner.sh",
                    artifact_path,
                    str(container["name"]),
                    str(container["image"]),
                    str(container["strategy"]),
                    str(config["downloads"]["software_root"]),
                    str(config["run"]["workspace"]),
                    str(config["downloads"]["mode"]),
                    str(config["versions"]["rke2_management"]),
                    *offline_images,
                )
            )
            _, stdout, stderr = client.exec_command(command, timeout=7200)
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError("remote local-rke2 runner failed")
            stdout.read()
            stderr.read()
        finally:
            client.close()

    def _upload_local_bundle(self, client: Any, config: dict[str, Any], run_dir: str) -> None:
        sftp = client.open_sftp()
        try:
            self._mkdirs(sftp, run_dir)
            for source in self.assets_root.rglob("*"):
                if source.is_file():
                    remote = f"{run_dir}/{source.relative_to(self.assets_root).as_posix()}"
                    self._mkdirs(sftp, remote.rsplit("/", 1)[0])
                    sftp.put(str(source), remote)
                    if source.suffix == ".sh":
                        sftp.chmod(remote, 0o700)
            self._put_text(sftp, f"{run_dir}/.dependency.env", self._dependency_env(config), mode=0o600)
            self._mkdirs(sftp, f"{run_dir}/ansible/inventory")
            self._mkdirs(sftp, f"{run_dir}/ansible/group_vars")
            self._put_text(
                sftp,
                f"{run_dir}/ansible/inventory/hosts.yml",
                json.dumps(self._local_inventory(config), ensure_ascii=False, indent=2) + "\n",
                mode=0o600,
            )
            self._put_text(
                sftp,
                f"{run_dir}/ansible/group_vars/all.yml",
                json.dumps(self._local_group_vars(config, run_dir), ensure_ascii=False, indent=2) + "\n",
                mode=0o600,
            )
        finally:
            sftp.close()

    def _local_inventory(self, config: dict[str, Any]) -> dict[str, Any]:
        resolver = DockerSecretResolver(self.secret_root)
        management = config["nodes"]["management"]["servers"]
        return {
            "all": {
                "vars": {
                    "ansible_connection": "paramiko",
                    "ansible_user": str(config["node_access"]["username"]),
                    "ansible_password": resolver.resolve(config["node_access"]["password_ref"]),
                    "ansible_become_password": resolver.resolve(config["node_access"]["password_ref"]),
                },
                "children": {
                    "management_servers": {
                        "hosts": {
                            node["hostname"]: {"ansible_host": str(node["ip"])}
                            for node in management
                        }
                    }
                },
            }
        }

    def _local_group_vars(self, config: dict[str, Any], run_dir: str) -> dict[str, Any]:
        management = config["nodes"]["management"]["servers"]
        registry = config["registry"]
        local = config.get("local_rke2", {})
        local_registry = local.get("registry", {})
        hostname = str(registry["hostname"])
        mirrors = local_registry.get("mirrors")
        if mirrors is None:
            mirrors = {
                "docker.io": {"endpoints": [f"https://{hostname}"], "rewrites": {"(^.+$)": "hub/$1"}},
                "registry.rancher.cn": {"endpoints": [f"https://{hostname}"], "rewrites": {}},
                "registry.rancher.com": {"endpoints": [f"https://{hostname}"], "rewrites": {}},
            }
        configs = local_registry.get("configs")
        if configs is None:
            auth_enabled = bool(registry.get("username") and registry.get("password_ref"))
            configs = {
                hostname: {
                    "auth": {
                        "enabled": auth_enabled,
                        "username": registry.get("username") or "",
                        "password": DockerSecretResolver(self.secret_root).resolve(registry["password_ref"])
                        if auth_enabled else "",
                    },
                    "tls": {"insecure_skip_verify": bool(registry.get("insecure_skip_verify", False)), "ca_file": ""},
                }
            }
        run_id = run_dir.rstrip("/").rsplit("/runs/", 1)[-1].rsplit("/", 1)[0]
        return {
            "automation_run_id": run_id,
            "rke2_download_mode": config["downloads"]["mode"],
            "rke2_control_artifact_path": f"{config['downloads']['software_root'].rstrip('/')}/rke2",
            "rke2_management_version": config["versions"]["rke2_management"],
            "rke2_bootstrap_endpoint": str(management[0]["ip"]),
            "rke2_registration_endpoint": str(management[0]["ip"]),
            "rke2_tls_sans": [str(node["ip"]) for node in management],
            "rke2_signed_cert_expiration_days": int(local.get("signed_cert_expiration_days", 3650)),
            "rke2_cluster_cidr": str(local.get("cluster_cidr", "10.42.0.0/16")),
            "rke2_service_cidr": str(local.get("service_cidr", "10.43.0.0/16")),
            "rke2_cluster_dns": str(local.get("cluster_dns", "10.43.0.10")),
            "rke2_offline_image_files": [str(item) for item in local.get("offline_image_files", [])],
            "rancher_kubeconfig": f"{config['run']['workspace'].rstrip('/')}/runs/{run_id}/kubeconfig/rke2.yaml",
            "registry_enabled": bool(hostname),
            "registry_hostname": hostname,
            "registry_tls_insecure_skip_verify": bool(registry.get("insecure_skip_verify", False)),
            "registry_mirrors": mirrors,
            "registry_configs": configs,
        }


class RancherExecutor(VmExecutor):
    """Install Rancher and its independent L7 endpoint after Local RKE2.

    The component receives only secret *references* from MCP.  It resolves the
    values immediately into a mode-0600 run directory on the SSH control host,
    runs the packaged Rancher skill assets inside the persistent control
    container, and leaves the existing Local RKE2 kubeconfig in place.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.assets_root = Path(__file__).parent / "assets" / "rancher"

    def submit(self, *, config: dict[str, Any], run_id: str,
               on_started: Callable[[str], None],
               on_complete: Callable[[ExecutionResult], None]) -> None:
        Thread(target=self._run_and_report, args=(config, run_id, on_started, on_complete), daemon=True,
               name=f"rancher-rke2-rancher-{run_id[-8:]}").start()

    def _run_and_report(self, config: dict[str, Any], run_id: str,
                        on_started: Callable[[str], None],
                        on_complete: Callable[[ExecutionResult], None]) -> None:
        artifact_path = f"{str(config['run']['workspace']).rstrip('/')}/runs/{run_id}/rancher"
        try:
            on_started(run_id)
            self.execute(config, artifact_path)
        except Exception:
            LOGGER.exception("Rancher execution failed for run %s", run_id)
            on_complete(ExecutionResult(
                run_id, False, "RANCHER_EXECUTION_FAILED", artifact_path,
                f"Rancher runner failed; inspect {artifact_path}/rancher-install.log and {artifact_path}/rancher-lb.log.",
            ))
            return
        on_complete(ExecutionResult(run_id, True, "RANCHER_SUCCEEDED", artifact_path))

    def execute(self, config: dict[str, Any], artifact_path: str) -> None:
        source = str(config.get("_rancher_kubeconfig_source", ""))
        if not source.startswith("/"):
            raise ValueError("A successful Local RKE2 kubeconfig artifact is required before Rancher")
        self._run_rancher(config, artifact_path)

    def _run_rancher(self, config: dict[str, Any], artifact_path: str) -> None:
        import paramiko

        control = config["execution"]["control_host"]
        client = paramiko.SSHClient()
        client.load_host_keys(str(self.known_hosts_path))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        password = DockerSecretResolver(self.secret_root).resolve(control["password_ref"])
        try:
            client.connect(hostname=str(control["address"]), port=int(control["port"]),
                           username=str(control["username"]), password=password,
                           allow_agent=False, look_for_keys=False, timeout=15,
                           banner_timeout=15, auth_timeout=15)
            self._upload_rancher_bundle(client, config, artifact_path)
            container = config["execution"]["container"]
            command = " ".join(shlex.quote(value) for value in (
                "bash", f"{artifact_path}/rancher-runner.sh", artifact_path,
                str(container["name"]), str(container["image"]), str(container["strategy"]),
                str(config["downloads"]["software_root"]), str(config["run"]["workspace"]),
                str(config["downloads"]["mode"])))
            _, stdout, stderr = client.exec_command(command, timeout=7200)
            exit_status = stdout.channel.recv_exit_status()
            stdout.read(); stderr.read()
            if exit_status != 0:
                raise RuntimeError(f"remote Rancher runner failed; inspect {artifact_path}")
        finally:
            client.close()

    def _upload_rancher_bundle(self, client: Any, config: dict[str, Any], run_dir: str) -> None:
        sftp = client.open_sftp()
        try:
            self._mkdirs(sftp, run_dir)
            for source in self.assets_root.rglob("*"):
                if source.is_file():
                    remote = f"{run_dir}/{source.relative_to(self.assets_root).as_posix()}"
                    self._mkdirs(sftp, remote.rsplit("/", 1)[0])
                    sftp.put(str(source), remote)
                    if source.suffix in {".sh", ".py"}:
                        sftp.chmod(remote, 0o700)
            self._put_text(sftp, f"{run_dir}/.dependency.env", self._dependency_env(config), mode=0o600)
            self._mkdirs(sftp, f"{run_dir}/ansible/inventory")
            self._mkdirs(sftp, f"{run_dir}/ansible/group_vars")
            self._mkdirs(sftp, f"{run_dir}/secrets")
            self._put_text(sftp, f"{run_dir}/ansible/inventory/hosts.yml",
                           json.dumps(self._rancher_inventory(config), ensure_ascii=False, indent=2) + "\n", mode=0o600)
            self._put_text(sftp, f"{run_dir}/ansible/group_vars/all.yml",
                           json.dumps(self._rancher_group_vars(config, run_dir), ensure_ascii=False, indent=2) + "\n", mode=0o600)
            resolver = DockerSecretResolver(self.secret_root)
            self._put_text(sftp, f"{run_dir}/secrets/rancher-bootstrap-password",
                           resolver.resolve(config["rancher"]["bootstrap_password_ref"]) + "\n", mode=0o600)
        finally:
            sftp.close()

    def _rancher_inventory(self, config: dict[str, Any]) -> dict[str, Any]:
        resolver = DockerSecretResolver(self.secret_root)
        node_password = resolver.resolve(config["node_access"]["password_ref"])
        control = config["execution"]["control_host"]
        lb = config["nodes"]["management"]["load_balancer"]
        return {"all": {"vars": {"ansible_connection": "paramiko"}, "children": {
            "rancher_lb": {"hosts": {str(lb["hostname"]): {"ansible_host": str(lb["ip"]),
                "ansible_user": str(config["node_access"]["username"]), "ansible_password": node_password,
                "ansible_become_password": node_password}}},
            "control_host": {"hosts": {"automation-control": {"ansible_host": str(control["address"]),
                "ansible_port": int(control["port"]), "ansible_user": str(control["username"]),
                "ansible_password": resolver.resolve(control["password_ref"]),
                "ansible_become_password": resolver.resolve(control["password_ref"])}}},
        }}}

    def _rancher_group_vars(self, config: dict[str, Any], run_dir: str) -> dict[str, Any]:
        rancher = config["rancher"]
        registry = config["registry"]
        lb = config["nodes"]["management"]["load_balancer"]
        servers = config["nodes"]["management"]["servers"]
        resolver = DockerSecretResolver(self.secret_root)
        version = str(config["versions"]["rancher"])
        enterprise = version.endswith("-ent")
        registry_auth = bool(registry.get("username") and registry.get("password_ref"))
        return {
            "automation_run_id": run_dir.rstrip("/").rsplit("/runs/", 1)[-1].rsplit("/", 1)[0],
            "rancher_version": version, "rancher_base_version": version.removesuffix("-ent"),
            "rancher_chart_edition": "enterprise" if enterprise else "standard",
            "rancher_download_mode": config["downloads"]["mode"],
            "rancher_software_root": config["downloads"]["software_root"],
            "rancher_rke2_version": config["versions"]["rke2_management"],
            "rancher_hostname": str(lb["ip"]), "rancher_access_mode": "ip-compatibility",
            "rancher_lb_ip": str(lb["ip"]), "rancher_replicas": int(rancher["replicas"]),
            "rancher_http_nodeport": int(rancher["nodeport"]),
            "rancher_helm_service_type": "NodePort" if enterprise else "ClusterIP",
            # Both Rancher charts expose the L7 backend as rancher-nodeport.
            # Enterprise creates it through Helm; standard creates it through Ansible.
            "rancher_nodeport_service_name": "rancher-nodeport",
            "rancher_run_root": run_dir,
            "rancher_kubeconfig": config["_rancher_kubeconfig_source"],
            "rancher_bootstrap_password_file": f"{run_dir}/secrets/rancher-bootstrap-password",
            "rancher_certificate": {"source": "generate", "validity_days": 3650,
                "ca_file": f"{run_dir}/cert/output/cacerts.pem", "cert_file": f"{run_dir}/cert/output/tls.crt",
                "key_file": f"{run_dir}/cert/output/tls.key", "ip_sans": [str(lb["ip"])]},
            "rancher_chart_path": f"{config['downloads']['software_root'].rstrip('/')}/rancher/rancher-{version}.tgz",
            "rancher_chart_manifest_path": f"{config['downloads']['software_root'].rstrip('/')}/rancher/rancher-{version}.tgz.manifest",
            "management_server_ips": [str(node["ip"]) for node in servers],
            "registry_enabled": bool(registry.get("hostname")), "registry_hostname": str(registry.get("hostname") or ""),
            "registry_insecure_skip_verify": bool(registry.get("insecure_skip_verify", False)),
            "registry_auth_secret_enabled": registry_auth, "registry_username": str(registry.get("username") or ""),
            "registry_password": resolver.resolve(registry["password_ref"]) if registry_auth else "",
            "nginx_version": "1.27.0", "nginx_image": f"{registry.get('hostname')}/hub/nginx:1.27.0",
        }


class DownstreamExecutor(RancherExecutor):
    """Create and register the Rancher-managed downstream custom RKE2 cluster.

    The component requires a successful Rancher run for the same configuration:
    its private-CA certificate artifact is reused to verify the Rancher API, and
    the persistent control container must already exist.  Terraform, the exact
    rancher/rancher2 Provider mirror, Rancher API token handling, and the
    role-ordered Ansible registration all run inside that container from the
    validated downstream skill assets.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.assets_root = Path(__file__).parent / "assets" / "downstream"

    def submit(
        self,
        *,
        config: dict[str, Any],
        run_id: str,
        on_started: Callable[[str], None],
        on_complete: Callable[[ExecutionResult], None],
    ) -> None:
        Thread(
            target=self._run_and_report,
            args=(config, run_id, on_started, on_complete),
            daemon=True,
            name=f"rancher-rke2-downstream-{run_id[-8:]}",
        ).start()

    def _run_and_report(
        self,
        config: dict[str, Any],
        run_id: str,
        on_started: Callable[[str], None],
        on_complete: Callable[[ExecutionResult], None],
    ) -> None:
        artifact_path = f"{str(config['run']['workspace']).rstrip('/')}/runs/{run_id}/downstream"
        try:
            on_started(run_id)
            self.execute(config, artifact_path)
        except Exception:
            LOGGER.exception("Downstream execution failed for run %s", run_id)
            on_complete(ExecutionResult(
                run_id,
                False,
                "DOWNSTREAM_EXECUTION_FAILED",
                artifact_path,
                f"Downstream runner failed; inspect {artifact_path}/downstream-register.log "
                f"and {artifact_path}/terraform-apply.log.",
            ))
            return
        on_complete(ExecutionResult(run_id, True, "DOWNSTREAM_SUCCEEDED", artifact_path))

    def execute(self, config: dict[str, Any], artifact_path: str) -> None:
        source = str(config.get("_rancher_ca_source", ""))
        if not source.startswith("/"):
            raise ValueError(
                "A successful Rancher private-CA certificate artifact is required before downstream"
            )
        self._run_downstream(config, artifact_path)

    def _run_downstream(self, config: dict[str, Any], artifact_path: str) -> None:
        import paramiko

        control = config["execution"]["control_host"]
        client = paramiko.SSHClient()
        client.load_host_keys(str(self.known_hosts_path))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        password = DockerSecretResolver(self.secret_root).resolve(control["password_ref"])
        try:
            client.connect(
                hostname=str(control["address"]),
                port=int(control["port"]),
                username=str(control["username"]),
                password=password,
                allow_agent=False,
                look_for_keys=False,
                timeout=15,
                banner_timeout=15,
                auth_timeout=15,
            )
            self._upload_downstream_bundle(client, config, artifact_path)
            container = config["execution"]["container"]
            command = " ".join(
                shlex.quote(value)
                for value in (
                    "bash",
                    f"{artifact_path}/downstream-runner.sh",
                    artifact_path,
                    str(container["name"]),
                    str(container["image"]),
                    str(container["strategy"]),
                    str(config["downloads"]["software_root"]),
                    str(config["run"]["workspace"]),
                    str(config["downloads"]["mode"]),
                    self._rancher_api_url(config),
                    str(config["versions"]["rancher"]),
                    self._rancher_api_url(config),
                    str(config["_rancher_ca_source"]),
                    str(config["versions"]["rancher2_provider"]),
                    str(config["versions"]["terraform"]),
                )
            )
            _, stdout, stderr = client.exec_command(command, timeout=10800)
            exit_status = stdout.channel.recv_exit_status()
            stdout.read()
            stderr.read()
            if exit_status != 0:
                raise RuntimeError(f"remote downstream runner failed; inspect {artifact_path}")
        finally:
            client.close()

    def _upload_downstream_bundle(
        self, client: Any, config: dict[str, Any], run_dir: str
    ) -> None:
        sftp = client.open_sftp()
        try:
            self._mkdirs(sftp, run_dir)
            for source in self.assets_root.rglob("*"):
                if source.is_file():
                    remote = self._asset_destination(source, run_dir)
                    self._mkdirs(sftp, remote.rsplit("/", 1)[0])
                    sftp.put(str(source), remote)
                    if source.suffix in {".sh", ".py"}:
                        sftp.chmod(remote, 0o700)
            self._put_text(sftp, f"{run_dir}/versions.tf", self._versions_tf(config))
            self._put_text(
                sftp,
                f"{run_dir}/terraform.tfvars.json",
                self._downstream_tfvars(config),
                mode=0o600,
            )
            self._put_text(
                sftp,
                f"{run_dir}/.dependency.env",
                self._dependency_env(config),
                mode=0o600,
            )
            self._mkdirs(sftp, f"{run_dir}/ansible/inventory")
            self._mkdirs(sftp, f"{run_dir}/ansible/group_vars")
            self._mkdirs(sftp, f"{run_dir}/secrets")
            self._put_text(
                sftp,
                f"{run_dir}/ansible/inventory/hosts.yml",
                json.dumps(
                    self._downstream_inventory(config),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                mode=0o600,
            )
            self._put_text(
                sftp,
                f"{run_dir}/ansible/group_vars/all.yml",
                json.dumps(
                    self._downstream_group_vars(config, run_dir),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                mode=0o600,
            )
            resolver = DockerSecretResolver(self.secret_root)
            self._put_text(
                sftp,
                f"{run_dir}/secrets/rancher-bootstrap-password",
                resolver.resolve(config["rancher"]["bootstrap_password_ref"]) + "\n",
                mode=0o600,
            )
        finally:
            sftp.close()

    def _asset_destination(self, source: Path, run_dir: str) -> str:
        """Flatten Terraform source files into the directory Terraform executes in."""
        relative = source.relative_to(self.assets_root)
        if (
            len(relative.parts) == 2
            and relative.parts[0] == "terraform"
            and source.suffix == ".tf"
        ):
            return f"{run_dir}/{source.name}"
        return f"{run_dir}/{relative.as_posix()}"

    @staticmethod
    def _rancher_api_url(config: dict[str, Any]) -> str:
        lb = config["nodes"]["management"]["load_balancer"]
        return f"https://{lb['ip']}"

    def _downstream_tfvars(self, config: dict[str, Any]) -> str:
        downstream = config["downstream_cluster"]
        payload = {
            "rancher_api_url": self._rancher_api_url(config),
            "rancher_insecure": True,
            "cluster_name": downstream["name"],
            "kubernetes_version": config["versions"]["rke2_downstream"],
            "rke_config": self._downstream_rke_config(config),
            "registries": self._downstream_registries(config),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    def _downstream_rke_config(self, config: dict[str, Any]) -> dict[str, Any]:
        downstream = config.get("downstream_cluster", {})
        return _deep_merge(
            _DEFAULT_DOWNSTREAM_RKE_CONFIG,
            dict(downstream.get("rke_config") or {}),
        )

    def _downstream_registries(self, config: dict[str, Any]) -> dict[str, Any]:
        downstream = config.get("downstream_cluster", {})
        submitted = downstream.get("registries")
        if isinstance(submitted, dict) and submitted:
            # An explicitly submitted registries object is authoritative: every
            # mirror item keeps exactly the submitted endpoints and rewrites.
            return dict(submitted)
        return self._default_downstream_registries(config)

    def _default_downstream_registries(self, config: dict[str, Any]) -> dict[str, Any]:
        """Reference Harbor mirror defaults from the validated downstream skill."""
        harbor = str(config.get("registry", {}).get("hostname", ""))
        insecure = bool(
            config.get("registry", {}).get("insecure_skip_verify", True)
        )
        rancher_version = str(config.get("versions", {}).get("rancher", ""))
        rancher_registry = (
            "registry.rancher.cn"
            if rancher_version.endswith("-ent")
            else "registry.rancher.com"
        )
        return {
            "enabled": True,
            "systemDefaultRegistry": "",
            "configs": [
                {
                    "hostname": harbor,
                    "authConfigSecretName": "myharbor-auth",
                    "tlsSecretName": "",
                    "caBundle": "",
                    "insecure": insecure,
                }
            ],
            "mirrors": [
                {
                    "hostname": "docker.io",
                    "endpoints": [f"https://{harbor}"],
                },
                {
                    "hostname": "dp.apps.rancher.io",
                    "endpoints": [f"https://{harbor}"],
                    "rewrites": {"(^.+$)": "dp.apps.rancher.io/$1"},
                },
                {
                    "hostname": "ghcr.io",
                    "endpoints": ["https://ghcr.zscr.io"],
                    "rewrites": {},
                },
                {
                    "hostname": "k8s.gcr.io",
                    "endpoints": [f"https://{harbor}"],
                    "rewrites": {"(^.+$)": "registry.k8s.io/$1"},
                },
                {
                    "hostname": "quay.io",
                    "endpoints": [f"https://{harbor}"],
                    "rewrites": {"(^.+$)": "quay.io/$1"},
                },
                {
                    "hostname": "registry.k8s.io",
                    "endpoints": [f"https://{harbor}"],
                    "rewrites": {"(^.+$)": "registry.k8s.io/$1"},
                },
                {
                    "hostname": rancher_registry,
                    "endpoints": [f"https://{harbor}"],
                },
                {
                    "hostname": "registry.suse.com",
                    "endpoints": [f"https://{harbor}"],
                    "rewrites": {"(^.+$)": "registry.suse.com/$1"},
                },
            ],
        }

    def _versions_tf(self, config: dict[str, Any]) -> str:
        template = (self.assets_root / "terraform" / "versions.tf.tmpl").read_text(
            encoding="utf-8"
        )
        # The template already wraps the placeholder in quotes.
        version = str(config["versions"]["rancher2_provider"])
        return template.replace("__RANCHER2_PROVIDER_VERSION__", version)

    def _downstream_inventory(self, config: dict[str, Any]) -> dict[str, Any]:
        resolver = DockerSecretResolver(self.secret_root)
        controlplane = config["nodes"]["downstream"]["controlplane"]
        workers = config["nodes"]["downstream"]["workers"]

        def entries(nodes: list[dict[str, Any]], roles: list[str]) -> dict[str, Any]:
            return {
                node["hostname"]: {
                    "ansible_host": str(node["ip"]),
                    "rancher_node_roles": roles,
                }
                for node in nodes
            }

        first_cp = entries([controlplane[0]], ["etcd", "controlplane"])
        remaining_cp = entries(controlplane[1:], ["etcd", "controlplane"])
        first_worker = entries([workers[0]], ["worker"])
        remaining_workers = entries(workers[1:], ["worker"])
        all_hosts = {
            **first_cp,
            **remaining_cp,
            **first_worker,
            **remaining_workers,
        }
        return {
            "all": {
                "vars": {
                    "ansible_connection": "paramiko",
                    "ansible_user": str(config["node_access"]["username"]),
                    "ansible_password": resolver.resolve(
                        config["node_access"]["password_ref"]
                    ),
                    "ansible_become_password": resolver.resolve(
                        config["node_access"]["password_ref"]
                    ),
                },
                "children": {
                    "downstream_first_controlplane": {"hosts": first_cp},
                    "downstream_first_worker": {"hosts": first_worker},
                    "downstream_remaining_controlplanes": {"hosts": remaining_cp},
                    "downstream_remaining_workers": {"hosts": remaining_workers},
                    "downstream_nodes": {"hosts": all_hosts},
                },
            }
        }

    def _downstream_group_vars(
        self, config: dict[str, Any], run_dir: str
    ) -> dict[str, Any]:
        return {
            "automation_run_id": run_dir.rstrip("/").rsplit("/runs/", 1)[-1].rsplit("/", 1)[0],
            "downstream_registration_command_file": (
                f"{run_dir}/secrets/downstream-registration-command"
            ),
            "downstream_minimum_kernel": "5.8",
            "downstream_registration_require_insecure_curl": True,
        }
