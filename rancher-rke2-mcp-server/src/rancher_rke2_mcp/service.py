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
    READ_ONLY_TOOLS,
    SCHEMA_VERSION,
    SECRET_REFERENCE_SCHEMES,
    SERVER_VERSION,
)
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
    def __init__(self, store: SQLiteStore):
        self.store = store

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
                "infrastructure_side_effects": False,
                "plaintext_credentials_compatible": False,
                "secret_reference_schemes": list(SECRET_REFERENCE_SCHEMES),
                "secrets_persisted": False,
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
            "executable": False,
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
