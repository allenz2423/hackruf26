"""In-memory tickets and server-owned Gemini Live WebSocket sessions."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import secrets
import struct
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import WebSocket
from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from starlette.websockets import WebSocketDisconnect

from app.api.schemas import CalculatorRequest, LeanProofRequest
from app.config import Settings
from app.live_modes import TutorLanguage, TutorMode
from app.security.rate_limit import InMemoryRateLimiter
from app.services.calculator import CalculatorInputError, evaluate_expression
from app.services.lean_proofs import (
    LeanProofBusy,
    LeanProofInputError,
    LeanProofService,
    LeanRuntimeUnavailable,
)
from app.services.live_coaching import (
    COACHING_INPUT_PAUSE_SECONDS,
    MAX_PENDING_TOOLS,
    AnnotationRequest,
    ScreenReviewResult,
    ToolJob,
    inspect_frame,
    interruption_event,
)
from app.services.live_coaching import (
    FrameState as _FrameState,
)
from app.services.live_coaching import (
    LiveTurnState as _CoachingState,
)
from app.services.live_config import (
    AUTOMATIC_SCREEN_REVIEW,
    CURRENT_SCREEN_CONTEXT,
    NO_CURRENT_SCREEN_CONTEXT,
    SCREEN_PAUSED_CONTEXT,
    SCREEN_RESUMED_CONTEXT,
    annotation_items_for_client,
    live_connect_config,
)
from app.services.tutor_memory import MemoryTarget, SessionTranscript, TutorMemoryService

logger = logging.getLogger("screen_tutor.live")
TICKET_TTL_SECONDS = 45
MAX_CLIENT_MESSAGE_BYTES = 3_200_000
MAX_FRAME_BYTES = 2_200_000
FRAME_INTERVAL_SECONDS = 1.0
ANNOTATION_REPLY_TIMEOUT_SECONDS = 15
PCM_ACTIVITY_THRESHOLD = 600
SESSION_WATCH_INTERVAL_SECONDS = 1.0


class _ScreenReviewTimeout(TimeoutError):
    """A proactive review exceeded its total budget, including tool work."""


@dataclass(frozen=True, slots=True)
class LiveSessionTicket:
    class_context: str | None
    expires_at: datetime
    mode: TutorMode = TutorMode.CONVERSATION
    language: TutorLanguage = TutorLanguage.AUTO
    memory_target: MemoryTarget | None = None


class LiveSessionTicketStore:
    """Keep short-lived, one-use browser session tickets in process memory."""

    def __init__(self) -> None:
        self._tickets: dict[str, LiveSessionTicket] = {}
        self._lock = asyncio.Lock()

    async def issue(
        self,
        class_context: str | None,
        *,
        mode: TutorMode = TutorMode.CONVERSATION,
        language: TutorLanguage = TutorLanguage.AUTO,
        memory_target: MemoryTarget | None = None,
    ) -> tuple[str, datetime]:
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=TICKET_TTL_SECONDS)
        ticket = secrets.token_urlsafe(32)
        async with self._lock:
            self._remove_expired(now)
            self._tickets[ticket] = LiveSessionTicket(class_context, expires_at, mode, language, memory_target)
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


class _ScreenPausedEvent(_ClientEvent):
    type: Literal["screen_paused"]


class _EndSessionEvent(_ClientEvent):
    type: Literal["end_session"]


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
    _TextEvent
    | _AudioEvent
    | _AudioEndEvent
    | _ScreenPausedEvent
    | _FrameEvent
    | _AnnotationResultEvent
    | _EndSessionEvent
)


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
        memory_service: TutorMemoryService | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._calculator_limiter = calculator_limiter
        self._lean_limiter = lean_limiter
        self._lean_service = lean_service
        self._memory_service = memory_service

    async def run(
        self,
        websocket: WebSocket,
        class_context: str | None,
        *,
        mode: TutorMode = TutorMode.CONVERSATION,
        language: TutorLanguage = TutorLanguage.AUTO,
        memory_target: MemoryTarget | None = None,
    ) -> None:
        request_id = secrets.token_hex(12)
        send_lock = asyncio.Lock()
        upstream_send_lock = asyncio.Lock()
        pending_annotations: dict[str, asyncio.Future[dict[str, Any]]] = {}
        frames = _FrameState()
        # Ordinary conversations also need responsive tool cancellation. Only
        # check_steps sessions enable the automatic screen-review scheduler.
        coaching = _CoachingState()
        peer = websocket.client.host if websocket.client else "unknown"
        transcript = SessionTranscript(enabled=False)

        async def send_client(payload: dict[str, Any], *, generation: int | None = None) -> None:
            async with send_lock:
                if generation is not None and generation != coaching.generation:
                    return
                await websocket.send_json(payload)

        try:
            memory_context = None
            memory_count = 0
            if memory_target is not None and self._memory_service is not None:
                enabled, memory_context, memory_count = await self._memory_service.context(memory_target)
                if not enabled:
                    memory_target = None
            else:
                memory_target = None
            transcript.enabled = memory_target is not None
            config = live_connect_config(
                class_context,
                mode=mode,
                language=language,
                include_lean_tool=self._settings.lean_tool_enabled,
                memory_context=memory_context,
            )
            async with self._client.aio.live.connect(
                model=self._settings.gemini_model,
                config=config,
            ) as session:
                await send_client({
                    "type": "ready", "memory": {"enabled": transcript.enabled, "loaded_count": memory_count},
                })
                input_task = asyncio.create_task(
                    self._read_client(
                        websocket,
                        session,
                        frames,
                        pending_annotations,
                        upstream_send_lock,
                        peer,
                        send_client,
                        coaching=coaching,
                        transcript=transcript,
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
                        coaching=coaching,
                        transcript=transcript,
                        forward_student_transcript=mode == TutorMode.QUIZ_REASONING,
                    )
                )
                tasks = [
                    input_task,
                    output_task,
                    asyncio.create_task(
                        self._review_screen(
                            session,
                            frames,
                            coaching,
                            upstream_send_lock,
                            enable_reviews=mode == TutorMode.CHECK_STEPS,
                        )
                    ),
                ]
                try:
                    done, _ = await asyncio.wait(
                        tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        task.result()
                    if output_task in done and not transcript.end_requested:
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
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
            if transcript.end_requested:
                # Media and provider tasks are released before recap generation.
                frames.clear()
                coaching.clear()
                status = "disabled"
                if memory_target is not None and self._memory_service is not None:
                    status = await self._memory_service.finish(memory_target, request_id, transcript)
                transcript.clear()
                try:
                    await send_client({"type": "session_ended", "memory_status": status})
                    await websocket.close(code=1000)
                except (WebSocketDisconnect, RuntimeError):
                    pass
        except WebSocketDisconnect:
            return
        except Exception as exc:  # noqa: BLE001 - upstream content and credentials stay private
            category = _classify_live_failure(exc)
            status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
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
                        "code": (
                            "screen_review_timeout"
                            if isinstance(exc, _ScreenReviewTimeout)
                            else f"upstream_{category}"
                        ),
                        "message": (
                            "The automatic screen check timed out. Start a new tutor session."
                            if isinstance(exc, _ScreenReviewTimeout)
                            else "The tutoring service could not complete this session."
                        ),
                    }
                )
                await websocket.close(code=1011)
            except Exception:
                pass
        finally:
            transcript.clear()
            frames.clear()
            coaching.clear()
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
        *,
        coaching: _CoachingState | None = None,
        transcript: SessionTranscript | None = None,
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

            if isinstance(event, _EndSessionEvent):
                if transcript is not None:
                    transcript.end_requested = True
                return
            if isinstance(event, _TextEvent):
                async with upstream_send_lock:
                    if coaching is not None:
                        coaching.user_turn(frames)
                    # Flush audio already buffered in the browser even when
                    # provider generation has finished before this question.
                    await send_client(interruption_event())
                    # Keep the latest image and the student's correction in one
                    # ordered update, without replaying any older screen frames.
                    parts = [types.Part(text=NO_CURRENT_SCREEN_CONTEXT)]
                    if frames.is_current() and frames.latest_image is not None:
                        parts = [
                            types.Part(text=f"{CURRENT_SCREEN_CONTEXT}\n{_frame_context(frames)}"),
                            types.Part(
                                inline_data=types.Blob(
                                    data=frames.latest_image, mime_type="image/jpeg"
                                )
                            ),
                        ]
                    parts.append(types.Part(text=event.text))
                    await session.send_client_content(
                        turns=[
                            types.Content(
                                role="user",
                                parts=parts,
                            )
                        ],
                        turn_complete=True,
                    )
                    if transcript is not None:
                        transcript.append("student", event.text + "\n")
                    del parts
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
                    if coaching is not None and _has_audio_activity(data):
                        # Silent microphone chunks must not disable screen-only
                        # checks. Provider VAD remains responsible for speech turns.
                        coaching.last_user_activity_at = time.monotonic()
                    await session.send_realtime_input(
                        audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000")
                    )
            elif isinstance(event, _AudioEndEvent):
                async with upstream_send_lock:
                    if coaching is not None:
                        coaching.student_speaking = False
                        coaching.last_user_activity_at = time.monotonic()
                    await session.send_realtime_input(audio_stream_end=True)
            elif isinstance(event, _ScreenPausedEvent):
                frames.clear()
                if frames.paused:
                    continue
                frames.paused = True
                async with upstream_send_lock:
                    if coaching is not None:
                        coaching.pause_screen()
                    await send_client(interruption_event())
                    await session.send_client_content(
                        turns=[
                            types.Content(
                                role="user", parts=[types.Part(text=SCREEN_PAUSED_CONTEXT)]
                            )
                        ],
                        turn_complete=False,
                    )
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
                try:
                    size, signature = await asyncio.to_thread(inspect_frame, data)
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
                async with upstream_send_lock:
                    if frames.paused:
                        await session.send_client_content(
                            turns=[
                                types.Content(
                                    role="user", parts=[types.Part(text=SCREEN_RESUMED_CONTEXT)]
                                )
                            ],
                            turn_complete=False,
                        )
                    was_invalid = bool(coaching and coaching.review and coaching.review.invalidated)
                    frames.accept(str(event.frame_id), data, size, signature)
                    if (
                        coaching is not None
                        and coaching.review_is_stale(frames)
                        and not was_invalid
                    ):
                        coaching.generation += 1
                        coaching.cancel_tools()
                        if coaching.review is not None and coaching.review.output_sent:
                            await send_client(interruption_event())
                            coaching.review.output_sent = False
                    await session.send_realtime_input(
                        video=types.Blob(data=data, mime_type="image/jpeg")
                    )
                    del data
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

    async def _review_screen(
        self,
        session: Any,
        frames: _FrameState,
        coaching: _CoachingState,
        upstream_send_lock: asyncio.Lock,
        *,
        enable_reviews: bool = True,
    ) -> None:
        while True:
            await asyncio.sleep(SESSION_WATCH_INTERVAL_SECONDS)
            async with upstream_send_lock:
                now = time.monotonic()
                if coaching.tool_failure is not None:
                    raise coaching.tool_failure
                if (
                    coaching.review is not None
                    and now - coaching.review.started_at
                    >= self._settings.live_review_timeout_seconds
                ):
                    raise _ScreenReviewTimeout
                if not enable_reviews or not coaching.can_review(frames, now):
                    continue
                coaching.start_review(frames, now)
                # Completing this server-owned turn triggers Live output without
                # a user question. Only the current bounded snapshot is included.
                await session.send_client_content(
                    turns=[
                        types.Content(
                            role="user",
                            parts=[
                                types.Part(
                                    text=(
                                        f"{AUTOMATIC_SCREEN_REVIEW}\n\n"
                                        f"review_id: {coaching.review.review_id}\n"
                                        f"{_frame_context(frames)}"
                                    )
                                ),
                                types.Part(
                                    inline_data=types.Blob(
                                        data=frames.latest_image, mime_type="image/jpeg"
                                    )
                                ),
                            ],
                        )
                    ],
                    turn_complete=True,
                )

    async def _read_upstream(
        self,
        session: Any,
        frames: _FrameState,
        pending_annotations: dict[str, asyncio.Future[dict[str, Any]]],
        upstream_send_lock: asyncio.Lock,
        peer: str,
        send_client: Any,
        *,
        coaching: _CoachingState | None = None,
        transcript: SessionTranscript | None = None,
        forward_student_transcript: bool = False,
    ) -> None:
        state = coaching if coaching is not None else _CoachingState()
        try:
            # receive() ends after one model turn. Tool work must not block
            # the next voice-activity, interruption, or cancellation message.
            while True:
                received_message = False
                async for message in session.receive():
                    received_message = True
                    voice_activity = getattr(message, "voice_activity", None)
                    if voice_activity is not None:
                        activity = voice_activity.voice_activity_type
                        if activity == types.VoiceActivityType.ACTIVITY_START:
                            state.student_speaking = True
                            state.user_turn(frames, voice=True)
                            # Generation may already be over while the browser
                            # is still playing buffered speech.
                            await send_client(interruption_event())
                        elif activity == types.VoiceActivityType.ACTIVITY_END:
                            state.student_speaking = False
                            state.last_user_activity_at = time.monotonic()
                            # Freeze the utterance's final view before tools or
                            # output start. Later streamed images cannot rebind it.
                            if (
                                state.voice_frame_pending
                                and state.awaiting_user_response
                                and not state.tools
                                and not state.turn_output_sent
                            ):
                                state.pin_annotation_frame(frames)
                            state.voice_frame_pending = False

                    cancellation = getattr(message, "tool_call_cancellation", None)
                    for call_id in getattr(cancellation, "ids", None) or []:
                        job = state.tools.get(call_id)
                        if job is not None and job.task is not None:
                            job.task.cancel()

                    content = message.server_content
                    transcription = getattr(content, "input_transcription", None)
                    if getattr(transcription, "text", None):
                        if forward_student_transcript:
                            await send_client({
                                "type": "student_transcript",
                                "transcript_id": str(uuid4()),
                                "text": str(transcription.text)[:12_000],
                            })
                        if transcript is not None:
                            transcript.append("student", transcription.text)
                        now = time.monotonic()
                        if (
                            not state.student_speaking
                            and now - state.last_transcription_at >= COACHING_INPUT_PAUSE_SECONDS
                        ):
                            state.user_turn(frames, voice=True)
                            await send_client(interruption_event())
                        state.last_transcription_at = now
                        state.last_user_activity_at = now
                    if content is not None and content.interrupted:
                        if frames.paused:
                            state.invalidate_review()
                        else:
                            state.provider_interrupted(frames)
                        await send_client(interruption_event())

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

                    valid = not state.discard_output and not state.review_is_stale(frames)
                    for call in calls:
                        # A stale visual review can still acknowledge its own
                        # bounded outcome silently. It cannot close a newer
                        # student response or authorize any visual action.
                        terminal_report = (
                            call.name == "report_screen_review"
                            and state.review is not None
                            and not state.awaiting_user_response
                            and not state.waiting_for_old_turn
                        )
                        if valid or terminal_report:
                            call_id = str(call.id or secrets.token_hex(12))
                            if call_id in state.tools:
                                continue
                            if len(state.tools) >= MAX_PENDING_TOOLS:
                                # Stop this session rather than growing an
                                # unbounded executor or blocking speech input.
                                raise RuntimeError("Live tool concurrency limit exceeded.")
                            state.model_busy = True
                            state.voice_frame_pending = False
                            state.awaiting_user_response = False
                            state.turn_complete = False
                            job = ToolJob(generation=state.generation)
                            state.tools[call_id] = job
                            job.task = asyncio.create_task(
                                self._execute_tool(
                                    call,
                                    call_id,
                                    job,
                                    session,
                                    frames,
                                    pending_annotations,
                                    upstream_send_lock,
                                    peer,
                                    send_client,
                                    state,
                                )
                            )

                    if content is not None:
                        event: dict[str, Any] = {
                            "type": "assistant",
                            "interrupted": False,
                            "turn_complete": bool(content.turn_complete),
                            "text": getattr(content.output_transcription, "text", None),
                            "audio": [],
                        }
                        for part in getattr(content.model_turn, "parts", None) or []:
                            if getattr(part, "thought", False):
                                continue
                            inline_data = part.inline_data
                            mime_type = (inline_data.mime_type or "") if inline_data else ""
                            if inline_data is not None and mime_type.startswith("audio/"):
                                event["audio"].append(
                                    {
                                        "data": _encode_audio(inline_data.data),
                                        "mime_type": mime_type,
                                    }
                                )
                            if part.text and not event["text"]:
                                event["text"] = part.text
                        has_output = bool(event["text"] or event["audio"])
                        if valid and not content.interrupted:
                            audible = state.allows_speech()
                            if has_output and audible:
                                state.turn_output_sent = True
                                if state.review is not None:
                                    state.review.output_sent = True
                            if not audible:
                                event["text"] = None
                                event["audio"] = []
                            if (has_output and audible) or event["turn_complete"]:
                                await send_client(event, generation=state.generation)
                                if transcript is not None and event["text"]:
                                    transcript.append("tutor", event["text"])
                        elif state.review is not None and state.review.output_sent:
                            await send_client(interruption_event())
                            state.review.output_sent = False
                        state.observe_content(
                            has_output=has_output,
                            complete=bool(content.turn_complete),
                            status=getattr(content, "interaction_status", None),
                        )
                        state.finish_if_idle()
                if not received_message:
                    return
        finally:
            tasks = [job.task for job in state.tools.values() if job.task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute_tool(
        self,
        call: Any,
        call_id: str,
        job: ToolJob,
        session: Any,
        frames: _FrameState,
        pending_annotations: dict[str, asyncio.Future[dict[str, Any]]],
        upstream_send_lock: asyncio.Lock,
        peer: str,
        send_client: Any,
        state: _CoachingState,
    ) -> None:
        try:
            response = await self._handle_function_call(
                call,
                session,
                frames,
                pending_annotations,
                peer,
                send_client,
                coaching=state,
            )
            async with upstream_send_lock:
                quiet_review = (
                    call.name == "report_screen_review"
                    and response.response.get("decision") in {"no_issue", "incomplete", "uncertain"}
                    and state.review is not None
                    and not state.awaiting_user_response
                    and not state.waiting_for_old_turn
                )
                if job.generation != state.generation or (
                    (state.discard_output or state.review_is_stale(frames)) and not quiet_review
                ):
                    return
                # A sent result may trigger more model output. Keep the gate
                # closed until the provider acknowledges its completed turn.
                state.turn_complete = False
                state.provider_idle = False
                state.model_busy = True
                if quiet_review:
                    response.scheduling = types.FunctionResponseScheduling.SILENT
                    response.will_continue = False
                await session.send_tool_response(function_responses=[response])
                if quiet_review:
                    # SILENT + will_continue=False explicitly terminates this
                    # nonblocking call without requesting further generation.
                    # Proactive abstention alone may emit no turnComplete.
                    # Finishing this control call cannot override a provider
                    # that explicitly reports background work in progress.
                    state.observe_content(has_output=False, complete=True, status=None)
                    await send_client(
                        {
                            "type": "assistant",
                            "interrupted": False,
                            "turn_complete": True,
                            "text": None,
                            "audio": [],
                        },
                        generation=job.generation,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # retrieved by the bounded session monitor
            state.tool_failure = exc
        finally:
            if state.tools.get(call_id) is job:
                state.tools.pop(call_id)
            state.finish_if_idle()

    async def _handle_function_call(
        self,
        call: Any,
        session: Any,
        frames: _FrameState,
        pending_annotations: dict[str, asyncio.Future[dict[str, Any]]],
        peer: str,
        send_client: Any,
        *,
        coaching: _CoachingState | None = None,
    ) -> types.FunctionResponse:
        name = str(call.name or "")
        call_id = str(call.id or "")
        args = call.args if isinstance(call.args, dict) else {}
        result: dict[str, Any]

        if name == "report_screen_review":
            try:
                outcome = ScreenReviewResult.model_validate(args)
            except ValidationError:
                result = {
                    "error": {
                        "code": "invalid_review_result",
                        "message": "Report a valid review ID and decision.",
                    }
                }
            else:
                if (
                    coaching is None
                    or coaching.review is None
                    or coaching.review.review_id != outcome.review_id
                    or coaching.awaiting_user_response
                    or coaching.waiting_for_old_turn
                ):
                    result = {
                        "error": {
                            "code": "stale_review",
                            "message": "This automatic review is no longer active.",
                        }
                    }
                elif coaching.review_is_stale(frames):
                    coaching.review.decision = "uncertain"
                    coaching.silenced_generation = coaching.generation
                    result = {
                        "accepted": False,
                        "decision": "uncertain",
                        "error": {
                            "code": "stale_review",
                            "message": "The reviewed screen changed. Finish this check silently.",
                        },
                    }
                else:
                    coaching.review.decision = outcome.decision
                    if outcome.decision != "mistake":
                        coaching.silenced_generation = coaching.generation
                    result = {"accepted": True, "decision": outcome.decision}
        elif name == "add_annotations":
            marks, mark_errors = annotation_items_for_client(args.get("items", []))
            reference = coaching.annotation_frame if coaching is not None else None
            try:
                request = AnnotationRequest.model_validate({**args, "items": marks})
            except ValidationError:
                request = None
            if request is None or mark_errors:
                result = {
                    "error": {
                        "code": "invalid_annotations",
                        "message": (
                            "Use 1 to 5 known marks with finite box_2d edges "
                            "[ymin, xmin, ymax, xmax] scaled 0 to 1000 "
                            "and labels up to 80 characters."
                        ),
                    }
                }
            elif (
                coaching is not None
                and coaching.review is not None
                and (
                    coaching.review.decision != "mistake"
                    or not coaching.annotation_is_current(frames)
                )
            ):
                result = {
                    "error": {
                        "code": "stale_review",
                        "message": (
                            "The reviewed frame is no longer suitable for a mark. "
                            "Give a verbal hint without marking remembered coordinates."
                        ),
                    }
                }
            elif reference is None or not reference.is_current(frames):
                result = {
                    "error": {
                        "code": "stale_frame",
                        "message": (
                            "No current pinned frame is available for this turn. "
                            "Give a verbal explanation without a mark."
                        ),
                    }
                }
            elif request.frame_id is not None and request.frame_id != reference.frame_id:
                result = {
                    "error": {
                        "code": "frame_mismatch",
                        "message": (
                            "The frame ID does not match this question/review. "
                            "Do not reuse old coordinates."
                        ),
                    }
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
                    payload = {
                        "type": "annotation_request",
                        "call_id": call_id,
                        "payload": {
                            "frame_id": reference.frame_id,
                            "items": [item.model_dump() for item in request.items],
                        },
                    }
                    if coaching is None:
                        await send_client(payload)
                    else:
                        await send_client(payload, generation=coaching.generation)
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


def _frame_context(frames: _FrameState) -> str:
    """Identify the image in this ordered snapshot update, without media logs."""
    return (
        f"frame_id: {frames.latest_frame_id}\n"
        f"Captured image dimensions: {frames.size}. "
        "For marks use box_2d [ymin, xmin, ymax, xmax], scaled 0 to 1000 "
        "relative to this exact image."
    )


def _has_audio_activity(data: bytes) -> bool:
    """Bounded energy guard; this is not speech recognition or a recording."""
    if len(data) < 2 or len(data) % 2:
        return False
    samples = len(data) // 2
    energy = sum(value * value for (value,) in struct.iter_unpack("<h", data))
    return energy > samples * PCM_ACTIVITY_THRESHOLD**2


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
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    details = str(getattr(exc, "message", "") or exc).lower()
    if status == 429 or "quota" in details or "billing" in details:
        return "quota_or_billing"
    if status in {401, 403} or "api key" in details or "permission" in details:
        return "credentials_or_access"
    if status == 404 or "not found" in details:
        return "model_unavailable"
    if status in {400, 1007}:
        return "configuration_rejected"
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "connection_failed"
