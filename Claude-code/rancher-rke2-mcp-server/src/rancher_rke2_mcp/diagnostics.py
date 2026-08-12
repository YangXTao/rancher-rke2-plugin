from __future__ import annotations

from datetime import datetime, timezone
import re
import shlex
from pathlib import PurePosixPath
from typing import Any, Callable


DIAGNOSTIC_DEPTHS = ("summary", "standard", "deep")

COMPONENT_LOGS: dict[str, tuple[str, ...]] = {
    "vm": (
        "install-control-dependencies.log",
        "provider-cache.log",
        "ip-conflicts.log",
        "terraform-init.log",
        "terraform-apply.log",
        "terraform-verify.log",
    ),
    "node-init": (
        "install-control-dependencies.log",
        "ansible-playbook.log",
    ),
    "local-rke2": (
        "install-control-dependencies.log",
        "prepare-rke2-artifacts.log",
        "ansible-inventory.log",
        "ansible-playbook.log",
    ),
    "rancher": (
        "install-control-dependencies.log",
        "rancher-install.log",
        "rancher-lb.log",
    ),
    "downstream": (
        "rancher-ca.log",
        "install-control-dependencies.log",
        "provider-cache.log",
        "rancher-api-token.log",
        "terraform-init.log",
        "terraform-apply.log",
        "save-registration-command.log",
        "downstream-register.log",
    ),
}

_PROCESS_PATTERN = (
    r"[a]pt(-get)?|[d]pkg|[a]nsible(-playbook)?|[t]erraform|[h]elm|"
    r"[k]ubectl|[n]ode-init-runner|[v]m-runner|[l]ocal-rke2-runner|"
    r"[r]ancher-runner|[d]ownstream-runner"
)
_NONZERO_EXIT = re.compile(r"EXIT_CODE:\s*([1-9][0-9]*)")


def _iso_from_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def build_findings(
    *,
    component_state: str,
    logs: list[dict[str, Any]],
    process_text: str,
    now_timestamp: float,
) -> tuple[list[dict[str, str]], list[str]]:
    """Derive conservative findings without claiming a root cause."""

    findings: list[dict[str, str]] = []
    hypotheses: list[str] = []
    existing = [item for item in logs if item.get("exists")]
    if not existing:
        findings.append(
            {
                "code": "COMPONENT_LOGS_NOT_FOUND",
                "severity": "warning",
                "message": "No allowlisted component logs exist in the artifact directory.",
            }
        )
        if component_state == "RUNNING":
            hypotheses.append(
                "The executor may still be preparing the component bundle or may not have reached the first logged command."
            )
        return findings, hypotheses

    latest = max(existing, key=lambda item: float(item.get("modified_timestamp", 0)))
    age_seconds = max(
        0, int(now_timestamp - float(latest.get("modified_timestamp", now_timestamp)))
    )
    latest["age_seconds"] = age_seconds
    tail = str(latest.get("tail", ""))
    nonzero = _NONZERO_EXIT.search(tail)
    if nonzero:
        findings.append(
            {
                "code": "LOGGED_COMMAND_FAILED",
                "severity": "error",
                "message": f"{latest['name']} records EXIT_CODE {nonzero.group(1)}.",
            }
        )

    process_active = bool(process_text.strip())
    if age_seconds <= 300:
        findings.append(
            {
                "code": "LOG_RECENTLY_UPDATED",
                "severity": "info",
                "message": f"{latest['name']} was updated {age_seconds} seconds ago.",
            }
        )
    elif component_state == "RUNNING" and process_active:
        findings.append(
            {
                "code": "STALE_LOG_WITH_ACTIVE_PROCESS",
                "severity": "warning",
                "message": (
                    f"{latest['name']} has not changed for {age_seconds} seconds, "
                    "but a related process is still present."
                ),
            }
        )
        hypotheses.append(
            "The current command may be slow, blocked on package/network I/O, or waiting inside a child process."
        )
    elif component_state == "RUNNING":
        findings.append(
            {
                "code": "STALE_LOG_WITHOUT_MATCHING_PROCESS",
                "severity": "warning",
                "message": (
                    f"{latest['name']} has not changed for {age_seconds} seconds and "
                    "no allowlisted related process was observed."
                ),
            }
        )
        hypotheses.append(
            "The remote runner may have exited without the workflow coordinator persisting its completion callback."
        )
    elif process_active:
        findings.append(
            {
                "code": "RELATED_PROCESS_PRESENT",
                "severity": "info",
                "message": "A related process is present on the control host or in the control container.",
            }
        )
    return findings, hypotheses


