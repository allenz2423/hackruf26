"""Study formats, saved exports, and source-preserving upload document regressions."""

from __future__ import annotations

import asyncio
import io
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

from pypdf import PdfReader
from starlette.requests import Request

from app.api.class_library import (
    _create_material_job,
    _public_material_content,
    download_study_file,
    get_study_material,
)
from app.api.errors import ApiError
from app.api.schemas import (
    FlashcardsContent,
    PracticeExamContent,
    QuizContent,
    StudyGuideContent,
    StudyOptions,
)
from app.api.upload_documents import (
    _create_upload_document_job,
    _reserve_document,
    document_detail,
    download_upload_document,
)
from app.config import Settings
from app.services.auth import SESSION_COOKIE, create_session, create_user
from app.services.database import create_database, recover_interrupted_jobs
from app.services.gemini_content import ContentGenerationFailure, GeminiContentService
from app.services.study_files import (
    _safe_math,
    compiler_path,
    create_guide_files,
    render_guide_latex,
)


def settings() -> Settings:
    return Settings(
        _env_file=None,
        sqlite_path=":memory:",
        file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
    )


def section(upload_id: UUID, body: str = "Glucose is a monosaccharide.") -> dict:
    return {
        "heading": "Carbohydrates",
        "body": body,
        "key_terms": ["glucose"],
        "sources": [{"upload_id": str(upload_id), "page": 5}],
    }


class StudyFormatTests(unittest.TestCase):
    def test_every_format_uses_its_schema_prompt_count_and_citations(self) -> None:
        upload_id = uuid4()
        source = {
            "id": upload_id,
            "title": "Biology notes",
            "extracted_text": "Glucose stores energy.",
        }
        questions = [
            {
                "id": "q1",
                "kind": "multiple_choice",
                "prompt": "Choose the carbohydrate.",
                "choices": [{"id": "a", "text": "Glucose"}, {"id": "b", "text": "Water"}],
                "answer": "a",
            },
            {
                "id": "q2",
                "kind": "true_false",
                "prompt": "Glucose stores energy.",
                "answer": "true",
            },
            {
                "id": "q3",
                "kind": "short_answer",
                "prompt": "Name the carbohydrate.",
                "answer": "glucose",
            },
            {
                "id": "q4",
                "kind": "short_answer",
                "prompt": "What does glucose store?",
                "answer": "energy",
            },
        ]
        for question in questions:
            question.update(
                explanation="From the supplied notes.", sources=[{"upload_id": str(upload_id)}]
            )
        fixtures = {
            "quiz": (QuizContent, {"format": "quiz", "questions": questions}),
            "practice_exam": (
                PracticeExamContent,
                {
                    "format": "practice_exam",
                    "instructions": "Time limit: 15 minutes.",
                    "questions": questions,
                },
            ),
            "study_guide": (
                StudyGuideContent,
                {"format": "study_guide", "sections": [section(upload_id) for _ in range(4)]},
            ),
            "flashcards": (
                FlashcardsContent,
                {
                    "format": "flashcards",
                    "cards": [
                        {
                            "id": f"c{i}",
                            "front": f"Concept {i}",
                            "back": "Glucose",
                            "sources": [{"upload_id": str(upload_id)}],
                        }
                        for i in range(4)
                    ],
                },
            ),
        }
        for kind, (schema, content) in fixtures.items():
            with self.subTest(kind=kind):
                service = GeminiContentService(None, settings())
                service._generate_json = Mock(return_value=schema.model_validate(content))
                title, result = service.generate_material(
                    kind=kind,
                    class_name="BIO 170",
                    scope={"kind": "uploads", "upload_ids": [str(upload_id)]},
                    options={
                        "count": 4,
                        "difficulty": "mixed",
                        **({"time_limit_minutes": 15} if kind == "practice_exam" else {}),
                    },
                    sources=[source],
                )
                self.assertIn("BIO 170", title)
                self.assertEqual(result["format"], kind)
                self.assertIs(service._generate_json.call_args.args[1], schema)
                self.assertIn("Return exactly 4", service._generate_json.call_args.args[0])
                self.assertTrue(service._generate_json.call_args.kwargs["system_instruction"])
                if kind in {"quiz", "practice_exam"}:
                    self.assertNotIn(
                        "answer", _public_material_content(kind, result)["questions"][0]
                    )

    def test_bad_count_and_citations_are_rejected(self) -> None:
        source_id = uuid4()
        for sections in [[section(source_id)], [section(uuid4()) for _ in range(4)]]:
            service = GeminiContentService(None, settings())
            service._generate_json = Mock(
                return_value=StudyGuideContent(format="study_guide", sections=sections)
            )
            with self.assertRaises(ContentGenerationFailure):
                service.generate_material(
                    kind="study_guide",
                    class_name="BIO 170",
                    scope={"kind": "all"},
                    options={"count": 4},
                    sources=[{"id": source_id, "title": "Notes", "extracted_text": "Glucose."}],
                )

    def test_100_item_limit_is_supported_across_generated_materials(self) -> None:
        questions = [
            {
                "id": f"q{index}",
                "kind": "short_answer",
                "prompt": f"Question {index}?",
                "answer": f"answer {index}",
                "accepted_answers": [f"answer {index}"],
                "explanation": "From the supplied notes.",
            }
            for index in range(100)
        ]
        contents = [
            QuizContent(format="quiz", questions=questions),
            PracticeExamContent(
                format="practice_exam", instructions="Practice exam.", questions=questions
            ),
            StudyGuideContent(
                format="study_guide",
                sections=[{"heading": f"Section {i}", "body": "Notes."} for i in range(100)],
            ),
            FlashcardsContent(
                format="flashcards",
                cards=[
                    {"id": f"c{i}", "front": f"Term {i}", "back": "Definition."}
                    for i in range(100)
                ],
            ),
        ]
        self.assertEqual(StudyOptions(count=100).count, 100)
        for content in contents:
            with self.subTest(kind=content.format):
                item_list = next(
                    value
                    for key, value in content.model_dump().items()
                    if key in {"questions", "sections", "cards"}
                )
                self.assertEqual(len(item_list), 100)
        with self.assertRaises(ValueError):
            StudyOptions(count=101)
        with self.assertRaises(ValueError):
            QuizContent(format="quiz", questions=[*questions, questions[0]])

    def test_100_question_generation_has_an_expanded_structured_output_budget(self) -> None:
        upload_id = uuid4()
        source = {
            "id": upload_id,
            "title": "Biology notes",
            "extracted_text": "Glucose stores energy.",
        }
        questions = [
            {
                "id": f"q{index}",
                "kind": "short_answer",
                "prompt": f"Question {index}?",
                "answer": f"answer {index}",
                "accepted_answers": [f"answer {index}"],
                "explanation": "From the supplied notes.",
                "sources": [{"upload_id": str(upload_id)}],
            }
            for index in range(100)
        ]
        service = GeminiContentService(None, settings())
        service._generate_json = Mock(
            return_value=QuizContent(format="quiz", questions=questions)
        )
        _, content = service.generate_material(
            kind="quiz",
            class_name="BIO 170",
            scope={"kind": "uploads", "upload_ids": [str(upload_id)]},
            options={
                "count": 100,
                "difficulty": "mixed",
                "question_types": ["short_answer"],
            },
            sources=[source],
        )
        self.assertEqual(len(content["questions"]), 100)
        self.assertEqual(service._generate_json.call_args.kwargs["max_output_tokens"], 48_000)


