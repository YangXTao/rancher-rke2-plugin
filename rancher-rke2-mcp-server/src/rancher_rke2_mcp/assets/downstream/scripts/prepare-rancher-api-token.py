#!/usr/bin/env python3
"""Verify Rancher, login as local admin, and store one protected API token."""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class RancherAPIError(RuntimeError):
    pass


def rancher_version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-ent)?", value)
    if not match:
        raise RancherAPIError(f"invalid Rancher version: {value}")
    return tuple(int(part) for part in match.groups())


def build_opener(ca_file: Path) -> urllib.request.OpenerDirector:
    if not ca_file.is_file():
        raise RancherAPIError(f"Rancher CA file is missing: {ca_file}")
    context = ssl.create_default_context(cafile=str(ca_file))
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
    )


def api_request(opener, base_url: str, path: str, method="GET", payload=None, bearer=None):
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise RancherAPIError(f"Rancher API {method} {path} returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RancherAPIError(f"Rancher API {method} {path} failed: {exc.reason}") from exc
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw.decode("utf-8", errors="replace")


def verify_rancher(opener, base_url: str) -> None:
    result = api_request(opener, base_url, "/ping")
    if str(result).strip().strip('"') != "pong":
        raise RancherAPIError(f"RANCHER_NOT_READY: /ping returned {result!r}")


def verify_token(opener, base_url: str, token: str) -> None:
    result = api_request(opener, base_url, "/v3/users?limit=1", bearer=token)
    if not isinstance(result, dict) or "data" not in result:
        raise RancherAPIError("RANCHER_TOKEN_VERIFY_FAILED: authenticated API response is invalid")


def create_login_token(opener, base_url: str, username: str, password: str) -> str:
    result = api_request(
        opener,
        base_url,
        "/v3-public/localProviders/local?action=login",
        method="POST",
        payload={"username": username, "password": password, "ttl": 60000},
    )
    if not isinstance(result, dict) or not result.get("token"):
        raise RancherAPIError("RANCHER_ADMIN_LOGIN_FAILED: login response did not contain a token")
    return str(result["token"])


def normalize_server_url(value: str) -> str:
    parsed = urllib.parse.urlparse(str(value).strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise RancherAPIError("Rancher server-url must be an HTTPS origin without path, query, or fragment")
    return f"https://{parsed.netloc}"


def ensure_server_url(opener, base_url: str, bearer: str, desired_server_url: str) -> str:
    desired = normalize_server_url(desired_server_url)
    result = api_request(opener, base_url, "/v3/settings/server-url", bearer=bearer)
    if not isinstance(result, dict):
        raise RancherAPIError("RANCHER_SERVER_URL_READ_FAILED: settings response is invalid")
    current_raw = result.get("value") or ""
    if not str(current_raw).strip():
        api_request(
            opener,
            base_url,
            "/v3/settings/server-url",
            method="PUT",
            payload={"name": "server-url", "value": desired},
            bearer=bearer,
        )
        verified = api_request(opener, base_url, "/v3/settings/server-url", bearer=bearer)
        verified_raw = verified.get("value") if isinstance(verified, dict) else None
        if not verified_raw or normalize_server_url(str(verified_raw)) != desired:
            raise RancherAPIError("RANCHER_SERVER_URL_VERIFY_FAILED: value did not persist")
        return "set"
    current = normalize_server_url(str(current_raw))
    if current != desired:
        raise RancherAPIError(
            f"RANCHER_SERVER_URL_DRIFT: configured={current!r} approved={desired!r}; refusing to overwrite"
        )
    return "matched"


def create_api_token(opener, base_url: str, login_token: str, version: str, token_api: str, description: str, ttl_ms: int | None):
    use_ext = token_api == "ext" or (token_api == "auto" and rancher_version_tuple(version) >= (2, 13, 0))
    if use_ext:
        spec = {"description": description}
        if ttl_ms is not None:
            spec["ttl"] = ttl_ms
        payload = {
            "apiVersion": "ext.cattle.io/v1",
            "kind": "Token",
            "metadata": {"generateName": "automation-"},
            "spec": spec,
        }
        result = api_request(
            opener, base_url, "/apis/ext.cattle.io/v1/tokens",
            method="POST", payload=payload, bearer=login_token,
        )
        token = (result or {}).get("status", {}).get("bearerToken") if isinstance(result, dict) else None
        api_used = "ext.cattle.io/v1"
    else:
        payload = {"type": "token", "description": description}
        if ttl_ms is not None:
            payload["ttl"] = ttl_ms
        result = api_request(
            opener, base_url, "/v3/token",
            method="POST", payload=payload, bearer=login_token,
        )
        token = (result or {}).get("token") if isinstance(result, dict) else None
        api_used = "v3"
    if not token:
        raise RancherAPIError(f"RANCHER_API_TOKEN_CREATE_FAILED: {api_used} response did not contain a bearer token")
    return str(token), api_used


def atomic_secret_write(path: Path, value: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.write("\n")
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--rancher-version", required=True)
    parser.add_argument("--admin-username", default="admin")
    parser.add_argument("--admin-password-file", type=Path, required=True)
    parser.add_argument("--ca-file", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--token-api", choices=("auto", "legacy", "ext"), default="auto")
    parser.add_argument("--description", default="rancher-rke2-automation")
    parser.add_argument("--ttl-ms", type=int)
    parser.add_argument("--desired-server-url", required=True)
    args = parser.parse_args()

    if urllib.parse.urlparse(args.api_url).scheme != "https":
        raise RancherAPIError("Rancher API URL must use HTTPS")
    if args.admin_username != "admin":
        raise RancherAPIError("Stage 60 currently requires the local admin user")
    if args.ttl_ms is not None and args.ttl_ms <= 0:
        raise RancherAPIError("token TTL must be positive when supplied")
    if not args.admin_password_file.is_file():
        raise RancherAPIError(f"admin password file is missing: {args.admin_password_file}")

    args.output_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.output_directory, 0o700)
    token_file = args.output_directory / "token-key"
    metadata_file = args.output_directory / "metadata.json"
    existing = [path.exists() for path in (token_file, metadata_file)]
    opener = build_opener(args.ca_file)
    verify_rancher(opener, args.api_url)
    if any(existing):
        if not all(existing):
            raise RancherAPIError("RANCHER_API_TOKEN_CHECKPOINT_INVALID: token checkpoint is incomplete")
        token = token_file.read_text(encoding="utf-8").strip()
        verify_token(opener, args.api_url, token)
        ensure_server_url(opener, args.api_url, token, args.desired_server_url)
        print(f"RANCHER_API_TOKEN_REUSED: {token_file}")
        return 0

    password = args.admin_password_file.read_text(encoding="utf-8").rstrip("\r\n")
    if not password:
        raise RancherAPIError("admin password file is empty")
    login_token = create_login_token(opener, args.api_url, args.admin_username, password)
    server_url_action = ensure_server_url(
        opener, args.api_url, login_token, args.desired_server_url
    )
    token, api_used = create_api_token(
        opener, args.api_url, login_token, args.rancher_version,
        args.token_api, args.description, args.ttl_ms,
    )
    verify_token(opener, args.api_url, token)
    atomic_secret_write(token_file, token)
    metadata = {
        "api_url": args.api_url,
        "rancher_version": args.rancher_version,
        "username": args.admin_username,
        "token_api": api_used,
        "description": args.description,
        "ttl_ms": args.ttl_ms,
        "server_url": normalize_server_url(args.desired_server_url),
        "server_url_action": server_url_action,
    }
    atomic_secret_write(metadata_file, json.dumps(metadata, ensure_ascii=False, sort_keys=True))
    print(f"RANCHER_API_TOKEN_CREATED: {token_file}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RancherAPIError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
