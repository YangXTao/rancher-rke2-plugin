from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
import uvicorn

from .auth import StaticBearerAuthMiddleware
from .constants import SERVER_VERSION
from .control_container import ControlContainerManager
from .diagnostics import DiagnosticsCollector
from .executor import (
    DownstreamExecutor,
    LocalRke2Executor,
    NodeInitExecutor,
    RancherExecutor,
    VmExecutor,
)
from .secrets import read_secret_setting
from .service import ReadOnlyPlanningService
from .storage import SQLiteStore


READ_ONLY_TOOL_ANNOTATIONS: ToolAnnotations = {
    "read_only_hint": True,
    "destructive_hint": False,
    "idempotent_hint": True,
    "open_world_hint": False,
}

MUTATION_TOOL_ANNOTATIONS: ToolAnnotations = {
    "read_only_hint": False,
    "destructive_hint": True,
    "idempotent_hint": True,
    "open_world_hint": False,
}


def create_server(
    db_path: str | Path | None = None,
    *,
    secret_root: str | Path | None = None,
) -> MCPServer:
    store_path = db_path or os.environ.get(
        "RANCHER_RKE2_MCP_DB",
        "./data/state.db",
    )
    selected_secret_root = str(
        secret_root or os.environ.get("RANCHER_RKE2_SECRET_ROOT", "/run/secrets")
    )
    selected_known_hosts = os.environ.get(
        "RANCHER_RKE2_CONTROL_KNOWN_HOSTS",
        "/run/secrets/control_host_known_hosts",
    )
    control_container_manager = ControlContainerManager(
        secret_root=selected_secret_root,
        known_hosts_path=selected_known_hosts,
    )
    executor_kwargs = {
        "secret_root": selected_secret_root,
        "known_hosts_path": selected_known_hosts,
        "control_container_manager": control_container_manager,
    }
    service = ReadOnlyPlanningService(
        SQLiteStore(store_path),
        secret_root=selected_secret_root,
        executor=VmExecutor(**executor_kwargs),
        node_executor=NodeInitExecutor(**executor_kwargs),
        local_executor=LocalRke2Executor(**executor_kwargs),
        rancher_executor=RancherExecutor(**executor_kwargs),
        downstream_executor=DownstreamExecutor(**executor_kwargs),
        diagnostics_collector=DiagnosticsCollector(
            secret_root=selected_secret_root,
            known_hosts_path=selected_known_hosts,
        ),
    )
    server = MCPServer(
        name="rancher-rke2",
        title="Rancher/RKE2 Workflow Executor",
        description="Validates YAML, creates plans, performs non-mutating preflight checks, and executes approved VM, node-init, Local RKE2, Rancher, or downstream components through the SSH control host.",
        instructions=(
            "Preflight resolves only Secret availability and TCP reachability. "
            "start_run accepts one component plan with exact approval. start_workflow "
            "accepts the ordered VM-to-node-init or VM-to-Local-RKE2 plan with one workflow approval "
            "and waits for node TCP/22 readiness between stages. The Rancher component reuses "
            "the newest successful Local RKE2 artifact for the same configuration; the downstream "
            "component reuses the newest successful Rancher private-CA certificate artifact for the "
            "same configuration, then creates rancher2_cluster_v2 and registers every custom node "
            "in the approved role order. Both mutation paths use strict known-host SSH verification "
            "and automatically bootstrap and validate the declared persistent control container "
            "before the first selected component."
            " collect_diagnostics authenticates to the same control host but runs only "
            "fixed read-only evidence commands and redacts returned text."
        ),
        version=SERVER_VERSION,
    )

    @server.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_capabilities() -> dict[str, Any]:
        """Return versions, components, tools, and the enforced read-only boundary."""
        return service.get_capabilities()

    @server.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_config_schema() -> dict[str, Any]:
        """Return the JSON Schema used to validate Rancher/RKE2 YAML."""
        return service.get_config_schema()

    @server.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def validate_config(config: str | dict[str, Any]) -> dict[str, Any]:
        """Validate a YAML string or parsed object and return a redacted preview."""
        return service.validate_config(config)

    @server.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def build_plan(
        config_digest: str,
        target_components: list[str],
    ) -> dict[str, Any]:
        """Create and persist a deployment plan; supported component and workflow plans are executable."""
        return service.build_plan(config_digest, target_components)

    @server.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_plan(plan_id: str) -> dict[str, Any]:
        """Read a previously generated non-executable plan."""
        return service.get_plan(plan_id)

    @server.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def preflight_plan(plan_id: str) -> dict[str, Any]:
        """Check mounted Secret availability and endpoint TCP reachability without changes."""
        return service.preflight_plan(plan_id)

    @server.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_preflight(preflight_id: str) -> dict[str, Any]:
        """Read a previously generated non-mutating preflight result."""
        return service.get_preflight(preflight_id)

    @server.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def render_runbook(
        plan_id: str,
        format: str = "markdown",
        output_profile: str = "human-step-by-step",
    ) -> dict[str, Any]:
        """Render an audited human-executable installation manual from a plan."""
        return service.render_runbook(
            plan_id,
            format=format,
            output_profile=output_profile,
        )

    @server.tool(annotations=MUTATION_TOOL_ANNOTATIONS)
    def start_run(
        plan_id: str,
        config_digest: str,
        preflight_id: str,
        approval_text: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Queue one approved component execution through the SSH control host."""
        return service.start_run(
            plan_id=plan_id,
            config_digest=config_digest,
            preflight_id=preflight_id,
            approval_text=approval_text,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=MUTATION_TOOL_ANNOTATIONS)
    def start_workflow(
        plan_id: str,
        config_digest: str,
        preflight_id: str,
        approval_text: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Queue one approved VM-to-node-init or VM-to-Local-RKE2 workflow."""
        return service.start_workflow(
            plan_id=plan_id,
            config_digest=config_digest,
            preflight_id=preflight_id,
            approval_text=approval_text,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_run(run_id: str) -> dict[str, Any]:
        """Read a persisted approval-gated run."""
        return service.get_run(run_id)

    @server.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def get_run_events(
        run_id: str, after_cursor: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """Read structured, redacted run events incrementally."""
        return service.get_run_events(run_id, after_cursor, limit)

    @server.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def collect_diagnostics(
        run_id: str,
        component: str | None = None,
        depth: str = "standard",
    ) -> dict[str, Any]:
        """Collect redacted, read-only runtime evidence for one run component."""
        return service.collect_diagnostics(run_id, component, depth)

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
