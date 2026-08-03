from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
import uvicorn

from .auth import StaticBearerAuthMiddleware
from .constants import SERVER_VERSION
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
    )
    server = MCPServer(
        name="rancher-rke2",
        title="Rancher/RKE2 Read-Only Planner",
        description="Validates YAML, creates non-executable plans, and performs non-mutating preflight checks.",
        instructions=(
            "This server is non-mutating. Preflight resolves only Secret availability "
            "and TCP reachability; it does not authenticate, execute, retry, cancel, "
            "or destroy infrastructure."
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
        """Create and persist a non-executable deployment plan."""
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
