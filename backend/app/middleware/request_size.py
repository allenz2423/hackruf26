"""Bound small capability endpoint bodies before JSON/Pydantic validation."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]], Awaitable[None]]
BOUNDED_BODY_PATHS = {"/api/live-token", "/api/tools/calculate"}


class RequestSizeLimitMiddleware:
    """Buffer bounded API requests and reject oversized/chunked bodies."""

    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[Any]], send: Callable[..., Awaitable[Any]]) -> None:
        if scope["type"] != "http" or scope.get("path") not in BOUNDED_BODY_PATHS:
            await self.app(scope, receive, send)
            return

        content_length = next(
            (value for key, value in scope.get("headers", []) if key.lower() == b"content-length"),
            None,
        )
        if content_length is not None:
            try:
                if int(content_length) > self.max_body_bytes:
                    await self._reject(send, scope)
                    return
            except ValueError:
                await self._reject(send, scope)
                return

        buffered_messages: list[dict[str, Any]] = []
        body_size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                buffered_messages.append(message)
                continue

            body_size += len(message.get("body", b""))
            if body_size > self.max_body_bytes:
                await self._reject(send, scope)
                return
            buffered_messages.append(message)
            if not message.get("more_body", False):
                break

        replay = deque(buffered_messages)

        async def replay_receive() -> dict[str, Any]:
            if replay:
                return replay.popleft()
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(send: Callable[..., Awaitable[Any]], scope: dict[str, Any]) -> None:
        request_id = scope.get("state", {}).get("request_id")
        payload = {
            "code": "request_too_large",
            "message": "The request body is too large.",
            "request_id": request_id,
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store, private"),
        ]
        if request_id:
            headers.append((b"x-request-id", request_id.encode("ascii")))
        await send({"type": "http.response.start", "status": 413, "headers": headers})
        await send({"type": "http.response.body", "body": body})
