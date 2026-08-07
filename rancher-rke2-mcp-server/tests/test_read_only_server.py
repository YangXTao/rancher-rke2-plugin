from __future__ import annotations

import asyncio
import ast
import json
from pathlib import Path
import re
import socket
import sqlite3
import time
from unittest.mock import patch

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
import uvicorn

from rancher_rke2_mcp.constants import MUTATION_TOOLS, READ_ONLY_TOOLS
from rancher_rke2_mcp.executor import (
    DownstreamExecutor,
    ExecutionResult,
    LocalRke2Executor,
    VmExecutor,
)
from rancher_rke2_mcp.secrets import DockerSecretResolver, read_secret_setting
from rancher_rke2_mcp.server import create_http_app, create_server
from rancher_rke2_mcp.service import ReadOnlyPlanningService
from rancher_rke2_mcp.storage import SQLiteStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = (PROJECT_ROOT / "examples" / "config.example.yaml").read_text(
    encoding="utf-8"
)


def make_service(
    tmp_path: Path, *, secret_root: Path | None = None, executor=None, node_executor=None,
    local_executor=None, rancher_executor=None, downstream_executor=None,
) -> ReadOnlyPlanningService:
    return ReadOnlyPlanningService(
        SQLiteStore(tmp_path / "state.db"),
        secret_root=str(secret_root or tmp_path / "secrets"),
        executor=executor,
        node_executor=node_executor,
        local_executor=local_executor,
        rancher_executor=rancher_executor,
        downstream_executor=downstream_executor,
    )


class QueuedExecutor:
    def ready(self) -> bool:
        return True

    def submit(self, **_: object) -> None:
        return None


class SuccessfulExecutor(QueuedExecutor):
    def submit(self, **kwargs: object) -> None:
        callback = kwargs["on_complete"]
        run_id = kwargs["run_id"]
        assert callable(callback)
        callback(ExecutionResult(run_id, True, "VM_EXECUTION_SUCCEEDED", "/data/rancher/automation/runs/test/vm"))


class RunningExecutor(QueuedExecutor):
    def submit(self, **kwargs: object) -> None:
        callback = kwargs["on_started"]
        run_id = kwargs["run_id"]
        assert callable(callback)
        callback(run_id)


class WorkflowExecutor(QueuedExecutor):
    def __init__(self) -> None:
        self.artifact_paths: list[str] = []

    def execute(self, _config: object, artifact_path: str) -> None:
        self.artifact_paths.append(artifact_path)


class RancherWorkflowExecutor(QueuedExecutor):
    def __init__(self) -> None:
        self.calls: list[tuple[object, str]] = []

    def execute(self, config: object, artifact_path: str) -> None:
        self.calls.append((config, artifact_path))


class CapturingExecutor(QueuedExecutor):
    def __init__(self) -> None:
        self.submitted: dict[str, object] = {}

    def submit(self, **kwargs: object) -> None:
        self.submitted = kwargs


def write_required_secrets(secret_root: Path) -> None:
    secret_root.mkdir()
    for name in (
        "control_host_password",
        "node_password",
        "vsphere_password",
        "rancher_bootstrap_password",
    ):
        (secret_root / name).write_text(f"{name}-value\n", encoding="utf-8")
    (secret_root / "control_host_known_hosts").write_text(
        "control.example ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestOnly\n",
        encoding="utf-8",
    )


def test_validates_yaml_with_secret_references(tmp_path: Path) -> None:
    result = make_service(tmp_path).validate_config(EXAMPLE)
    assert result["ok"] is True
    assert result["state"] == "VALIDATED"
    serialized = json.dumps(result, ensure_ascii=False)
    assert "docker-secret://control_host_password" in serialized
    assert '"password":' not in serialized
    assert result["data"]["config_digest"].startswith("sha256:")


def test_rejects_legacy_plaintext_credentials(tmp_path: Path) -> None:
    legacy = EXAMPLE.replace(
        'password_ref: "docker-secret://control_host_password"',
        'password: "must-not-be-stored"',
        1,
    )
    result = make_service(tmp_path).validate_config(legacy)
    assert result["ok"] is False
    assert result["state"] == "INVALID"
    assert result["errors"][0]["path"] == "$"
    assert "plaintext secret fields are forbidden" in result["errors"][0]["message"]


def test_rejects_proxy_url_with_embedded_credentials(tmp_path: Path) -> None:
    unsafe = EXAMPLE.replace(
        'proxy_url: ""',
        'proxy_url: "http://user:password@proxy.example:60000"',
    )
    result = make_service(tmp_path).validate_config(unsafe)
    assert result["ok"] is False
    assert "URLs containing credentials are forbidden" in json.dumps(result)


