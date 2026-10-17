"""Integration tests for the generated study-guide quality gate."""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from starlette.requests import Request
from starlette.responses import Response

from app.api.class_library import (
    STUDY_GUIDE_QUALITY_VERSION,
    _create_material_job,
    download_study_file,
    get_study_material,
)
from app.api.errors import ApiError
from app.config import Settings
from app.services.auth import SESSION_COOKIE, create_session, create_user
from app.services.database import create_database
from app.services.gemini_content import ContentGenerationFailure
from app.services.study_files import StudyFileFailure


def settings() -> Settings:
    return Settings(
        _env_file=None,
        sqlite_path=":memory:",
        file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
    )


def guide(upload_id: UUID, *, heading: str = "Vectors") -> dict:
    return {
        "format": "study_guide",
        "schema_version": 2,
        "sections": [
            {
                "heading": heading,
                "key_terms": ["vector"],
                "sources": [{"upload_id": str(upload_id), "page": 1}],
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": "A vector can be resolved into perpendicular components.",
                    }
                ],
            }
        ],
    }


class QualityGateService:
    """Small provider stand-in with inspectable reviewer and repair calls."""

    def __init__(self, upload_id: UUID, initial: dict | None = None) -> None:
        self.upload_id = upload_id
        self.initial = initial or guide(upload_id)
        self.review_results: list[list[dict[str, str]]] = []
        self.repair_results: list[dict] = []
        self.review_calls: list[dict] = []
        self.review_scopes: list[set[int] | None] = []
        self.repair_calls: list[dict] = []

    def generate_material(self, **_kwargs):
        return "Vector guide", deepcopy(self.initial)

    def review_study_guide(
        self,
        content: object,
        _sources: list[dict],
        *,
        _selected_sections: set[int] | None = None,
    ):
        self.review_calls.append(deepcopy(content))
        self.review_scopes.append(
            set(_selected_sections) if _selected_sections is not None else None
        )
        if self.review_results:
            return self.review_results.pop(0)
        return []

    def repair_study_guide(self, content: object, _sources: list[dict], findings: list[dict]):
        self.repair_calls.append({"content": deepcopy(content), "findings": deepcopy(findings)})
        if self.repair_results:
            return deepcopy(self.repair_results.pop(0))
        return deepcopy(content)


class FailingGuideGenerationService(QualityGateService):
    def __init__(self, upload_id: UUID) -> None:
        super().__init__(upload_id)
        self.generation_calls = 0

    def generate_material(self, **_kwargs):
        self.generation_calls += 1
        raise ContentGenerationFailure(
            "A section failed structured validation.",
            code="invalid_model_response",
            diagnostic_code="schema_validation_failed",
        )


class StudyGuideQualityGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.settings = settings()
        self.db = await create_database(self.settings)
        self.addAsyncCleanup(self.db.close)
        self.user = await create_user(
            self.db, username="quality_student", password="fixture-password"
        )
        self.token, _ = await create_session(self.db, self.user["id"], 1)
        self.class_id, self.upload_id, self.material_id = uuid4(), uuid4(), uuid4()
        await self.db.execute(
            "INSERT INTO course_classes (id, owner_id, name) VALUES (?1, ?2, 'ME 301')",
            self.class_id,
            self.user["id"],
        )
        await self.db.execute(
            "INSERT INTO course_notes (id, class_id, kind, title, source_type, extracted_text, "
            "status, processing_progress) VALUES (?1, ?2, 'notes', 'Vector notes', 'pasted_text', "
            "'A vector has perpendicular components.', 'ready', 100)",
            self.upload_id,
            self.class_id,
        )
        self.service = QualityGateService(self.upload_id)
        self.application = SimpleNamespace(
            state=SimpleNamespace(
                settings=self.settings,
                database=self.db,
                gemini_content_service=self.service,
            )
        )

    def request(self) -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
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

    async def add_material(
        self,
        *,
        status: str = "generating",
        content: dict | None = None,
        options: dict | None = None,
    ) -> None:
        await self.db.execute(
            "INSERT INTO study_materials (id, class_id, origin_class_id, resource_scope, "
            "created_by, "
            "publication_state, kind, title, status, content, options) "
            "VALUES (?1, ?2, ?2, 'class_owned', ?3, 'published', 'study_guide', 'Vector guide', "
            "?4, ?5, ?6)",
            self.material_id,
            self.class_id,
            self.user["id"],
            status,
            json.dumps(content) if content is not None else None,
            json.dumps(options or {}),
        )
        await self.db.execute(
            "INSERT INTO study_material_sources (material_id, source_class_id, source_note_id, "
            "source_title, source_kind, position, class_id, note_id) "
            "VALUES (?1, ?2, ?3, 'Vector notes', 'notes', 0, ?2, ?3)",
            self.material_id,
            self.class_id,
            self.upload_id,
        )

    async def insert_cached_exports(
        self, *, pdf: bytes = b"old-pdf", latex: bytes = b"old-latex"
    ) -> None:
        await self.db.executemany(
            "INSERT INTO study_material_files (material_id, format, content) VALUES (?1, ?2, ?3)",
            [(self.material_id, "pdf", pdf), (self.material_id, "latex", latex)],
        )

    def export_patch(self, pdf: bytes = b"verified-pdf", latex: bytes = b"verified-latex"):
        return patch(
            "app.api.class_library.create_guide_files",
            return_value={"pdf": pdf, "latex": latex},
        )

    async def test_clean_generation_persists_validation_version_without_model_review(self) -> None:
        await self.add_material()
        with self.export_patch() as render, patch(
            "app.api.class_library._validate_guide_pdf"
        ) as validate:
            await _create_material_job(self.application, self.material_id)

        row = await self.db.fetchrow(
            "SELECT status, quality_review_version, content FROM study_materials WHERE id=?1",
            self.material_id,
        )
        self.assertEqual(
            (row["status"], row["quality_review_version"]),
            ("ready", STUDY_GUIDE_QUALITY_VERSION),
        )
        saved_guide = json.loads(row["content"])
        self.assertEqual(saved_guide["sections"][0]["heading"], "Vectors")
        self.assertEqual(
            saved_guide["sections"][0]["blocks"][0]["text"],
            "A vector can be resolved into perpendicular components.",
        )
        files = await self.db.fetch(
            "SELECT format, content FROM study_material_files WHERE material_id=?1 ORDER BY format",
            self.material_id,
        )
        self.assertEqual(
            [(item["format"], item["content"]) for item in files],
            [("latex", b"verified-latex"), ("pdf", b"verified-pdf")],
        )
        self.assertEqual(self.service.review_calls, [])
        self.assertEqual(self.service.repair_calls, [])
        self.assertFalse(render.call_args.kwargs["strict_math"])
        validate.assert_called_once()

    async def test_invalid_study_guide_does_not_restart_the_entire_generation(self) -> None:
        self.service = FailingGuideGenerationService(self.upload_id)
        self.application.state.gemini_content_service = self.service
        await self.add_material()

        await _create_material_job(self.application, self.material_id)

        self.assertEqual(self.service.generation_calls, 1)
        row = await self.db.fetchrow(
            "SELECT status, failure_code FROM study_materials WHERE id=?1", self.material_id
        )
        self.assertEqual((row["status"], row["failure_code"]), ("failed", "invalid_model_response"))

    async def test_oversized_guide_fails_deterministically_without_reviewer_or_repair(self) -> None:
        # Two max-length authored blocks per section exceed the current
        # 400,000-character guide limit while remaining valid v2 content.
        verbose_text = ("A source-supported mechanics explanation. " * 300)[:10_000]
        oversized = guide(self.upload_id)
        oversized["sections"] = []
        for index in range(20):
            section = guide(self.upload_id)["sections"][0]
            section["heading"] = f"Topic {index + 1}"
            section["blocks"][0]["text"] = verbose_text
            section["blocks"].append(
                {"type": "paragraph", "text": verbose_text}
            )
            oversized["sections"].append(section)
        self.service.initial = oversized
        await self.add_material()

        with self.export_patch() as render, patch("app.api.class_library._validate_guide_pdf"):
            await _create_material_job(self.application, self.material_id)

        self.assertEqual(self.service.repair_calls, [])
        self.assertEqual(self.service.review_calls, [])
        render.assert_not_called()
        saved = await self.db.fetchrow(
            "SELECT status, failure_code, quality_review_version FROM study_materials WHERE id=?1",
            self.material_id,
        )
        self.assertEqual(
            (saved["status"], saved["failure_code"], saved["quality_review_version"]),
            ("failed", "study_guide_quality_failed", 0),
        )

    async def test_deterministic_heading_finding_does_not_call_reviewer_or_rewrite_content(
        self,
    ) -> None:
        first = guide(self.upload_id, heading="1 of 1: Vector components")
        self.service.initial = first
        await self.add_material()

        with self.export_patch(), patch("app.api.class_library._validate_guide_pdf"):
            await _create_material_job(self.application, self.material_id)

        self.assertEqual(self.service.review_calls, [])
        self.assertEqual(self.service.repair_calls, [])
        self.assertEqual(
            self.service.initial["sections"][0]["heading"],
            "1 of 1: Vector components",
        )
        saved = await self.db.fetchrow(
            "SELECT status, quality_review_version, content FROM study_materials WHERE id=?1",
            self.material_id,
        )
        self.assertEqual(
            (saved["status"], saved["quality_review_version"]),
            ("ready", STUDY_GUIDE_QUALITY_VERSION),
        )
        self.assertEqual(
            json.loads(saved["content"])["sections"][0]["heading"], "1 of 1: Vector components"
        )

    async def test_authored_sections_are_preserved_without_post_generation_edits(self) -> None:
        initial = guide(self.upload_id)
        second = deepcopy(initial["sections"][0])
        second["heading"] = "Stress components"
        initial["sections"].append(second)
        self.service.initial = initial
        await self.add_material()

        with self.export_patch(), patch("app.api.class_library._validate_guide_pdf"):
            await _create_material_job(self.application, self.material_id)

        self.assertEqual(self.service.review_calls, [])
        self.assertEqual(self.service.repair_calls, [])
        saved = await self.db.fetchrow(
            "SELECT status, quality_review_version, content FROM study_materials WHERE id=?1",
            self.material_id,
        )
        self.assertEqual(
            (saved["status"], saved["quality_review_version"]),
            ("ready", STUDY_GUIDE_QUALITY_VERSION),
        )
        saved_sections = json.loads(saved["content"])["sections"]
        self.assertEqual(
            [(section["heading"], section["blocks"][0]["text"]) for section in saved_sections],
            [(section["heading"], section["blocks"][0]["text"]) for section in initial["sections"]],
        )

    async def test_unsupported_math_does_not_block_guide_export(self) -> None:
        self.service.initial["sections"][0]["blocks"][0]["text"] = (
            r"A quantity may be written as $\\sigma_{max}+\\input{bad}$."
        )
        await self.add_material()

        with self.export_patch() as render, patch(
            "app.api.class_library._validate_guide_pdf"
        ):
            await _create_material_job(self.application, self.material_id)

        row = await self.db.fetchrow(
            "SELECT status, content, quality_review_version FROM study_materials WHERE id=?1",
            self.material_id,
        )
        self.assertEqual(
            (row["status"], row["quality_review_version"]),
            ("ready", STUDY_GUIDE_QUALITY_VERSION),
        )
        self.assertIn(r"\\input", json.loads(row["content"])["sections"][0]["blocks"][0]["text"])
        self.assertEqual(self.service.review_calls, [])
        self.assertEqual(self.service.repair_calls, [])
        self.assertFalse(render.call_args.kwargs["strict_math"])

    async def test_legacy_detail_reexports_without_model_review_or_rewrite(self) -> None:
        legacy = guide(self.upload_id, heading="1 of 1: Vector components")
        self.service.initial = legacy
        await self.add_material(status="ready", content=legacy)

        with self.export_patch(), patch("app.api.class_library._validate_guide_pdf"):
            detail = await get_study_material(self.material_id, self.request(), Response())

        self.assertEqual(detail.content["sections"][0]["heading"], "1 of 1: Vector components")
        self.assertEqual(self.service.review_calls, [])
        self.assertEqual(self.service.repair_calls, [])
        saved = await self.db.fetchrow(
            "SELECT quality_review_version FROM study_materials WHERE id=?1", self.material_id
        )
        self.assertEqual(saved["quality_review_version"], STUDY_GUIDE_QUALITY_VERSION)

    async def test_requested_section_count_mismatch_cannot_be_marked_ready(self) -> None:
        extra = guide(self.upload_id, heading="Stress components")["sections"][0]
        initial = guide(self.upload_id)
        initial["sections"].append(extra)
        self.service.initial = initial
        await self.add_material(options={"count": 1})

        with self.export_patch() as render, patch("app.api.class_library._validate_guide_pdf"):
            await _create_material_job(self.application, self.material_id)

        saved = await self.db.fetchrow(
            "SELECT status, failure_code, quality_review_version FROM study_materials WHERE id=?1",
            self.material_id,
        )
        self.assertEqual(
            (saved["status"], saved["failure_code"], saved["quality_review_version"]),
            ("failed", "study_guide_quality_failed", 0),
        )
        render.assert_not_called()

    async def test_legacy_count_mismatch_stays_readable_but_withholds_exports(self) -> None:
        extra = guide(self.upload_id, heading="Stress components")["sections"][0]
        legacy = guide(self.upload_id)
        legacy["sections"].append(extra)
        await self.add_material(status="ready", content=legacy, options={"count": 1})
        await self.insert_cached_exports()

        with self.export_patch() as render, patch("app.api.class_library._validate_guide_pdf"):
            detail = await get_study_material(self.material_id, self.request(), Response())

        self.assertEqual(detail.status, "ready")
        self.assertEqual(len(detail.content["sections"]), 2)
        self.assertEqual(detail.download_urls, {})
        render.assert_not_called()

    async def test_prior_quality_version_is_deterministically_reexported(self) -> None:
        await self.add_material(status="ready", content=guide(self.upload_id))
        await self.db.execute(
            "UPDATE study_materials SET quality_review_version=1 WHERE id=?1",
            self.material_id,
        )

        with self.export_patch(), patch("app.api.class_library._validate_guide_pdf"):
            detail = await get_study_material(self.material_id, self.request(), Response())

        self.assertEqual(detail.content["sections"][0]["heading"], "Vectors")
        self.assertEqual(self.service.review_calls, [])
        self.assertEqual(self.service.repair_calls, [])
        self.assertEqual(
            await self.db.fetchval(
                "SELECT quality_review_version FROM study_materials WHERE id=?1",
                self.material_id,
            ),
            STUDY_GUIDE_QUALITY_VERSION,
        )

    async def test_legacy_download_replaces_cached_export_without_model_review(self) -> None:
        legacy = guide(self.upload_id, heading="1 of 1: Vector components")
        await self.add_material(status="ready", content=legacy)
        await self.insert_cached_exports()

        with (
            self.export_patch(
                pdf=b"newly-verified-pdf", latex=b"newly-verified-latex"
            ) as render,
            patch("app.api.class_library._validate_guide_pdf"),
        ):
            response = await download_study_file(self.material_id, "pdf", self.request())

        self.assertEqual(response.body, b"newly-verified-pdf")
        self.assertEqual(self.service.review_calls, [])
        self.assertEqual(self.service.repair_calls, [])
        self.assertFalse(render.call_args.kwargs["strict_math"])
        persisted = await self.db.fetchrow(
            "SELECT content FROM study_material_files WHERE material_id=?1 AND format='pdf'",
            self.material_id,
        )
        self.assertEqual(persisted["content"], b"newly-verified-pdf")

    async def test_legacy_export_failure_keeps_guide_readable_and_withholds_downloads(self) -> None:
        legacy = guide(self.upload_id)
        await self.add_material(status="ready", content=legacy)
        await self.insert_cached_exports()

        with patch(
            "app.api.class_library.create_guide_files",
            side_effect=StudyFileFailure("export unavailable"),
        ) as render, patch(
            "app.api.class_library._validate_guide_pdf"
        ):
            detail = await get_study_material(self.material_id, self.request(), Response())
            self.assertEqual(detail.status, "ready")
            self.assertEqual(detail.content, legacy)
            self.assertEqual(detail.download_urls, {})
            with self.assertRaises(ApiError) as file_error:
                await download_study_file(self.material_id, "pdf", self.request())
            self.assertEqual(file_error.exception.status_code, 503)

        self.assertEqual(render.call_count, 2)
        cached = await self.db.fetchrow(
            "SELECT content FROM study_material_files WHERE material_id=?1 AND format='pdf'",
            self.material_id,
        )
        self.assertEqual(cached["content"], b"old-pdf")
        version = await self.db.fetchval(
            "SELECT quality_review_version FROM study_materials WHERE id=?1", self.material_id
        )
        self.assertEqual(version, 0)


if __name__ == "__main__":
    unittest.main()
