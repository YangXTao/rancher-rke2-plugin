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
                f"{run_dir}/ansible/group_vars/all.json",
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
