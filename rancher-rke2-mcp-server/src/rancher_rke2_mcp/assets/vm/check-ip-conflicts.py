#!/usr/bin/env python3
"""Ping-only VM target IP conflict check."""
from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess


def ping(address: str, timeout_seconds: int) -> bool:
    result = subprocess.run(
        ["ping", "-c", "1", "-W", str(timeout_seconds), address],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("addresses", nargs="+")
    parser.add_argument("--timeout", type=int, default=1)
    args = parser.parse_args()
    addresses = [str(ipaddress.ip_address(value)) for value in args.addresses]
    results = {value: ("in-use" if ping(value, args.timeout) else "available") for value in addresses}
    conflicts = [value for value, status in results.items() if status == "in-use"]
    print(json.dumps({"method": "ping-only", "addresses": addresses, "results": results, "conflicts": conflicts}))
    if conflicts:
        print("IP_CONFLICT: " + ", ".join(conflicts))
        return 2
    print("IP_CHECK_CLEAR: no target IP responded to ICMP; VM creation may continue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