def test_rejects_credential_url_in_extension_field(tmp_path: Path) -> None:
    unsafe = EXAMPLE.replace(
        "deliverables:\n",
        'custom_endpoint: "https://user:password@example.internal/api"\n\n'
        "deliverables:\n",
    )
    result = make_service(tmp_path).validate_config(unsafe)
    assert result["ok"] is False
    assert "URLs containing credentials are forbidden" in json.dumps(result)


def test_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    result = make_service(tmp_path).validate_config("run: {}\nrun: {}\n")
    assert result["ok"] is False
    assert result["state"] == "INVALID"
    assert "duplicate key" in result["errors"][0]["message"]


def test_builds_non_executable_plan_without_secrets(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    validation = service.validate_config(EXAMPLE)
    digest = validation["data"]["config_digest"]
    result = service.build_plan(digest, ["all"])
    assert result["ok"] is True
    plan = result["data"]["plan"]
    assert plan["read_only"] is True
    assert plan["executable"] is False
    assert [item["component"] for item in plan["component_plans"]] == [
        "vm",
        "node-init",
        "local-rke2",
        "rancher",
        "downstream",
    ]
    serialized = json.dumps(plan, ensure_ascii=False)
    assert "docker-secret://" not in serialized
    assert '"password":' not in serialized


def test_sqlite_persists_references_not_plaintext_fields(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    result = ReadOnlyPlanningService(SQLiteStore(database)).validate_config(EXAMPLE)
    assert result["ok"] is True
    store = SQLiteStore(database)
    normalized = store.get_config(result["data"]["config_digest"])
    assert normalized is not None
    serialized = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    assert "docker-secret://control_host_password" in serialized
    assert '"password":' not in serialized
    assert '"bootstrap_password":' not in serialized


def test_legacy_sqlite_row_with_plaintext_secret_is_not_returned(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    store = SQLiteStore(database)
    with store._connect() as connection:
        connection.execute(
            """
            INSERT INTO validated_configs(config_digest, normalized_json, created_at)
            VALUES (?, ?, ?)
            """,
            (
                "sha256:legacy",
                json.dumps({"execution": {"control_host": {"password": "legacy"}}}),
                "2026-07-31T00:00:00Z",
            ),
        )
    assert store.get_config("sha256:legacy") is None


def test_mcp_client_discovers_read_only_and_approval_gate_tools(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = create_server(tmp_path / "state.db")
        async with Client(server) as client:
            response = await client.list_tools()
            names = tuple(tool.name for tool in response.tools)
            assert set(names) == set(READ_ONLY_TOOLS) | set(MUTATION_TOOLS)
            assert len(names) == 11
            capability = await client.call_tool("get_capabilities", {})
            assert capability.structured_content["ok"] is True
            assert capability.structured_content["data"]["mutation_tools"] == [
                "start_run", "start_workflow"
            ]
            assert (
                capability.structured_content["data"][
                    "plaintext_credentials_compatible"
                ]
                is False
            )
            assert capability.structured_content["data"]["secrets_persisted"] is False
            assert capability.structured_content["data"]["preflight_mutates_infrastructure"] is False

    asyncio.run(scenario())


def test_start_run_requires_current_preflight_and_is_idempotent(tmp_path: Path) -> None:
    secret_root = tmp_path / "secrets"
    write_required_secrets(secret_root)
    service = make_service(tmp_path, secret_root=secret_root, executor=QueuedExecutor())
    validation = service.validate_config(EXAMPLE)
    digest = validation["data"]["config_digest"]
    plan = service.build_plan(digest, ["vm"])["data"]["plan"]

    with patch("rancher_rke2_mcp.preflight.socket.create_connection"):
        preflight = service.preflight_plan(plan["plan_id"])["data"]["preflight"]

    first = service.start_run(
        plan_id=plan["plan_id"],
        config_digest=digest,
        preflight_id=preflight["preflight_id"],
        approval_text=plan["approval_text"],
        idempotency_key="vm-run-001",
    )
    assert first["ok"] is True
    assert first["state"] == "QUEUED"
    run_id = first["data"]["run"]["run_id"]
    assert re.fullmatch(r"run-\d{8}T\d{6}Z-[0-9a-f]{12}", run_id)

    replay = service.start_run(
        plan_id=plan["plan_id"],
        config_digest=digest,
        preflight_id=preflight["preflight_id"],
        approval_text=plan["approval_text"],
        idempotency_key="vm-run-001",
    )
    assert replay["data"]["idempotent_replay"] is True
    assert replay["data"]["run"]["run_id"] == run_id

    events = service.get_run_events(run_id)
    assert events["state"] == "EVENTS_AVAILABLE"
    assert events["data"]["events"][0]["type"] == "RUN_QUEUED"


def test_get_capabilities_reports_downstream_execution_scope(tmp_path: Path) -> None:
    service = make_service(tmp_path, downstream_executor=QueuedExecutor())
    capabilities = service.get_capabilities()["data"]
    assert "downstream" in capabilities["execution_scope"]
    assert capabilities["infrastructure_side_effects"] is True


def test_downstream_plan_is_executable(tmp_path: Path) -> None:
    secret_root = tmp_path / "secrets"
    write_required_secrets(secret_root)
    service = make_service(
        tmp_path,
        secret_root=secret_root,
        downstream_executor=QueuedExecutor(),
    )
    digest = service.validate_config(EXAMPLE)["data"]["config_digest"]
    plan = service.build_plan(digest, ["downstream"])["data"]["plan"]
    assert plan["executable"] is True
    assert plan["execution_mode"] == "COMPONENT"
    assert plan["component_plans"][0]["details"]["cluster_name"] == "downstream"


def test_preflight_includes_rancher_lb_https_for_downstream_plan(
    tmp_path: Path,
) -> None:
    secret_root = tmp_path / "secrets"
    write_required_secrets(secret_root)
    service = make_service(tmp_path, secret_root=secret_root)
    validation = service.validate_config(EXAMPLE)
    plan = service.build_plan(
        validation["data"]["config_digest"], ["downstream"]
    )

    with patch("rancher_rke2_mcp.preflight.socket.create_connection"):
        result = service.preflight_plan(plan["data"]["plan"]["plan_id"])

    checks = result["data"]["preflight"]["checks"]
    rancher_lb_check = next(
        item for item in checks if item["name"] == "tcp.rancher_lb_https"
    )
    assert rancher_lb_check["status"] == "PASSED"
    assert rancher_lb_check["target"] == {"host": "192.0.2.20", "port": 443}
    node_checks = [
        item for item in checks if item["name"].startswith("tcp.node_ssh.")
    ]
    assert len(node_checks) == 10
    assert all(item["status"] == "PASSED" for item in node_checks)


def test_start_run_downstream_requires_successful_rancher_run(
    tmp_path: Path,
) -> None:
    secret_root = tmp_path / "secrets"
    write_required_secrets(secret_root)
    downstream_executor = CapturingExecutor()
    service = make_service(
        tmp_path,
        secret_root=secret_root,
        downstream_executor=downstream_executor,
    )
    digest = service.validate_config(EXAMPLE)["data"]["config_digest"]
    plan = service.build_plan(digest, ["downstream"])["data"]["plan"]
    with patch("rancher_rke2_mcp.preflight.socket.create_connection"):
        preflight = service.preflight_plan(plan["plan_id"])["data"]["preflight"]

    result = service.start_run(
        plan_id=plan["plan_id"],
        config_digest=digest,
        preflight_id=preflight["preflight_id"],
        approval_text=plan["approval_text"],
        idempotency_key="downstream-no-rancher",
    )
    assert result["ok"] is False
    assert result["state"] == "RANCHER_ARTIFACT_REQUIRED"
    assert downstream_executor.submitted == {}


def test_start_run_downstream_reuses_rancher_ca_artifact(tmp_path: Path) -> None:
    secret_root = tmp_path / "secrets"
    write_required_secrets(secret_root)
    downstream_executor = CapturingExecutor()
    service = make_service(
        tmp_path,
        secret_root=secret_root,
        downstream_executor=downstream_executor,
    )
    digest = service.validate_config(EXAMPLE)["data"]["config_digest"]
    service.store.save_run(
        {
            "run_id": "run-rancher-success-000000",
            "state": "SUCCEEDED",
            "plan_id": "plan-rancher-seed",
            "config_digest": digest,
            "preflight_id": "preflight-rancher-seed",
            "target_components": ["rancher"],
            "created_at": "2026-08-06T00:00:00Z",
            "updated_at": "2026-08-06T00:00:00Z",
            "execution_backend": "ssh-control-container",
            "component_states": [],
        }
    )
    plan = service.build_plan(digest, ["downstream"])["data"]["plan"]
    with patch("rancher_rke2_mcp.preflight.socket.create_connection"):
        preflight = service.preflight_plan(plan["plan_id"])["data"]["preflight"]

    result = service.start_run(
        plan_id=plan["plan_id"],
        config_digest=digest,
        preflight_id=preflight["preflight_id"],
        approval_text=plan["approval_text"],
        idempotency_key="downstream-with-rancher",
    )
    assert result["ok"] is True
    assert result["state"] == "QUEUED"
    submitted_config = downstream_executor.submitted["config"]
    assert isinstance(submitted_config, dict)
    assert submitted_config["_rancher_ca_source"] == (
        "/data/rancher/automation/runs/run-rancher-success-000000/"
        "rancher/cert/output/cacerts.pem"
    )


def test_downstream_versions_tf_renders_exact_provider_version(tmp_path: Path) -> None:
    executor = DownstreamExecutor(
        secret_root="/run/secrets",
        assets_root=(
            PROJECT_ROOT
            / "src"
            / "rancher_rke2_mcp"
            / "assets"
            / "downstream"
        ),
    )
    rendered = executor._downstream_versions_tf(
        {"versions": {"rancher2_provider": "13.1.4"}}
    )
    assert 'version = "13.1.4"' in rendered
    assert "__RANCHER2_PROVIDER_VERSION__" not in rendered


def test_downstream_assets_cover_required_stages() -> None:
    assets = PROJECT_ROOT / "src" / "rancher_rke2_mcp" / "assets" / "downstream"
    runner = (assets / "downstream-runner.sh").read_text(encoding="utf-8")
    playbook = (
        assets / "ansible" / "playbooks" / "downstream-register.yml"
    ).read_text(encoding="utf-8")
    executor_source = (
        PROJECT_ROOT / "src" / "rancher_rke2_mcp" / "executor.py"
    ).read_text(encoding="utf-8")

    assert "install-control-dependencies.log" in runner
    assert "provider-cache.log" in runner
    assert "rancher-api-token.log" in runner
    assert "terraform-init.log" in runner
    assert "terraform-apply.log" in runner
    assert "save-registration-command.log" in runner
    assert "downstream-register.log" in runner
    assert "DOWNSTREAM_LOG_MISSING" in runner
    assert "checkpoint.yaml" in runner
    assert "downstream_first_controlplane" in playbook
    assert "downstream_first_worker" in playbook
    assert "downstream_nodes" in playbook
    assert "class DownstreamExecutor" in executor_source
    assert "_rancher_ca_source" in executor_source


def test_registry_defaults_follow_edition_policy(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    validation = service.validate_config(EXAMPLE)
    config = service.store.get_config(validation["data"]["config_digest"])
    assert config is not None

    downstream = DownstreamExecutor(secret_root="/run/secrets")
    local = LocalRke2Executor(secret_root="/run/secrets")

    downstream_mirrors = {
        item["hostname"]: item
        for item in downstream._default_downstream_registries(config)["mirrors"]
    }
    assert downstream_mirrors["docker.io"]["rewrites"] == {}
    assert "registry.rancher.cn" in downstream_mirrors
    assert "registry.rancher.com" not in downstream_mirrors

    local_vars = local._local_group_vars(
        config,
        "/data/rancher/automation/runs/run-registry-test/local-rke2",
    )
    local_mirrors = local_vars["registry_mirrors"]
    assert local_mirrors["docker.io"]["rewrites"] == {}
    assert "registry.rancher.cn" in local_mirrors
    assert "registry.rancher.com" not in local_mirrors

    non_ent = dict(config)
    non_ent["versions"] = dict(config["versions"])
    non_ent["versions"]["rancher"] = "2.9.2"
    standard_mirrors = {
        item["hostname"]: item
        for item in downstream._default_downstream_registries(non_ent)["mirrors"]
    }
    assert "registry.rancher.com" in standard_mirrors
    assert "registry.rancher.cn" not in standard_mirrors


def test_executor_start_persists_running_state(tmp_path: Path) -> None:
    secret_root = tmp_path / "secrets"
    write_required_secrets(secret_root)
    service = make_service(tmp_path, secret_root=secret_root, executor=RunningExecutor())
    digest = service.validate_config(EXAMPLE)["data"]["config_digest"]
    plan = service.build_plan(digest, ["vm"])["data"]["plan"]
    with patch("rancher_rke2_mcp.preflight.socket.create_connection"):
        preflight = service.preflight_plan(plan["plan_id"])["data"]["preflight"]
    result = service.start_run(
        plan_id=plan["plan_id"], config_digest=digest,
        preflight_id=preflight["preflight_id"], approval_text=plan["approval_text"],
        idempotency_key="running-vm-run",
    )
    run_id = result["data"]["run"]["run_id"]
    stored = service.get_run(run_id)
    assert stored["state"] == "RUNNING"
    assert stored["data"]["run"]["component_states"][0]["checkpoint"] == "control_host_execution_started"
    events = service.get_run_events(run_id)["data"]["events"]
    assert [event["type"] for event in events] == ["RUN_QUEUED", "RUN_STARTED"]


def test_workflow_runs_vm_then_node_init_after_one_approval(tmp_path: Path) -> None:
    secret_root = tmp_path / "secrets"
    write_required_secrets(secret_root)
    vm_executor = WorkflowExecutor()
    node_executor = WorkflowExecutor()
    service = make_service(
        tmp_path,
        secret_root=secret_root,
        executor=vm_executor,
        node_executor=node_executor,
    )
    digest = service.validate_config(EXAMPLE)["data"]["config_digest"]
    plan = service.build_plan(digest, ["vm", "node-init"])["data"]["plan"]
    assert plan["executable"] is True
    assert plan["approval_text"] == f"APPROVE WORKFLOW {plan['plan_id']}"
    with patch("rancher_rke2_mcp.preflight.socket.create_connection"):
        preflight = service.preflight_plan(plan["plan_id"])["data"]["preflight"]
    with patch.object(service, "_wait_for_workflow_nodes", return_value=True):
        result = service.start_workflow(
            plan_id=plan["plan_id"],
            config_digest=digest,
            preflight_id=preflight["preflight_id"],
            approval_text=plan["approval_text"],
            idempotency_key="vm-node-workflow-001",
        )
        run_id = result["data"]["run"]["run_id"]
        deadline = time.monotonic() + 1
        stored = service.get_run(run_id)
        while stored["state"] in {"QUEUED", "RUNNING"} and time.monotonic() < deadline:
            time.sleep(0.01)
            stored = service.get_run(run_id)
    assert stored["state"] == "SUCCEEDED"
    assert [item["state"] for item in stored["data"]["run"]["component_states"]] == [
        "SUCCEEDED", "SUCCEEDED"
    ]
    assert vm_executor.artifact_paths[0].endswith("/vm")
    assert node_executor.artifact_paths[0].endswith("/node-init")


def test_workflow_runs_local_rke2_after_node_init(tmp_path: Path) -> None:
    secret_root = tmp_path / "secrets"
    write_required_secrets(secret_root)
    vm_executor = WorkflowExecutor()
    node_executor = WorkflowExecutor()
    local_executor = WorkflowExecutor()
    service = make_service(
        tmp_path,
        secret_root=secret_root,
        executor=vm_executor,
        node_executor=node_executor,
        local_executor=local_executor,
    )
    digest = service.validate_config(EXAMPLE)["data"]["config_digest"]
    plan = service.build_plan(digest, ["vm", "node-init", "local-rke2"])["data"]["plan"]
    assert plan["executable"] is True
    assert plan["execution_mode"] == "WORKFLOW"
    with patch("rancher_rke2_mcp.preflight.socket.create_connection"):
        preflight = service.preflight_plan(plan["plan_id"])["data"]["preflight"]
    with patch.object(service, "_wait_for_workflow_nodes", return_value=True):
        result = service.start_workflow(
            plan_id=plan["plan_id"],
            config_digest=digest,
            preflight_id=preflight["preflight_id"],
            approval_text=plan["approval_text"],
            idempotency_key="vm-node-local-workflow-001",
        )
        run_id = result["data"]["run"]["run_id"]
        deadline = time.monotonic() + 1
        stored = service.get_run(run_id)
        while stored["state"] in {"QUEUED", "RUNNING"} and time.monotonic() < deadline:
            time.sleep(0.01)
            stored = service.get_run(run_id)
    assert stored["state"] == "SUCCEEDED"
    assert [item["state"] for item in stored["data"]["run"]["component_states"]] == [
        "SUCCEEDED", "SUCCEEDED", "SUCCEEDED"
    ]
    assert local_executor.artifact_paths[0].endswith("/local-rke2")


def test_workflow_runs_rancher_after_local_rke2_with_one_approval(
    tmp_path: Path,
) -> None:
    secret_root = tmp_path / "secrets"
    write_required_secrets(secret_root)
    vm_executor = WorkflowExecutor()
    node_executor = WorkflowExecutor()
    local_executor = WorkflowExecutor()
    rancher_executor = RancherWorkflowExecutor()
    service = make_service(
        tmp_path,
        secret_root=secret_root,
        executor=vm_executor,
        node_executor=node_executor,
        local_executor=local_executor,
        rancher_executor=rancher_executor,
    )
    capabilities = service.get_capabilities()["data"]
    assert ["vm", "node-init", "local-rke2", "rancher"] in capabilities[
        "workflow_execution_scopes"
    ]
    digest = service.validate_config(EXAMPLE)["data"]["config_digest"]
    plan = service.build_plan(
        digest, ["vm", "node-init", "local-rke2", "rancher"]
    )["data"]["plan"]
    assert plan["executable"] is True
    assert plan["execution_mode"] == "WORKFLOW"
    assert plan["approval_text"] == f"APPROVE WORKFLOW {plan['plan_id']}"
    with patch("rancher_rke2_mcp.preflight.socket.create_connection"):
        preflight = service.preflight_plan(plan["plan_id"])["data"]["preflight"]
    with patch.object(service, "_wait_for_workflow_nodes", return_value=True):
        result = service.start_workflow(
            plan_id=plan["plan_id"],
            config_digest=digest,
            preflight_id=preflight["preflight_id"],
            approval_text=plan["approval_text"],
            idempotency_key="vm-node-local-rancher-workflow-001",
        )
        run_id = result["data"]["run"]["run_id"]
        deadline = time.monotonic() + 1
        stored = service.get_run(run_id)
        while stored["state"] in {"QUEUED", "RUNNING"} and time.monotonic() < deadline:
            time.sleep(0.01)
            stored = service.get_run(run_id)
    assert stored["state"] == "SUCCEEDED"
    assert [
        item["state"] for item in stored["data"]["run"]["component_states"]
    ] == ["SUCCEEDED", "SUCCEEDED", "SUCCEEDED", "SUCCEEDED"]
    assert local_executor.artifact_paths[0].endswith("/local-rke2")
    assert rancher_executor.calls[0][1].endswith("/rancher")
    rancher_config = rancher_executor.calls[0][0]
    assert isinstance(rancher_config, dict)
    assert rancher_config["_rancher_kubeconfig_source"].endswith(
        "/runs/" + run_id + "/kubeconfig/rke2.yaml"
    )


def test_local_rke2_assets_guard_against_an_empty_inventory() -> None:
    assets = PROJECT_ROOT / "src" / "rancher_rke2_mcp" / "assets" / "local-rke2"
    runner = (assets / "local-rke2-runner.sh").read_text(encoding="utf-8")
    verifier = (assets / "verify-inventory.py").read_text(encoding="utf-8")
    playbook = (assets / "ansible" / "playbooks" / "local-rke2.yml").read_text(
        encoding="utf-8"
    )
    tasks = (assets / "ansible" / "roles" / "local_rke2" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )
    executor_source = (PROJECT_ROOT / "src" / "rancher_rke2_mcp" / "executor.py").read_text(
        encoding="utf-8"
    )
    assert "ansible-inventory --list" in runner
    assert "verify-inventory.py" in runner
    assert "inventory/hosts.yml" in executor_source
    assert "group_vars/all.yml" in executor_source
    assert "INVENTORY_GROUP_INVALID" in verifier
    assert "INVENTORY_VARIABLE_INVALID" in verifier
    assert "vars_files:" in playbook
    assert "../group_vars/all.yml" in playbook
    assert "Count Ready local servers with whitespace-safe column parsing" in tasks
    assert "rke2_ready_server_count.stdout | int == 3" in tasks
    assert "Wait for every local server to report Ready after image pulls" in tasks
    assert "retries: 30" in tasks
    assert "delay: 20" in tasks


def test_start_run_rejects_full_plan_in_0_4_0(tmp_path: Path) -> None:
    secret_root = tmp_path / "secrets"
    write_required_secrets(secret_root)
    service = make_service(tmp_path, secret_root=secret_root, executor=QueuedExecutor())
    validation = service.validate_config(EXAMPLE)
    digest = validation["data"]["config_digest"]
    plan = service.build_plan(digest, ["all"])["data"]["plan"]

    with patch("rancher_rke2_mcp.preflight.socket.create_connection"):
        preflight = service.preflight_plan(plan["plan_id"])["data"]["preflight"]

    result = service.start_run(
        plan_id=plan["plan_id"],
        config_digest=digest,
        preflight_id=preflight["preflight_id"],
        approval_text=plan["approval_text"],
        idempotency_key="all-run-001",
    )
    assert result["ok"] is False
    assert result["state"] == "UNSUPPORTED_SCOPE"


def test_preflight_checks_secret_availability_and_tcp_without_exposing_values(
    tmp_path: Path,
) -> None:
    secret_root = tmp_path / "secrets"
    write_required_secrets(secret_root)
    service = make_service(tmp_path, secret_root=secret_root)
    validation = service.validate_config(EXAMPLE)
    plan = service.build_plan(validation["data"]["config_digest"], ["all"])
    plan_id = plan["data"]["plan"]["plan_id"]

    with patch(
        "rancher_rke2_mcp.preflight._default_tcp_probe",
        return_value=None,
    ):
        # The default argument is bound at construction time, so patch the socket
        # primitive used by that probe rather than contacting example endpoints.
        with patch("rancher_rke2_mcp.preflight.socket.create_connection"):
            result = service.preflight_plan(plan_id)

    assert result["ok"] is True
    assert result["state"] == "PASSED"
    preflight = result["data"]["preflight"]
    assert preflight["non_mutating"] is True
    assert preflight["authentication_attempted"] is False
    assert preflight["summary"]["failed"] == 0
    assert preflight["summary"]["skipped"] == 13
    assert any(item["name"] == "tcp.vsphere_https" for item in preflight["checks"])
    assert all(
        item["status"] == "SKIPPED"
        for item in preflight["checks"]
        if item["name"].startswith("tcp.node_ssh.")
    )
    serialized = json.dumps(preflight, ensure_ascii=False)
    assert "control_host_password-value" not in serialized
    assert "node_password-value" not in serialized

    fetched = service.get_preflight(preflight["preflight_id"])
    assert fetched["state"] == "PASSED"


def test_preflight_checks_node_ssh_when_plan_does_not_include_vm(
    tmp_path: Path,
) -> None:
    secret_root = tmp_path / "secrets"
    write_required_secrets(secret_root)
    service = make_service(tmp_path, secret_root=secret_root)
    validation = service.validate_config(EXAMPLE)
    plan = service.build_plan(validation["data"]["config_digest"], ["node-init"])

    with patch("rancher_rke2_mcp.preflight.socket.create_connection"):
        result = service.preflight_plan(plan["data"]["plan"]["plan_id"])

    checks = result["data"]["preflight"]["checks"]
    node_checks = [
        item for item in checks if item["name"].startswith("tcp.node_ssh.")
    ]
    assert len(node_checks) == 10
    assert all(item["status"] == "PASSED" for item in node_checks)


def test_preflight_reports_unmounted_secret_without_returning_reference_value(
    tmp_path: Path,
) -> None:
    secret_root = tmp_path / "secrets"
    secret_root.mkdir()
    service = make_service(tmp_path, secret_root=secret_root)
    validation = service.validate_config(EXAMPLE)
    plan = service.build_plan(validation["data"]["config_digest"], ["vm"])
    with patch("rancher_rke2_mcp.preflight.socket.create_connection"):
        result = service.preflight_plan(plan["data"]["plan"]["plan_id"])

    assert result["ok"] is False
    assert result["state"] == "FAILED"
    checks = result["data"]["preflight"]["checks"]
    assert any(
        item["name"] == "secret.control_host_password" and item["status"] == "FAILED"
        for item in checks
    )


def test_only_the_vm_executor_may_import_ssh_client() -> None:
    forbidden = {
        "subprocess",
        "ansible_runner",
        "docker",
        "pyVmomi",
        "kubernetes",
    }
    source_root = PROJECT_ROOT / "src" / "rancher_rke2_mcp"
    imported: set[str] = set()
    for path in source_root.glob("*.py"):
        if path.name == "executor.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(forbidden)


def test_vm_dependency_installer_handles_pristine_tzdata_with_readonly_localtime() -> None:
    assets = PROJECT_ROOT / "src" / "rancher_rke2_mcp" / "assets" / "vm"
    runner = (assets / "vm-runner.sh").read_text(encoding="utf-8")
    installer = (assets / "install-control-dependencies.sh").read_text(encoding="utf-8")

    assert '-v /etc/localtime:/etc/localtime:ro' in runner
    assert 'install-control-dependencies.sh' in runner
    assert "tzdata.postinst" in installer
    assert "dpkg --configure -a" in installer
    assert "apt-mark hold tzdata" in installer
    assert "mv -f \"$backup\" \"$postinst\"" in installer


def test_vm_proxy_environment_is_limited_to_dependency_downloads() -> None:
    assets = PROJECT_ROOT / "src" / "rancher_rke2_mcp" / "assets" / "vm"
    runner = (assets / "vm-runner.sh").read_text(encoding="utf-8")
    installer = (assets / "install-control-dependencies.sh").read_text(encoding="utf-8")
    executor_source = (PROJECT_ROOT / "src" / "rancher_rke2_mcp" / "executor.py").read_text(encoding="utf-8")

    assert 'source "$run_dir/.dependency.env"' in installer
    assert "DEPENDENCY_ENV_MISSING" in runner
    assert "unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy" in runner
    assert "def _dependency_env" in executor_source
    assert "Terraform talks directly to vCenter" in executor_source


def test_vm_provider_cache_uses_persistent_filesystem_mirror() -> None:
    assets = PROJECT_ROOT / "src" / "rancher_rke2_mcp" / "assets" / "vm"
    runner = (assets / "vm-runner.sh").read_text(encoding="utf-8")
    cache_script = (assets / "prepare-terraform-provider-cache.sh").read_text(encoding="utf-8")

    assert 'provider-cache.log' in runner
    assert 'prepare-terraform-provider-cache.sh' in runner
    assert 'TF_CLI_CONFIG_FILE=/software/terraform/provider-cache-config/vmware-vsphere.tfrc' in runner
    assert 'provider-downloads/registry.terraform.io/vmware/vsphere/$version' in cache_script
    assert 'providers/registry.terraform.io/vmware/vsphere/$version/$platform' in cache_script
    assert 'OFFLINE_PROVIDER_FILE_MISSING' in cache_script
    assert 'PROVIDER_CHECKSUM_MISMATCH' in cache_script
    assert 'filesystem_mirror' in cache_script


def test_vm_terraform_assets_are_uploaded_to_the_execution_directory() -> None:
    assets = PROJECT_ROOT / "src" / "rancher_rke2_mcp" / "assets" / "vm"
    executor = VmExecutor(secret_root="/run/secrets", assets_root=assets)

    assert executor._asset_destination(assets / "terraform" / "vm.tf", "/run/vm") == "/run/vm/vm.tf"
    assert executor._asset_destination(assets / "terraform" / "data.tf", "/run/vm") == "/run/vm/data.tf"
    assert executor._asset_destination(assets / "run-logged.sh", "/run/vm") == "/run/vm/run-logged.sh"


def test_vm_runner_requires_expected_vm_resources_in_terraform_state() -> None:
    assets = PROJECT_ROOT / "src" / "rancher_rke2_mcp" / "assets" / "vm"
    runner = (assets / "vm-runner.sh").read_text(encoding="utf-8")
    verifier = (assets / "verify-vm-state.py").read_text(encoding="utf-8")

    assert "terraform state list" in runner
    assert "verify-vm-state.py" in runner
    assert "VM_STATE_COUNT_MISMATCH" in verifier
    assert "vsphere_virtual_machine.vm[" in verifier


def test_successful_executor_persists_vm_result(tmp_path: Path) -> None:
    secret_root = tmp_path / "secrets"
    write_required_secrets(secret_root)
    service = make_service(tmp_path, secret_root=secret_root, executor=SuccessfulExecutor())
    digest = service.validate_config(EXAMPLE)["data"]["config_digest"]
    plan = service.build_plan(digest, ["vm"])["data"]["plan"]
    with patch("rancher_rke2_mcp.preflight.socket.create_connection"):
        preflight = service.preflight_plan(plan["plan_id"])["data"]["preflight"]
    result = service.start_run(
        plan_id=plan["plan_id"], config_digest=digest,
        preflight_id=preflight["preflight_id"], approval_text=plan["approval_text"],
        idempotency_key="successful-vm-run",
    )
    stored = service.get_run(result["data"]["run"]["run_id"])
    assert stored["state"] == "SUCCEEDED"
    assert stored["data"]["run"]["component_states"][0]["artifact_path"].endswith("/vm")


def test_reads_bearer_token_from_secret_file(tmp_path: Path) -> None:
    token_file = tmp_path / "mcp_bearer_token"
    token_file.write_text("x" * 40 + "\n", encoding="utf-8")
    result = read_secret_setting(
        value_env="TOKEN",
        file_env="TOKEN_FILE",
        environ={"TOKEN": "legacy-value", "TOKEN_FILE": str(token_file)},
    )
    assert result == "x" * 40


def test_resolves_only_docker_secrets_inside_fixed_root(tmp_path: Path) -> None:
    secret = tmp_path / "vsphere_password"
    secret.write_text("sensitive-value\n", encoding="utf-8")
    resolver = DockerSecretResolver(tmp_path)
    assert resolver.resolve("docker-secret://vsphere_password") == "sensitive-value"

    for unsafe in (
        "file:///tmp/secret",
        "docker-secret://../secret",
        "docker-secret:///absolute",
    ):
        try:
            resolver.resolve(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe reference unexpectedly resolved: {unsafe}")


def test_authenticated_streamable_http_end_to_end(tmp_path: Path) -> None:
    async def scenario() -> None:
        token = "test-token-that-is-longer-than-32-characters"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        mcp_server = create_server(tmp_path / "http-state.db")
        app = create_http_app(token, mcp_server)
        uvicorn_server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="error",
            )
        )
        task = asyncio.create_task(uvicorn_server.serve())
        try:
            for _ in range(100):
                if uvicorn_server.started:
                    break
                await asyncio.sleep(0.02)
            assert uvicorn_server.started

            url = f"http://127.0.0.1:{port}/mcp"
            async with httpx2.AsyncClient() as unauthorized:
                response = await unauthorized.post(url, json={})
                assert response.status_code == 401

            async with httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {token}"}
            ) as authorized:
                transport = streamable_http_client(url, http_client=authorized)
                async with Client(transport, cache=None) as client:
                    tools = await client.list_tools()
                    assert {item.name for item in tools.tools} == set(
                        READ_ONLY_TOOLS
                    ) | set(MUTATION_TOOLS)

                    validation_result = await client.call_tool(
                        "validate_config",
                        {"config": EXAMPLE},
                    )
                    validation = validation_result.structured_content
                    assert validation["state"] == "VALIDATED"

                    plan_result = await client.call_tool(
                        "build_plan",
                        {
                            "config_digest": validation["data"]["config_digest"],
                            "target_components": ["all"],
                        },
                    )
                    plan = plan_result.structured_content
                    assert plan["state"] == "PLANNED"

                    read_result = await client.call_tool(
                        "get_plan",
                        {"plan_id": plan["data"]["plan"]["plan_id"]},
                    )
                    assert read_result.structured_content["state"] == "PLANNED"
        finally:
            uvicorn_server.should_exit = True
            await asyncio.wait_for(task, timeout=10)

    asyncio.run(scenario())
