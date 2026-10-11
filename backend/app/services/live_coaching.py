"""Bounded, in-memory turn coordination for proactive Live coaching."""

from __future__ import annotations

import asyncio
import hashlib
import io
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_FRAME_AGE_SECONDS = 10.0
SCREEN_REVIEW_INTERVAL_SECONDS = 5.0
SCREEN_REVIEW_STABLE_SECONDS = 1.5
SCREEN_REVIEW_MAX_AGE_SECONDS = 2.5
COACHING_INPUT_PAUSE_SECONDS = 2.0
COACHING_FEEDBACK_PAUSE_SECONDS = 3.0
MAX_PENDING_TOOLS = 4
MAX_IMAGE_PIXELS = 4_194_304


class ScreenReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    review_id: str = Field(pattern=r"^[a-f0-9]{32}$", max_length=32, strict=True)
    decision: Literal["mistake", "no_issue", "incomplete", "uncertain"]


class AnnotationRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    x: float = Field(ge=0, le=1, strict=True, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, strict=True, allow_inf_nan=False)
    width: float = Field(gt=0, le=1, strict=True, allow_inf_nan=False)
    height: float = Field(gt=0, le=1, strict=True, allow_inf_nan=False)

    @model_validator(mode="after")
    def within_frame(self) -> AnnotationRegion:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("Annotation exceeds frame bounds.")
        return self


class AnnotationItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["pointer", "rectangle", "underline", "note"]
    region: AnnotationRegion
    label: str = Field(max_length=80, strict=True)


class AnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    frame_id: str | None = Field(default=None, min_length=1, max_length=36, strict=True)
    items: list[AnnotationItem] = Field(min_length=1, max_length=5)


@dataclass(frozen=True, slots=True)
class AnnotationFrame:
    """Immutable geometry reference; does not retain an additional screenshot."""

    frame_id: str
    size: tuple[int, int] | None
    signature: bytes | None
    epoch: int
    captured_at: float

    def is_current(self, frames: FrameState) -> bool:
        return bool(
            frames.is_current()
            and self.epoch == frames.epoch
            and self.size == frames.size
            and time.monotonic() - self.captured_at <= MAX_FRAME_AGE_SECONDS
            and not major_screen_change(self.signature, frames.signature)
        )


