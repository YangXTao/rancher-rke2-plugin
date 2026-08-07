from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    IPvAnyAddress,
    PositiveInt,
    StringConstraints,
    model_validator,
)

SecretReference = Annotated[
    str,
    StringConstraints(
        min_length=17,
        max_length=144,
        pattern=r"^docker-secret://[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
    ),
]


class ExtensibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class RunConfig(ExtensibleModel):
    run_id: str = "auto"
    components: str | list[str] = "all"
    workspace: str = "/data/rancher/automation"


class ControlHost(ExtensibleModel):
    address: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1)
    password_ref: SecretReference = Field(
        description="Reference to a Docker Secret; plaintext credentials are rejected.",
    )


class ContainerConfig(ExtensibleModel):
    strategy: Literal["reuse-or-create", "reuse", "create"] = "reuse-or-create"
    name: str = "rancher-rke2-control"
    image: str = "ubuntu:24.04"
    network: str = "host"
    mounts: dict[str, Any] = Field(default_factory=dict)


class ExecutionConfig(ExtensibleModel):
    control_host: ControlHost
    container: ContainerConfig = Field(default_factory=ContainerConfig)


class NodeAccess(ExtensibleModel):
    username: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    password_ref: SecretReference = Field(
        description="Reference to a Docker Secret; plaintext credentials are rejected.",
    )


class VSphereConfig(ExtensibleModel):
    server: str = Field(min_length=1)
    username: str = Field(min_length=1)
    password_ref: SecretReference = Field(
        description="Reference to a Docker Secret; plaintext credentials are rejected.",
    )
    allow_unverified_ssl: bool = True
    datacenter: str = Field(min_length=1)
    resource_pool: str = Field(min_length=1)
    datastore: str = Field(min_length=1)
    network: str = Field(min_length=1)
    template: str = Field(min_length=1)
    vm_folder: str = ""


class NetworkConfig(ExtensibleModel):
    netmask: int = Field(default=24, ge=1, le=32)
    gateway: IPvAnyAddress
    dns_servers: list[IPvAnyAddress] = Field(min_length=1)
    vm_domain: str = ""


class NodeSpec(ExtensibleModel):
    hostname: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]*$")
    ip: IPvAnyAddress
    cpu: PositiveInt
    memory_mb: int = Field(ge=1024)
    disk_gb: int = Field(ge=20)


class ManagementNodes(ExtensibleModel):
    servers: list[NodeSpec] = Field(min_length=3, max_length=3)
    load_balancer: NodeSpec


class DownstreamNodes(ExtensibleModel):
    controlplane: list[NodeSpec] = Field(min_length=1)
    workers: list[NodeSpec] = Field(min_length=1)


class NodesConfig(ExtensibleModel):
    management: ManagementNodes
    downstream: DownstreamNodes


class DownloadsConfig(ExtensibleModel):
    mode: Literal["online", "offline"]
    proxy_url: str = ""
    proxy_username: str | None = None
    proxy_password_ref: SecretReference | None = None
    software_root: str = "/software"

    @model_validator(mode="after")
    def validate_proxy_credentials(self) -> "DownloadsConfig":
        if self.proxy_url:
            parsed = urlsplit(self.proxy_url)
            if parsed.username is not None or parsed.password is not None:
                raise ValueError(
                    "proxy_url must not contain credentials; use proxy_username "
                    "and proxy_password_ref"
                )
        if bool(self.proxy_username) != bool(self.proxy_password_ref):
            raise ValueError(
                "proxy_username and proxy_password_ref must be configured together"
            )
        return self


class RegistryConfig(ExtensibleModel):
    hostname: str = Field(min_length=1)
    username: str | None = None
    password_ref: SecretReference | None = Field(
        default=None,
        description="Reference to a Docker Secret; plaintext credentials are rejected.",
    )
    insecure_skip_verify: bool = False

    @model_validator(mode="after")
    def validate_registry_credentials(self) -> "RegistryConfig":
        if bool(self.username) != bool(self.password_ref):
            raise ValueError(
                "registry username and password_ref must be configured together"
            )
        return self


class VersionsConfig(ExtensibleModel):
    terraform: str = Field(min_length=1, pattern=r"^[0-9][0-9A-Za-z._+-]*$")
    vsphere_provider: str = Field(min_length=1, pattern=r"^[0-9][0-9A-Za-z._+~<>= -]*$")
    rke2_management: str = Field(min_length=1)
    rancher: str = Field(min_length=1)
    rancher2_provider: str = Field(min_length=1)
    rke2_downstream: str = Field(min_length=1)


class RancherConfig(ExtensibleModel):
    bootstrap_password_ref: SecretReference = Field(
        description="Reference to a Docker Secret; plaintext credentials are rejected.",
    )
    nodeport: int = Field(default=30080, ge=30000, le=32767)
    replicas: int = Field(default=3, ge=1)


class RegistrationConfig(ExtensibleModel):
    order: list[str] = Field(
        default_factory=lambda: [
            "first-controlplane",
            "first-worker",
            "remaining-controlplanes",
            "remaining-workers",
        ]
    )


class DownstreamClusterConfig(ExtensibleModel):
    name: str = Field(min_length=1)
    registration: RegistrationConfig = Field(default_factory=RegistrationConfig)
    rke_config: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Complete downstream Rancher rkeConfig set the user customizes; "
            "when omitted the validated reference defaults are used."
        ),
    )
    registries: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Downstream Rancher2 registries object; when omitted the validated "
            "reference Harbor mirror defaults are used."
        ),
    )


class AutomationConfig(ExtensibleModel):
    run: RunConfig = Field(default_factory=RunConfig)
    execution: ExecutionConfig
    node_access: NodeAccess
    vsphere: VSphereConfig
    network: NetworkConfig
    nodes: NodesConfig
    downloads: DownloadsConfig
    registry: RegistryConfig
    versions: VersionsConfig
    rancher: RancherConfig
    downstream_cluster: DownstreamClusterConfig
    local_rke2: dict[str, Any] = Field(default_factory=dict)
    node_init: dict[str, Any] = Field(default_factory=dict)
    deliverables: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_inventory(self) -> "AutomationConfig":
        all_nodes = [
            *self.nodes.management.servers,
            self.nodes.management.load_balancer,
            *self.nodes.downstream.controlplane,
            *self.nodes.downstream.workers,
        ]
        hostnames = [node.hostname.lower() for node in all_nodes]
        addresses = [str(node.ip) for node in all_nodes]
        duplicate_hosts = sorted(
            {value for value in hostnames if hostnames.count(value) > 1}
        )
        duplicate_ips = sorted(
            {value for value in addresses if addresses.count(value) > 1}
        )
        errors = []
        if duplicate_hosts:
            errors.append("duplicate hostnames: " + ", ".join(duplicate_hosts))
        if duplicate_ips:
            errors.append("duplicate node IPs: " + ", ".join(duplicate_ips))
        expected_order = [
            "first-controlplane",
            "first-worker",
            "remaining-controlplanes",
            "remaining-workers",
        ]
        if self.downstream_cluster.registration.order != expected_order:
            errors.append(
                "downstream registration order must be "
                + ", ".join(expected_order)
            )
        if errors:
            raise ValueError("; ".join(errors))
        return self


IPAddress = IPv4Address | IPv6Address
