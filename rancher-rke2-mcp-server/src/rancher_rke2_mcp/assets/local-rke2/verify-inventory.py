#!/usr/bin/env python3
"""Fail Local RKE2 before Ansible can silently skip an empty inventory."""

from __future__ import annotations

import json
import sys
from pathlib import Path


if len(sys.argv) != 4:
    raise SystemExit("usage: verify-inventory.py <inventory.json> <group> <expected-host-count>")

inventory_path = Path(sys.argv[1])
group = sys.argv[2]
expected_count = int(sys.argv[3])
inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
hosts = inventory.get(group, {}).get("hosts", [])
if not isinstance(hosts, list) or len(hosts) != expected_count:
    raise SystemExit(
        f"INVENTORY_GROUP_INVALID group={group} expected={expected_count} actual={len(hosts) if isinstance(hosts, list) else 'invalid'}"
    )
print(f"INVENTORY_GROUP_VALID group={group} hosts={','.join(hosts)}")
