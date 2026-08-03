from __future__ import annotations

from collections.abc import Callable
import socket
from typing import Any
from urllib.parse import urlsplit

from .secrets import DockerSecretResolver


TcpProbe = Callable[[str, int, float], None]


def _default_tcp_probe(host: str, port: int, timeout: float) -> None:
    """Open and immediately close a TCP connection without authenticating."""
    with socket.create_connection((host, port), timeout=timeout):
        pass


def _endpoint(value: str, default_port: int) -> tuple[str, int]:
    """Parse hostname[:port] or a URL without accepting URL userinfo."""
    parsed = urlsplit(value if "://" in value else f"//{value}")
    if not parsed.hostname:
        raise ValueError("endpoint has no hostname")
    return parsed.hostname, parsed.port or default_port


class NonMutatingPreflight:
    """Verify prerequisite references and TCP reachability without mutations.

    Secret values are read only long enough to prove that a mounted Docker Secret
    is present and non-empty. They are never returned, logged, or persisted.
    """

    def __init__(
        self,
        resolver: DockerSecretResolver,
        *,
        timeout_seconds: float = 5.0,
        tcp_probe: TcpProbe = _default_tcp_probe,
    ):
        self.resolver = resolver
        self.timeout_seconds = timeout_seconds
        self.tcp_probe = tcp_probe

    def run(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        checks.extend(self._secret_checks(config))
        checks.extend(self._connectivity_checks(config))
        return checks

    def _secret_checks(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        references: list[tuple[str, str | None]] = [
            ("control_host_password", config["execution"]["control_host"]["password_ref"]),
            ("node_password", config["node_access"]["password_ref"]),
            ("vsphere_password", config["vsphere"]["password_ref"]),
            ("rancher_bootstrap_password", config["rancher"]["bootstrap_password_ref"]),
            ("proxy_password", config["downloads"].get("proxy_password_ref")),
            ("registry_password", config["registry"].get("password_ref")),
        ]
        checks: list[dict[str, Any]] = []
        for label, reference in references:
            if not reference:
                checks.append(
                    self._check(
                        name=f"secret.{label}",
                        category="secret",
                        status="SKIPPED",
                        message="No Secret reference is required by this configuration.",
                    )
                )
                continue
            try:
                # Deliberately discard the resolved value immediately.
                self.resolver.resolve(reference)
            except (FileNotFoundError, ValueError, OSError):
                checks.append(
                    self._check(
                        name=f"secret.{label}",
                        category="secret",
                        status="FAILED",
                        message="Referenced Docker Secret is unavailable or empty.",
                    )
                )
            else:
                checks.append(
                    self._check(
                        name=f"secret.{label}",
                        category="secret",
                        status="PASSED",
                        message="Referenced Docker Secret is mounted and non-empty.",
                    )
                )
        return checks

    def _connectivity_checks(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        control_host = config["execution"]["control_host"]
        checks.append(
            self._tcp_check(
                "tcp.control_host_ssh",
                str(control_host["address"]),
                int(control_host["port"]),
            )
        )

        node_port = int(config["node_access"]["port"])
        nodes = [
            *config["nodes"]["management"]["servers"],
            config["nodes"]["management"]["load_balancer"],
            *config["nodes"]["downstream"]["controlplane"],
            *config["nodes"]["downstream"]["workers"],
        ]
        for node in nodes:
            checks.append(
                self._tcp_check(
                    f"tcp.node_ssh.{node['hostname']}", str(node["ip"]), node_port
                )
            )

        checks.append(self._parsed_tcp_check("tcp.vsphere_https", config["vsphere"]["server"], 443))
        checks.append(self._parsed_tcp_check("tcp.registry_https", config["registry"]["hostname"], 443))

        proxy_url = config["downloads"].get("proxy_url", "")
        if proxy_url:
            checks.append(self._parsed_tcp_check("tcp.proxy", proxy_url, 80))
        else:
            checks.append(
                self._check(
                    name="tcp.proxy",
                    category="connectivity",
                    status="SKIPPED",
                    message="No proxy is configured.",
                )
            )
        return checks

    def _parsed_tcp_check(
        self, name: str, endpoint: str, default_port: int
    ) -> dict[str, Any]:
        try:
            host, port = _endpoint(endpoint, default_port)
        except ValueError:
            return self._check(
                name=name,
                category="connectivity",
                status="FAILED",
                message="Configured endpoint is invalid.",
            )
        return self._tcp_check(name, host, port)

    def _tcp_check(self, name: str, host: str, port: int) -> dict[str, Any]:
        try:
            self.tcp_probe(host, port, self.timeout_seconds)
        except OSError:
            return self._check(
                name=name,
                category="connectivity",
                status="FAILED",
                target={"host": host, "port": port},
                message="TCP connection could not be established within the configured timeout.",
            )
        return self._check(
            name=name,
            category="connectivity",
            status="PASSED",
            target={"host": host, "port": port},
            message="TCP connection succeeded; no authentication or remote change was attempted.",
        )

    @staticmethod
    def _check(
        *,
        name: str,
        category: str,
        status: str,
        message: str,
        target: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": name,
            "category": category,
            "status": status,
            "message": message,
        }
        if target:
            result["target"] = target
        return result
