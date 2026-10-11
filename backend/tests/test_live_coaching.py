"""State, transport, cancellation, and image bounds for proactive coaching."""

from __future__ import annotations

import asyncio
import io
import time
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from google.genai import types
from PIL import Image, ImageDraw

from app.config import Settings
from app.live_modes import TutorMode
from app.security.rate_limit import InMemoryRateLimiter
from app.services.live_coaching import FrameState, LiveTurnState, ToolJob, inspect_frame
from app.services.live_config import AUTOMATIC_SCREEN_REVIEW, live_connect_config
from app.services.live_sessions import GeminiLiveSessionService, _ScreenReviewTimeout


def jpeg(*, cursor: bool = False, background: str = "white") -> bytes:
    with Image.new("RGB", (640, 360), background) as image:
        draw = ImageDraw.Draw(image)
        draw.text((30, 40), "2(x + 3) = 2x + 3", fill="black")
        if cursor:
            draw.line((300, 40, 300, 60), fill="black", width=2)
        output = io.BytesIO()
        image.save(output, "JPEG")
        return output.getvalue()


def frame(data: bytes | None = None) -> FrameState:
    data = data or jpeg()
    result = FrameState()
    result.accept(str(uuid4()), data, *inspect_frame(data))
    result.first_frame_at -= 6
    result.last_change_at -= 3
    return result


def service() -> GeminiLiveSessionService:
    return GeminiLiveSessionService(
        None,
        Settings(
            _env_file=None,
            file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
            lean_tool_enabled=False,
        ),
        calculator_limiter=InMemoryRateLimiter(30, 60),
        lean_limiter=InMemoryRateLimiter(30, 60),
        lean_service=None,
    )


