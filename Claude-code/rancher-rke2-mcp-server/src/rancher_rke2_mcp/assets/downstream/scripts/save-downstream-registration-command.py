#!/usr/bin/env python3
"""Save Terraform's registration command and guarantee curl TLS bypass is explicit."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def ensure_insecure_curl(command: str) -> str:
    command = command.strip()
    if not command or "\n" in command or "\r" in command:
        raise ValueError("registration command must be one non-empty line")
    match = re.search(r"\bcurl\b([^|]*)", command)
    if not match:
        raise ValueError("registration command does not contain curl")
    curl_options = match.group(1)
    tokens = curl_options.split()
    has_insecure = "--insecure" in tokens or any(
        token.startswith("-") and not token.startswith("--") and "k" in token[1:]
        for token in tokens
    )
    if not has_insecure:
        command = command[:match.start()] + "curl --insecure" + command[match.start() + 4:]
    return command


def atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value + "\n")
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("terraform_directory", type=Path)
    parser.add_argument("output_file", type=Path)
    args = parser.parse_args()
    result = subprocess.run(
        ["terraform", f"-chdir={args.terraform_directory}", "output", "-raw", "registration_command"],
        check=False, capture_output=True, text=True,
    )
    if result.returncode:
        print("ERROR: terraform registration command output failed", file=sys.stderr)
        return result.returncode
    try:
        command = ensure_insecure_curl(result.stdout)
    except ValueError as exc:
        print(f"ERROR: INVALID_REGISTRATION_COMMAND: {exc}", file=sys.stderr)
        return 3
    atomic_write(args.output_file, command)
    print(f"REGISTRATION_COMMAND_SAVED: {args.output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
