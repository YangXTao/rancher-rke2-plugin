#!/usr/bin/env python3
"""Fail Local RKE2 before Ansible can silently skip an empty inventory."""

from __future__ import annotations

import json
import sys
from pathlib import Path


if len(sys.argv) != 5:
    raise SystemExit(
        "usage: verify-inventory.py <inventory.json> <group> <expected-host-count> <rke2-version>"
    )

inventory_path = Path(sys.argv[1])
group = sys.argv[2]
expected_count = int(sys.argv[3])
expected_version = sys.argv[4]
inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
hosts = inventory.get(group, {}).get("hosts", [])
if not isinstance(hosts, list) or len(hosts) != expected_count:
    raise SystemExit(
        f"INVENTORY_GROUP_INVALID group={group} expected={expected_count} actual={len(hosts) if isinstance(hosts, list) else 'invalid'}"
    )
hostvars = inventory.get("_meta", {}).get("hostvars", {})
missing_version = [
    host for host in hosts if hostvars.get(host, {}).get("rke2_management_version") != expected_version
]
if missing_version:
    raise SystemExit(
        "INVENTORY_VARIABLE_INVALID "
        f"variable=rke2_management_version expected={expected_version} hosts={','.join(missing_version)}"
    )
print(
    f"INVENTORY_GROUP_VALID group={group} hosts={','.join(hosts)} "
    f"rke2_management_version={expected_version}"
)
