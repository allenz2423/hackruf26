"""Saved source lifecycle, PDF extraction, and balanced context regressions."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from fastapi import BackgroundTasks
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pypdf import PdfWriter
from starlette.requests import Request
from starlette.responses import Response

from app.api.class_library import (
    _process_assessment_job,
    _process_upload_job,
    _provider_error_metadata,
    _reprocess_upload_job,
    get_study_material,
    patch_upload,
)
from app.api.schemas import UploadPatch
from app.config import Settings
from app.services.auth import SESSION_COOKIE, create_session, create_user
from app.services.database import create_database
from app.services.gemini_content import GeminiContentService
from app.services.source_context import source_excerpts


def settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
        **{"sqlite_path": ":memory:", **overrides},
    )


class SourceContextTests(unittest.TestCase):
    def source(self, text, summary="Wrong summary"):
        return {"id": uuid4(), "title": "Fixture", "extracted_text": text, "summary": summary}

    def test_every_large_selected_source_gets_an_excerpt(self):
        sources = [self.source("A" * 300_000), self.source("B" * 300_000)]
        excerpts = source_excerpts(sources, 250_000)
        self.assertEqual(len(excerpts), 2)
        self.assertEqual([len(item["text"]) for item in excerpts], [125_000, 125_000])
        self.assertTrue(all(item["truncated"] for item in excerpts))

    def test_short_sources_release_budget_and_full_text_wins_over_summary(self):
        sources = [self.source("Start " + "A" * 1000 + " End"), self.source("Detailed fact")]
        excerpts = source_excerpts(sources, 200)
        self.assertEqual(sum(len(item["text"]) for item in excerpts), 200)
        self.assertEqual(excerpts[1]["text"], "Detailed fact")
        self.assertFalse(excerpts[1]["truncated"])
        self.assertTrue(excerpts[0]["text"].startswith("Start "))
        self.assertTrue(excerpts[0]["text"].endswith(" End"))
        self.assertIn("omitted", excerpts[0]["text"])

    def test_small_budget_and_empty_sources_are_bounded(self):
        self.assertEqual(source_excerpts([self.source("", summary="")], 40_000), [])
        self.assertEqual(source_excerpts([self.source("ABC")], 0), [])
        excerpts = source_excerpts([self.source("ABC") for _ in range(3)], 4)
        self.assertEqual(sum(len(item["text"]) for item in excerpts), 4)
        self.assertEqual(len(excerpts), 3)


class PdfExtractionTests(unittest.TestCase):
    def setUp(self):
        self.service = GeminiContentService(None, settings())
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "scanned.pdf"
        writer = PdfWriter()
        for _ in range(12):
            writer.add_blank_page(width=600, height=800)
        writer.write(self.path)

    def test_page_markers_alone_cannot_skip_vision_extraction(self):
        with patch.object(
            self.service, "_generate_from_file", return_value="Scanned facts"
        ) as vision:
            self.assertEqual(
                self.service._extract_text(self.path, "notes", "application/pdf"), "Scanned facts"
            )
        vision.assert_called_once()

    def test_readable_cover_cannot_hide_scanned_pages(self):
        pages = [
            Mock(extract_text=Mock(return_value="Readable cover " * 20)),
            Mock(extract_text=Mock(return_value="")),
        ]
        with (
            patch(
                "app.services.gemini_content.PdfReader", return_value=SimpleNamespace(pages=pages)
            ),
            patch.object(
                self.service, "_generate_from_file", return_value="Cover and scanned pages"
            ) as vision,
        ):
            self.assertIn(
                "scanned pages", self.service._extract_text(self.path, "notes", "application/pdf")
            )
        vision.assert_called_once()

    def test_readable_pdf_retains_native_text_and_page_boundaries(self):
        pages = [Mock(extract_text=Mock(return_value="Detailed original facts " * 10))]
        with (
            patch(
                "app.services.gemini_content.PdfReader", return_value=SimpleNamespace(pages=pages)
            ),
            patch.object(self.service, "_generate_from_file") as vision,
        ):
            text = self.service._extract_text(self.path, "notes", "application/pdf")
        self.assertTrue(text.startswith("[Page 1]\nDetailed original facts"))
        vision.assert_not_called()


class PowerPointImageExtractionTests(unittest.TestCase):
    def setUp(self):
        self.service = GeminiContentService(None, settings())
        self.path = Path("slides.pptx")
        self.text_shape = SimpleNamespace(
            has_text_frame=True,
            text="The sample slide contains useful native text.",
            shape_type=MSO_SHAPE_TYPE.TEXT_BOX,
        )

    def fake_presentation(self, mime_type):
        image_shape = SimpleNamespace(
            shape_type=MSO_SHAPE_TYPE.PICTURE,
            image=SimpleNamespace(blob=b"fixture-image", content_type=mime_type),
        )
        return SimpleNamespace(slides=[SimpleNamespace(shapes=[self.text_shape, image_shape])])

    def test_unsupported_vector_image_is_skipped_without_losing_slide_text(self):
        with (
            patch(
                "app.services.gemini_content.Presentation",
                return_value=self.fake_presentation("image/x-wmf"),
            ),
            patch.object(self.service, "_generate_plain") as describe_images,
        ):
            extracted = self.service._extract_text(self.path, "slides", "application/pptx")

        self.assertIn("The sample slide contains useful native text.", extracted)
        self.assertNotIn("[Slide image descriptions]", extracted)
        describe_images.assert_not_called()

    def test_supported_raster_image_still_gets_a_generated_description(self):
        with (
            patch(
                "app.services.gemini_content.Presentation",
                return_value=self.fake_presentation("image/png"),
            ),
            patch.object(
                self.service, "_generate_plain", return_value="A labeled diagram."
            ) as describe_images,
        ):
            extracted = self.service._extract_text(self.path, "slides", "application/pptx")

        self.assertIn("The sample slide contains useful native text.", extracted)
        self.assertIn("A labeled diagram.", extracted)
        describe_images.assert_called_once()


class ProviderErrorMetadataTests(unittest.TestCase):
    def test_provider_log_fields_keep_status_but_exclude_error_content(self):
        error = RuntimeError("private source text and request data")
        error.code = 400
        error.status = "INVALID_ARGUMENT"

        self.assertEqual(_provider_error_metadata(error), (400, "INVALID_ARGUMENT"))

    def test_provider_log_fields_reject_unbounded_or_unstructured_values(self):
        error = RuntimeError("private source text")
        error.code = "400"
        error.status = "invalid argument: private text"

        self.assertEqual(_provider_error_metadata(error), (None, None))


class SourceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = settings()
        self.db = await create_database(self.settings)
        self.addAsyncCleanup(self.db.close)
        self.user = await create_user(
            self.db, username="review_student", password="fixture-password"
        )
        self.token, _ = await create_session(self.db, self.user["id"], 1)
        self.class_id, self.note_id, self.material_id = uuid4(), uuid4(), uuid4()
        await self.db.execute(
            "INSERT INTO course_classes (id, owner_id, name) VALUES (?1, ?2, 'BIO170')",
            self.class_id,
            self.user["id"],
        )
        await self.db.execute(
            "INSERT INTO course_notes (id, class_id, title, source_type, extracted_text, status, "
            "processing_progress) "
            "VALUES (?1, ?2, 'Notes', 'pasted_text', 'Original facts', 'ready', 100)",
            self.note_id,
            self.class_id,
        )
        self.application = SimpleNamespace(
            state=SimpleNamespace(
                settings=self.settings,
                database=self.db,
                gemini_content_service=object(),
                private_storage=object(),
            )
        )

    def request(self):
        return Request(
            {
                "type": "http",
                "method": "PATCH",
                "path": "/api",
                "scheme": "http",
                "headers": [
                    (b"origin", b"http://localhost:5173"),
                    (b"cookie", f"{SESSION_COOKIE}={self.token}".encode()),
                ],
                "client": ("127.0.0.1", 12345),
                "server": ("localhost", 8000),
                "app": self.application,
            }
        )

    async def saved_material(self):
        await self.db.execute(
            "INSERT INTO study_materials (id, class_id, kind, title, status, content) "
            "VALUES (?1, ?2, 'study_guide', 'Saved guide', 'ready', ?3)",
            self.material_id,
            self.class_id,
            json.dumps({"sections": []}),
        )
        await self.db.execute(
            "INSERT INTO study_material_sources (material_id, class_id, note_id) VALUES (?1, ?2, "
            "?3)",
            self.material_id,
            self.class_id,
            self.note_id,
        )
        await self.db.execute(
            "INSERT INTO study_material_files (material_id, format, content) "
            "VALUES (?1, 'pdf', ?2)",
            self.material_id,
            b"saved PDF",
        )
        await self.db.execute(
            "INSERT INTO study_material_attempts (id, material_id, answers, score_percent) VALUES "
            "(?1, ?2, '{}', 80)",
            uuid4(),
            self.material_id,
        )

    async def test_edit_and_delete_preserve_saved_material_files_and_attempts(self):
        await self.saved_material()
        await patch_upload(
            self.note_id,
            UploadPatch(extracted_text="Revised facts"),
            self.request(),
            BackgroundTasks(),
        )
        detail = await get_study_material(self.material_id, self.request(), Response())
        self.assertEqual(detail.source_warnings, ["changed"])
        await self.db.execute("DELETE FROM course_notes WHERE id = ?1", self.note_id)
        detail = await get_study_material(self.material_id, self.request(), Response())
        self.assertEqual(detail.source_warnings, ["changed", "missing"])
        self.assertEqual(await self.db.fetchval("SELECT count(*) FROM study_material_files"), 1)
        self.assertEqual(await self.db.fetchval("SELECT count(*) FROM study_material_attempts"), 1)
        await self.db.execute("DELETE FROM course_classes WHERE id = ?1", self.class_id)
        self.assertEqual(await self.db.fetchval("SELECT count(*) FROM study_materials"), 0)
        self.assertEqual(await self.db.fetchval("SELECT count(*) FROM study_material_files"), 0)
        self.assertEqual(await self.db.fetchval("SELECT count(*) FROM study_material_attempts"), 0)

    async def test_unchanged_kind_does_not_mark_material_outdated(self):
        await self.saved_material()
        await patch_upload(
            self.note_id, UploadPatch(kind="notes"), self.request(), BackgroundTasks()
        )
        detail = await get_study_material(self.material_id, self.request(), Response())
        self.assertEqual(detail.source_warnings, [])

    async def test_old_queued_jobs_cannot_claim_a_newly_edited_source(self):
        await self.db.execute(
            "UPDATE course_notes SET status = 'processing', extracted_text = 'New edit' "
            "WHERE id = ?1",
            self.note_id,
        )
        process = Mock()
        self.application.state.gemini_content_service = SimpleNamespace(process_text=process)
        await _process_upload_job(self.application, self.note_id, expected_revision=0)
        await _reprocess_upload_job(self.application, self.note_id, True, expected_revision=0)
        process.assert_not_called()
        self.assertEqual(
            await self.db.fetchval("SELECT status FROM course_notes WHERE id = ?1", self.note_id),
            "processing",
        )

    async def test_rename_during_processing_preserves_success_or_failure(self):
        for job_kind in ("upload", "reprocess", "assessment"):
            for fail in (False, True):
                with self.subTest(job=job_kind, fail=fail):
                    await self.db.execute(
                        "UPDATE course_notes SET status = 'processing', failure_code = NULL WHERE "
                        "id = ?1",
                        self.note_id,
                    )
                    started, release = threading.Event(), threading.Event()
                    result = {
                        "extracted_text": "Processed facts",
                        "summary": "Facts",
                        "key_topics": [],
                        "transcript_segments": [],
                        "duration_seconds": None,
                        "syllabus_outline": None,
                    }

                    def process(*args, started=started, release=release, fail=fail, result=result):
                        started.set()
                        if not release.wait(5):
                            raise TimeoutError("Fixture wait expired")
                        if fail:
                            raise ValueError("Synthetic failure")
                        return result

                    self.application.state.gemini_content_service = SimpleNamespace(
                        process_text=process,
                        process_assessment=lambda *args: {
                            "processing_status": "ready",
                            "has_student_responses": False,
                            "questions": [],
                            "grading_mode": "not_graded",
                            "score_percent": None,
                            "grading_result": None,
                            "grade_confidence": None,
                        },
                    )
                    if job_kind == "assessment":
                        assessment_id = uuid4()
                        await self.db.execute(
                            "DELETE FROM uploaded_assessments WHERE note_id = ?1", self.note_id
                        )
                        await self.db.execute(
                            "INSERT INTO uploaded_assessments (id, class_id, note_id) VALUES (?1, "
                            "?2, ?3)",
                            assessment_id,
                            self.class_id,
                            self.note_id,
                        )
                        coro = _process_assessment_job(self.application, assessment_id, True)
                    elif job_kind == "reprocess":
                        coro = _reprocess_upload_job(self.application, self.note_id, True)
                    else:
                        coro = _process_upload_job(self.application, self.note_id)
                    with patch("app.api.class_library.create_default_document", new=AsyncMock()):
                        task = asyncio.create_task(coro)
                        try:
                            self.assertTrue(await asyncio.to_thread(started.wait, 5))
                            revision = await self.db.fetchval(
                                "SELECT processing_revision FROM course_notes WHERE id = ?1",
                                self.note_id,
                            )
                            await patch_upload(
                                self.note_id,
                                UploadPatch(title="Renamed notes"),
                                self.request(),
                                BackgroundTasks(),
                            )
                            self.assertEqual(
                                await self.db.fetchval(
                                    "SELECT processing_revision FROM course_notes WHERE id = ?1",
                                    self.note_id,
                                ),
                                revision,
                            )
                        finally:
                            release.set()
                        await task
                    row = await self.db.fetchrow(
                        "SELECT status, title FROM course_notes WHERE id = ?1", self.note_id
                    )
                    self.assertEqual(
                        row, {"status": "failed" if fail else "ready", "title": "Renamed notes"}
                    )

    async def test_existing_database_migrates_and_removes_destructive_trigger(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            schema = Path("backend/app/services/sqlite_schema.sql").read_text()
            source_index_start = schema.index("-- Immutable source revisions")
            source_index_end = schema.index("CREATE TABLE IF NOT EXISTS study_materials")
            schema = schema[:source_index_start] + schema[source_index_end:]
            schema = "\n".join(
                line
                for line in schema.splitlines()
                if not any(
                    column in line
                    for column in (
                        "processing_revision",
                        "source_index_status",
                        "sources_outdated",
                        "sources_missing",
                        "context_truncated",
                        "latency_median_ms",
                        "latency_p95_ms",
                    )
                )
            )
            with sqlite3.connect(path) as legacy:
                legacy.executescript(
                    schema
                    + """
                    CREATE TRIGGER course_notes_delete_derived_materials
                    BEFORE DELETE ON course_notes
                    BEGIN DELETE FROM study_materials WHERE id IN
                    (SELECT material_id FROM study_material_sources WHERE note_id = OLD.id); END;
                """
                )
            legacy.close()
            upgraded = await create_database(settings(sqlite_path=str(path)))
            try:
                self.assertEqual(
                    await upgraded.fetchval(
                        "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' AND name = "
                        "'course_notes_delete_derived_materials'"
                    ),
                    0,
                )
                await upgraded.execute("SELECT processing_revision FROM course_notes")
                await upgraded.execute(
                    "SELECT sources_outdated, sources_missing, context_truncated FROM "
                    "study_materials"
                )
                await upgraded.execute(
                    "SELECT latency_median_ms, latency_p95_ms FROM quiz_reasoning_metrics"
                )
            finally:
                await upgraded.close()


if __name__ == "__main__":
    unittest.main()
