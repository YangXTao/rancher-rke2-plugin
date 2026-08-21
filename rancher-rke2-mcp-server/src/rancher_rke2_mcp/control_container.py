from __future__ import annotations

import logging
from pathlib import Path
import shlex
from threading import Lock
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from .secrets import DockerSecretResolver


LOGGER = logging.getLogger(__name__)


class ControlContainerManager:
    """Bootstrap and validate the shared control container before every component.

    Preparation is cached per run directory, so a multi-component workflow pays
    the bootstrap cost once. Every independently started component still receives
    the same guarantee and no longer depends on the VM component having run first.
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
        self.assets_root = assets_root or Path(__file__).parent / "assets" / "control-container"
        self._lock = Lock()
        self._prepared_run_roots: set[str] = set()

    def ready(self) -> bool:
        return self.known_hosts_path.is_file() and self.assets_root.is_dir()

    def prepare(self, config: dict[str, Any], artifact_path: str) -> None:
        run_root = artifact_path.rsplit("/", 1)[0]
        with self._lock:
            if run_root in self._prepared_run_roots:
                return
            self._prepare_remote(config, run_root)
            self._prepared_run_roots.add(run_root)

    def _prepare_remote(self, config: dict[str, Any], run_root: str) -> None:
        import paramiko

        control = config["execution"]["control_host"]
        manager_dir = f"{run_root}/control-container"
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
            try:
                self._upload_bundle(client, config, manager_dir)
                command = " ".join(
                    shlex.quote(value)
                    for value in (
                        "bash",
                        f"{manager_dir}/control-container-runner.sh",
                        manager_dir,
                    )
                )
                _, stdout, stderr = client.exec_command(command, timeout=1800)
                exit_status = stdout.channel.recv_exit_status()
                stdout.read()
                stderr.read()
                if exit_status != 0:
                    raise RuntimeError("remote control-container preparation failed")
            finally:
                cleanup = " ".join(
                    shlex.quote(value)
                    for value in (
                        "rm",
                        "-f",
                        f"{manager_dir}/.control-container.env",
                        f"{manager_dir}/.registry-password",
                    )
                )
                try:
                    _, cleanup_stdout, _ = client.exec_command(cleanup, timeout=15)
                    cleanup_stdout.channel.recv_exit_status()
                except Exception:
                    LOGGER.warning(
                        "Unable to remove temporary control-container credential files"
                    )
        finally:
            client.close()

    def _upload_bundle(
        self, client: Any, config: dict[str, Any], manager_dir: str
    ) -> None:
        sftp = client.open_sftp()
        try:
            self._mkdirs(sftp, manager_dir)
            for source in self.assets_root.iterdir():
                if source.is_file():
                    remote = f"{manager_dir}/{source.name}"
                    sftp.put(str(source), remote)
                    sftp.chmod(remote, 0o700)
            self._put_text(
                sftp,
                f"{manager_dir}/.control-container.env",
                self._environment(config),
                mode=0o600,
            )
            registry = config["registry"]
            if registry.get("password_ref"):
                registry_password = DockerSecretResolver(self.secret_root).resolve(
                    registry["password_ref"]
                )
                self._put_text(
                    sftp,
                    f"{manager_dir}/.registry-password",
                    registry_password,
                    mode=0o600,
                )
        finally:
            sftp.close()

    def _environment(self, config: dict[str, Any]) -> str:
        container = config["execution"]["container"]
        docker = container["docker"]
        mounts = container["mounts"]
        downloads = config["downloads"]
        registry = config["registry"]
        values = {
            "CONTROL_MODE": str(downloads["mode"]),
            "CONTROL_SOFTWARE_ROOT": str(mounts["software"]),
            "CONTROL_WORKSPACE_ROOT": str(mounts["workspace"]),
            "CONTROL_CONTAINER_NAME": str(container["name"]),
            "CONTROL_CONTAINER_STRATEGY": str(container["strategy"]),
            "CONTROL_CONTAINER_IMAGE": str(container["image"]),
            "CONTROL_IMAGE_ARCHIVE": str(container.get("image_archive", "")),
            "CONTROL_IMAGE_PULL_POLICY": str(container["image_pull_policy"]),
            "CONTROL_ALLOW_OFFLINE_REGISTRY_PULL": str(
                container["allow_offline_registry_pull"]
            ).lower(),
            "CONTROL_DOCKER_VERSION": str(docker["version"]),
            "CONTROL_DOCKER_ARCHIVE": str(docker["archive"]),
            "CONTROL_DOCKER_URL": str(docker["url"]),
            "CONTROL_REGISTRY_HOST": str(registry["hostname"]),
            "CONTROL_REGISTRY_USERNAME": str(registry.get("username") or ""),
            "CONTROL_REGISTRY_INSECURE": str(
                registry.get("insecure_skip_verify", False)
            ).lower(),
            "CONTROL_PROXY_URL": self._proxy_url(config),
        }
        return "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items())

    def _proxy_url(self, config: dict[str, Any]) -> str:
        downloads = config["downloads"]
        if not downloads.get("proxy_username"):
            return str(downloads.get("proxy_url") or "")
        resolver = DockerSecretResolver(self.secret_root)
        parsed = urlsplit(str(downloads["proxy_url"]))
        password = quote(resolver.resolve(downloads["proxy_password_ref"]), safe="")
        username = quote(str(downloads["proxy_username"]), safe="")
        return urlunsplit(
            (
                parsed.scheme,
                f"{username}:{password}@{parsed.netloc}",
                parsed.path,
                parsed.query,
                parsed.fragment,
            )
        )

    @staticmethod
    def _mkdirs(sftp: Any, path: str) -> None:
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
    def _put_text(sftp: Any, path: str, content: str, *, mode: int) -> None:
        with sftp.file(path, "w") as remote:
            remote.write(content)
        sftp.chmod(path, mode)
