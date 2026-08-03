from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any
from uuid import uuid4

from .constants import (
    COMPONENT_DEPENDENCIES,
    COMPONENT_ORDER,
    CONTRACT_VERSION,
    MUTATION_TOOLS,
    PLAN_TTL_HOURS,
    PREFLIGHT_TCP_TIMEOUT_SECONDS,
    READ_ONLY_TOOLS,
    SCHEMA_VERSION,
    SECRET_REFERENCE_SCHEMES,
    SERVER_VERSION,
)
from .preflight import NonMutatingPreflight
from .executor import ExecutionResult, VmExecutor
from .secrets import DockerSecretResolver
from .storage import SQLiteStore
from .validation import config_schema, validate


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _envelope(
    *,
    ok: bool,
    state: str,
    data: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "request_id": "req-" + uuid4().hex,
        "state": state,
        "data": data or {},
        "warnings": warnings or [],
        "errors": errors or [],
        "artifacts": [],
    }


class ReadOnlyPlanningService:
    def __init__(
        self,
        store: SQLiteStore,
        *,
        secret_root: str = "/run/secrets",
        preflight_timeout_seconds: float = PREFLIGHT_TCP_TIMEOUT_SECONDS,
        executor: VmExecutor | None = None,
    ):
        self.store = store
        self.secret_root = secret_root
        self.preflight_timeout_seconds = preflight_timeout_seconds
        self.executor = executor

    def get_capabilities(self) -> dict[str, Any]:
        return _envelope(
            ok=True,
            state="READY",
            data={
                "server_name": "rancher-rke2",
                "server_version": SERVER_VERSION,
                "contract_version": CONTRACT_VERSION,
                "schema_version": SCHEMA_VERSION,
                "transport": "streamable-http",
                "deployment": "remote-docker",
                "components": list(COMPONENT_ORDER),
                "modes": ["online", "offline"],
                "read_only_tools": list(READ_ONLY_TOOLS),
                "mutation_tools": list(MUTATION_TOOLS),
                "infrastructure_side_effects": bool(
                    self.executor is not None and self.executor.ready()
                ),
                "plaintext_credentials_compatible": False,
                "secret_reference_schemes": list(SECRET_REFERENCE_SCHEMES),
                "secrets_persisted": False,
                "preflight_network_checks": True,
                "preflight_authentication_attempted": False,
                "preflight_mutates_infrastructure": False,
                "execution_scope": ["vm"],
                "execution_backend": "ssh-control-container",
                "execution_backend_configured": bool(
                    self.executor is not None and self.executor.ready()
                ),
                "transport_security_required": True,
            },
        )

    def get_config_schema(self) -> dict[str, Any]:
        return _envelope(
            ok=True,
            state="SCHEMA_AVAILABLE",
            data={"schema": config_schema()},
        )

    def validate_config(self, config: str | dict[str, Any]) -> dict[str, Any]:
        outcome = validate(config)
        if not outcome.valid:
            return _envelope(
                ok=False,
                state="INVALID",
                warnings=outcome.warnings,
                errors=outcome.errors,
            )
        assert outcome.normalized is not None
        assert outcome.config_digest is not None
        created_at = _iso(_now())
        self.store.save_config(
            outcome.config_digest,
            outcome.normalized,
            created_at,
        )
        return _envelope(
            ok=True,
            state="VALIDATED",
            data={
                "valid": True,
                "schema_version": SCHEMA_VERSION,
                "config_digest": outcome.config_digest,
                "validated_at": created_at,
                "redacted_preview": outcome.redacted_preview,
            },
            warnings=outcome.warnings,
        )

    def build_plan(
        self,
        config_digest: str,
        target_components: list[str],
    ) -> dict[str, Any]:
        config = self.store.get_config(config_digest)
        if config is None:
            return _envelope(
                ok=False,
                state="NOT_FOUND",
                errors=[
                    {
                        "path": "config_digest",
                        "message": "Unknown digest; call validate_config first.",
                    }
                ],
            )
        try:
            components = self._normalize_components(target_components)
        except ValueError as exc:
            return _envelope(
                ok=False,
                state="INVALID",
                errors=[{"path": "target_components", "message": str(exc)}],
            )

        created = _now()
        expires = created + timedelta(hours=PLAN_TTL_HOURS)
        plan_id = "plan-" + uuid4().hex
        component_plans = [
            self._component_plan(component, config) for component in components
        ]
        plan = {
            "plan_id": plan_id,
            "state": "PLANNED",
            "read_only": True,
            "executable": components == ["vm"],
            "config_digest": config_digest,
            "target_components": components,
            "created_at": _iso(created),
            "expires_at": _iso(expires),
            "component_plans": component_plans,
            "global_prerequisites": [
                "专属服务器上的只读 MCP 容器可用",
                "配置已通过结构与一致性校验",
                "本阶段不会进行任何目标环境连通性探测",
            ],
            "warnings": [
                "这是静态部署计划，不代表目标环境已经具备条件。",
                "计划不包含执行命令，也不能启动、续跑或销毁基础设施。",
                "在计划有效期内可调用 preflight_plan 执行非变更前置检查。",
                "SQLite 只保存 Secret 引用；真实凭据不进入配置摘要或计划。",
            ],
            "approval_text": f"APPROVE PLAN {plan_id}",
        }
        plan["plan_digest"] = self._plan_digest(plan)
        self.store.save_plan(plan)
        return _envelope(ok=True, state="PLANNED", data={"plan": plan})

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        plan = self.store.get_plan(plan_id)
        if plan is None:
            return _envelope(
                ok=False,
                state="NOT_FOUND",
                errors=[{"path": "plan_id", "message": "Unknown plan ID."}],
            )
        expires = datetime.fromisoformat(plan["expires_at"].replace("Z", "+00:00"))
        state = "EXPIRED" if expires <= _now() else "PLANNED"
        plan["state"] = state
        return _envelope(ok=True, state=state, data={"plan": plan})

    def preflight_plan(self, plan_id: str) -> dict[str, Any]:
        plan = self.store.get_plan(plan_id)
        if plan is None:
            return _envelope(
                ok=False,
                state="NOT_FOUND",
                errors=[{"path": "plan_id", "message": "Unknown plan ID."}],
            )
        expires = datetime.fromisoformat(plan["expires_at"].replace("Z", "+00:00"))
        if expires <= _now():
            return _envelope(
                ok=False,
                state="EXPIRED",
                errors=[
                    {
                        "path": "plan_id",
                        "message": "Plan has expired; validate configuration and build a new plan.",
                    }
                ],
            )
        config = self.store.get_config(plan["config_digest"])
        if config is None:
            return _envelope(
                ok=False,
                state="NOT_FOUND",
                errors=[
                    {
                        "path": "plan_id",
                        "message": "The configuration associated with this plan is unavailable.",
                    }
                ],
            )

        checks = NonMutatingPreflight(
            DockerSecretResolver(self.secret_root),
            timeout_seconds=self.preflight_timeout_seconds,
        ).run(config, planned_components=plan["target_components"])
        failed = sum(item["status"] == "FAILED" for item in checks)
        passed = sum(item["status"] == "PASSED" for item in checks)
        skipped = sum(item["status"] == "SKIPPED" for item in checks)
        state = "PASSED" if failed == 0 else "FAILED"
        created_at = _iso(_now())
        preflight = {
            "preflight_id": "preflight-" + uuid4().hex,
            "plan_id": plan_id,
            "config_digest": plan["config_digest"],
            "state": state,
            "non_mutating": True,
            "authentication_attempted": False,
            "created_at": created_at,
            "expires_at": plan["expires_at"],
            "summary": {"passed": passed, "failed": failed, "skipped": skipped},
            "checks": checks,
        }
        self.store.save_preflight(preflight)
        return _envelope(
            ok=failed == 0,
            state=state,
            data={"preflight": preflight},
            warnings=[
                "Preflight only resolves Secret availability and opens TCP connections; it does not authenticate, execute commands, or change infrastructure."
            ],
        )

    def get_preflight(self, preflight_id: str) -> dict[str, Any]:
        preflight = self.store.get_preflight(preflight_id)
        if preflight is None:
            return _envelope(
                ok=False,
                state="NOT_FOUND",
                errors=[{"path": "preflight_id", "message": "Unknown preflight ID."}],
            )
        expires = datetime.fromisoformat(
            preflight["expires_at"].replace("Z", "+00:00")
        )
        state = "EXPIRED" if expires <= _now() else preflight["state"]
        return _envelope(
            ok=state == "PASSED", state=state, data={"preflight": preflight}
        )

    def start_run(
        self,
        *,
        plan_id: str,
        config_digest: str,
        preflight_id: str,
        approval_text: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Queue one approval-gated VM-only execution on the SSH control host."""
        request_fingerprint = self._request_fingerprint(
            plan_id, config_digest, preflight_id, approval_text
        )
        existing = self.store.get_idempotency_key(idempotency_key)
        if existing is not None:
            if existing["request_fingerprint"] != request_fingerprint:
                return _envelope(
                    ok=False,
                    state="IDEMPOTENCY_CONFLICT",
                    errors=[
                        {
                            "path": "idempotency_key",
                            "message": "This key was already used for a different request.",
                        }
                    ],
                )
            run = self.store.get_run(existing["run_id"])
            if run is None:
                return _envelope(
                    ok=False,
                    state="INTERNAL_ERROR",
                    errors=[
                        {
                            "path": "idempotency_key",
                            "message": "The persisted idempotent run is unavailable.",
                        }
                    ],
                )
            return _envelope(
                ok=True,
                state=run["state"],
                data={"run": run, "idempotent_replay": True},
            )

        plan = self.store.get_plan(plan_id)
        if plan is None:
            return _envelope(
                ok=False,
                state="NOT_FOUND",
                errors=[{"path": "plan_id", "message": "Unknown plan ID."}],
            )
        if plan["config_digest"] != config_digest:
            return _envelope(
                ok=False,
                state="CONFIG_MISMATCH",
                errors=[
                    {
                        "path": "config_digest",
                        "message": "The supplied digest does not match the plan.",
                    }
                ],
            )
        if datetime.fromisoformat(plan["expires_at"].replace("Z", "+00:00")) <= _now():
            return _envelope(
                ok=False,
                state="EXPIRED",
                errors=[
                    {
                        "path": "plan_id",
                        "message": "Plan has expired; validate, plan, and preflight again.",
                    }
                ],
            )
        if plan["target_components"] != ["vm"]:
            return _envelope(
                ok=False,
                state="UNSUPPORTED_SCOPE",
                errors=[
                    {
                        "path": "plan_id",
                        "message": "0.5.4 accepts only a VM-only plan for start_run.",
                    }
                ],
            )
        expected_approval = plan["approval_text"]
        if approval_text != expected_approval:
            return _envelope(
                ok=False,
                state="APPROVAL_REQUIRED",
                errors=[
                    {
                        "path": "approval_text",
                        "message": "Approval text must exactly match the plan approval text.",
                    }
                ],
            )

        preflight = self.store.get_preflight(preflight_id)
        if preflight is None:
            return _envelope(
                ok=False,
                state="NOT_FOUND",
                errors=[
                    {"path": "preflight_id", "message": "Unknown preflight ID."}
                ],
            )
        if preflight["plan_id"] != plan_id or preflight["config_digest"] != config_digest:
            return _envelope(
                ok=False,
                state="PREFLIGHT_MISMATCH",
                errors=[
                    {
                        "path": "preflight_id",
                        "message": "Preflight does not belong to the selected plan and configuration.",
                    }
                ],
            )
        if preflight["state"] != "PASSED" or datetime.fromisoformat(
            preflight["expires_at"].replace("Z", "+00:00")
        ) <= _now():
            return _envelope(
                ok=False,
                state="PREFLIGHT_REQUIRED",
                errors=[
                    {
                        "path": "preflight_id",
                        "message": "A current PASSED preflight is required before starting a run.",
                    }
                ],
            )

        if self.executor is None or not self.executor.ready():
            return _envelope(
                ok=False,
                state="EXECUTION_BACKEND_UNAVAILABLE",
                errors=[
                    {
                        "path": "execution.control_host",
                        "message": "The VM executor requires a mounted control_host_known_hosts file.",
                    }
                ],
            )

        config = self.store.get_config(config_digest)
        if config is None:
            return _envelope(
                ok=False,
                state="NOT_FOUND",
                errors=[{"path": "config_digest", "message": "Configuration is unavailable."}],
            )
        created_at = _iso(_now())
        run_id = "run-" + uuid4().hex
        run = {
            "run_id": run_id,
            "state": "QUEUED",
            "plan_id": plan_id,
            "config_digest": config_digest,
            "preflight_id": preflight_id,
            "target_components": ["vm"],
            "created_at": created_at,
            "updated_at": created_at,
            "execution_backend": "ssh-control-container",
            "component_states": [
                {
                    "component": "vm",
                    "state": "QUEUED",
                    "checkpoint": "awaiting_control_host",
                    "message": "Approved VM execution is queued for the SSH control host.",
                }
            ],
        }
        self.store.save_run(run)
        self.store.save_idempotency_key(idempotency_key, request_fingerprint, run_id)
        self.store.append_run_event(
            run_id,
            created_at,
            {
                "type": "RUN_QUEUED",
                "component": "vm",
                "message": "Approval and preflight were accepted; VM execution was queued.",
            },
        )
        self.executor.submit(
            config=config,
            run_id=run_id,
            on_complete=self._complete_vm_run,
        )
        return _envelope(
            ok=True,
            state="QUEUED",
            data={"run": run, "idempotent_replay": False},
        )

    def _complete_vm_run(self, result: ExecutionResult) -> None:
        run = self.store.get_run(result.run_id)
        if run is None:
            return
        now = _iso(_now())
        run["state"] = "SUCCEEDED" if result.succeeded else "FAILED"
        run["updated_at"] = now
        run["component_states"] = [
            {
                "component": "vm",
                "state": run["state"],
                "checkpoint": "terraform_apply" if result.succeeded else "terraform_or_control_host_failed",
                "message": result.code,
                "artifact_path": result.artifact_path,
            }
        ]
        self.store.update_run(run)
        self.store.append_run_event(
            run["run_id"],
            now,
            {
                "type": result.code,
                "component": "vm",
                "artifact_path": result.artifact_path,
            },
        )

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            return _envelope(
                ok=False,
                state="NOT_FOUND",
                errors=[{"path": "run_id", "message": "Unknown run ID."}],
            )
        return _envelope(ok=run["state"] == "SUCCEEDED", state=run["state"], data={"run": run})

    def get_run_events(
        self, run_id: str, after_cursor: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        if self.store.get_run(run_id) is None:
            return _envelope(
                ok=False,
                state="NOT_FOUND",
                errors=[{"path": "run_id", "message": "Unknown run ID."}],
            )
        try:
            selected_limit = max(1, min(int(limit), 100))
            if after_cursor is not None:
                int(after_cursor)
        except (TypeError, ValueError):
            return _envelope(
                ok=False,
                state="INVALID",
                errors=[
                    {
                        "path": "after_cursor",
                        "message": "after_cursor must be a numeric event cursor.",
                    }
                ],
            )
        events = self.store.get_run_events(run_id, after_cursor, selected_limit)
        return _envelope(
            ok=True,
            state="EVENTS_AVAILABLE",
            data={
                "run_id": run_id,
                "events": events,
                "next_cursor": events[-1]["cursor"] if events else after_cursor,
            },
        )

    @staticmethod
    def _normalize_components(target_components: list[str]) -> list[str]:
        if not target_components:
            raise ValueError("at least one component is required")
        requested = [item.strip().lower() for item in target_components]
        if requested == ["all"] or "all" in requested:
            if len(requested) != 1:
                raise ValueError("'all' cannot be combined with component names")
            return list(COMPONENT_ORDER)
        unknown = sorted(set(requested) - set(COMPONENT_ORDER))
        if unknown:
            raise ValueError("unknown components: " + ", ".join(unknown))
        return [name for name in COMPONENT_ORDER if name in set(requested)]

    @staticmethod
    def _plan_digest(plan: dict[str, Any]) -> str:
        payload = dict(plan)
        payload.pop("plan_digest", None)
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _request_fingerprint(*values: str) -> str:
        return "sha256:" + hashlib.sha256(
            "\x00".join(values).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _component_plan(
        component: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        management = config["nodes"]["management"]
        downstream = config["nodes"]["downstream"]
        versions = config["versions"]
        safe_details: dict[str, Any]
        if component == "vm":
            node_count = (
                len(management["servers"])
                + 1
                + len(downstream["controlplane"])
                + len(downstream["workers"])
            )
            safe_details = {
                "intended_vm_count": node_count,
                "datacenter": config["vsphere"]["datacenter"],
                "resource_pool": config["vsphere"]["resource_pool"],
                "datastore": config["vsphere"]["datastore"],
                "network": config["vsphere"]["network"],
                "template": config["vsphere"]["template"],
            }
        elif component == "node-init":
            safe_details = {
                "intended_node_count": (
                    len(management["servers"])
                    + len(downstream["controlplane"])
                    + len(downstream["workers"])
                ),
                "checks": [
                    "sysctl profile",
                    "kernel modules",
                    "limits",
                    "SELinux",
                    "swap",
                    "firewall",
                ],
            }
        elif component == "local-rke2":
            safe_details = {
                "server_count": len(management["servers"]),
                "rke2_version": versions["rke2_management"],
                "cni": config.get("local_rke2", {}).get("cni", "cilium"),
                "load_balancer_hostname": management["load_balancer"]["hostname"],
            }
        elif component == "rancher":
            safe_details = {
                "rancher_version": versions["rancher"],
                "replicas": config["rancher"]["replicas"],
                "nodeport": config["rancher"]["nodeport"],
                "publication_host": management["load_balancer"]["hostname"],
            }
        else:
            safe_details = {
                "cluster_name": config["downstream_cluster"]["name"],
                "rke2_version": versions["rke2_downstream"],
                "controlplane_count": len(downstream["controlplane"]),
                "worker_count": len(downstream["workers"]),
                "registration_order": config["downstream_cluster"]["registration"][
                    "order"
                ],
            }
        return {
            "component": component,
            "sequence": COMPONENT_ORDER.index(component) + 1,
            "dependencies": list(COMPONENT_DEPENDENCIES[component]),
            "operation": "PLAN_ONLY",
            "would_change_infrastructure": True,
            "changes_applied": False,
            "details": safe_details,
        }
