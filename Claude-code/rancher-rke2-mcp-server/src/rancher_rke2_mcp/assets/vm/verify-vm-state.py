#!/usr/bin/env python3
"""Verify that Terraform state contains every intended vSphere VM."""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", required=True, type=int)
    args = parser.parse_args()
    resources = [
        line.strip()
        for line in sys.stdin
        if line.strip().startswith("vsphere_virtual_machine.vm[")
    ]
    if len(resources) != args.expected:
        print(
            f"VM_STATE_COUNT_MISMATCH expected={args.expected} actual={len(resources)}",
            file=sys.stderr,
        )
        return 14
    print(f"VM_STATE_VERIFIED count={len(resources)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
