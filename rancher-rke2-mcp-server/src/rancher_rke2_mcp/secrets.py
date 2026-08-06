from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any


DOCKER_SECRET_REFERENCE = re.compile(
    r"^docker-secret://(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]{0,127})$"
)
URL_USERINFO = re.compile(r"://[^/@\s]+:[^/@\s]+@")
PLAINTEXT_SECRET_KEY_PARTS = (
    "password",
    "passwd",
    "token",
    "secret",
    "credential",
    "private_key",
    "ssh_key",
    "api_key",
)

# Rancher2 Terraform registries entries carry Kubernetes Secret *names*, which
# are identifiers such as ``myharbor-auth`` and never credential material.
NON_CREDENTIAL_SECRET_NAME_KEYS = frozenset(
    {"authconfigsecretname", "tlssecretname"}
)


def ensure_reference_only_config(value: Any, path: str = "$") -> None:
    """Reject plaintext secret fields and credential-bearing URLs recursively."""
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            item_path = f"{path}.{key_text}"
            if (
                any(part in lowered for part in PLAINTEXT_SECRET_KEY_PARTS)
                and not lowered.endswith("_ref")
                and lowered not in NON_CREDENTIAL_SECRET_NAME_KEYS
            ):
                raise ValueError(
                    f"{item_path}: plaintext secret fields are forbidden; "
                    "use a *_ref field with docker-secret://<name>"
                )
            ensure_reference_only_config(item, item_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            ensure_reference_only_config(item, f"{path}[{index}]")
    elif isinstance(value, str) and URL_USERINFO.search(value):
        raise ValueError(
            f"{path}: URLs containing credentials are forbidden; "
            "use a separate *_ref field"
        )


def read_secret_setting(
    *,
    value_env: str,
    file_env: str,
    environ: dict[str, str] | None = None,
) -> str:
    """Read a secret from a file, with an environment fallback for local tests."""
    selected = environ if environ is not None else os.environ
    secret_file = selected.get(file_env, "").strip()
    if secret_file:
        value = Path(secret_file).read_text(encoding="utf-8").rstrip("\r\n")
    else:
        value = selected.get(value_env, "")
    if not value:
        raise ValueError(f"set {file_env} to a readable non-empty secret file")
    return value


class DockerSecretResolver:
    """Resolve docker-secret:// references inside one fixed secret directory."""

    def __init__(self, root: str | Path = "/run/secrets"):
        self.root = Path(root).resolve()

    def resolve(self, reference: str) -> str:
        match = DOCKER_SECRET_REFERENCE.fullmatch(reference)
        if match is None:
            raise ValueError("unsupported or malformed secret reference")
        candidate = (self.root / match.group("name")).resolve()
        if candidate.parent != self.root:
            raise ValueError("secret reference escapes the configured secret root")
        if not candidate.is_file():
            raise FileNotFoundError(f"referenced Docker Secret is unavailable: {reference}")
        value = candidate.read_text(encoding="utf-8").rstrip("\r\n")
        if not value:
            raise ValueError(f"referenced Docker Secret is empty: {reference}")
        return value
