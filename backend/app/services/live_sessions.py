"""In-memory tickets and server-owned Gemini Live WebSocket sessions."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import WebSocket
from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from starlette.websockets import WebSocketDisconnect

from app.api.schemas import CalculatorRequest, LeanProofRequest
from app.config import Settings
from app.security.rate_limit import InMemoryRateLimiter
from app.services.calculator import CalculatorInputError, evaluate_expression
from app.services.lean_proofs import (
    LeanProofBusy,
    LeanProofInputError,
    LeanProofService,
    LeanRuntimeUnavailable,
)
from app.services.live_config import live_connect_config

logger = logging.getLogger("screen_tutor.live")
TICKET_TTL_SECONDS = 45
MAX_CLIENT_MESSAGE_BYTES = 3_200_000
MAX_FRAME_BYTES = 2_200_000
FRAME_INTERVAL_SECONDS = 1.0
ANNOTATION_REPLY_TIMEOUT_SECONDS = 15


@dataclass(frozen=True, slots=True)
class LiveSessionTicket:
    class_context: str | None
    expires_at: datetime


class LiveSessionTicketStore:
    """Keep short-lived, one-use browser session tickets in process memory."""

    def __init__(self) -> None:
        self._tickets: dict[str, LiveSessionTicket] = {}
        self._lock = asyncio.Lock()

    async def issue(self, class_context: str | None) -> tuple[str, datetime]:
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=TICKET_TTL_SECONDS)
        ticket = secrets.token_urlsafe(32)
        async with self._lock:
            self._remove_expired(now)
            self._tickets[ticket] = LiveSessionTicket(class_context, expires_at)
        return ticket, expires_at

    async def consume(self, ticket: str) -> LiveSessionTicket | None:
        now = datetime.now(UTC)
        async with self._lock:
            value = self._tickets.pop(ticket, None)
            self._remove_expired(now)
        if value is None or value.expires_at <= now:
            return None
        return value

    def _remove_expired(self, now: datetime) -> None:
        expired = [key for key, value in self._tickets.items() if value.expires_at <= now]
        for key in expired:
            self._tickets.pop(key, None)


class _ClientEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _TextEvent(_ClientEvent):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=5_000, strict=True)


class _AudioEvent(_ClientEvent):
    type: Literal["audio"]
    data: str = Field(min_length=1, max_length=32_000, strict=True)


class _AudioEndEvent(_ClientEvent):
    type: Literal["audio_end"]


class _FrameEvent(_ClientEvent):
    type: Literal["frame"]
    frame_id: UUID
    data: str = Field(min_length=1, max_length=3_000_000, strict=True)


class _AnnotationResultEvent(_ClientEvent):
    type: Literal["annotation_result"]
    call_id: str = Field(min_length=1, max_length=256, strict=True)
    status: str = Field(pattern="^(rendered|rejected)$")
    accepted: int = Field(ge=0, le=5)
    errors: list[str] = Field(default_factory=list, max_length=5)


_EVENT_ADAPTER = TypeAdapter(
    _TextEvent | _AudioEvent | _AudioEndEvent | _FrameEvent | _AnnotationResultEvent
)


@dataclass(slots=True)
class _FrameState:
    latest_frame_id: str | None = None
    last_frame_at: float = 0.0


class GeminiLiveSessionService:
    """Bridge validated browser media to one fixed, server-configured Live session."""

    def __init__(
        self,
        client: genai.Client,
        settings: Settings,
        *,
        calculator_limiter: InMemoryRateLimiter,
        lean_limiter: InMemoryRateLimiter,
        lean_service: LeanProofService | None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._calculator_limiter = calculator_limiter
        self._lean_limiter = lean_limiter
        self._lean_service = lean_service

    async def run(self, websocket: WebSocket, class_context: str | None) -> None:
        request_id = secrets.token_hex(12)
        send_lock = asyncio.Lock()
        upstream_send_lock = asyncio.Lock()
        pending_annotations: dict[str, asyncio.Future[dict[str, Any]]] = {}
        frames = _FrameState()
        peer = websocket.client.host if websocket.client else "unknown"

        async def send_client(payload: dict[str, Any]) -> None:
            async with send_lock:
                await websocket.send_json(payload)

        try:
            config = live_connect_config(
                class_context,
                include_lean_tool=self._settings.lean_tool_enabled,
            )
            async with self._client.aio.live.connect(
                model=self._settings.gemini_model,
                config=config,
            ) as session:
                await send_client({"type": "ready"})
                input_task = asyncio.create_task(
                    self._read_client(
                        websocket,
                        session,
                        frames,
                        pending_annotations,
                        upstream_send_lock,
                        peer,
                        send_client,
                    )
                )
                output_task = asyncio.create_task(
                    self._read_upstream(
                        session,
                        frames,
                        pending_annotations,
                        upstream_send_lock,
                        peer,
                        send_client,
                    )
                )
                try:
                    done, _ = await asyncio.wait(
                        (input_task, output_task),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        task.result()
                    if output_task in done:
                        await send_client(
                            {
                                "type": "error",
                                "code": "session_ended",
                                "message": (
                                    "The tutoring session ended. Start a new session to continue."
                                ),
                            }
                        )
                        await websocket.close(code=1011)
                finally:
                    tasks = (input_task, output_task)
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except WebSocketDisconnect:
            return
        except Exception as exc:  # noqa: BLE001 - upstream content and credentials stay private
            category = _classify_live_failure(exc)
            status = getattr(exc, "status_code", None)
            logger.error(
                "live_session_failed request_id=%s category=%s status=%s exception_type=%s",
                request_id,
                category,
                status if isinstance(status, int) else "unknown",
                type(exc).__name__,
            )
            try:
                await send_client(
                    {
                        "type": "error",
                        "code": f"upstream_{category}",
                        "message": "The tutoring service could not complete this session.",
                    }
                )
                await websocket.close(code=1011)
            except Exception:
                pass
        finally:
            for future in pending_annotations.values():
                if not future.done():
                    future.cancel()

    async def _read_client(
        self,
        websocket: WebSocket,
        session: Any,
        frames: _FrameState,
        pending_annotations: dict[str, asyncio.Future[dict[str, Any]]],
        upstream_send_lock: asyncio.Lock,
        peer: str,
        send_client: Any,
    ) -> None:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                return
            if len(raw.encode("utf-8")) > MAX_CLIENT_MESSAGE_BYTES:
                await send_client(
                    {
                        "type": "error",
                        "code": "message_too_large",
                        "message": "The message was too large.",
                    }
                )
                await websocket.close(code=1009)
                return
            try:
                event = _EVENT_ADAPTER.validate_python(json.loads(raw))
            except (json.JSONDecodeError, ValidationError):
                await send_client(
                    {
                        "type": "error",
                        "code": "invalid_message",
                        "message": "The session message was invalid.",
                    }
                )
                await websocket.close(code=1003)
                return

            if isinstance(event, _TextEvent):
                async with upstream_send_lock:
                    await session.send_client_content(
                        turns=[
                            types.Content(
                                role="user",
                                parts=[types.Part(text=event.text)],
                            )
                        ],
                        turn_complete=True,
                    )
            elif isinstance(event, _AudioEvent):
                try:
                    data = _decode_base64(event.data, max_bytes=24_000)
                except ValueError:
                    await send_client(
                        {
                            "type": "error",
                            "code": "invalid_audio",
                            "message": "The audio chunk was invalid.",
                        }
                    )
                    await websocket.close(code=1003)
                    return
                async with upstream_send_lock:
                    await session.send_realtime_input(
                        audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000")
                    )
            elif isinstance(event, _AudioEndEvent):
                async with upstream_send_lock:
                    await session.send_realtime_input(audio_stream_end=True)
            elif isinstance(event, _FrameEvent):
                now = time.monotonic()
                if now - frames.last_frame_at < FRAME_INTERVAL_SECONDS:
                    continue
                try:
                    data = _decode_base64(event.data, max_bytes=MAX_FRAME_BYTES)
                except ValueError:
                    await send_client(
                        {
                            "type": "error",
                            "code": "invalid_frame",
                            "message": "The screen frame was invalid.",
                        }
                    )
                    await websocket.close(code=1003)
                    return
                if not data.startswith(b"\xff\xd8"):
                    await send_client(
                        {
                            "type": "error",
                            "code": "invalid_frame",
                            "message": "The screen frame was invalid.",
                        }
                    )
                    await websocket.close(code=1003)
                    return
                async with upstream_send_lock:
                    await session.send_realtime_input(
                        video=types.Blob(data=data, mime_type="image/jpeg")
                    )
                frames.latest_frame_id = str(event.frame_id)
                frames.last_frame_at = now
            elif isinstance(event, _AnnotationResultEvent):
                future = pending_annotations.get(event.call_id)
                if future is not None and not future.done():
                    future.set_result(
                        {
                            "status": event.status,
                            "accepted": event.accepted,
                            "errors": event.errors,
                        }
                    )

    async def _read_upstream(
        self,
        session: Any,
        frames: _FrameState,
        pending_annotations: dict[str, asyncio.Future[dict[str, Any]]],
        upstream_send_lock: asyncio.Lock,
        peer: str,
        send_client: Any,
    ) -> None:
        # receive() ends after one model turn; keep listening until disconnection.
        while True:
            received_message = False
            async for message in session.receive():
                received_message = True
                content = message.server_content
                if content is not None:
                    event: dict[str, Any] = {
                        "type": "assistant",
                        "interrupted": bool(content.interrupted),
                        "turn_complete": bool(content.turn_complete),
                        "text": getattr(content.output_transcription, "text", None),
                        "audio": [],
                    }
                    for part in getattr(content.model_turn, "parts", None) or []:
                        inline_data = part.inline_data
                        mime_type = inline_data.mime_type or "" if inline_data is not None else ""
                        if inline_data is not None and mime_type.startswith("audio/"):
                            event["audio"].append(
                                {
                                    "data": _encode_audio(inline_data.data),
                                    "mime_type": mime_type,
                                }
                            )
                        if part.text and not event["text"]:
                            event["text"] = part.text
                    if (
                        event["interrupted"]
                        or event["turn_complete"]
                        or event["text"]
                        or event["audio"]
                    ):
                        await send_client(event)

                tool_call = message.tool_call
                calls = list(getattr(tool_call, "function_calls", None) or [])
                known_ids = {call.id for call in calls if call.id}
                model_turn = content.model_turn if content is not None else None
                for part in getattr(model_turn, "parts", None) or []:
                    function_call = part.function_call
                    if function_call is not None and function_call.id not in known_ids:
                        calls.append(function_call)
                        if function_call.id:
                            known_ids.add(function_call.id)
                if calls:
                    responses = []
                    for call in calls:
                        response = await self._handle_function_call(
                            call,
                            session,
                            frames,
                            pending_annotations,
                            peer,
                            send_client,
                        )
                        responses.append(response)
                    async with upstream_send_lock:
                        await session.send_tool_response(function_responses=responses)
            if not received_message:
                return

    async def _handle_function_call(
        self,
        call: Any,
        session: Any,
        frames: _FrameState,
        pending_annotations: dict[str, asyncio.Future[dict[str, Any]]],
        peer: str,
        send_client: Any,
    ) -> types.FunctionResponse:
        name = str(call.name or "")
        call_id = str(call.id or "")
        args = call.args if isinstance(call.args, dict) else {}
        result: dict[str, Any]

        if name == "add_annotations":
            frame_id = frames.latest_frame_id
            if not frame_id:
                result = {
                    "error": {"code": "no_frame", "message": "No recent screen frame is available."}
                }
            elif not call_id:
                result = {
                    "error": {
                        "code": "invalid_call",
                        "message": "The annotation request could not be tracked.",
                    }
                }
            else:
                future = asyncio.get_running_loop().create_future()
                pending_annotations[call_id] = future
                try:
                    await send_client(
                        {
                            "type": "annotation_request",
                            "call_id": call_id,
                            "payload": {"frame_id": frame_id, "items": args.get("items", [])},
                        }
                    )
                    result = await asyncio.wait_for(
                        future,
                        timeout=ANNOTATION_REPLY_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    result = {
                        "error": {
                            "code": "annotation_timeout",
                            "message": "The annotation could not be displayed.",
                        }
                    }
                finally:
                    pending_annotations.pop(call_id, None)
        elif name == "calculate_expression":
            retry_after = await self._calculator_limiter.check(peer)
            if retry_after is not None:
                result = {
                    "error": {
                        "code": "rate_limited",
                        "message": "Please wait before another calculation.",
                    }
                }
            else:
                try:
                    expression = CalculatorRequest.model_validate(args).expression
                    value = await asyncio.to_thread(evaluate_expression, expression)
                    result = {"result": value}
                except (CalculatorInputError, ValidationError):
                    result = {
                        "error": {
                            "code": "invalid_expression",
                            "message": "The expression is outside calculator limits.",
                        }
                    }
        elif name == "verify_lean_proof" and self._settings.lean_tool_enabled:
            retry_after = await self._lean_limiter.check(peer)
            if retry_after is not None:
                result = {
                    "error": {
                        "code": "rate_limited",
                        "message": "Please wait before another proof check.",
                    }
                }
            elif self._lean_service is None:
                result = {
                    "error": {
                        "code": "lean_unavailable",
                        "message": "Proof checking is unavailable.",
                    }
                }
            else:
                try:
                    proof_request = LeanProofRequest.model_validate(args)
                    checked = await self._lean_service.verify(
                        proof_request.proposition,
                        proof_request.proof,
                    )
                    result = {"status": checked.status, "diagnostics": checked.diagnostics}
                except (LeanProofInputError, ValidationError):
                    result = {
                        "error": {
                            "code": "invalid_proof",
                            "message": "The proof input is outside supported limits.",
                        }
                    }
                except LeanProofBusy:
                    result = {
                        "error": {"code": "lean_busy", "message": "The proof checker is busy."}
                    }
                except LeanRuntimeUnavailable:
                    result = {
                        "error": {
                            "code": "lean_unavailable",
                            "message": "Proof checking is unavailable.",
                        }
                    }
        else:
            result = {
                "error": {"code": "unsupported_tool", "message": "This action is not supported."}
            }

        return types.FunctionResponse(id=call_id, name=name, response=result)


def _decode_base64(value: str, *, max_bytes: int) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Invalid base64 media.") from None
    if not decoded or len(decoded) > max_bytes:
        raise ValueError("Media is empty or exceeds the session limit.")
    return decoded


def _encode_audio(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, str):
        return value
    return base64.b64encode(bytes(value)).decode("ascii")


def _classify_live_failure(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    details = str(getattr(exc, "message", "") or exc).lower()
    if status == 429 or "quota" in details or "billing" in details:
        return "quota_or_billing"
    if status in {401, 403} or "api key" in details or "permission" in details:
        return "credentials_or_access"
    if status == 404 or "not found" in details:
        return "model_unavailable"
    if status == 400:
        return "configuration_rejected"
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "connection_failed"
