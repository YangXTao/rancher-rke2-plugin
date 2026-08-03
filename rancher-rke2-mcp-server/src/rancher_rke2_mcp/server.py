from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
import uvicorn

from .auth import StaticBearerAuthMiddleware
from .constants import SERVER_VERSION
from .executor import VmExecutor
from .secrets import read_secret_setting
from .service import ReadOnlyPlanningService
from .storage import SQLiteStore


def create_server(
    db_path: str | Path | None = None,
    *,
    secret_root: str | Path | None = None,
) -> MCPServer:
    store_path = db_path or os.environ.get(
        "RANCHER_RKE2_MCP_DB",
        "./data/state.db",
    )
    service = ReadOnlyPlanningService(
        SQLiteStore(store_path),
        secret_root=str(
            secret_root or os.environ.get("RANCHER_RKE2_SECRET_ROOT", "/run/secrets")
        ),
        executor=VmExecutor(
            secret_root=str(
                secret_root or os.environ.get("RANCHER_RKE2_SECRET_ROOT", "/run/secrets")
            ),
            known_hosts_path=os.environ.get(
                "RANCHER_RKE2_CONTROL_KNOWN_HOSTS",
                "/run/secrets/control_host_known_hosts",
            ),
        ),
    )
    server = MCPServer(
        name="rancher-rke2",
        title="Rancher/RKE2 VM Executor",
        description="Validates YAML, creates plans, performs non-mutating preflight checks, and executes approved VM-only plans through the SSH control host.",
        instructions=(
            "Preflight resolves only Secret availability and TCP reachability. "
            "start_run accepts only a VM-only plan with an exact approval and matching "
            "PASSED preflight. Version 0.5.1 uses strict known-host SSH verification "
            "and executes Terraform only inside the declared control container."
        ),
        version=SERVER_VERSION,
    )

    @server.tool()
    def get_capabilities() -> dict[str, Any]:
        """Return versions, components, tools, and the enforced read-only boundary."""
        return service.get_capabilities()

    @server.tool()
    def get_config_schema() -> dict[str, Any]:
        """Return the JSON Schema used to validate Rancher/RKE2 YAML."""
        return service.get_config_schema()

    @server.tool()
    def validate_config(config: str | dict[str, Any]) -> dict[str, Any]:
        """Validate a YAML string or parsed object and return a redacted preview."""
        return service.validate_config(config)

    @server.tool()
    def build_plan(
        config_digest: str,
        target_components: list[str],
    ) -> dict[str, Any]:
        """Create and persist a deployment plan; only a VM-only plan is executable."""
        return service.build_plan(config_digest, target_components)

    @server.tool()
    def get_plan(plan_id: str) -> dict[str, Any]:
        """Read a previously generated non-executable plan."""
        return service.get_plan(plan_id)

    @server.tool()
    def preflight_plan(plan_id: str) -> dict[str, Any]:
        """Check mounted Secret availability and endpoint TCP reachability without changes."""
        return service.preflight_plan(plan_id)

    @server.tool()
    def get_preflight(preflight_id: str) -> dict[str, Any]:
        """Read a previously generated non-mutating preflight result."""
        return service.get_preflight(preflight_id)

    @server.tool()
    def start_run(
        plan_id: str,
        config_digest: str,
        preflight_id: str,
        approval_text: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Queue an approved VM-only execution through the SSH control host."""
        return service.start_run(
            plan_id=plan_id,
            config_digest=config_digest,
            preflight_id=preflight_id,
            approval_text=approval_text,
            idempotency_key=idempotency_key,
        )

    @server.tool()
    def get_run(run_id: str) -> dict[str, Any]:
        """Read a persisted approval-gated run."""
        return service.get_run(run_id)

    @server.tool()
    def get_run_events(
        run_id: str, after_cursor: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """Read structured, redacted run events incrementally."""
        return service.get_run_events(run_id, after_cursor, limit)

    return server


mcp = create_server()


def create_http_app(token: str, server: MCPServer | None = None):
    selected_server = server or mcp
    inner = selected_server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        host=os.environ.get("RANCHER_RKE2_MCP_HOST", "0.0.0.0"),
    )
    return StaticBearerAuthMiddleware(inner, token)


def main() -> None:
    token = read_secret_setting(
        value_env="RANCHER_RKE2_MCP_TOKEN",
        file_env="RANCHER_RKE2_MCP_TOKEN_FILE",
    )
    host = os.environ.get("RANCHER_RKE2_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("RANCHER_RKE2_MCP_PORT", "8787"))
    log_level = os.environ.get("RANCHER_RKE2_MCP_LOG_LEVEL", "INFO").lower()
    app = create_http_app(token)
    uvicorn.run(app, host=host, port=port, log_level=log_level)
