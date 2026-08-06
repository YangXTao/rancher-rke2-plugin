from __future__ import annotations

COMPONENT_ORDER = (
    "vm",
    "node-init",
    "local-rke2",
    "rancher",
    "downstream",
)

COMPONENT_DEPENDENCIES = {
    "vm": (),
    "node-init": ("vm",),
    "local-rke2": ("vm", "node-init"),
    "rancher": ("local-rke2",),
    "downstream": ("vm", "node-init", "rancher"),
}

READ_ONLY_TOOLS = (
    "get_capabilities",
    "get_config_schema",
    "validate_config",
    "build_plan",
    "get_plan",
    "preflight_plan",
    "get_preflight",
    "get_run",
    "get_run_events",
)

MUTATION_TOOLS = ("start_run", "start_workflow")

SCHEMA_VERSION = "0.10.0"
CONTRACT_VERSION = "0.10.0"
SERVER_VERSION = "0.10.3"
MAX_YAML_BYTES = 1024 * 1024
PLAN_TTL_HOURS = 24
PREFLIGHT_TCP_TIMEOUT_SECONDS = 5.0

SECRET_REFERENCE_SCHEMES = ("docker-secret",)