def inspect_frame(data: bytes) -> tuple[tuple[int, int], bytes]:
    """Validate a bounded JPEG and retain only a tiny luminance signature.

    This detects obvious viewport changes, never mathematical correctness.
    The full decoded image is released before returning.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (
                image.format != "JPEG"
                or image.width > 4096
                or image.height > 4096
                or image.width * image.height > MAX_IMAGE_PIXELS
            ):
                raise ValueError("Image exceeds Live frame bounds.")
            size = image.size
            image.draft("L", (128, 72))
            with image.convert("L") as grayscale:
                with grayscale.resize((32, 18)) as thumbnail:
                    signature = thumbnail.tobytes()
        return size, signature
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("Invalid Live image.") from None


def major_screen_change(before: bytes | None, after: bytes | None) -> bool:
    """Ignore small local changes; conservatively identify broad visual changes."""
    if not before or not after or len(before) != len(after):
        return False
    differences = [abs(a - b) for a, b in zip(before, after, strict=True)]
    return (
        sum(value > 40 for value in differences) > len(differences) * 0.45
        and sum(differences) > len(differences) * 30
    )


@dataclass(slots=True)
class FrameState:
    latest_frame_id: str | None = None
    last_frame_at: float = 0.0
    first_frame_at: float = 0.0
    latest_image: bytes | None = None
    latest_digest: bytes | None = None
    last_change_at: float = 0.0
    size: tuple[int, int] | None = None
    signature: bytes | None = None
    epoch: int = 0
    paused: bool = False

    def is_current(self) -> bool:
        return bool(
            self.latest_frame_id
            and not self.paused
            and time.monotonic() - self.last_frame_at <= MAX_FRAME_AGE_SECONDS
        )

    def accept(self, frame_id: str, data: bytes, size: tuple[int, int], signature: bytes) -> None:
        now = time.monotonic()
        if self.latest_frame_id is None:
            self.first_frame_at = now
        digest = hashlib.sha256(data).digest()
        if digest != self.latest_digest:
            self.last_change_at = now
        self.latest_frame_id = frame_id
        self.latest_image = data
        self.latest_digest = digest
        self.last_frame_at = now
        self.size = size
        self.signature = signature
        self.paused = False

    def clear(self) -> None:
        self.latest_frame_id = None
        self.latest_image = None
        self.latest_digest = None
        self.signature = None
        self.size = None
        self.last_frame_at = self.first_frame_at = self.last_change_at = 0.0
        self.epoch += 1


@dataclass(slots=True)
class ScreenReview:
    frame_id: str
    digest: bytes
    signature: bytes | None
    size: tuple[int, int] | None
    epoch: int
    captured_at: float
    started_at: float
    invalidated: bool = False
    output_sent: bool = False
    review_id: str = field(default_factory=lambda: secrets.token_hex(16))
    decision: str | None = None


@dataclass(slots=True)
class ToolJob:
    generation: int
    task: asyncio.Task | None = None


@dataclass(slots=True)
class LiveTurnState:
    """Generation and tools are tracked even in ordinary conversation mode."""

    model_busy: bool = False
    student_speaking: bool = False
    last_user_activity_at: float = 0.0
    last_model_activity_at: float = 0.0
    last_review_at: float = 0.0
    last_transcription_at: float = 0.0
    generation: int = 0
    provider_idle: bool | None = None
    interaction_status: str | None = None
    turn_complete: bool = False
    awaiting_user_response: bool = False
    waiting_for_old_turn: bool = False
    discard_output: bool = False
    turn_output_sent: bool = False
    review: ScreenReview | None = None
    annotation_frame: AnnotationFrame | None = None
    voice_frame_pending: bool = False
    reviewed_digests: deque[bytes] = field(default_factory=lambda: deque(maxlen=16))
    tools: dict[str, ToolJob] = field(default_factory=dict)
    tool_failure: Exception | None = None
    silenced_generation: int | None = None

    def cancel_tools(self) -> None:
        for job in self.tools.values():
            if job.task is not None:
                job.task.cancel()

    def pin_annotation_frame(self, frames: FrameState) -> None:
        self.annotation_frame = (
            AnnotationFrame(
                frame_id=frames.latest_frame_id,
                size=frames.size,
                signature=frames.signature,
                epoch=frames.epoch,
                captured_at=frames.last_frame_at,
            )
            if frames.is_current() and frames.latest_frame_id is not None
            else None
        )

    def user_turn(self, frames: FrameState | None = None, *, voice: bool = False) -> None:
        # The old interruption/completion boundary must not release the gate
        # while the new student question is still waiting for its response.
        self.waiting_for_old_turn |= self.model_busy and not self.awaiting_user_response
        self.invalidate_review()
        self.generation += 1
        self.cancel_tools()
        self.annotation_frame = None
        self.voice_frame_pending = voice
        if frames is not None:
            self.pin_annotation_frame(frames)
        self.model_busy = self.awaiting_user_response = True
        self.provider_idle = False
        self.interaction_status = None
        self.turn_complete = False
        self.discard_output = self.waiting_for_old_turn
        self.last_user_activity_at = time.monotonic()

    def invalidate_review(self) -> None:
        if self.review is not None:
            self.review.invalidated = True

    def pause_screen(self) -> None:
        self.invalidate_review()
        self.generation += 1
        self.cancel_tools()
        self.annotation_frame = None
        self.voice_frame_pending = False
        self.awaiting_user_response = False
        self.waiting_for_old_turn = False
        self.discard_output = self.model_busy

    def provider_interrupted(self, frames: FrameState | None = None) -> None:
        if not self.awaiting_user_response:
            self.user_turn(frames, voice=True)
        self.invalidate_review()
        self.waiting_for_old_turn = True
        self.discard_output = True

    def observe_content(self, *, has_output: bool, complete: bool, status: str | None) -> None:
        if self.waiting_for_old_turn:
            if complete:
                self.waiting_for_old_turn = False
                self.discard_output = False
                self.review = None
                self.turn_output_sent = False
                # Completion belonged to the interrupted response, not the
                # new question. Keep waiting for the new response.
                self.turn_complete = False
            return
        if has_output:
            self.voice_frame_pending = False
            self.awaiting_user_response = False
            self.model_busy = True
            self.turn_complete = False
            self.provider_idle = False
        if status == "IN_PROGRESS":
            self.interaction_status = status
            self.model_busy = True
            self.provider_idle = False
        elif status in {"IDLE", "REQUIRES_ACTION"}:
            self.interaction_status = status
            self.provider_idle = True
        if complete:
            self.turn_complete = True
            self.awaiting_user_response = False
            if status not in {"IN_PROGRESS", "IDLE", "REQUIRES_ACTION"}:
                self.provider_idle = None

    def finish_if_idle(self) -> None:
        if (
            self.turn_complete
            and self.provider_idle is not False
            and self.interaction_status != "IN_PROGRESS"
            and not self.tools
            and not self.awaiting_user_response
            and not self.waiting_for_old_turn
        ):
            self.model_busy = False
            if self.turn_output_sent:
                self.last_model_activity_at = time.monotonic()
            if self.review is not None and not self.review.invalidated:
                self.reviewed_digests.append(self.review.digest)
            self.review = None
            self.annotation_frame = None
            self.voice_frame_pending = False
            self.discard_output = False
            self.turn_output_sent = False

    def review_is_stale(self, frames: FrameState) -> bool:
        if self.review is not None:
            self.review.invalidated |= (
                not frames.is_current()
                or self.review.epoch != frames.epoch
                or (self.review.size is not None and self.review.size != frames.size)
                or major_screen_change(self.review.signature, frames.signature)
            )
            return self.review.invalidated
        return False

    def annotation_is_current(self, frames: FrameState) -> bool:
        return bool(
            self.review is not None
            and not self.review_is_stale(frames)
            and time.monotonic() - self.review.captured_at <= MAX_FRAME_AGE_SECONDS
        )

    def can_review(self, frames: FrameState, now: float) -> bool:
        settled = now - frames.last_change_at >= SCREEN_REVIEW_STABLE_SECONDS
        deadline = now - max(frames.first_frame_at, self.last_review_at)
        return bool(
            not self.model_busy
            and not self.tools
            and self.review is None
            and not self.student_speaking
            and not self.awaiting_user_response
            and frames.is_current()
            and frames.latest_image is not None
            and frames.latest_digest is not None
            and now - frames.last_frame_at <= SCREEN_REVIEW_MAX_AGE_SECONDS
            and (settled or deadline >= SCREEN_REVIEW_INTERVAL_SECONDS)
            and now - self.last_user_activity_at >= COACHING_INPUT_PAUSE_SECONDS
            and now - self.last_model_activity_at >= COACHING_FEEDBACK_PAUSE_SECONDS
            and now - self.last_review_at >= SCREEN_REVIEW_INTERVAL_SECONDS
            and frames.latest_digest not in self.reviewed_digests
        )

    def start_review(self, frames: FrameState, now: float) -> None:
        assert frames.latest_frame_id is not None and frames.latest_digest is not None
        self.generation += 1
        self.pin_annotation_frame(frames)
        self.voice_frame_pending = False
        self.model_busy = True
        self.provider_idle = False
        self.interaction_status = None
        self.turn_complete = self.discard_output = self.turn_output_sent = False
        self.last_review_at = now
        self.review = ScreenReview(
            frame_id=frames.latest_frame_id,
            digest=frames.latest_digest,
            signature=frames.signature,
            size=frames.size,
            epoch=frames.epoch,
            captured_at=frames.last_frame_at,
            started_at=now,
        )

    def allows_speech(self) -> bool:
        return bool(
            self.silenced_generation != self.generation
            and (self.review is None or self.review.decision == "mistake")
        )

    def clear(self) -> None:
        self.generation += 1
        self.cancel_tools()
        self.tools.clear()
        self.tool_failure = None
        self.review = None
        self.annotation_frame = None
        self.voice_frame_pending = False
        self.reviewed_digests.clear()


def interruption_event() -> dict:
    """An interruption never carries audio that could restart cleared playback."""
    return {
        "type": "assistant",
        "interrupted": True,
        "turn_complete": False,
        "text": None,
        "audio": [],
    }
