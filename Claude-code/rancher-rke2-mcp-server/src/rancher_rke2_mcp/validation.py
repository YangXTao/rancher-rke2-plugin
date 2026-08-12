from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from pydantic import ValidationError
import yaml
from yaml.constructor import ConstructorError

from .constants import MAX_YAML_BYTES, SCHEMA_VERSION
from .models import AutomationConfig
from .redaction import redact
from .secrets import ensure_reference_only_config


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key: {key}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class ValidationOutcome:
    valid: bool
    normalized: dict[str, Any] | None
    redacted_preview: dict[str, Any] | None
    config_digest: str | None
    errors: list[dict[str, str]]
    warnings: list[str]


def _parse_input(config: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(config, dict):
        return config
    if not isinstance(config, str):
        raise TypeError("config must be a YAML string or an object")
    if len(config.encode("utf-8")) > MAX_YAML_BYTES:
        raise ValueError(f"YAML exceeds the {MAX_YAML_BYTES}-byte limit")
    loaded = yaml.load(config, Loader=UniqueKeyLoader)
    if not isinstance(loaded, dict):
        raise ValueError("YAML root must be a mapping")
    return loaded


def _digest(config: dict[str, Any]) -> str:
    canonical = json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def validate(config: str | dict[str, Any]) -> ValidationOutcome:
    warnings = [
        "明文凭据字段已禁用；SQLite 只保存 docker-secret:// 引用。",
        "本阶段只校验结构和一致性，不探测 vSphere、SSH、Registry 或 Kubernetes 连通性。",
    ]
    try:
        raw = _parse_input(config)
        ensure_reference_only_config(raw)
        model = AutomationConfig.model_validate(raw)
        normalized = model.model_dump(mode="json")
    except ValidationError as exc:
        errors = []
        for item in exc.errors(include_input=False, include_url=False):
            location = ".".join(str(part) for part in item.get("loc", ())) or "$"
            errors.append({"path": location, "message": str(item.get("msg", "invalid"))})
        return ValidationOutcome(False, None, None, None, errors, warnings)
    except (yaml.YAMLError, TypeError, ValueError) as exc:
        return ValidationOutcome(
            False,
            None,
            None,
            None,
            [{"path": "$", "message": str(exc)}],
            warnings,
        )

    return ValidationOutcome(
        True,
        normalized,
        redact(normalized),
        _digest(normalized),
        [],
        warnings,
    )


def config_schema() -> dict[str, Any]:
    schema = AutomationConfig.model_json_schema()
    schema["$id"] = f"urn:rancher-rke2:config-schema:{SCHEMA_VERSION}"
    schema["x-read-only-planning"] = False
    schema["x-vm-execution"] = {
        "available": True,
        "scope": ["vm"],
        "requires": [
            "current_passed_preflight",
            "exact_plan_approval",
            "idempotency_key",
            "control_host_known_hosts",
        ],
    }
    schema["x-plaintext-credentials-compatible"] = False
    schema["x-secret-reference-schemes"] = ["docker-secret"]
    schema["x-non-mutating-preflight"] = {
        "available": True,
        "checks": ["mounted_secret_availability", "tcp_reachability"],
        "node_ssh_policy": (
            "Node SSH checks are skipped when the plan includes the VM component; "
            "they are checked only for plans that do not create VMs."
        ),
        "does_not_perform": ["authentication", "remote_command_execution"],
    }
    return schema