class LatexExportTests(unittest.TestCase):
    def test_tex_programs_and_encoded_commands_are_not_math(self) -> None:
        for value in [
            r"\input{/etc/passwd}",
            r"^^5cinput{file}",
            r"\write18{command}",
            r"\csname input\endcsname",
            "x%comment",
        ]:
            self.assertFalse(_safe_math(value))
        self.assertTrue(_safe_math(r"x^2 + \frac{1}{2} + \beta"))

    def test_source_appendix_escapes_text_and_keeps_source_identifiers(self) -> None:
        upload_id = uuid4()
        original = "FULL_SOURCE_MARKER\nValue = 5% & β bonds.\n" + r"\input{secret}"
        latex = render_guide_latex(
            title="BIO #170",
            class_name="BIO",
            material_id=upload_id,
            content={
                "format": "upload_document",
                "detail_level": "balanced",
                "sections": [section(upload_id)],
            },
            sources=[{"id": upload_id, "title": "Carbs.pdf"}],
            original_text=original,
        )
        self.assertIn(r"FULL\_SOURCE\_MARKER", latex)
        self.assertIn(r"5\% \& β", latex)
        self.assertNotIn(r"\input{secret}", latex)
        self.assertIn("Original extracted source text", latex)
        self.assertIn("Carbs.pdf", latex)
        self.assertIn(str(upload_id), latex)

    @unittest.skipUnless(compiler_path(settings()), "Tectonic is not installed")
    def test_real_pdf_contains_guide_and_full_source_appendix(self) -> None:
        upload_id = uuid4()
        files = create_guide_files(
            settings(),
            title="Biology guide",
            class_name="BIO 170",
            material_id=upload_id,
            content={
                "format": "study_guide",
                "sections": [section(upload_id, r"Formula $x^2 + \frac{1}{2}$ and β bonds.")],
            },
            sources=[{"id": upload_id, "title": "Carbs.pdf"}],
            original_text="FULL_SOURCE_MARKER\nOriginal quantity = 170.5 mg.",
        )
        text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(files["pdf"])).pages)
        for marker in [
            "Biology guide",
            "Carbohydrates",
            "Carbs.pdf",
            "FULL_SOURCE_MARKER",
            "170.5",
            "β",
        ]:
            self.assertIn("".join(marker.split()), "".join(text.split()))
        self.assertTrue(files["pdf"].startswith(b"%PDF-"))


class SavedDocumentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.settings = settings()
        self.db = await create_database(self.settings)
        self.addAsyncCleanup(self.db.close)
        self.user = await create_user(self.db, username="doc_student", password="fixture-password")
        self.token, _ = await create_session(self.db, self.user["id"], 1)
        self.class_id, self.upload_id, self.material_id = uuid4(), uuid4(), uuid4()
        self.original = "Glucose is a monosaccharide. Original data = 170.5 mg."
        await self.db.execute(
            "INSERT INTO course_classes (id, owner_id, name) VALUES (?1, ?2, 'BIO 170')",
            self.class_id,
            self.user["id"],
        )
        await self.db.execute(
            "INSERT INTO course_notes (id, class_id, kind, title, source_type, "
            "extracted_text, status, processing_progress) "
            "VALUES (?1, ?2, 'notes', 'Carbs.pdf', 'pasted_text', ?3, 'ready', 100)",
            self.upload_id,
            self.class_id,
            self.original,
        )
        self.guide = {
            "format": "study_guide",
            "sections": [section(self.upload_id) for _ in range(4)],
        }
        self.service = SimpleNamespace(
            generate_material=lambda **kwargs: ("Biology guide", self.guide),
            generate_upload_document_chunk=lambda **kwargs: [section(self.upload_id)],
        )
        self.application = SimpleNamespace(
            state=SimpleNamespace(
                settings=self.settings, database=self.db, gemini_content_service=self.service
            )
        )
        self.files = {"pdf": b"%PDF-test-persistent-bytes", "latex": b"saved latex"}

    def request(self, token: str | None = None) -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api",
                "scheme": "http",
                "headers": [
                    (b"origin", b"http://localhost:5173"),
                    (b"cookie", f"{SESSION_COOKIE}={token or self.token}".encode()),
                ],
                "client": ("127.0.0.1", 12345),
                "server": ("localhost", 8000),
                "app": self.application,
            }
        )

    async def add_material(self, status: str = "generating") -> None:
        await self.db.execute(
            "INSERT INTO study_materials (id, class_id, kind, title, status, "
            "content) VALUES (?1, ?2, 'study_guide', 'Guide', ?3, ?4)",
            self.material_id,
            self.class_id,
            status,
            json.dumps(self.guide) if status == "ready" else None,
        )
        await self.db.execute(
            "INSERT INTO study_material_sources (material_id, class_id, note_id) "
            "VALUES (?1, ?2, ?3)",
            self.material_id,
            self.class_id,
            self.upload_id,
        )

    async def test_generated_guide_and_files_save_together_and_delete_together(self) -> None:
        await self.add_material()
        with patch("app.api.class_library.create_guide_files", return_value=self.files):
            await _create_material_job(self.application, self.material_id)
        detail = await get_study_material(self.material_id, self.request())
        self.assertEqual(detail.status, "ready")
        self.assertEqual(set(detail.download_urls), {"pdf", "latex"})
        for kind in self.files:
            response = await download_study_file(self.material_id, kind, self.request())
            self.assertEqual(response.body, self.files[kind])
        await self.db.execute("DELETE FROM study_materials WHERE id = ?1", self.material_id)
        self.assertEqual(await self.db.fetchval("SELECT count(*) FROM study_material_files"), 0)

    async def test_legacy_guide_backfills_once_without_calling_the_model(self) -> None:
        await self.add_material("ready")
        with patch("app.api.class_library.create_guide_files", return_value=self.files) as render:
            await download_study_file(self.material_id, "pdf", self.request())
            await download_study_file(self.material_id, "latex", self.request())
            render.assert_called_once()

    async def test_file_download_checks_owner_and_ready_status(self) -> None:
        await self.add_material()
        with self.assertRaises(ApiError) as not_ready:
            await download_study_file(self.material_id, "pdf", self.request())
        self.assertEqual(not_ready.exception.status_code, 409)
        other = await create_user(self.db, username="other_student", password="fixture-password")
        other_token, _ = await create_session(self.db, other["id"], 1)
        with self.assertRaises(ApiError) as wrong_owner:
            await download_study_file(self.material_id, "pdf", self.request(other_token))
        self.assertEqual(wrong_owner.exception.status_code, 404)

    async def test_companion_preserves_original_and_caches_detail_variants(self) -> None:
        for level in ["balanced", "concise", "detailed"]:
            job_id, created = await _reserve_document(self.db, self.upload_id, level)
            self.assertTrue(created)
            with patch(
                "app.api.upload_documents.create_guide_files", return_value=self.files
            ) as render:
                await _create_upload_document_job(self.application, self.upload_id, level, job_id)
            self.assertEqual(render.call_args.kwargs["original_text"], self.original)
            self.assertEqual(
                (await document_detail(self.db, self.upload_id, level))["status"], "ready"
            )
            _, created_again = await _reserve_document(self.db, self.upload_id, level)
            self.assertFalse(created_again)
            response = await download_upload_document(
                self.upload_id, "original_text", self.request(), level
            )
            self.assertEqual(response.body.decode(), self.original)
        self.assertEqual(
            await self.db.fetchval(
                "SELECT extracted_text FROM course_notes WHERE id = ?1", self.upload_id
            ),
            self.original,
        )
        await self.db.execute("DELETE FROM course_notes WHERE id = ?1", self.upload_id)
        self.assertEqual(await self.db.fetchval("SELECT count(*) FROM upload_documents"), 0)

    async def test_all_long_source_chunks_are_analyzed(self) -> None:
        original = "x" * 85_000 + "LAST_PAGE_MARKER"
        await self.db.execute(
            "UPDATE course_notes SET extracted_text = ?2 WHERE id = ?1", self.upload_id, original
        )
        chunks = []
        self.service.generate_upload_document_chunk = lambda **kwargs: (
            chunks.append(kwargs["text"]) or [section(self.upload_id)]
        )
        job_id, _ = await _reserve_document(self.db, self.upload_id, "balanced")
        with patch("app.api.upload_documents.create_guide_files", return_value=self.files):
            await _create_upload_document_job(self.application, self.upload_id, "balanced", job_id)
        self.assertEqual("".join(chunks), original)
        self.assertEqual(len(chunks), 3)

    async def test_stale_companion_cannot_overwrite_an_edited_source(self) -> None:
        started, release = threading.Event(), threading.Event()

        def slow_generation(**kwargs):
            started.set()
            release.wait(5)
            return [section(self.upload_id)]

        self.service.generate_upload_document_chunk = slow_generation
        job_id, _ = await _reserve_document(self.db, self.upload_id, "balanced")
        task = asyncio.create_task(
            _create_upload_document_job(self.application, self.upload_id, "balanced", job_id)
        )
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            await self.db.execute(
                "UPDATE course_notes SET extracted_text = 'New source text' WHERE id = ?1",
                self.upload_id,
            )
        finally:
            release.set()
            await task
        self.assertEqual((await document_detail(self.db, self.upload_id))["status"], "not_created")

    async def test_companion_failure_and_restart_do_not_erase_original(self) -> None:
        self.service.generate_upload_document_chunk = Mock(
            side_effect=RuntimeError("provider failure")
        )
        job_id, _ = await _reserve_document(self.db, self.upload_id, "balanced")
        await _create_upload_document_job(self.application, self.upload_id, "balanced", job_id)
        self.assertEqual((await document_detail(self.db, self.upload_id))["status"], "failed")
        self.assertEqual(
            await self.db.fetchval("SELECT status FROM course_notes WHERE id = ?1", self.upload_id),
            "ready",
        )
        await _reserve_document(self.db, self.upload_id, "balanced")
        await recover_interrupted_jobs(self.db)
        self.assertEqual(
            (await document_detail(self.db, self.upload_id))["failure_code"],
            "generation_interrupted",
        )
        self.assertEqual(
            await self.db.fetchval(
                "SELECT extracted_text FROM course_notes WHERE id = ?1", self.upload_id
            ),
            self.original,
        )


if __name__ == "__main__":
    unittest.main()
