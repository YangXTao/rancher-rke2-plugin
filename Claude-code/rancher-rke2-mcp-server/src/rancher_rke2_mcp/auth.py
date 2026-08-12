from __future__ import annotations

import hmac
import json
from typing import Any, Awaitable, Callable

ASGIApp = Callable[
    [dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]],
    Awaitable[None],
]


class StaticBearerAuthMiddleware:
    """Minimal bearer-token protection for a dedicated internal MCP server."""

    def __init__(self, app: ASGIApp, token: str):
        if len(token) < 32:
            raise ValueError("RANCHER_RKE2_MCP_TOKEN must be at least 32 characters")
        self.app = app
        self.expected = f"Bearer {token}"

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        supplied = headers.get("authorization", "")
        if not hmac.compare_digest(supplied, self.expected):
            body = json.dumps(
                {"error": "unauthorized", "message": "Bearer token required"}
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