class DiagnosticsCollector:
    """Collect fixed, read-only evidence from the declared SSH control host."""

    def __init__(
        self,
        *,
        secret_root: str,
        known_hosts_path: str,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        from pathlib import Path

        self.secret_root = secret_root
        self.known_hosts_path = Path(known_hosts_path)
        self.client_factory = client_factory

    def ready(self) -> bool:
        return self.known_hosts_path.is_file()

    def collect(
        self,
        *,
        config: dict[str, Any],
        run_id: str,
        component: str,
        component_state: str,
        artifact_path: str,
        depth: str,
    ) -> dict[str, Any]:
        from .secrets import DockerSecretResolver

        if component not in COMPONENT_LOGS:
            raise ValueError("unsupported diagnostic component")
        if depth not in DIAGNOSTIC_DEPTHS:
            raise ValueError("unsupported diagnostic depth")

        if self.client_factory is None:
            import paramiko

            client = paramiko.SSHClient()
            client.load_host_keys(str(self.known_hosts_path))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            client = self.client_factory()

        control = config["execution"]["control_host"]
        password = DockerSecretResolver(self.secret_root).resolve(
            control["password_ref"]
        )
        collected_at = datetime.now(timezone.utc)
        now_timestamp = collected_at.timestamp()
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
            logs, artifact_exists = self._collect_logs(
                client,
                artifact_path=artifact_path,
                component=component,
                depth=depth,
            )
            process_snapshots = self._collect_processes(client, config, depth)
            combined_processes = "\n".join(
                str(process_snapshots[name].get("stdout", ""))
                for name in ("control_host", "control_container")
            )
            findings, hypotheses = build_findings(
                component_state=component_state,
                logs=logs,
                process_text=combined_processes,
                now_timestamp=now_timestamp,
            )
            return {
                "run_id": run_id,
                "component": component,
                "component_state": component_state,
                "depth": depth,
                "collected_at": collected_at.isoformat().replace("+00:00", "Z"),
                "non_mutating": True,
                "authentication_attempted": True,
                "artifact": {
                    "path": artifact_path,
                    "exists": artifact_exists,
                },
                "logs": logs,
                "process_snapshots": process_snapshots,
                "findings": findings,
                "hypotheses": hypotheses,
                "limitations": [
                    "Evidence is sampled once and does not prove that a process is making forward progress.",
                    "Only allowlisted logs and process metadata are collected; configuration, state, kubeconfig, and private-key files are excluded.",
                ],
            }
        finally:
            client.close()

    def _collect_logs(
        self,
        client: Any,
        *,
        artifact_path: str,
        component: str,
        depth: str,
    ) -> tuple[list[dict[str, Any]], bool]:
        sftp = client.open_sftp()
        line_limits = {"summary": 40, "standard": 100, "deep": 200}
        byte_limits = {"summary": 16 * 1024, "standard": 64 * 1024, "deep": 128 * 1024}
        tail_count = {"summary": 1, "standard": 2, "deep": len(COMPONENT_LOGS[component])}
        try:
            try:
                sftp.stat(artifact_path)
                artifact_exists = True
            except OSError:
                artifact_exists = False

            logs: list[dict[str, Any]] = []
            for name in COMPONENT_LOGS[component]:
                path = str(PurePosixPath(artifact_path) / name)
                item: dict[str, Any] = {"name": name, "path": path, "exists": False}
                try:
                    attrs = sftp.stat(path)
                except OSError:
                    logs.append(item)
                    continue
                item.update(
                    {
                        "exists": True,
                        "size_bytes": int(attrs.st_size),
                        "modified_at": _iso_from_timestamp(float(attrs.st_mtime)),
                        "modified_timestamp": float(attrs.st_mtime),
                    }
                )
                logs.append(item)

            selected = sorted(
                (item for item in logs if item["exists"]),
                key=lambda item: float(item["modified_timestamp"]),
                reverse=True,
            )[: tail_count[depth]]
            selected_names = {item["name"] for item in selected}
            for item in logs:
                if item["name"] in selected_names:
                    item["tail"] = self._read_tail(
                        sftp,
                        item["path"],
                        size=int(item["size_bytes"]),
                        max_bytes=byte_limits[depth],
                        max_lines=line_limits[depth],
                    )
            return logs, artifact_exists
        finally:
            sftp.close()

    @staticmethod
    def _read_tail(
        sftp: Any,
        path: str,
        *,
        size: int,
        max_bytes: int,
        max_lines: int,
    ) -> str:
        offset = max(0, size - max_bytes)
        with sftp.file(path, "rb") as handle:
            handle.seek(offset)
            data = handle.read(max_bytes)
        text = data.decode("utf-8", errors="replace")
        if offset and "\n" in text:
            text = text.split("\n", 1)[1]
        return "\n".join(text.splitlines()[-max_lines:])

    def _collect_processes(
        self, client: Any, config: dict[str, Any], depth: str
    ) -> dict[str, dict[str, Any]]:
        process_command = (
            "ps -eo pid=,ppid=,etime=,stat=,comm=,args= "
            f"| grep -E {shlex.quote(_PROCESS_PATTERN)} | head -n 80 || true"
        )
        container_name = str(config["execution"]["container"]["name"])
        commands = {
            "control_host": process_command,
            "control_container": (
                f"docker exec {shlex.quote(container_name)} sh -lc "
                f"{shlex.quote(process_command)}"
            ),
        }
        if depth in {"standard", "deep"}:
            audit_command = (
                "if command -v dpkg >/dev/null 2>&1; then dpkg --audit; "
                "elif command -v rpm >/dev/null 2>&1; then rpm --verifydb >/dev/null 2>&1; "
                "fi"
            )
            commands["package_database_audit"] = (
                f"docker exec {shlex.quote(container_name)} sh -lc "
                f"{shlex.quote(audit_command)}"
            )
        if depth == "deep":
            commands["disk_usage"] = "df -P | head -n 80"
        return {
            name: self._run_command(client, command)
            for name, command in commands.items()
        }

    @staticmethod
    def _run_command(client: Any, command: str) -> dict[str, Any]:
        _, stdout, stderr = client.exec_command(command, timeout=15)
        exit_status = int(stdout.channel.recv_exit_status())
        stdout_text = stdout.read(128 * 1024).decode("utf-8", errors="replace")
        stderr_text = stderr.read(32 * 1024).decode("utf-8", errors="replace")
        return {
            "exit_status": exit_status,
            "stdout": stdout_text,
            "stderr": stderr_text,
        }
