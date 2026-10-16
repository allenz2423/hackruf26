"""Regression checks for model tools and server-side quiz scoring."""

from __future__ import annotations

import asyncio
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from google.genai import types
from starlette.requests import Request

from app.api.class_library import (
    _create_material_job,
    _normalize_answer,
    _process_upload_job,
    _reprocess_upload_job,
    submit_attempt,
)
from app.api.schemas import AttemptSubmission, LeanProofResponse
from app.config import Settings
from app.security.rate_limit import InMemoryRateLimiter
from app.services.auth import SESSION_COOKIE, create_session, create_user
from app.services.database import create_database, recover_interrupted_jobs
from app.services.gemini_content import GeminiContentService
from app.services.live_sessions import GeminiLiveSessionService, _FrameState


def make_test_settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
        sqlite_path=":memory:",
        **overrides,
    )


class LiveToolRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.lean = SimpleNamespace(
            verify=AsyncMock(
                return_value=LeanProofResponse(
                    status="verified", diagnostics="Test proof accepted."
                )
            )
        )
        self.service = GeminiLiveSessionService(
            None,
            make_test_settings(lean_tool_enabled=True),
            calculator_limiter=InMemoryRateLimiter(30, 60),
            lean_limiter=InMemoryRateLimiter(30, 60),
            lean_service=self.lean,
        )

    async def call(self, name: str, args: dict) -> dict:
        response = await self.service._handle_function_call(
            types.FunctionCall(id="test-call", name=name, args=args),
            None,
            _FrameState(),
            {},
            "test-peer",
            None,
        )
        self.assertEqual(response.id, "test-call")
        self.assertEqual(response.name, name)
        return response.response

    async def test_calculator_returns_numeric_and_symbolic_strings(self) -> None:
        self.assertEqual(
            await self.call("calculate_expression", {"expression": "13*17+5"}), {"result": "226"}
        )
        self.assertEqual(
            await self.call("calculate_expression", {"expression": "expand((x+1)**2)"}),
            {"result": "x**2 + 2*x + 1"},
        )

    async def test_bad_calculator_arguments_return_tool_errors(self) -> None:
        for args in [
            {},
            {"expression": 7},
            {"expression": "1+1", "extra": True},
            {"expression": '__import__("os")'},
        ]:
            with self.subTest(args=args):
                result = await self.call("calculate_expression", args)
                self.assertEqual(result["error"]["code"], "invalid_expression")

    async def test_bad_lean_arguments_do_not_reach_the_runner(self) -> None:
        for args in [
            {},
            {"proposition": 7, "proof": "trivial"},
            {"proposition": "True", "proof": ["trivial"]},
            {"proposition": "True", "proof": "trivial", "extra": True},
        ]:
            with self.subTest(args=args):
                result = await self.call("verify_lean_proof", args)
                self.assertEqual(result["error"]["code"], "invalid_proof")
        self.lean.verify.assert_not_awaited()

    async def test_valid_lean_arguments_reach_the_runner(self) -> None:
        result = await self.call("verify_lean_proof", {"proposition": "True", "proof": "trivial"})
        self.assertEqual(result["status"], "verified")
        self.lean.verify.assert_awaited_once_with("True", "trivial")


class QuizScoringRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.settings = make_test_settings()
        self.database = await create_database(self.settings)
        self.user = await create_user(
            self.database, username="scoring_student", password="fixture-password"
        )
        self.session_token, _ = await create_session(self.database, self.user["id"], 1)
        self.material_id = uuid4()
        class_id = uuid4()
        self.questions = [
            {"id": "negative", "kind": "short_answer", "answer": "-1"},
            {"id": "decimal", "kind": "short_answer", "answer": "1.0"},
            {"id": "operator", "kind": "short_answer", "answer": "x+y"},
            {
                "id": "canonical",
                "kind": "short_answer",
                "answer": "glycogen",
                "accepted_answers": ["animal storage polysaccharide"],
            },
            {
                "id": "choice",
                "kind": "multiple_choice",
                "answer": "A",
                "choices": [{"id": "A", "text": "First"}, {"id": "a", "text": "Second"}],
            },
        ]
        await self.database.execute(
            "INSERT INTO course_classes (id, owner_id, name) VALUES (?1, ?2, ?3)",
            class_id,
            self.user["id"],
            "Isolated scoring fixture",
        )
        await self.database.execute(
            "INSERT INTO study_materials "
            "(id,origin_class_id,resource_scope,owner_id,created_by,kind,title,status,content,"
            "assessment_validation_state) "
            "VALUES (?1,?2,'personal',?3,?3,'quiz','Scoring fixture','ready',?4,'validated')",
            self.material_id,
            class_id,
            self.user["id"],
            json.dumps({"questions": self.questions}),
        )

    async def asyncTearDown(self) -> None:
        await self.database.close()

    async def submit(self, answers: dict[str, str]):
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/study-materials/test/attempts",
                "headers": [
                    (b"origin", b"http://localhost:5173"),
                    (b"cookie", f"{SESSION_COOKIE}={self.session_token}".encode()),
                ],
                "state": {},
                "app": SimpleNamespace(
                    state=SimpleNamespace(
                        settings=self.settings,
                        database=self.database,
                        private_storage=None,
                    )
                ),
            }
        )
        return await submit_attempt(self.material_id, AttemptSubmission(answers=answers), request)

    async def test_changed_math_meaning_and_wrong_choice_are_incorrect(self) -> None:
        result = await self.submit(
            {"negative": "1", "decimal": "10", "operator": "xy", "choice": "a"}
        )
        self.assertEqual(result.correct_count, 0)
        self.assertEqual(result.score_percent, 0)

    async def test_canonical_answers_always_count_and_are_saved(self) -> None:
        result = await self.submit(
            {
                "negative": " -1 ",
                "decimal": "1.0",
                "operator": "x+y",
                "canonical": " GLYCOGEN ",
                "choice": "A",
            }
        )
        self.assertEqual(result.correct_count, 5)
        self.assertEqual(result.score_percent, 100)
        saved = await self.database.fetchval(
            "SELECT score_percent FROM study_material_attempts WHERE id = ?1",
            result.attempt_id,
        )
        self.assertEqual(saved, 100)

    async def test_accepted_alternative_still_counts(self) -> None:
        result = await self.submit({"canonical": "animal   storage polysaccharide"})
        self.assertTrue(
            next(q.correct for q in result.question_results if q.question_id == "canonical")
        )

    async def test_unvalidated_legacy_personal_assessment_cannot_be_newly_graded(self) -> None:
        from app.api.errors import ApiError

        await self.database.execute(
            "UPDATE study_materials SET assessment_validation_state='legacy_unvalidated' "
            "WHERE id=?1",
            self.material_id,
        )
        with self.assertRaises(ApiError) as raised:
            await self.submit({"negative": "-1"})
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.code, "assessment_unvalidated")
        self.assertEqual(
            await self.database.fetchval(
                "SELECT count(*) FROM study_material_attempts WHERE material_id=?1",
                self.material_id,
            ),
            0,
        )

    async def test_modern_validated_personal_assessment_requires_run_snapshot(self) -> None:
        from app.api.errors import ApiError

        await self.database.execute(
            "UPDATE study_materials SET assessment_engine_version=1 WHERE id=?1",
            self.material_id,
        )
        with self.assertRaises(ApiError) as raised:
            await self.submit({"negative": "-1"})
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.code, "quiz_run_required")
        self.assertEqual(
            await self.database.fetchval(
                "SELECT count(*) FROM study_material_attempts WHERE material_id=?1",
                self.material_id,
            ),
            0,
        )

    def test_normalization_preserves_mathematical_punctuation(self) -> None:
        for left, right in [("-1", "1"), ("1.0", "10"), ("x+y", "xy"), ("a/b", "ab")]:
            self.assertNotEqual(_normalize_answer(left), _normalize_answer(right))
        self.assertEqual(_normalize_answer("  ＧＬＹＣＯＧＥＮ  "), "glycogen")

    def test_question_validation_includes_canonical_answer(self) -> None:
        question = {
            "id": "canonical",
            "kind": "short_answer",
            "answer": "glycogen",
            "accepted_answers": ["animal storage polysaccharide"],
        }
        GeminiContentService._validate_questions([question], {"short_answer"})
        self.assertEqual(
            question["accepted_answers"], ["glycogen", "animal storage polysaccharide"]
        )


class ProcessingLifecycleRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.settings = make_test_settings()
        self.database = await create_database(self.settings)
        self.class_id = uuid4()
        await self.database.execute(
            "INSERT INTO course_classes (id, owner_id, name) VALUES (?1, ?2, 'Lifecycle fixture')",
            self.class_id,
            self.settings.local_owner_id,
        )

    async def asyncTearDown(self) -> None:
        await self.database.close()

    async def note(self, status: str = "processing"):
        note_id = uuid4()
        await self.database.execute(
            "INSERT INTO course_notes (id, class_id, title, source_type, extracted_text, "
            "status, processing_progress) VALUES (?1, ?2, 'Fixture', 'pasted_text', "
            "'Synthetic notes', ?3, ?4)",
            note_id,
            self.class_id,
            status,
            100 if status == "ready" else 0,
        )
        return note_id

    async def test_startup_recovers_interrupted_work_and_preserves_ready_records(self) -> None:
        pending = await self.note()
        ready = await self.note("ready")
        assessment_id, material_id, ready_material, guide_preview_id = (
            uuid4(), uuid4(), uuid4(), uuid4()
        )
        await self.database.execute(
            "INSERT INTO uploaded_assessments (id, class_id, note_id) VALUES (?1, ?2, ?3)",
            assessment_id,
            self.class_id,
            pending,
        )
        for record_id, status, content in [
            (material_id, "generating", None),
            (ready_material, "ready", '{"questions":[]}'),
        ]:
            await self.database.execute(
                "INSERT INTO study_materials (id, class_id, kind, title, status, content) "
                "VALUES (?1, ?2, 'quiz', 'Fixture', ?3, ?4)",
                record_id,
                self.class_id,
                status,
                content,
            )
        preview = {"format": "study_guide", "sections": [{"heading": "Saved preview"}]}
        await self.database.execute(
            "INSERT INTO study_materials (id, class_id, kind, title, status, preview_content, "
            "preview_revision) VALUES (?1, ?2, 'study_guide', 'Guide', 'generating', ?3, 7)",
            guide_preview_id,
            self.class_id,
            json.dumps(preview),
        )
        await recover_interrupted_jobs(self.database)
        self.assertEqual(
            await self.database.fetchrow(
                "SELECT status, failure_code FROM course_notes WHERE id = ?1",
                pending,
            ),
            {"status": "failed", "failure_code": "processing_interrupted"},
        )
        self.assertEqual(
            await self.database.fetchval(
                "SELECT status FROM course_notes WHERE id = ?1",
                ready,
            ),
            "ready",
        )
        self.assertEqual(
            await self.database.fetchrow(
                "SELECT processing_status, failure_code FROM uploaded_assessments WHERE id = ?1",
                assessment_id,
            ),
            {"processing_status": "failed", "failure_code": "processing_interrupted"},
        )
        self.assertEqual(
            await self.database.fetchrow(
                "SELECT status, failure_code FROM study_materials WHERE id = ?1",
                material_id,
            ),
            {"status": "failed", "failure_code": "generation_interrupted"},
        )
        self.assertEqual(
            await self.database.fetchrow(
                "SELECT status, failure_code, preview_content, preview_revision "
                "FROM study_materials WHERE id = ?1",
                guide_preview_id,
            ),
            {
                "status": "failed",
                "failure_code": "generation_interrupted",
                "preview_content": json.dumps(preview),
                "preview_revision": 7,
            },
        )
        self.assertEqual(
            await self.database.fetchval(
                "SELECT status FROM study_materials WHERE id = ?1",
                ready_material,
            ),
            "ready",
        )
        await recover_interrupted_jobs(self.database)

    async def test_old_upload_failure_does_not_overwrite_a_new_edit(self) -> None:
        for reprocess in [False, True]:
            with self.subTest(reprocess=reprocess):
                note_id = await self.note()
                started, release = threading.Event(), threading.Event()

                def delayed_failure(*args, started=started, release=release):
                    started.set()
                    if not release.wait(5):
                        raise TimeoutError("Fixture wait expired")
                    raise ValueError("Synthetic processing failure")

                application = SimpleNamespace(
                    state=SimpleNamespace(
                        database=self.database,
                        gemini_content_service=SimpleNamespace(process_text=delayed_failure),
                        private_storage=object(),
                    )
                )
                task = asyncio.create_task(
                    _reprocess_upload_job(application, note_id, True)
                    if reprocess
                    else _process_upload_job(application, note_id)
                )
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 5))
                    await self.database.execute(
                        "UPDATE course_notes SET extracted_text = 'New edit', "
                        "updated_at = '2099-01-01T00:00:00Z' WHERE id = ?1",
                        note_id,
                    )
                finally:
                    release.set()
                with self.assertLogs("screen_tutor.class_library", level="ERROR"):
                    await task
                self.assertEqual(
                    await self.database.fetchrow(
                        "SELECT status, failure_code, extracted_text FROM course_notes "
                        "WHERE id = ?1",
                        note_id,
                    ),
                    {"status": "processing", "failure_code": None, "extracted_text": "New edit"},
                )

    async def test_completed_material_is_not_regenerated(self) -> None:
        material_id = uuid4()
        await self.database.execute(
            "INSERT INTO study_materials (id, class_id, kind, title, status, content) "
            "VALUES (?1, ?2, 'quiz', 'Ready fixture', 'ready', '{\"questions\":[]}')",
            material_id,
            self.class_id,
        )
        service = SimpleNamespace(
            generate_material=lambda **kwargs: self.fail("Ready material regenerated")
        )
        application = SimpleNamespace(
            state=SimpleNamespace(
                database=self.database,
                gemini_content_service=service,
            )
        )
        await _create_material_job(application, material_id)
        self.assertEqual(
            await self.database.fetchval(
                "SELECT title FROM study_materials WHERE id = ?1",
                material_id,
            ),
            "Ready fixture",
        )


if __name__ == "__main__":
    unittest.main()
