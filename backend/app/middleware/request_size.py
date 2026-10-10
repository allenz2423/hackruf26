"""Enforce route-specific request limits without buffering uploaded files in RAM."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]], Awaitable[None]]
CLASS_ID_UPLOAD_PATH = re.compile(
    r"^/api/classes/[0-9a-fA-F-]{36}/(?:uploads|assessments)$"
)


class _RequestTooLarge(Exception):
    pass


class RequestSizeLimitMiddleware:
    """Limit capability JSON, class-library JSON, and multipart upload requests."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        capability_max_bytes: int,
        library_json_max_bytes: int,
        upload_max_bytes: int,
    ) -> None:
        self.app = app
        self.capability_max_bytes = capability_max_bytes
        self.library_json_max_bytes = library_json_max_bytes
        self.upload_max_bytes = upload_max_bytes

    def _limit_for(self, scope: dict[str, Any]) -> int | None:
        if scope["type"] != "http":
            return None
        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        if path in {"/api/live-token", "/api/tools/calculate"}:
            return self.capability_max_bytes
        if method == "POST" and CLASS_ID_UPLOAD_PATH.fullmatch(path):
            return self.upload_max_bytes + 2 * 1024 * 1024
        if method in {"POST", "PATCH", "PUT", "DELETE"} and path.startswith(
            ("/api/classes", "/api/uploads", "/api/study-materials")
        ):
            return self.library_json_max_bytes
        return None

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        limit = self._limit_for(scope)
        if limit is None:
            await self.app(scope, receive, send)
            return

        content_length = next(
            (value for key, value in scope.get("headers", []) if key.lower() == b"content-length"),
            None,
        )
        if content_length is not None:
            try:
                if int(content_length) > limit:
                    await self._reject(send, scope)
                    return
            except ValueError:
                await self._reject(send, scope)
                return

        body_size = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal body_size
            message = await receive()
            if message["type"] == "http.request":
                body_size += len(message.get("body", b""))
                if body_size > limit:
                    raise _RequestTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestTooLarge:
            await self._reject(send, scope)

    @staticmethod
    async def _reject(
        send: Callable[..., Awaitable[Any]],
        scope: dict[str, Any],
    ) -> None:
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
