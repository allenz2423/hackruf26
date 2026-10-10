"""Attach opaque request IDs without recording request content."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from typing import Any

ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]], Awaitable[None]]


class RequestContextMiddleware:
    """Assign a server-generated request ID and add it to every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[Any]], send: Callable[..., Awaitable[Any]]) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = secrets.token_hex(12)
        state = scope.setdefault("state", {})
        state["request_id"] = request_id

        async def send_with_request_id(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                has_cache_control = any(key.lower() == b"cache-control" for key, _ in headers)
                if scope.get("path", "").startswith("/api/") and not has_cache_control:
                    headers.append((b"cache-control", b"no-store, private"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_request_id)