class CoachingStateTests(unittest.TestCase):
    def test_continued_typing_cannot_starve_checks(self):
        view, state = frame(), LiveTurnState()
        view.last_change_at = time.monotonic()
        self.assertTrue(state.can_review(view, time.monotonic()))

    def test_first_frame_waits_for_settling_or_deadline(self):
        view, state = frame(), LiveTurnState()
        view.first_frame_at = view.last_change_at = time.monotonic()
        self.assertFalse(state.can_review(view, time.monotonic()))
        view.first_frame_at -= 5
        self.assertTrue(state.can_review(view, time.monotonic()))

    def test_cursor_change_keeps_speech_and_original_annotation_frame(self):
        view, state = frame(), LiveTurnState()
        state.start_review(view, time.monotonic())
        reviewed_id = state.review.frame_id
        data = jpeg(cursor=True)
        view.accept(str(uuid4()), data, *inspect_frame(data))
        self.assertFalse(state.review_is_stale(view))
        self.assertTrue(state.annotation_is_current(view))
        self.assertEqual(state.review.frame_id, reviewed_id)

    def test_major_viewport_change_and_source_pause_invalidate(self):
        for change in ["viewport", "pause"]:
            with self.subTest(change=change):
                view, state = frame(), LiveTurnState()
                state.start_review(view, time.monotonic())
                if change == "viewport":
                    data = jpeg(background="black")
                    view.accept(str(uuid4()), data, *inspect_frame(data))
                else:
                    view.clear()
                    view.paused = True
                self.assertTrue(state.review_is_stale(view))

    def test_silent_completion_releases_gate_and_suppresses_identical_view(self):
        view, state = frame(), LiveTurnState()
        state.start_review(view, time.monotonic())
        state.observe_content(has_output=False, complete=True, status="IDLE")
        state.finish_if_idle()
        self.assertFalse(state.model_busy)
        self.assertIsNone(state.review)
        self.assertEqual(state.last_model_activity_at, 0)
        state.last_review_at -= 6
        self.assertFalse(state.can_review(view, time.monotonic()))

    def test_spoken_completion_starts_feedback_cooldown(self):
        view, state = frame(), LiveTurnState()
        state.start_review(view, time.monotonic())
        state.turn_output_sent = True
        state.observe_content(has_output=True, complete=True, status=None)
        state.finish_if_idle()
        self.assertGreater(state.last_model_activity_at, 0)
        self.assertFalse(state.can_review(frame(jpeg(cursor=True)), time.monotonic()))

    def test_in_progress_completion_and_outstanding_tools_do_not_release_gate(self):
        view, state = frame(), LiveTurnState()
        state.start_review(view, time.monotonic())
        state.observe_content(has_output=False, complete=True, status="IN_PROGRESS")
        state.finish_if_idle()
        self.assertTrue(state.model_busy)
        state.tools["pending"] = ToolJob(state.generation)
        state.observe_content(has_output=False, complete=True, status="IDLE")
        state.finish_if_idle()
        self.assertTrue(state.model_busy)
        state.tools.clear()
        state.finish_if_idle()
        self.assertFalse(state.model_busy)

    def test_interrupted_old_completion_does_not_complete_new_question(self):
        view, state = frame(), LiveTurnState()
        state.start_review(view, time.monotonic())
        state.user_turn()
        state.provider_interrupted()
        state.observe_content(has_output=False, complete=True, status="IDLE")
        state.finish_if_idle()
        self.assertTrue(state.model_busy)
        self.assertTrue(state.awaiting_user_response)
        self.assertFalse(state.discard_output)
        state.observe_content(has_output=True, complete=True, status="IDLE")
        state.finish_if_idle()
        self.assertFalse(state.model_busy)

    def test_explicit_question_bypasses_automatic_cooldown(self):
        state = LiveTurnState(last_model_activity_at=time.monotonic())
        state.user_turn()
        self.assertTrue(state.awaiting_user_response)

    def test_review_snapshot_age_is_independent_of_latest_frame_age(self):
        view, state = frame(), LiveTurnState()
        state.start_review(view, time.monotonic())
        state.review.captured_at -= 11
        self.assertFalse(state.annotation_is_current(view))
        self.assertFalse(state.review_is_stale(view))

    def test_disconnect_clear_drops_frame_and_review_references(self):
        view, state = frame(), LiveTurnState()
        state.start_review(view, time.monotonic())
        state.reviewed_digests.append(view.latest_digest)
        view.clear()
        state.clear()
        self.assertIsNone(view.latest_image)
        self.assertIsNone(view.signature)
        self.assertIsNone(state.review)
        self.assertFalse(state.reviewed_digests)

    def test_invalid_or_oversized_images_are_rejected_before_decode(self):
        with self.assertRaises(ValueError):
            inspect_frame(b"\xff\xd8not-a-jpeg")
        with Image.new("RGB", (4097, 1), "white") as image:
            stream = io.BytesIO()
            image.save(stream, "JPEG")
            with self.assertRaises(ValueError):
                inspect_frame(stream.getvalue())

    def test_default_model_setup_uses_only_supported_configuration(self):
        config = live_connect_config(mode=TutorMode.CHECK_STEPS)
        self.assertIsNone(config.thinking_config)
        self.assertIsNone(config.enable_affective_dialog)
        self.assertIsNone(config.proactivity)
        self.assertEqual(
            config.realtime_input_config.activity_handling,
            types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
        )


class FakeSession:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.send_client_content = AsyncMock()
        self.send_realtime_input = AsyncMock()
        self.send_tool_response = AsyncMock()

    async def receive(self):
        while True:
            message = await self.messages.get()
            yield message
            if message.server_content and message.server_content.turn_complete:
                return


class CoachingTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = service()
        self.session = FakeSession()
        self.state = LiveTurnState()
        self.frames = frame()
        self.events = []
        self.pending_annotations = {}
        self.lock = asyncio.Lock()

        async def send(payload, **kwargs):
            self.events.append(payload)

        self.send = send
        self.reader = asyncio.create_task(
            self.service._read_upstream(
                self.session,
                self.frames,
                self.pending_annotations,
                self.lock,
                "fixture",
                send,
                coaching=self.state,
            )
        )

    async def asyncTearDown(self):
        self.reader.cancel()
        await asyncio.gather(self.reader, return_exceptions=True)

    async def wait_for(self, predicate):
        async with asyncio.timeout(2):
            while not predicate():
                if self.reader.done():
                    self.reader.result()
                    self.fail("Receive loop stopped unexpectedly")
                await asyncio.sleep(0.005)

    async def emit(self, **kwargs):
        await self.session.messages.put(types.LiveServerMessage(**kwargs))

    async def test_receive_loop_keeps_handling_speech_during_tool_execution(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def slow_tool(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.service._handle_function_call = AsyncMock(side_effect=slow_tool)
        await self.emit(
            tool_call=types.LiveServerToolCall(
                function_calls=[types.FunctionCall(id="slow", name="verify_lean_proof", args={})]
            )
        )
        await asyncio.wait_for(started.wait(), 1)
        await self.emit(
            voice_activity=types.VoiceActivity(
                voice_activity_type=types.VoiceActivityType.ACTIVITY_START
            )
        )
        await self.wait_for(lambda: bool(self.events))
        self.assertTrue(self.events[0]["interrupted"])
        self.assertEqual(self.events[0]["audio"], [])
        await asyncio.wait_for(cancelled.wait(), 1)
        self.session.send_tool_response.assert_not_awaited()

    async def test_provider_cancellation_drops_tool_result(self):
        started = asyncio.Event()

        async def slow_tool(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        self.service._handle_function_call = AsyncMock(side_effect=slow_tool)
        await self.emit(
            tool_call=types.LiveServerToolCall(
                function_calls=[
                    types.FunctionCall(id="cancel", name="calculate_expression", args={})
                ]
            )
        )
        await asyncio.wait_for(started.wait(), 1)
        await self.emit(tool_call_cancellation=types.LiveServerToolCallCancellation(ids=["cancel"]))
        await self.wait_for(lambda: not self.state.tools)
        self.session.send_tool_response.assert_not_awaited()

    async def test_interruption_never_forwards_audio_from_same_event(self):
        self.state.start_review(self.frames, time.monotonic())
        await self.emit(
            server_content=types.LiveServerContent(
                interrupted=True,
                model_turn=types.Content(
                    parts=[
                        types.Part.from_bytes(data=b"old audio", mime_type="audio/pcm;rate=24000")
                    ]
                ),
            )
        )
        await self.wait_for(lambda: bool(self.events))
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0]["audio"], [])
        self.assertIsNone(self.events[0]["text"])

    async def test_updated_cursor_does_not_suppress_spoken_feedback(self):
        self.state.start_review(self.frames, time.monotonic())
        self.state.review.decision = "mistake"
        data = jpeg(cursor=True)
        self.frames.accept(str(uuid4()), data, *inspect_frame(data))
        await self.emit(
            server_content=types.LiveServerContent(
                output_transcription=types.Transcription(text="Check the distribution."),
                turn_complete=True,
                interaction_status="IDLE",
            )
        )
        await self.wait_for(lambda: bool(self.events))
        self.assertEqual(self.events[0]["text"], "Check the distribution.")
        self.assertFalse(self.state.model_busy)

    async def test_changed_frame_annotation_returns_error_without_rebinding(self):
        self.state.start_review(self.frames, time.monotonic())
        self.state.review.decision = "mistake"
        data = jpeg(background="black")
        self.frames.accept(str(uuid4()), data, *inspect_frame(data))
        response = await self.service._handle_function_call(
            types.FunctionCall(id="mark", name="add_annotations", args={"items": [
                {"kind": "rectangle", "label": "Review step", "box_2d": [100, 100, 400, 500]}
            ]}),
            self.session,
            self.frames,
            {},
            "fixture",
            self.send,
            coaching=self.state,
        )
        self.assertEqual(response.response["error"]["code"], "stale_review")
        self.assertFalse(self.events)

    async def test_watchdog_ends_stuck_review_instead_of_overlapping(self):
        self.state.start_review(self.frames, time.monotonic() - 61)
        with patch("app.services.live_sessions.SESSION_WATCH_INTERVAL_SECONDS", 0.005):
            with self.assertRaises(_ScreenReviewTimeout):
                await self.service._review_screen(self.session, self.frames, self.state, self.lock)
        self.session.send_client_content.assert_not_awaited()

    async def test_conversation_mode_does_not_schedule_automatic_checks(self):
        with patch("app.services.live_sessions.SESSION_WATCH_INTERVAL_SECONDS", 0.005):
            monitor = asyncio.create_task(
                self.service._review_screen(
                    self.session,
                    self.frames,
                    self.state,
                    self.lock,
                    enable_reviews=False,
                )
            )
            await asyncio.sleep(0.025)
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        self.session.send_client_content.assert_not_awaited()

    async def test_scheduler_sends_snapshot_without_user_input_or_microphone(self):
        with patch("app.services.live_sessions.SESSION_WATCH_INTERVAL_SECONDS", 0.005):
            monitor = asyncio.create_task(
                self.service._review_screen(self.session, self.frames, self.state, self.lock)
            )
            await self.wait_for(lambda: self.session.send_client_content.await_count == 1)
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        args = self.session.send_client_content.await_args.kwargs
        self.assertTrue(args["turn_complete"])
        self.assertTrue(args["turns"][0].parts[0].text.startswith(AUTOMATIC_SCREEN_REVIEW))
        self.assertIn(self.state.review.review_id, args["turns"][0].parts[0].text)
        self.assertEqual(args["turns"][0].parts[1].inline_data.data, self.frames.latest_image)
        self.assertTrue(self.state.model_busy)

    async def test_pending_annotation_is_cancelled_when_student_speaks(self):
        self.state.start_review(self.frames, time.monotonic())
        self.state.review.decision = "mistake"
        await self.emit(
            tool_call=types.LiveServerToolCall(
                function_calls=[
                    types.FunctionCall(id="mark", name="add_annotations", args={"items": [
                        {"kind": "rectangle", "label": "Review step", "box_2d": [100, 100, 400, 500]}
                    ]})
                ]
            )
        )
        await self.wait_for(lambda: "mark" in self.pending_annotations)
        await self.emit(
            voice_activity=types.VoiceActivity(
                voice_activity_type=types.VoiceActivityType.ACTIVITY_START
            )
        )
        await self.wait_for(lambda: not self.pending_annotations and not self.state.tools)
        self.assertTrue(any(event.get("interrupted") for event in self.events))
        self.session.send_tool_response.assert_not_awaited()

    async def test_waiting_for_input_is_not_provider_completion(self):
        self.state.start_review(self.frames, time.monotonic())
        await self.emit(server_content=types.LiveServerContent(waiting_for_input=True))
        await asyncio.sleep(0.025)
        self.assertTrue(self.state.model_busy)

    async def test_explicit_quiet_report_finishes_without_native_completion(self):
        self.state.start_review(self.frames, time.monotonic())
        review_id = self.state.review.review_id
        await self.emit(
            tool_call=types.LiveServerToolCall(
                function_calls=[
                    types.FunctionCall(
                        id="quiet",
                        name="report_screen_review",
                        args={
                            "review_id": review_id,
                            "decision": "no_issue",
                        },
                    )
                ]
            )
        )
        await self.wait_for(lambda: not self.state.model_busy)
        response = self.session.send_tool_response.await_args.kwargs["function_responses"][0]
        self.assertEqual(response.scheduling, types.FunctionResponseScheduling.SILENT)
        self.assertFalse(response.will_continue)
        self.assertFalse(any(event.get("audio") or event.get("text") for event in self.events))
        self.assertIsNone(self.state.review)
        self.assertEqual(self.state.last_model_activity_at, 0)

    async def test_model_speech_is_suppressed_before_mistake_decision(self):
        self.state.start_review(self.frames, time.monotonic())
        await self.emit(
            server_content=types.LiveServerContent(
                output_transcription=types.Transcription(text="Everything looks good."),
            )
        )
        await asyncio.sleep(0.025)
        self.assertFalse(self.events)
        self.assertTrue(self.state.model_busy)

    async def test_quiet_report_does_not_override_explicit_provider_background_work(self):
        self.state.start_review(self.frames, time.monotonic())
        review_id = self.state.review.review_id
        await self.emit(server_content=types.LiveServerContent(interaction_status="IN_PROGRESS"))
        await self.wait_for(lambda: self.state.interaction_status == "IN_PROGRESS")
        await self.emit(
            tool_call=types.LiveServerToolCall(
                function_calls=[
                    types.FunctionCall(
                        id="quiet-background",
                        name="report_screen_review",
                        args={"review_id": review_id, "decision": "no_issue"},
                    )
                ]
            )
        )
        await self.wait_for(lambda: self.session.send_tool_response.await_count == 1)
        self.assertTrue(self.state.model_busy)
        self.assertIsNotNone(self.state.review)
        await self.emit(server_content=types.LiveServerContent(interaction_status="IDLE"))
        await self.wait_for(lambda: not self.state.model_busy)
        self.assertIsNone(self.state.review)

    async def test_invalidated_review_can_finish_silently_and_resume_fresh_checks(self):
        for reason in ["pause", "viewport"]:
            with self.subTest(reason=reason):
                self.frames = frame()
                self.state.start_review(self.frames, time.monotonic())
                review_id = self.state.review.review_id
                if reason == "pause":
                    self.frames.clear()
                    self.frames.paused = True
                    self.state.pause_screen()
                else:
                    data = jpeg(background="black")
                    self.frames.accept(str(uuid4()), data, *inspect_frame(data))
                    self.assertTrue(self.state.review_is_stale(self.frames))
                    self.state.generation += 1
                await self.emit(
                    tool_call=types.LiveServerToolCall(
                        function_calls=[
                            types.FunctionCall(
                                id=f"stale-{reason}",
                                name="report_screen_review",
                                args={"review_id": review_id, "decision": "mistake"},
                            )
                        ]
                    )
                )
                await self.wait_for(lambda: not self.state.model_busy)
                response = self.session.send_tool_response.await_args.kwargs["function_responses"][
                    0
                ]
                self.assertEqual(response.response["decision"], "uncertain")
                self.assertFalse(response.response["accepted"])
                self.assertEqual(response.scheduling, types.FunctionResponseScheduling.SILENT)
                self.assertIsNone(self.state.review)
                self.assertFalse(
                    any(event.get("audio") or event.get("text") for event in self.events)
                )
                self.state.last_review_at -= 6
                self.assertTrue(self.state.can_review(frame(jpeg(cursor=True)), time.monotonic()))

    async def test_old_report_cannot_finish_new_student_response(self):
        self.state.start_review(self.frames, time.monotonic())
        old_id = self.state.review.review_id
        self.state.user_turn()
        await self.emit(
            tool_call=types.LiveServerToolCall(
                function_calls=[
                    types.FunctionCall(
                        id="old-report",
                        name="report_screen_review",
                        args={"review_id": old_id, "decision": "no_issue"},
                    )
                ]
            )
        )
        await asyncio.sleep(0.025)
        self.session.send_tool_response.assert_not_awaited()
        self.assertTrue(self.state.model_busy)
        self.assertTrue(self.state.awaiting_user_response)

    async def test_wrong_review_id_cannot_authorize_speech(self):
        self.state.start_review(self.frames, time.monotonic())
        result = await self.service._handle_function_call(
            types.FunctionCall(
                id="old",
                name="report_screen_review",
                args={
                    "review_id": "0" * 32,
                    "decision": "mistake",
                },
            ),
            self.session,
            self.frames,
            {},
            "fixture",
            self.send,
            coaching=self.state,
        )
        self.assertEqual(result.response["error"]["code"], "stale_review")
        self.assertFalse(self.state.allows_speech())

    async def test_malformed_review_result_is_bounded_and_rejected(self):
        self.state.start_review(self.frames, time.monotonic())
        for args in [
            {},
            {"review_id": "x" * 5000, "decision": "mistake"},
            {
                "review_id": self.state.review.review_id,
                "decision": "run_shell",
            },
        ]:
            with self.subTest(args=list(args)):
                result = await self.service._handle_function_call(
                    types.FunctionCall(id="invalid", name="report_screen_review", args=args),
                    self.session,
                    self.frames,
                    {},
                    "fixture",
                    self.send,
                    coaching=self.state,
                )
                self.assertEqual(result.response["error"]["code"], "invalid_review_result")

    async def test_transcription_flushes_audio_after_provider_finished_generating(self):
        self.state.model_busy = False
        await self.emit(
            server_content=types.LiveServerContent(
                input_transcription=types.Transcription(text="Hold on"),
            )
        )
        await self.wait_for(lambda: bool(self.events))
        self.assertTrue(self.events[0]["interrupted"])
        self.assertTrue(self.state.awaiting_user_response)
        self.assertFalse(self.state.waiting_for_old_turn)

    async def test_successful_tool_result_waits_for_provider_followup(self):
        await self.emit(
            tool_call=types.LiveServerToolCall(
                function_calls=[
                    types.FunctionCall(
                        id="arithmetic",
                        name="calculate_expression",
                        args={
                            "expression": "2*3",
                        },
                    )
                ]
            )
        )
        await self.wait_for(lambda: self.session.send_tool_response.await_count == 1)
        self.assertTrue(self.state.model_busy)
        await self.emit(
            server_content=types.LiveServerContent(
                turn_complete=True,
                interaction_status="IDLE",
            )
        )
        await self.wait_for(lambda: not self.state.model_busy)


if __name__ == "__main__":
    unittest.main()
