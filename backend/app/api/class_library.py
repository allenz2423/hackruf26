"""Authenticated class-library, upload, and generated-study endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import unicodedata
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pypdf import PdfReader

from app.api.dependencies import (
    database,
    enforce_sqlite_api_policy,
    private_storage,
)
from app.api.errors import ApiError
from app.api.schemas import (
    AssessmentDetail,
    AssessmentSummary,
    AttemptResult,
    AttemptSubmission,
    ClassCreate,
    ClassPatch,
    ClassSummary,
    MaterialDetail,
    MaterialSummary,
    QuestionAttemptResult,
    QuestionBankGenerationRequest,
    ResourcePublicationUpdate,
    StudyGuideContent,
    StudyMaterialCreate,
    StudyMaterialFlagCreate,
    SyllabusOutline,
    SyllabusOutlineUpdate,
    UploadDetail,
    UploadPatch,
    UploadSummary,
)
from app.api.upload_documents import create_default_document, document_detail
from app.config import Settings
from app.services.ai_usage_ledger import bind_usage_context, reset_usage_context
from app.services.class_access import (
    can_view_assessment_key,
    can_view_unreleased,
    class_access,
    is_class_manager,
    is_effectively_published,
    is_private_assessment,
    material_access,
    require_capability,
    require_resource_mutation,
)
from app.services.database import SQLiteConnection, SQLiteDatabase, json_value
from app.services.gemini_content import (
    MAX_NOTE_CONTEXT_CHARS,
    ContentGenerationFailure,
    GeminiContentService,
)
from app.services.learning_scoring import (
    award_run_completion,
    materialize_released_evidence_for_run,
    record_assessment_outcome,
)
from app.services.question_bank import (
    create_generated_assessment,
    create_validated_private_assessment,
    validate_material_bank_sources,
)
from app.services.source_context import source_excerpts
from app.services.source_index import (
    begin_index_job,
    fetch_authorized_chunk,
    file_sha256,
    make_source_blocks,
    persist_revision,
    process_queued_jobs,
    queue_ready_note_index,
    retry_ready_note_index,
    search_authorized_sources,
)
from app.services.storage import LocalPrivateStorage, UploadTooLarge
from app.services.study_document_quality import MAX_SERIALIZED_BYTES, review_study_guide_quality
from app.services.study_files import StudyFileFailure, compiler_path, create_guide_files

logger = logging.getLogger("screen_tutor.class_library")
router = APIRouter(tags=["class-library"])
STUDY_GUIDE_QUALITY_VERSION = 7


def _provider_error_metadata(exc: Exception) -> tuple[int | None, str | None]:
    """Return safe structured provider status fields without logging exception content."""
    code = getattr(exc, "code", None)
    if type(code) is not int:
        code = None
    status = getattr(exc, "status", None)
    if not isinstance(status, str):
        status = getattr(status, "name", None)
    if not isinstance(status, str) or not re.fullmatch(r"[A-Z0-9_]{1,48}", status):
        status = None
    return code, status


def _study_generation_failure_code(exc: Exception) -> str:
    """Return a stable error identifier without retaining provider or student text."""
    if isinstance(exc, ApiError):
        code = exc.code
    elif isinstance(exc, ContentGenerationFailure):
        code = exc.code
    elif isinstance(exc, TimeoutError) or getattr(exc, "status", None) == "PROVIDER_TIMEOUT":
        code = "provider_timeout"
    else:
        code = "generation_failed"
    return code if re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code) else "generation_failed"

FILE_TYPES: dict[str, tuple[str, str]] = {
    ".pdf": ("application/pdf", "document"),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "document"),
    ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "slides"),
    ".txt": ("text/plain", "document"),
    ".md": ("text/markdown", "document"),
    ".png": ("image/png", "notes"),
    ".jpg": ("image/jpeg", "notes"),
    ".jpeg": ("image/jpeg", "notes"),
    ".mp4": ("video/mp4", "lecture_video"),
    ".webm": ("video/webm", "lecture_video"),
    ".mov": ("video/quicktime", "lecture_video"),
    ".mp3": ("audio/mpeg", "lecture_audio"),
    ".m4a": ("audio/mp4", "lecture_audio"),
    ".wav": ("audio/wav", "lecture_audio"),
}
LECTURE_KINDS = {"lecture_video", "lecture_audio"}
NOTE_KINDS = {"notes", "document", "syllabus", "slides", "lecture_video", "lecture_audio"}
ASSESSMENT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md", ".png", ".jpg", ".jpeg"}


async def _authorize(request: Request) -> tuple[UUID, SQLiteDatabase, Settings]:
    settings: Settings = request.app.state.settings
    user_id = await enforce_sqlite_api_policy(request, settings)
    return user_id, database(request), settings


async def _owned_class(
    database: SQLiteDatabase,
    class_id: UUID,
    user_id: UUID,
    *,
    required: str = "read",
) -> dict[str, Any]:
    """Compatibility name for the shared class authorization boundary."""
    return await class_access(database, class_id, user_id, required=required)


async def _class_summary(
    database: SQLiteDatabase,
    row: dict[str, Any],
    *,
    access: dict[str, Any] | None = None,
) -> ClassSummary:
    access = access or row
    counts = await database.fetchrow(
        "SELECT "
        "(SELECT count(*) FROM course_notes n WHERE n.class_id = ?1 AND "
        "(?3 OR n.created_by = ?2 OR n.publication_state = 'published' OR "
        "(n.publication_state = 'scheduled' AND n.release_at <= strftime('%Y-%m-%dT%H:%M:%fZ','now')))) "
        "AS upload_count, "
        "(SELECT count(*) FROM study_materials m WHERE m.class_id = ?1 AND "
        "(?3 OR m.created_by = ?2 OR m.publication_state = 'published' OR "
        "(m.publication_state = 'scheduled' AND m.release_at <= strftime('%Y-%m-%dT%H:%M:%fZ','now')))) "
        "AS material_count",
        row["id"],
        access.get("user_id", ""),
        int(is_class_manager(access)),
    )
    return ClassSummary(
        id=row["id"],
        name=row["name"],
        color=row["color"],
        icon=row["icon"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        upload_count=counts["upload_count"],
        material_count=counts["material_count"],
        is_owner=bool(access.get("is_owner", False)),
        capabilities=access.get("capabilities", []),
    )


def _upload_summary(row: dict[str, Any]) -> UploadSummary:
    return UploadSummary(
        id=row["id"],
        class_id=row["class_id"],
        title=row["title"],
        kind=row["kind"],
        source_type=row["source_type"],
        original_filename=row["original_filename"],
        mime_type=row["mime_type"],
        file_size_bytes=row["file_size_bytes"],
        duration_seconds=row["duration_seconds"],
        status=row["status"],
        processing_progress=row["processing_progress"],
        failure_code=row["failure_code"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        resource_scope=row.get("resource_scope", "class_owned"),
        created_by=row.get("created_by"),
        publication_state=row.get("publication_state", "published"),
        release_at=row.get("release_at"),
        source_index_status=row.get("source_index_status", "unindexed"),
    )


def _assessment_key_visible(row: dict[str, Any], access: dict[str, Any], user_id: UUID) -> bool:
    return can_view_assessment_key(row, access, user_id)


def _assessment_summary(row: dict[str, Any]) -> AssessmentSummary:
    questions = json_value(row["questions"], [])
    question_count = len(questions) if isinstance(questions, list) else 0
    return AssessmentSummary(
        id=row["id"],
        class_id=row["class_id"],
        note_id=row["note_id"],
        title=row["title"],
        original_filename=row["original_filename"],
        status=row["processing_status"],
        has_student_responses=row["has_student_responses"],
        question_count=question_count,
        grading_mode=row["grading_mode"],
        score_percent=float(row["score_percent"]) if row["score_percent"] is not None else None,
        grade_confidence=(
            float(row["grade_confidence"]) if row["grade_confidence"] is not None else None
        ),
        failure_code=row["failure_code"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        processed_at=row["processed_at"],
    )


async def _reset_assessment_for_processing(
    connection: SQLiteConnection, note_id: UUID
) -> UUID | None:
    row = await connection.fetchrow(
        "UPDATE uploaded_assessments SET processing_status = 'processing', "
        "has_student_responses = false, questions = '[]', grading_mode = 'not_graded', "
        "score_percent = NULL, grading_result = NULL, grade_confidence = NULL, "
        "failure_code = NULL, processed_at = NULL, graded_at = NULL, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
        "WHERE note_id = ?1 RETURNING id",
        note_id,
    )
    return row["id"] if row is not None else None


def _material_item_count(content: object, kind: str, fallback: int = 0) -> int:
    data = json_value(content, {})
    if not isinstance(data, dict):
        return fallback
    key = "questions" if kind in {"quiz", "practice_exam"} else "sections" if kind == "study_guide" else "cards"
    items = data.get(key)
    return len(items) if isinstance(items, list) else fallback


def _requested_guide_section_count(material: dict[str, Any]) -> int | None:
    options = json_value(material.get("options"), {})
    count = options.get("count") if isinstance(options, dict) else None
    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        return count
    return None


def _material_summary(row: dict[str, Any]) -> MaterialSummary:
    values = dict(row)
    latest = None
    policy = row.get("feedback_release_policy", "after_completion")
    feedback_released = bool(values.get("feedback_released"))
    score_allowed = policy == "after_completion" or (policy == "manual" and feedback_released)
    if values.get("latest_score") is not None and score_allowed:
        latest = {"score_percent": values["latest_score"], "completed_at": values["latest_completed_at"]}
    content = json_value(values.get("content"), {})
    return MaterialSummary(
        id=row["id"],
        class_id=row["class_id"],
        kind=row["kind"],
        title=row["title"],
        scope=json_value(row["scope"], {}),
        options=json_value(row["options"], {}),
        status=row["status"],
        item_count=_material_item_count(content, row["kind"], int(values.get("requested_count") or 0)),
        failure_code=row["failure_code"],
        created_at=row["created_at"],
        generated_at=row["generated_at"],
        latest_attempt=latest,
        source_warnings=[
            warning for column, warning in (
                ("sources_outdated", "changed"),
                ("sources_missing", "missing"),
                ("context_truncated", "truncated"),
            ) if values.get(column)
        ],
        origin_class_id=row.get("origin_class_id"),
        resource_scope=row.get("resource_scope", "class_owned"),
        created_by=row.get("created_by"),
        publication_state=row.get("publication_state", "published"),
        release_at=row.get("release_at"),
        feedback_release_policy=row.get("feedback_release_policy", "after_completion"),
    )


def _public_material_content(
    kind: str,
    value: object,
    *,
    visible_source_ids: set[str] | None = None,
    include_assessment_feedback: bool = False,
) -> dict | None:
    content = json_value(value)
    if not isinstance(content, dict):
        return None
    result = json.loads(json.dumps(content))
    if kind in {"quiz", "practice_exam"}:
        questions = result.get("questions", [])
        # Assessment delivery happens through immutable runs, never material detail.
        safe_questions = []
        for question in questions if isinstance(questions, list) else []:
            if not isinstance(question, dict):
                continue
            item = {
                "prompt": str(question.get("prompt", ""))[:5_000],
                "kind": str(question.get("kind", "short_answer")),
            }
            if isinstance(question.get("level"), int):
                item["level"] = question["level"]
            safe_questions.append(item)
        return {
            "format": kind,
            "question_count": len(safe_questions),
            "questions": safe_questions,
        }
    collections = [result.get("questions", []), result.get("sections", []), result.get("cards", [])]
    if visible_source_ids is not None:
        for group in collections:
            for item in group if isinstance(group, list) else []:
                if isinstance(item, dict) and isinstance(item.get("sources"), list):
                    item["sources"] = [
                        source for source in item["sources"]
                        if isinstance(source, dict)
                        and str(source.get("upload_id")) in visible_source_ids
                    ]
                if kind == "study_guide" and isinstance(item, dict):
                    blocks = item.get("blocks", [])
                    if isinstance(blocks, list):
                        for block in blocks:
                            if isinstance(block, dict) and isinstance(block.get("sources"), list):
                                block["sources"] = [
                                    source
                                    for source in block["sources"]
                                    if isinstance(source, dict)
                                    and str(source.get("upload_id")) in visible_source_ids
                                ]
    return result


async def _visible_material_source_ids(
    db: SQLiteDatabase,
    material_id: UUID,
    user_id: UUID,
    material: dict[str, Any],
    access: dict[str, Any] | None,
) -> set[str]:
    rows = await db.fetch(
        "SELECT COALESCE(s.source_note_id,s.note_id) AS note_id, "
        "COALESCE(s.source_class_id,s.class_id) AS class_id,n.created_by, "
        "n.publication_state,n.release_at,n.resource_scope,a.id AS assessment_id,a.owner_id AS assessment_owner,"
        "a.has_student_responses,a.score_percent,a.grading_result,a.answer_key_release_state,"
        "a.answer_key_release_at FROM study_material_sources s "
        "LEFT JOIN course_notes n ON n.id=COALESCE(s.source_note_id,s.note_id) "
        "LEFT JOIN uploaded_assessments a ON a.note_id=n.id "
        "WHERE s.material_id=?1",
        material_id,
    )
    visible: set[str] = set()
    for row in rows:
        if row["note_id"] is None or row["class_id"] is None:
            continue
        if access is None:
            try:
                source_access = await class_access(db, row["class_id"], user_id, required="read")
            except ApiError:
                continue
        elif str(material.get("class_id") or material.get("origin_class_id")) == str(row["class_id"]):
            source_access = access
        else:
            try:
                source_access = await class_access(db, row["class_id"], user_id, required="read")
            except ApiError:
                continue
        if (
            is_effectively_published(row["publication_state"], row["release_at"])
            or can_view_unreleased(source_access, user_id, row["created_by"])
        ):
            if row["assessment_id"] is not None and not can_view_assessment_key(
                {
                    "owner_id": row["assessment_owner"],
                    "created_by": row["created_by"],
                    "resource_scope": row["resource_scope"],
                    "has_student_responses": row["has_student_responses"],
                    "score_percent": row["score_percent"],
                    "grading_result": row["grading_result"],
                    "answer_key_release_state": row["answer_key_release_state"],
                    "answer_key_release_at": row["answer_key_release_at"],
                },
                source_access,
                user_id,
            ):
                continue
            visible.add(str(row["note_id"]))
    return visible


def _guide_content_fatal_quality_code(content: object) -> str | None:
    review = review_study_guide_quality(content)
    fatal_codes = {
        "non_json_content": "quality_guide_not_json",
        "non_finite_number": "quality_guide_non_finite_number",
        "invalid_content": "quality_guide_schema_invalid",
    }
    return next(
        (fatal_codes[issue.code] for issue in review.issues if issue.code in fatal_codes), None
    )


def _validate_guide_pdf(files: dict[str, bytes], content: dict) -> None:
    pdf = files.get("pdf")
    latex = files.get("latex")
    if not pdf or not pdf.startswith(b"%PDF-") or not latex:
        raise StudyFileFailure("Study-guide export is incomplete.")
    try:
        latex_text = latex.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StudyFileFailure("Study-guide LaTeX export is unreadable.") from exc
    if any(
        marker not in latex_text
        for marker in (r"\documentclass", r"\begin{document}", r"\end{document}")
    ):
        raise StudyFileFailure("Study-guide LaTeX export is incomplete.")
    try:
        document = PdfReader(io.BytesIO(pdf), strict=True)
        if not document.pages or len(document.pages) > 500:
            raise StudyFileFailure("Study-guide PDF has an invalid page count.")
        extracted = "\n".join(page.extract_text() or "" for page in document.pages)
        if not extracted.strip():
            raise StudyFileFailure("Study-guide PDF has no readable text.")
    except StudyFileFailure:
        raise
    except Exception as exc:
        raise StudyFileFailure("Study-guide PDF is unreadable.") from exc
    if content.get("format") != "study_guide" or content.get("schema_version") not in {1, 2}:
        raise StudyFileFailure("Study-guide export has an unsupported content version.")
    normalized_pdf_text = "".join(
        character.casefold() for character in extracted if character.isalnum()
    )
    for section in content.get("sections", []):
        heading = str(section.get("heading") or "")
        normalized_heading = "".join(
            character.casefold() for character in heading if character.isalnum()
        )
        if normalized_heading and normalized_heading not in normalized_pdf_text:
            raise StudyFileFailure("Study-guide PDF is missing a section heading.")


def _validate_and_export_study_guide(
    *,
    service: GeminiContentService | None,
    settings: Settings,
    title: str,
    class_name: str,
    material_id: UUID,
    content: object,
    sources: list[dict],
    requested_section_count: int | None = None,
) -> tuple[dict, dict[str, bytes]]:
    """Validate the authored structure and export it without a reviewer-model pass."""
    try:
        parsed = StudyGuideContent.model_validate(content)
        normalized = parsed.model_dump(mode="json", exclude_none=True)
    except Exception as exc:
        raise ContentGenerationFailure(
            "The study guide does not match the supported structured format.",
            code="invalid_model_response",
            diagnostic_code="quality_guide_schema_invalid",
        ) from exc

    if parsed.schema_version == 2 and service is not None:
        normalizer = getattr(service, "normalize_study_guide", None)
        if callable(normalizer):
            # This operation assigns IDs, inherits citations, and derives fallback text;
            # it does not make a provider request or change authored claims.
            normalized = normalizer(normalized, sources)

    # The requested count is part of the saved material contract. Validate it at
    # the finalization boundary as well as during drafting so malformed or older
    # generation paths cannot mark an overlong/short guide ready.
    if (
        isinstance(requested_section_count, int)
        and not isinstance(requested_section_count, bool)
        and requested_section_count > 0
        and len(normalized.get("sections", [])) != requested_section_count
    ):
        raise ContentGenerationFailure(
            "The study guide does not contain the requested number of sections.",
            code="study_guide_quality_failed",
            diagnostic_code="guide_section_count_mismatch",
            validation_issues=[
                {
                    "location": "sections",
                    "type": "section_count_mismatch",
                    "expected": requested_section_count,
                    "actual": len(normalized.get("sections", [])),
                }
            ],
        )

    deterministic = review_study_guide_quality(normalized)
    size_issues = [
        issue
        for issue in deterministic.issues
        if issue.code in {"serialized_size_exceeded", "text_size_exceeded"}
    ]
    if size_issues:
        raise ContentGenerationFailure(
            "The study guide is larger than the supported document limits.",
            code="study_guide_quality_failed",
            diagnostic_code="guide_size_limits",
            validation_issues=[
                {"location": issue.location or "guide", "type": issue.code}
                for issue in size_issues[:8]
            ],
        )

    fatal_code = _guide_content_fatal_quality_code(normalized)
    if fatal_code:
        raise ContentGenerationFailure(
            "The study guide could not be checked in its structured format.",
            code="invalid_model_response",
            diagnostic_code=fatal_code,
        )

    try:
        files = create_guide_files(
            settings,
            # The renderer typesets safe expressions and leaves malformed or unsupported
            # notation readable as escaped text. A math typo must not discard the guide.
            strict_math=False,
            title=title,
            class_name=class_name,
            material_id=material_id,
            content=normalized,
            sources=sources,
        )
        _validate_guide_pdf(files, normalized)
    except StudyFileFailure as exc:
        raise ContentGenerationFailure(
            "The study guide could not be exported as a readable document.",
            code="study_guide_quality_failed",
            diagnostic_code="study_guide_export_failed",
        ) from exc
    return normalized, files


async def _ensure_study_guide_quality(
    application: Any,
    database: SQLiteDatabase,
    material_id: UUID,
    user_id: UUID,
    material: dict[str, Any],
    access: dict[str, Any] | None,
    *,
    row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministically revalidate and re-export guides created by older pipelines."""
    if (
        material.get("kind") != "study_guide"
        or material.get("status") != "ready"
        or int(material.get("quality_review_version") or 0) >= STUDY_GUIDE_QUALITY_VERSION
    ):
        return row or material

    service: GeminiContentService | None = getattr(
        application.state, "gemini_content_service", None
    )
    settings: Settings | None = getattr(application.state, "settings", None)
    if settings is None:
        raise ApiError(
            503,
            "study_guide_quality_unavailable",
            "This study guide cannot be exported yet. Try again shortly.",
        )

    try:
        source_ids = await _visible_material_source_ids(
            database, material_id, user_id, material, access
        )
        source_rows = await database.fetch(
            "SELECT COALESCE(n.id,s.source_note_id,s.note_id) AS id,"
            "COALESCE(n.title,s.source_title) AS title,COALESCE(n.kind,s.source_kind) AS kind "
            "FROM study_material_sources s "
            "LEFT JOIN course_notes n ON n.id=COALESCE(s.source_note_id,s.note_id) "
            "WHERE s.material_id=?1 ORDER BY s.position",
            material_id,
        )
        sources = [
            source
            for source in source_rows
            if source.get("id") is not None and str(source["id"]) in source_ids
        ]
        class_name = await database.fetchval(
            "SELECT name FROM course_classes WHERE id=COALESCE(?1,?2)",
            material.get("class_id"),
            material.get("origin_class_id"),
        )
        old_content = material.get("content")
        content = json_value(old_content, {})
        normalized, files = await asyncio.to_thread(
            _validate_and_export_study_guide,
            service=service,
            settings=settings,
            title=str(material.get("title") or "Study guide"),
            class_name=str(class_name or "Personal study materials"),
            material_id=material_id,
            content=content,
            sources=sources,
            requested_section_count=_requested_guide_section_count(material),
        )
        async with database.acquire() as connection:
            async with connection.transaction():
                updated = await connection.fetchrow(
                    "UPDATE study_materials SET content=?2,quality_review_version=?3,"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                    "WHERE id=?1 AND status='ready' AND quality_review_version<?3 "
                    "AND content=?4 RETURNING id",
                    material_id,
                    json.dumps(normalized),
                    STUDY_GUIDE_QUALITY_VERSION,
                    old_content,
                )
                if updated:
                    await connection.execute(
                        "DELETE FROM study_material_files WHERE material_id=?1", material_id
                    )
                    await connection.executemany(
                        "INSERT INTO study_material_files (material_id,format,content) "
                        "VALUES (?1,?2,?3)",
                        [(material_id, format, data) for format, data in files.items()],
                    )
        if not updated:
            latest = await database.fetchrow(
                "SELECT * FROM study_materials WHERE id=?1", material_id
            )
            if (
                latest is None
                or latest.get("status") != "ready"
                or int(latest.get("quality_review_version") or 0)
                < STUDY_GUIDE_QUALITY_VERSION
            ):
                raise ContentGenerationFailure(
                    "The guide changed during deterministic validation.",
                    code="study_guide_quality_failed",
                    diagnostic_code="quality_validation_compare_and_swap_failed",
                )
        return await database.fetchrow(
            "SELECT m.*,c.name AS class_name FROM study_materials m "
            "LEFT JOIN course_classes c ON c.id=COALESCE(m.class_id,m.origin_class_id) "
            "WHERE m.id=?1",
            material_id,
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.warning(
            "study_guide_validation_failed material_id=%s error_code=%s",
            material_id,
            _study_generation_failure_code(exc),
        )
        raise ApiError(
            503,
            "study_guide_quality_unavailable",
            "This study guide did not pass document validation or export. Try again later.",
        ) from exc


def _infer_kind(filename: str, extension: str, default_kind: str) -> str:
    if "syllabus" in filename.casefold():
        return "syllabus"
    return default_kind


def _validate_upload_kind(extension: str, kind: str) -> None:
    if extension not in FILE_TYPES:
        raise ApiError(415, "unsupported_file_type", "This file type is not supported.")
    if kind not in NOTE_KINDS:
        raise ApiError(400, "invalid_upload_kind", "This upload kind is not supported.")
    default_kind = FILE_TYPES[extension][1]
    if extension in {".mp4", ".webm", ".mov"} and kind != "lecture_video":
        raise ApiError(400, "invalid_upload_kind", "Video files must use the lecture_video kind.")
    if extension in {".mp3", ".m4a", ".wav"} and kind != "lecture_audio":
        raise ApiError(400, "invalid_upload_kind", "Audio files must use the lecture_audio kind.")
    if extension not in {".mp4", ".webm", ".mov", ".mp3", ".m4a", ".wav"} and kind in LECTURE_KINDS:
        raise ApiError(400, "invalid_upload_kind", "Only audio and video files can be lectures.")
    if kind == "slides" and extension not in {".pptx", ".pdf"}:
        raise ApiError(400, "invalid_upload_kind", "Slides must be uploaded as a PPTX or PDF.")
    if kind == "notes" and default_kind == "document" and extension not in {".pdf", ".docx", ".pptx", ".txt", ".md"}:
        raise ApiError(400, "invalid_upload_kind", "This file type cannot be uploaded as notes.")


def _validate_file_signature(path: Path, extension: str) -> bool:
    with path.open("rb") as source:
        head = source.read(32)
    if extension == ".pdf":
        return head.startswith(b"%PDF-")
    if extension == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in {".jpg", ".jpeg"}:
        return head.startswith(b"\xff\xd8\xff")
    if extension in {".docx", ".pptx"}:
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
            return "word/document.xml" in names if extension == ".docx" else "ppt/presentation.xml" in names
        except (OSError, zipfile.BadZipFile):
            return False
    if extension in {".txt", ".md"}:
        return b"\x00" not in head
    if extension in {".mp4", ".mov", ".m4a"}:
        return head[4:8] == b"ftyp"
    if extension == ".webm":
        return head.startswith(b"\x1aE\xdf\xa3")
    if extension == ".wav":
        return head.startswith(b"RIFF") and head[8:12] == b"WAVE"
    if extension == ".mp3":
        return head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)
    return False


async def _rate_limit(request: Request, key: str, user_id: UUID) -> None:
    limiter = getattr(request.app.state, key)
    retry_after = await limiter.check(str(user_id))
    if retry_after is not None:
        raise ApiError(429, f"{key}_rate_limited", "Too many requests. Please wait before trying again.", retry_after)


@router.get("/api/classes")
async def list_classes(request: Request) -> dict[str, list[ClassSummary]]:
    user_id, database, _ = await _authorize(request)
    rows = await database.fetch(
        "SELECT c.id,c.name,c.color,c.icon,c.created_at,c.updated_at FROM course_classes c "
        "WHERE c.owner_id=?1 OR EXISTS (SELECT 1 FROM class_memberships mm "
        "WHERE mm.class_id=c.id AND mm.user_id=?1 AND mm.status='active') "
        "ORDER BY c.updated_at DESC,c.id",
        user_id,
    )
    items = []
    for row in rows:
        access = await class_access(database, row["id"], user_id)
        access["user_id"] = user_id
        items.append(await _class_summary(database, row, access=access))
    return {"items": items}


@router.post("/api/classes", status_code=201)
async def create_class(payload: ClassCreate, request: Request) -> ClassSummary:
    user_id, database, settings = await _authorize(request)
    name = payload.name.strip()
    if not name or len(name) > settings.max_class_name_length:
        raise ApiError(400, "invalid_class_name", "Enter a class name within the allowed length.")
    class_id = uuid4()
    async with database.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(
                "INSERT INTO course_classes (id, owner_id, name, color, icon) "
                "VALUES (?1, ?2, ?3, ?4, ?5) "
                "RETURNING id, name, color, icon, created_at, updated_at",
                class_id, user_id, name, payload.color, payload.icon,
            )
            await connection.execute(
                "INSERT INTO class_memberships (class_id,user_id,status,added_by) "
                "VALUES (?1,?2,'active',?2)", class_id, user_id,
            )
            await connection.executemany(
                "INSERT INTO class_membership_capabilities "
                "(class_id,user_id,capability,granted_by) VALUES (?1,?2,?3,?2)",
                [(str(class_id), str(user_id), capability) for capability in (
                    "read", "create", "edit", "delete", "delete_others", "invite",
                    "manage_members", "manage_permissions"
                )],
            )
    access = await class_access(database, class_id, user_id)
    access["user_id"] = user_id
    return await _class_summary(database, row, access=access)


@router.patch("/api/classes/{class_id}")
async def update_class(class_id: UUID, payload: ClassPatch, request: Request) -> ClassSummary:
    user_id, database, settings = await _authorize(request)
    access = await _owned_class(database, class_id, user_id, required="manage_permissions")
    changes: dict[str, Any] = {}
    if "name" in payload.model_fields_set:
        name = (payload.name or "").strip()
        if not name or len(name) > settings.max_class_name_length:
            raise ApiError(400, "invalid_class_name", "Enter a class name within the allowed length.")
        changes["name"] = name
    # Sending null for color or icon resets it to the default look.
    for column in ("color", "icon"):
        if column in payload.model_fields_set:
            changes[column] = getattr(payload, column)
    if not changes:
        raise ApiError(400, "invalid_request", "Nothing to update.")

    # Column names come from the fixed list above, never from the request.
    assignments = ", ".join(f"{column} = ?{index}" for index, column in enumerate(changes, start=2))
    row = await database.fetchrow(
        f"UPDATE course_classes SET {assignments}, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
        "WHERE id = ?1 "
        "RETURNING id, name, color, icon, created_at, updated_at",
        class_id,
        *changes.values(),
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    access["user_id"] = user_id
    return await _class_summary(database, row, access=access)


@router.delete("/api/classes/{class_id}", status_code=204)
async def delete_class(class_id: UUID, request: Request) -> Response:
    user_id, database, _ = await _authorize(request)
    storage = private_storage(request)
    async with database.acquire() as connection:
        async with connection.transaction():
            rows = await connection.fetch(
                "SELECT n.storage_key FROM course_notes n "
                "JOIN course_classes c ON c.id = n.class_id "
                "WHERE c.id = ?1 AND c.owner_id = ?2 AND n.storage_key IS NOT NULL",
                class_id,
                user_id,
            )
            result = await connection.execute(
                "DELETE FROM course_classes WHERE id = ?1 AND owner_id = ?2",
                class_id,
                user_id,
            )
            if result == "DELETE 0":
                raise ApiError(404, "not_found", "The requested resource was not found.")
    await request.app.state.live_session_ticket_store.revoke_class(class_id)
    for row in rows:
        storage.delete(row["storage_key"])
    return Response(status_code=204)


@router.post("/api/classes/{class_id}/uploads", status_code=202)
async def create_upload(
    class_id: UUID,
    background_tasks: BackgroundTasks,
    request: Request,
    file: Annotated[UploadFile | None, File()] = None,
    text: str | None = Form(default=None),
    title: str | None = Form(default=None),
    kind: str | None = Form(default=None),
) -> UploadSummary:
    user_id, database, settings = await _authorize(request)
    if getattr(request.app.state, "gemini_content_service", None) is None:
        raise ApiError(503, "content_processing_unavailable", "Course material processing is unavailable.")
    access = await _owned_class(database, class_id, user_id, required="create")
    await _rate_limit(request, "upload_rate_limiter", user_id)
    upload_count = await database.fetchval(
        "SELECT count(*) FROM course_notes WHERE class_id = ?1",
        class_id,
    )
    if upload_count >= settings.max_uploads_per_class:
        raise ApiError(409, "class_upload_limit_reached", "This class has reached its upload limit.")
    if (file is None) == (text is None):
        raise ApiError(400, "invalid_upload", "Send exactly one file or pasted text.")

    storage_key: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    extracted_text: str | None = None
    source_type = "file"
    if file is not None:
        filename = Path((file.filename or "").replace("\\", "/")).name.strip()
        extension = Path(filename).suffix.casefold()
        info = FILE_TYPES.get(extension)
        if not filename or info is None:
            await file.close()
            raise ApiError(415, "unsupported_file_type", "This file type is not supported.")
        mime_type, default_kind = info
        chosen_kind = kind or _infer_kind(filename, extension, default_kind)
        _validate_upload_kind(extension, chosen_kind)
        storage = private_storage(request)
        try:
            storage_key, file_size = await storage.save_upload(file, settings.max_upload_bytes)
        except UploadTooLarge:
            raise ApiError(413, "upload_too_large", "This file exceeds the configured upload limit.") from None
        except ValueError:
            raise ApiError(400, "invalid_upload", "The uploaded file is empty or invalid.") from None
        path = storage.path_for(storage_key)
        if not _validate_file_signature(path, extension):
            storage.delete(storage_key)
            raise ApiError(415, "invalid_file_content", "The file content does not match its file type.")
        note_title = (title or filename).strip()
    else:
        source_type = "pasted_text"
        if not text or not text.strip() or len(text) > 1_000_000:
            raise ApiError(400, "invalid_upload_text", "Pasted text must contain 1 to 1,000,000 characters.")
        chosen_kind = kind or "notes"
        if chosen_kind not in NOTE_KINDS:
            raise ApiError(400, "invalid_upload_kind", "This upload kind is not supported.")
        if chosen_kind in LECTURE_KINDS or chosen_kind == "slides":
            raise ApiError(400, "invalid_upload_kind", "Pasted text cannot be a lecture or slide deck.")
        note_title = (title or "Pasted notes").strip()
        if not note_title:
            raise ApiError(400, "invalid_upload_title", "Enter a title for the pasted notes.")
        extracted_text = text.strip()

    if not note_title or len(note_title) > 255:
        if storage_key:
            private_storage(request).delete(storage_key)
        raise ApiError(400, "invalid_upload_title", "Enter a title of 1 to 255 characters.")

    try:
        manager = is_class_manager(access)
        row = await database.fetchrow(
            "INSERT INTO course_notes "
            "(id, class_id, resource_scope, created_by, publication_state, published_at, "
            "title, kind, source_type, original_filename, mime_type, storage_key, "
            "file_size_bytes, extracted_text, status, processing_progress) "
            "VALUES (?1, ?2, ?3, ?4, ?5, CASE WHEN ?5='published' THEN "
            "strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE NULL END, "
            "?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, 'processing', 0) "
            "RETURNING *",
            uuid4(),
            class_id,
            "class_owned" if manager else "student_contribution",
            user_id,
            "published" if manager else "draft",
            note_title,
            chosen_kind,
            source_type,
            filename,
            mime_type,
            storage_key,
            file_size,
            extracted_text,
        )
    except Exception:
        if storage_key:
            private_storage(request).delete(storage_key)
        raise
    background_tasks.add_task(_process_upload_job, request.app, row["id"], row["processing_revision"])
    return _upload_summary(row)


@router.post("/api/classes/{class_id}/assessments", status_code=202)
async def create_assessment(
    class_id: UUID,
    background_tasks: BackgroundTasks,
    request: Request,
    file: Annotated[UploadFile | None, File()] = None,
    title: Annotated[str | None, Form()] = None,
) -> AssessmentSummary:
    """Store one private existing test and queue extraction/grading."""
    user_id, database, settings = await _authorize(request)
    if getattr(request.app.state, "gemini_content_service", None) is None:
        raise ApiError(
            503,
            "content_processing_unavailable",
            "Assessment processing is unavailable.",
        )
    access = await _owned_class(database, class_id, user_id, required="create")
    await _rate_limit(request, "upload_rate_limiter", user_id)
    await _rate_limit(request, "generation_rate_limiter", user_id)
    upload_count = await database.fetchval(
        "SELECT count(*) FROM course_notes WHERE class_id = ?1",
        class_id,
    )
    if upload_count >= settings.max_uploads_per_class:
        raise ApiError(
            409,
            "class_upload_limit_reached",
            "This class has reached its upload limit.",
        )
    if file is None:
        raise ApiError(400, "invalid_assessment_upload", "Send one existing test file.")

    filename = Path((file.filename or "").replace("\\", "/")).name.strip()
    extension = Path(filename).suffix.casefold()
    if not filename or extension not in ASSESSMENT_EXTENSIONS:
        await file.close()
        raise ApiError(
            415,
            "unsupported_assessment_file_type",
            "Existing tests must be PDF, DOCX, PPTX, TXT, Markdown, PNG, or JPEG files.",
        )
    mime_type, note_kind = FILE_TYPES[extension]
    note_title = (title if title is not None else filename).strip()
    if not note_title or len(note_title) > 255:
        await file.close()
        raise ApiError(400, "invalid_upload_title", "Enter a title of 1 to 255 characters.")

    storage = private_storage(request)
    try:
        storage_key, file_size = await storage.save_upload(file, settings.max_upload_bytes)
    except UploadTooLarge:
        raise ApiError(
            413,
            "upload_too_large",
            "This file exceeds the configured upload limit.",
        ) from None
    except ValueError:
        raise ApiError(400, "invalid_upload", "The uploaded file is empty or invalid.") from None
    if not _validate_file_signature(storage.path_for(storage_key), extension):
        storage.delete(storage_key)
        raise ApiError(
            415,
            "invalid_file_content",
            "The file content does not match its file type.",
        )

    try:
        async with database.acquire() as connection:
            async with connection.transaction():
                manager = is_class_manager(access)
                note = await connection.fetchrow(
                    "INSERT INTO course_notes "
                    "(id, class_id, resource_scope, created_by, publication_state, published_at, "
                    "title, kind, source_type, original_filename, mime_type, storage_key, "
                    "file_size_bytes, status, processing_progress) "
                    "VALUES (?1, ?2, ?3, ?4, ?5, CASE WHEN ?5='published' THEN "
                    "strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE NULL END, "
                    "?6, ?7, 'file', ?8, ?9, ?10, ?11, "
                    "'processing', 0) "
                    "RETURNING id, processing_revision",
                    uuid4(),
                    class_id,
                    "class_owned" if manager else "student_contribution",
                    user_id,
                    "published" if manager else "draft",
                    note_title,
                    note_kind,
                    filename,
                    mime_type,
                    storage_key,
                    file_size,
                )
                assessment = await connection.fetchrow(
                    "INSERT INTO uploaded_assessments (id, class_id, note_id, owner_id, "
                    "answer_key_release_state) VALUES (?1, ?2, ?3, ?4, 'draft') RETURNING *",
                    uuid4(),
                    class_id,
                    note["id"],
                    user_id,
                )
    except Exception:
        storage.delete(storage_key)
        raise

    background_tasks.add_task(
        _process_assessment_job, request.app, assessment["id"], False, note["processing_revision"]
    )
    return AssessmentSummary(
        id=assessment["id"],
        class_id=assessment["class_id"],
        note_id=assessment["note_id"],
        title=note_title,
        original_filename=filename,
        status="processing",
        has_student_responses=False,
        question_count=0,
        grading_mode="not_graded",
        score_percent=None,
        grade_confidence=None,
        failure_code=None,
        created_at=assessment["created_at"],
        updated_at=assessment["updated_at"],
        processed_at=None,
    )


@router.get("/api/classes/{class_id}/assessments")
async def list_assessments(
    class_id: UUID, request: Request
) -> dict[str, list[AssessmentSummary]]:
    user_id, database, _ = await _authorize(request)
    access = await _owned_class(database, class_id, user_id)
    rows = await database.fetch(
        "SELECT a.*,n.title,n.original_filename,n.resource_scope,n.created_by FROM uploaded_assessments a "
        "JOIN course_notes n ON n.id = a.note_id "
        "WHERE a.class_id = ?1 ORDER BY a.created_at DESC, a.id",
        class_id,
    )
    visible = []
    for row in rows:
        note = await database.fetchrow(
            "SELECT created_by,publication_state,release_at FROM course_notes WHERE id=?1", row["note_id"]
        )
        if note is None or (
            not is_effectively_published(note["publication_state"], note["release_at"])
            and not can_view_unreleased(access, user_id, note["created_by"])
        ):
            continue
        if is_private_assessment(row) and str(row["owner_id"]) != str(user_id):
            continue
        safe_row = dict(row)
        if str(row["owner_id"]) != str(user_id):
            safe_row.update(score_percent=None, grade_confidence=None)
        visible.append(_assessment_summary(safe_row))
    return {"items": visible}


@router.get("/api/assessments/{assessment_id}")
async def get_assessment(assessment_id: UUID, request: Request) -> AssessmentDetail:
    user_id, database, _ = await _authorize(request)
    row = await database.fetchrow(
        "SELECT a.*, n.title, n.original_filename, n.storage_key,n.created_by AS note_created_by, "
        "n.publication_state,n.release_at,n.resource_scope "
        "FROM uploaded_assessments a "
        "JOIN course_notes n ON n.id = a.note_id AND n.class_id = a.class_id "
        "WHERE a.id = ?1",
        assessment_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    access = await _owned_class(database, row["class_id"], user_id)
    if (
        not is_effectively_published(row["publication_state"], row["release_at"])
        and not can_view_unreleased(access, user_id, row["note_created_by"])
    ):
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if is_private_assessment(row) and str(row["owner_id"]) != str(user_id):
        raise ApiError(404, "not_found", "The requested resource was not found.")

    questions = json_value(row["questions"], [])
    if not isinstance(questions, list):
        questions = []
    grading_result = json_value(row["grading_result"])
    if not isinstance(grading_result, dict):
        grading_result = None
    has_key_access = _assessment_key_visible(
        {**row, "created_by": row["note_created_by"]}, access, user_id
    )
    if not has_key_access:
        questions = [
            {
                **question,
                "answer_key": None,
                "answer_key_status": "missing",
                "student_response": None,
                "response_status": "blank",
            }
            for question in questions if isinstance(question, dict)
        ]
        grading_result = None
    file_url, expires = (None, None)
    if has_key_access:
        file_url, expires = private_storage(request).signed_url(
            str(request.base_url).rstrip("/"), row["note_id"]
        )
    return AssessmentDetail(
        **_assessment_summary({
            **row,
            "score_percent": row["score_percent"] if str(row["owner_id"]) == str(user_id) else None,
            "grade_confidence": row["grade_confidence"] if str(row["owner_id"]) == str(user_id) else None,
        }).model_dump(),
        questions=questions,
        grading_result=grading_result,
        review_reason=(grading_result or {}).get("review_reason"),
        file_url=file_url,
        file_url_expires_at=datetime.fromtimestamp(expires, UTC) if expires is not None else None,
    )


@router.get("/api/classes/{class_id}/uploads")
async def list_uploads(class_id: UUID, request: Request) -> dict[str, list[UploadSummary]]:
    user_id, database, _ = await _authorize(request)
    access = await _owned_class(database, class_id, user_id)
    rows = await database.fetch(
        "SELECT n.*,a.owner_id AS assessment_owner,a.has_student_responses,a.score_percent AS assessment_score,"
        "a.grading_result AS assessment_result FROM course_notes n "
        "LEFT JOIN uploaded_assessments a ON a.note_id=n.id "
        "WHERE n.class_id = ?1 ORDER BY n.created_at DESC, n.id",
        class_id,
    )
    return {"items": [
        _upload_summary(row) for row in rows
        if not is_private_assessment({
            "has_student_responses": row.get("has_student_responses"),
            "score_percent": row.get("assessment_score"),
            "grading_result": row.get("assessment_result"),
            "owner_id": row.get("assessment_owner"),
        }) or str(row.get("assessment_owner")) == str(user_id)
        if is_effectively_published(row["publication_state"], row["release_at"])
        or can_view_unreleased(access, user_id, row["created_by"])
    ]}


@router.get("/api/classes/{class_id}/sources/search")
async def search_class_sources(
    class_id: UUID,
    request: Request,
    response: Response,
    query: str = Query(min_length=1, max_length=500),
    upload_id: UUID | None = None,
    limit: int = Query(default=8, ge=1, le=20),
) -> dict[str, list[dict[str, Any]]]:
    user_id, database, _ = await _authorize(request)
    await _rate_limit(request, "source_search_rate_limiter", user_id)
    response.headers["Cache-Control"] = "private, no-store"
    results = await search_authorized_sources(
        database, class_id=str(class_id), user_id=str(user_id), query=query,
        upload_id=str(upload_id) if upload_id else None, limit=limit,
    )
    # Search results contain excerpts of class material and must not enter shared caches.
    return results


@router.get("/api/classes/{class_id}/sources/chunks/{chunk_revision_id}")
async def get_class_source_chunk(
    class_id: UUID,
    chunk_revision_id: UUID,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    user_id, database, _ = await _authorize(request)
    await _rate_limit(request, "source_search_rate_limiter", user_id)
    result = await fetch_authorized_chunk(
        database, class_id=str(class_id), user_id=str(user_id),
        chunk_revision_id=str(chunk_revision_id),
    )
    response.headers["Cache-Control"] = "private, no-store"
    return result


@router.get("/api/uploads/{upload_id}")
async def get_upload(upload_id: UUID, request: Request) -> UploadDetail:
    user_id, database, _ = await _authorize(request)
    row = await database.fetchrow(
        "SELECT n.*,a.owner_id AS assessment_owner,a.has_student_responses,a.score_percent AS assessment_score,"
        "a.grading_result AS assessment_result,a.answer_key_release_state,a.answer_key_release_at,n.resource_scope "
        "FROM course_notes n LEFT JOIN uploaded_assessments a ON a.note_id=n.id WHERE n.id = ?1",
        upload_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    access = await _owned_class(database, row["class_id"], user_id)
    if is_private_assessment({
        "has_student_responses": row.get("has_student_responses"),
        "score_percent": row.get("assessment_score"),
        "grading_result": row.get("assessment_result"),
        "owner_id": row.get("assessment_owner"),
    }) and str(row.get("assessment_owner")) != str(user_id):
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if (
        not is_effectively_published(row["publication_state"], row["release_at"])
        and not can_view_unreleased(access, user_id, row["created_by"])
    ):
        raise ApiError(404, "not_found", "The requested resource was not found.")
    assessment = await database.fetchrow(
        "SELECT a.id,a.owner_id,a.has_student_responses,a.score_percent,a.grading_result,"
        "a.answer_key_release_state,a.answer_key_release_at,"
        "n.created_by,n.resource_scope FROM uploaded_assessments a JOIN course_notes n ON n.id=a.note_id "
        "WHERE a.note_id=?1",
        upload_id,
    )
    key_visible = assessment is None or _assessment_key_visible(assessment, access, user_id)
    file_url = None
    expires_at = None
    if row["storage_key"] and key_visible:
        storage = private_storage(request)
        file_url, expires = storage.signed_url(str(request.base_url).rstrip("/"), upload_id)
        expires_at = datetime.fromtimestamp(expires, UTC)
    return UploadDetail(
        **_upload_summary(row).model_dump(),
        extracted_text=row["extracted_text"] if key_visible else None,
        summary=row["summary"] if key_visible else None,
        key_topics=json_value(row["key_topics"], []) if key_visible else [],
        transcript_segments=json_value(row["transcript_segments"], []) if key_visible else [],
        syllabus_outline=json_value(row["syllabus_outline"]) if key_visible else None,
        file_url=file_url,
        file_url_expires_at=expires_at,
        document=await document_detail(database, upload_id) if key_visible else None,
    )


@router.get("/api/uploads/{upload_id}/file", name="download_upload_file")
async def download_upload_file(
    upload_id: UUID,
    request: Request,
    expires: int,
    signature: str,
) -> FileResponse:
    storage = private_storage(request)
    if not storage.verify_url(upload_id, expires, signature):
        raise ApiError(403, "file_link_expired", "This file link is invalid or has expired.")
    user_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db = database(request)
    row = await db.fetchrow(
        "SELECT n.storage_key,n.original_filename,n.mime_type,n.class_id,n.created_by,"
        "n.publication_state,n.release_at,n.resource_scope,a.id AS assessment_id,a.owner_id AS assessment_owner,"
        "a.answer_key_release_state,a.answer_key_release_at,a.has_student_responses,a.score_percent,a.grading_result "
        "FROM course_notes n LEFT JOIN uploaded_assessments a ON a.note_id=n.id WHERE n.id=?1",
        upload_id,
    )
    if row is None or not row["storage_key"]:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    access = await _owned_class(db, row["class_id"], user_id)
    if is_private_assessment(row) and str(row["assessment_owner"]) != str(user_id):
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if (
        not is_effectively_published(row["publication_state"], row["release_at"])
        and not can_view_unreleased(access, user_id, row["created_by"])
    ):
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if row["assessment_id"] is not None and not can_view_assessment_key(row, access, user_id):
        raise ApiError(404, "not_found", "The requested resource was not found.")
    path = storage.path_for(row["storage_key"])
    if not path.is_file():
        raise ApiError(404, "not_found", "The requested resource was not found.")
    filename = Path(row["original_filename"] or "course-material").name.replace("\r", "").replace("\n", "")
    return FileResponse(
        path,
        media_type=row["mime_type"] or "application/octet-stream",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}"},
    )


@router.patch("/api/uploads/{upload_id}")
async def patch_upload(
    upload_id: UUID,
    payload: UploadPatch,
    request: Request,
    background_tasks: BackgroundTasks,
) -> UploadSummary:
    user_id, database, _ = await _authorize(request)
    patch = payload.model_dump(exclude_unset=True)
    if not patch or any(value is None for value in patch.values()):
        raise ApiError(400, "invalid_upload_update", "Provide one or more non-empty upload fields.")
    if "extracted_text" in patch and not patch["extracted_text"].strip():
        raise ApiError(400, "invalid_upload_update", "Extracted text cannot be empty.")
    current = await database.fetchrow("SELECT n.* FROM course_notes n WHERE n.id=?1", upload_id)
    if current is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    access = await _owned_class(database, current["class_id"], user_id)
    require_resource_mutation(access, user_id, current["created_by"], "edit")
    fields: list[str] = []
    values: list[Any] = [upload_id]
    kind = patch.get("kind", current["kind"])
    if kind not in NOTE_KINDS:
        raise ApiError(400, "invalid_upload_kind", "This upload kind is not supported.")
    if kind in LECTURE_KINDS and current["source_type"] == "pasted_text":
        raise ApiError(400, "invalid_upload_kind", "Pasted text cannot be changed to a lecture.")
    if "kind" in patch and current["source_type"] == "file":
        original_extension = Path(current["original_filename"] or "").suffix.casefold()
        _validate_upload_kind(original_extension, kind)
    if "kind" in patch and current["source_type"] == "pasted_text" and kind == "slides":
        raise ApiError(400, "invalid_upload_kind", "Pasted text cannot be changed to slides.")
    for key in ("title", "kind", "extracted_text"):
        if key in patch:
            value = patch[key].strip() if isinstance(patch[key], str) else patch[key]
            if key == "title" and (not value or len(value) > 255):
                raise ApiError(400, "invalid_upload_title", "Enter a title of 1 to 255 characters.")
            if key == "kind" and value in LECTURE_KINDS and current["kind"] not in LECTURE_KINDS:
                raise ApiError(400, "invalid_upload_kind", "Only audio or video files can be lectures.")
            values.append(value)
            fields.append(f"{key} = ?{len(values)}")
    reprocess = "kind" in patch or "extracted_text" in patch
    assessment_id: UUID | None = None
    if reprocess and getattr(request.app.state, "gemini_content_service", None) is None:
        raise ApiError(503, "content_processing_unavailable", "Course material processing is unavailable.")
    if reprocess:
        fields.extend([
            "status = 'processing'", "processing_progress = 0", "failure_code = NULL",
            "processed_at = NULL", "processing_revision = processing_revision + 1",
        ])
    fields.append("updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')")
    query = (
        "UPDATE course_notes SET " + ", ".join(fields)
        + " WHERE id = ?1 RETURNING *"
    )
    async with database.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(query, *values)
            if row is None:
                raise ApiError(404, "not_found", "The requested resource was not found.")
            if reprocess:
                assessment_id = await _reset_assessment_for_processing(connection, upload_id)
    if reprocess:
        if assessment_id is not None:
            background_tasks.add_task(
                _process_assessment_job,
                request.app,
                assessment_id,
                "extracted_text" in patch,
                row["processing_revision"],
            )
        else:
            background_tasks.add_task(
                _reprocess_upload_job,
                request.app,
                upload_id,
                "extracted_text" in patch,
                row["processing_revision"],
            )
    return _upload_summary(row)


@router.delete("/api/uploads/{upload_id}", status_code=204)
async def delete_upload(upload_id: UUID, request: Request) -> Response:
    user_id, database, _ = await _authorize(request)
    async with database.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(
                "SELECT n.storage_key,n.class_id,n.created_by,n.resource_scope,n.publication_state,"
                "n.release_at,a.owner_id AS assessment_owner,a.has_student_responses,"
                "a.score_percent,a.grading_result FROM course_notes n "
                "LEFT JOIN uploaded_assessments a ON a.note_id=n.id WHERE n.id=?1",
                upload_id,
            )
            if row is None:
                raise ApiError(404, "not_found", "The requested resource was not found.")
            access = await class_access(connection, row["class_id"], user_id)
            if (
                not access["is_owner"]
                and (row["created_by"] is None or str(row["created_by"]) != str(user_id))
                and is_private_assessment({
                    "owner_id": row["assessment_owner"],
                    "has_student_responses": row["has_student_responses"],
                    "score_percent": row["score_percent"],
                    "grading_result": row["grading_result"],
                })
            ):
                raise ApiError(404, "not_found", "The requested resource was not found.")
            require_resource_mutation(
                access,
                user_id,
                row["created_by"],
                "delete",
                resource_scope=row["resource_scope"],
                publication_state=row["publication_state"],
                release_at=row["release_at"],
            )
            await connection.execute("DELETE FROM course_notes WHERE id = ?1", upload_id)
    private_storage(request).delete(row["storage_key"])
    return Response(status_code=204)


@router.post("/api/uploads/{upload_id}/retry", status_code=202)
async def retry_upload(upload_id: UUID, request: Request, background_tasks: BackgroundTasks) -> UploadSummary:
    user_id, database, _ = await _authorize(request)
    async with database.acquire() as connection:
        async with connection.transaction():
            current = await connection.fetchrow(
                "SELECT n.status,n.source_index_status,n.processing_revision,"
                "n.class_id,n.created_by,"
                "n.resource_scope,n.publication_state,n.release_at,a.id AS assessment_id,"
                "a.owner_id AS assessment_owner,a.has_student_responses,"
                "a.score_percent,a.grading_result,"
                "a.answer_key_release_state,a.answer_key_release_at "
                "FROM course_notes n LEFT JOIN uploaded_assessments a ON a.note_id=n.id "
                "WHERE n.id=?1", upload_id,
            )
            if current is None:
                raise ApiError(404, "not_found", "The requested resource was not found.")
            access = await class_access(connection, current["class_id"], user_id)
            require_resource_mutation(access, user_id, current["created_by"], "edit")
            if current["status"] == "ready" and current["source_index_status"] == "failed":
                assessment = {
                    "owner_id": current["assessment_owner"],
                    "has_student_responses": current["has_student_responses"],
                    "score_percent": current["score_percent"],
                    "grading_result": current["grading_result"],
                }
                if current["assessment_id"] is not None and (
                    is_private_assessment(assessment)
                    or not can_view_assessment_key({**assessment, **current}, access, user_id)
                ):
                    raise ApiError(404, "not_found", "The requested resource was not found.")
                queued = await retry_ready_note_index(
                    connection, note_id=str(upload_id),
                    processing_revision=current["processing_revision"],
                )
                if not queued:
                    raise ApiError(
                        409, "source_index_retry_conflict", "The source index could not be retried."
                    )
                row = await connection.fetchrow(
                    "SELECT * FROM course_notes WHERE id=?1", upload_id
                )
                background_tasks.add_task(
                    _retry_source_index_job, request.app, str(upload_id),
                    current["processing_revision"],
                )
                return _upload_summary(row)
            if current["status"] != "failed":
                raise ApiError(409, "upload_not_failed", "Only failed uploads can be retried.")
            if getattr(request.app.state, "gemini_content_service", None) is None:
                raise ApiError(
                    503, "content_processing_unavailable",
                    "Course material processing is unavailable.",
                )
            row = await connection.fetchrow(
                "UPDATE course_notes SET status = 'processing', processing_progress = 0, "
                "failure_code = NULL, processed_at = NULL, processing_revision = processing_revision + 1, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?1 RETURNING *",
                upload_id,
            )
            assessment_id = (
                await _reset_assessment_for_processing(connection, upload_id)
                if current["assessment_id"] is not None
                else None
            )
    if assessment_id is not None:
        background_tasks.add_task(
            _process_assessment_job, request.app, assessment_id, False, row["processing_revision"]
        )
    else:
        background_tasks.add_task(_process_upload_job, request.app, upload_id, row["processing_revision"])
    return _upload_summary(row)


async def _retry_source_index_job(
    application: Any, note_id: str, processing_revision: int
) -> None:
    """Retry local indexing only; extracted text and provider-derived fields remain unchanged."""
    await process_queued_jobs(application, limit=1, note_id=note_id)


async def _process_upload_job(
    application: Any, upload_id: UUID, expected_revision: int | None = None
) -> None:
    """Extract or transcribe one upload, updating only the active processing attempt."""
    database: SQLiteDatabase | None = getattr(application.state, "database", None)
    content_service = getattr(application.state, "gemini_content_service", None)
    storage: LocalPrivateStorage | None = getattr(application.state, "private_storage", None)
    if database is None or content_service is None or storage is None:
        return
    row = None
    try:
        row = await database.fetchrow(
            "UPDATE course_notes SET processing_progress = 10, processing_revision = processing_revision + 1, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?1 "
            "AND status = 'processing' AND (?2 IS NULL OR processing_revision = ?2) "
            "RETURNING id, kind, source_type, storage_key, mime_type, extracted_text, processing_revision",
            upload_id,
            expected_revision,
        )
        if row is None:
            return
        if not await begin_index_job(database, str(upload_id), row["processing_revision"]):
            return
        if row["source_type"] == "pasted_text":
            result = await asyncio.to_thread(content_service.process_text, row["extracted_text"], row["kind"])
            path = None
        else:
            path = storage.path_for(row["storage_key"])
            result = await asyncio.to_thread(content_service.process_upload, path, row["kind"], row["mime_type"])
        extracted_text = result["extracted_text"]
        transcript_segments = result.get("transcript_segments", [])
        blocks = await asyncio.to_thread(
            make_source_blocks,
            path=path,
            kind=row["kind"],
            source_type=row["source_type"],
            mime_type=row["mime_type"],
            extracted_text=extracted_text,
            transcript_segments=transcript_segments,
        )
        source_hash = await asyncio.to_thread(file_sha256, path)
        async with database.acquire() as connection:
            async with connection.transaction():
                revision_id = await persist_revision(
                    connection,
                    note_id=str(upload_id),
                    processing_revision=row["processing_revision"],
                    source_hash=source_hash,
                    extracted_text=extracted_text,
                    extraction_method=(
                        blocks[0].extraction_method if blocks else "extracted_text"
                    ),
                    blocks=blocks,
                    mark_note_ready=True,
                    upload_result=result,
                )
        if revision_id is not None:
            await create_default_document(application, upload_id)
    except Exception as exc:
        provider_code, provider_status = _provider_error_metadata(exc)
        logger.error(
            "upload_processing_failed upload_id=%s category=%s provider_code=%s provider_status=%s",
            upload_id,
            type(exc).__name__,
            provider_code,
            provider_status,
        )
        if row is None:
            return
        try:
            await database.execute(
                "UPDATE course_notes SET status='failed', source_index_status='failed', "
                "failure_code='processing_failed', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?1 AND status = 'processing' AND processing_revision = ?2",
                upload_id,
                row["processing_revision"],
            )
            await database.execute(
                "UPDATE source_index_jobs SET status='failed',"
                "last_error_code='upload_processing_failed',"
                "completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE note_id=?1 AND processing_revision=?2 AND status IN ('running','queued')",
                upload_id,
                row["processing_revision"],
            )
        except Exception:
            logger.error("upload_failure_state_update_failed upload_id=%s", upload_id)


async def _process_assessment_job(
    application: Any, assessment_id: UUID, use_extracted_text: bool = False,
    expected_revision: int | None = None,
) -> None:
    """Extract and grade one imported test, keeping its upload and assessment states aligned."""
    database: SQLiteDatabase | None = getattr(application.state, "database", None)
    content_service: GeminiContentService | None = getattr(
        application.state, "gemini_content_service", None
    )
    storage: LocalPrivateStorage | None = getattr(application.state, "private_storage", None)
    if database is None or content_service is None or storage is None:
        return

    active_revision = None
    note_id = None
    try:
        row = await database.fetchrow(
            "SELECT a.id AS assessment_id,a.class_id,a.note_id,a.owner_id,n.kind,n.storage_key, "
            "n.mime_type, n.extracted_text, n.processing_revision AS note_revision "
            "FROM uploaded_assessments a "
            "JOIN course_notes n ON n.id = a.note_id AND n.class_id = a.class_id "
            "WHERE a.id = ?1 AND a.processing_status = 'processing' AND n.status = 'processing'",
            assessment_id,
        )
        if row is None:
            return
        note_id = row["note_id"]
        # Older uploaded assessment rows have no submitter attribution. Their
        # processing may use the stable class owner's access, but the resulting
        # response/grade remains unattributed and private.
        actor_id = row["owner_id"] or await database.fetchval(
            "SELECT owner_id FROM course_classes WHERE id=?1", row["class_id"]
        )
        if actor_id is None:
            raise RuntimeError("assessment owner is unavailable")
        access = await class_access(database, row["class_id"], actor_id, required="read")
        active_revision = row["note_revision"]
        if expected_revision is not None and active_revision != expected_revision:
            return
        progress = await database.fetchrow(
            "UPDATE course_notes SET processing_progress = 10, processing_revision = processing_revision + 1, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ?1 AND status = 'processing' AND processing_revision = ?2 RETURNING processing_revision",
            note_id,
            active_revision,
        )
        if progress is None:
            return
        active_revision = progress["processing_revision"]

        if use_extracted_text and row["extracted_text"]:
            extracted = await asyncio.to_thread(
                content_service.process_text, row["extracted_text"], row["kind"]
            )
        else:
            extracted = await asyncio.to_thread(
                content_service.process_upload,
                storage.path_for(row["storage_key"]),
                row["kind"],
                row["mime_type"],
            )
        progress = await database.fetchrow(
            "UPDATE course_notes SET processing_progress = 55, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ?1 AND status = 'processing' AND processing_revision = ?2 RETURNING processing_revision",
            note_id,
            active_revision,
        )
        if progress is None:
            return
        active_revision = progress["processing_revision"]

        class_note_rows = await database.fetch(
            "SELECT n.id,n.title,n.extracted_text,n.summary,n.created_by,n.publication_state,n.release_at "
            "FROM course_notes n "
            "WHERE n.class_id = ?1 AND n.status = 'ready' "
            "AND (n.extracted_text IS NOT NULL OR n.summary IS NOT NULL) "
            "AND NOT EXISTS (SELECT 1 FROM uploaded_assessments a WHERE a.note_id = n.id) "
            "ORDER BY n.updated_at DESC, n.id",
            row["class_id"],
        )
        access = await class_access(database, row["class_id"], actor_id, required="read")
        class_note_rows = [
            note for note in class_note_rows
            if is_effectively_published(note["publication_state"], note["release_at"])
            or can_view_unreleased(access, actor_id, note["created_by"])
        ]
        analysis = await asyncio.to_thread(
            content_service.process_assessment,
            extracted["extracted_text"],
            [dict(note) for note in class_note_rows],
        )

        async with database.acquire() as connection:
            async with connection.transaction():
                saved_note = await connection.fetchrow(
                    "UPDATE course_notes SET extracted_text = ?2, summary = ?3, "
                    "key_topics = ?4, transcript_segments = ?5, "
                    "duration_seconds = ?6, syllabus_outline = ?7, status = 'ready', "
                    "publication_state = CASE WHEN ?9=1 THEN 'draft' ELSE publication_state END, "
                    "processing_progress = 100, failure_code = NULL, processed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE id = ?1 AND status = 'processing' AND processing_revision = ?8 RETURNING id",
                    note_id,
                    extracted["extracted_text"],
                    extracted["summary"],
                    json.dumps(extracted["key_topics"]),
                    json.dumps(extracted["transcript_segments"]),
                    extracted["duration_seconds"],
                    json.dumps(extracted["syllabus_outline"])
                    if extracted["syllabus_outline"] is not None
                    else None,
                    active_revision,
                    int(is_private_assessment(analysis)),
                )
                if saved_note is None:
                    return
                await connection.execute(
                    "UPDATE uploaded_assessments SET processing_status = ?2, "
                    "has_student_responses = ?3, questions = ?4, grading_mode = ?5, "
                    "score_percent = ?6, grading_result = ?7, grade_confidence = ?8, "
                    "failure_code = NULL, processed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                    "graded_at = CASE WHEN ?5 IN ('answer_key', 'class_notes_estimated') "
                    "THEN strftime('%Y-%m-%dT%H:%M:%fZ', 'now') ELSE NULL END WHERE id = ?1 AND processing_status = 'processing'",
                    assessment_id,
                    analysis["processing_status"],
                    analysis["has_student_responses"],
                    json.dumps(analysis["questions"]),
                    analysis["grading_mode"],
                    analysis["score_percent"],
                    json.dumps(analysis["grading_result"])
                    if analysis["grading_result"] is not None
                    else None,
                    analysis["grade_confidence"],
                )
        await create_default_document(application, UUID(str(note_id)))
        if not is_private_assessment(analysis):
            await queue_ready_note_index(
                database, note_id=str(note_id), processing_revision=active_revision
            )
            await process_queued_jobs(application, limit=1, note_id=str(note_id))
    except Exception as exc:
        logger.error(
            "assessment_processing_failed assessment_id=%s category=%s",
            assessment_id,
            type(exc).__name__,
        )
        if note_id is None:
            return
        try:
            async with database.acquire() as connection:
                async with connection.transaction():
                    failed_note = await connection.fetchrow(
                        "UPDATE course_notes SET status = 'failed', "
                        "failure_code = 'processing_failed', "
                        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?1 "
                        "AND status = 'processing' "
                        "AND processing_revision = ?2 RETURNING id",
                        note_id,
                        active_revision,
                    )
                    if failed_note is not None:
                        await connection.execute(
                            "UPDATE uploaded_assessments SET processing_status = 'failed', "
                            "score_percent = NULL, grade_confidence = NULL, grading_result = NULL, "
                            "failure_code = 'processing_failed', processed_at = NULL, "
                            "graded_at = NULL, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                            "WHERE id = ?1 AND processing_status = 'processing'",
                            assessment_id,
                        )
        except Exception:
            logger.error("assessment_failure_state_update_failed assessment_id=%s", assessment_id)


async def _reprocess_upload_job(
    application: Any, upload_id: UUID, use_extracted_text: bool,
    expected_revision: int | None = None,
) -> None:
    """Rebuild summaries/outlines after a user edits the text or kind."""
    database: SQLiteDatabase | None = getattr(application.state, "database", None)
    content_service = getattr(application.state, "gemini_content_service", None)
    storage: LocalPrivateStorage | None = getattr(application.state, "private_storage", None)
    if database is None or content_service is None or storage is None:
        return
    row = None
    try:
        row = await database.fetchrow(
            "UPDATE course_notes SET processing_progress = 10, processing_revision = processing_revision + 1, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?1 "
            "AND status = 'processing' AND (?2 IS NULL OR processing_revision = ?2) "
            "RETURNING id, kind, source_type, storage_key, mime_type, extracted_text, processing_revision",
            upload_id,
            expected_revision,
        )
        if row is None:
            return
        if not await begin_index_job(database, str(upload_id), row["processing_revision"]):
            return
        if use_extracted_text or row["source_type"] == "pasted_text":
            result = await asyncio.to_thread(content_service.process_text, row["extracted_text"], row["kind"])
            path = None
        else:
            path = storage.path_for(row["storage_key"])
            result = await asyncio.to_thread(
                content_service.process_upload,
                path,
                row["kind"],
                row["mime_type"],
            )
        extracted_text = result["extracted_text"]
        transcript_segments = result.get("transcript_segments", [])
        blocks = await asyncio.to_thread(
            make_source_blocks,
            path=path,
            kind=row["kind"],
            source_type=row["source_type"],
            mime_type=row["mime_type"],
            extracted_text=extracted_text,
            transcript_segments=transcript_segments,
        )
        source_hash = await asyncio.to_thread(file_sha256, path)
        async with database.acquire() as connection:
            async with connection.transaction():
                revision_id = await persist_revision(
                    connection,
                    note_id=str(upload_id),
                    processing_revision=row["processing_revision"],
                    source_hash=source_hash,
                    extracted_text=extracted_text,
                    extraction_method=(
                        blocks[0].extraction_method if blocks else "extracted_text"
                    ),
                    blocks=blocks,
                    mark_note_ready=True,
                    upload_result=result,
                )
        if revision_id is not None:
            await create_default_document(application, upload_id)
    except Exception as exc:
        provider_code, provider_status = _provider_error_metadata(exc)
        logger.error(
            "upload_reprocessing_failed upload_id=%s category=%s provider_code=%s provider_status=%s",
            upload_id,
            type(exc).__name__,
            provider_code,
            provider_status,
        )
        if row is None:
            return
        try:
            await database.execute(
                "UPDATE course_notes SET status='failed', source_index_status='failed', "
                "failure_code='processing_failed', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?1 AND status = 'processing' AND processing_revision = ?2",
                upload_id,
                row["processing_revision"],
            )
            await database.execute(
                "UPDATE source_index_jobs SET status='failed',"
                "last_error_code='upload_processing_failed',"
                "completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE note_id=?1 AND processing_revision=?2 AND status IN ('running','queued')",
                upload_id,
                row["processing_revision"],
            )
        except Exception:
            logger.error("upload_failure_state_update_failed upload_id=%s", upload_id)


@router.get("/api/classes/{class_id}/syllabus")
async def get_syllabus(class_id: UUID, request: Request) -> SyllabusOutline:
    user_id, database, _ = await _authorize(request)
    access = await _owned_class(database, class_id, user_id)
    row = await database.fetchrow(
        "SELECT n.id,n.syllabus_outline,n.created_by,n.publication_state,n.release_at FROM course_notes n "
        "WHERE n.class_id = ?1 AND n.kind = 'syllabus' AND n.status = 'ready' "
        "AND (n.publication_state='published' OR (n.publication_state='scheduled' "
        "AND n.release_at <= strftime('%Y-%m-%dT%H:%M:%fZ','now')) OR n.created_by=?2 OR ?3=1) "
        "ORDER BY n.updated_at DESC LIMIT 1",
        class_id,
        user_id,
        int(is_class_manager(access)),
    )
    if row is None:
        return SyllabusOutline(note_id=None, topics=[], exams=[])
    outline = json_value(row["syllabus_outline"], {})
    if not isinstance(outline, dict):
        outline = {}
    return SyllabusOutline(note_id=row["id"], **outline)


@router.put("/api/classes/{class_id}/syllabus")
async def update_syllabus(
    class_id: UUID,
    payload: SyllabusOutlineUpdate,
    request: Request,
) -> SyllabusOutline:
    user_id, database, _ = await _authorize(request)
    await _owned_class(database, class_id, user_id, required="manage_permissions")
    row = await database.fetchrow(
        "SELECT id FROM course_notes WHERE class_id = ?1 AND kind = 'syllabus' "
        "AND status = 'ready' ORDER BY updated_at DESC LIMIT 1",
        class_id,
    )
    if row is None:
        raise ApiError(409, "syllabus_not_ready", "Upload and process a syllabus before editing its outline.")
    outline = payload.model_dump(mode="json")
    topic_ids = {topic["id"] for topic in outline["topics"]}
    topic_names = {topic["title"].casefold() for topic in outline["topics"]}
    for exam in outline["exams"]:
        if any(reference not in topic_ids and reference.casefold() not in topic_names for reference in exam["topics"]):
            raise ApiError(400, "invalid_syllabus_outline", "An exam refers to a topic outside this syllabus.")
    await database.execute(
        "UPDATE course_notes SET syllabus_outline = ?2, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?1",
        row["id"],
        json.dumps(outline),
    )
    return SyllabusOutline(note_id=row["id"], **outline)


@router.patch("/api/resources/{resource_type}/{resource_id}/publication")
async def update_resource_publication(
    resource_type: Literal["uploads", "study_materials"],
    resource_id: UUID,
    payload: ResourcePublicationUpdate,
    request: Request,
) -> dict[str, Any]:
    user_id, db, _ = await _authorize(request)
    if resource_type == "uploads":
        row = await db.fetchrow(
            "SELECT n.id,n.class_id,n.created_by,a.owner_id AS assessment_owner,"
            "a.has_student_responses,a.score_percent,a.grading_result FROM course_notes n "
            "LEFT JOIN uploaded_assessments a ON a.note_id=n.id WHERE n.id=?1", resource_id
        )
    else:
        row = await db.fetchrow(
            "SELECT id,class_id,created_by,resource_scope,kind,status "
            "FROM study_materials WHERE id=?1",
            resource_id,
        )
    if row is None or row["class_id"] is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if resource_type == "uploads" and is_private_assessment({
        "has_student_responses": row.get("has_student_responses"),
        "score_percent": row.get("score_percent"),
        "grading_result": row.get("grading_result"),
        "owner_id": row.get("assessment_owner"),
    }):
        raise ApiError(409, "private_assessment", "Student-submitted assessments cannot be published to a class.")
    if resource_type == "study_materials" and row.get("resource_scope") == "personal":
        raise ApiError(409, "personal_resource_private", "Personal study materials cannot be published directly.")
    access = await _owned_class(db, row["class_id"], user_id)
    if not is_class_manager(access):
        if str(row["created_by"]) != str(user_id):
            raise ApiError(403, "insufficient_permission", "You do not have permission to publish this resource.")
        require_capability(access, "create")
    release_at = payload.release_at.isoformat(timespec="milliseconds").replace("+00:00", "Z") \
        if payload.release_at else None
    published_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z") \
        if payload.state == "published" else None
    table = "course_notes" if resource_type == "uploads" else "study_materials"
    async with db.acquire() as connection:
        async with connection.transaction():
            if (
                resource_type == "study_materials"
                and row["kind"] in {"quiz", "practice_exam"}
                and payload.state in {"published", "scheduled"}
            ):
                if row["status"] != "ready":
                    raise ApiError(
                        409,
                        "material_not_ready",
                        "Wait for generation to finish before publishing.",
                    )
                await validate_material_bank_sources(
                    connection,
                    material_id=str(resource_id),
                    class_id=str(row["class_id"]),
                    user_id=str(user_id),
                )
            updated = await connection.fetchrow(
                f"UPDATE {table} SET publication_state=?2,release_at=?3,published_at=?4, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?1 "
                "RETURNING id,publication_state,release_at,published_at",
                resource_id, payload.state, release_at, published_at,
            )
    return dict(updated)


@router.patch("/api/assessments/{assessment_id}/answer-key-release")
async def update_assessment_answer_key_release(
    assessment_id: UUID,
    payload: ResourcePublicationUpdate,
    request: Request,
) -> dict[str, Any]:
    user_id, db, _ = await _authorize(request)
    row = await db.fetchrow(
        "SELECT a.id,a.class_id,a.owner_id,a.has_student_responses,a.score_percent,a.grading_result,"
        "n.created_by,n.resource_scope FROM uploaded_assessments a "
        "JOIN course_notes n ON n.id=a.note_id WHERE a.id=?1", assessment_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if is_private_assessment(row):
        raise ApiError(409, "private_assessment", "Student-submitted assessment results cannot be released to a class.")
    access = await _owned_class(db, row["class_id"], user_id)
    if not is_class_manager(access):
        if str(row["owner_id"]) != str(user_id) or str(row["created_by"]) != str(user_id):
            raise ApiError(403, "insufficient_permission", "You do not have permission to release this answer key.")
        require_capability(access, "create")
    release_at = payload.release_at.isoformat(timespec="milliseconds").replace("+00:00", "Z") \
        if payload.release_at else None
    updated = await db.fetchrow(
        "UPDATE uploaded_assessments SET answer_key_release_state=?2,answer_key_release_at=?3, "
        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?1 "
        "RETURNING id,answer_key_release_state,answer_key_release_at",
        assessment_id, payload.state, release_at,
    )
    return dict(updated)


async def _resolve_material_sources(
    database: SQLiteDatabase,
    class_id: UUID,
    scope: dict,
    user_id: UUID,
    access: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict]:
    def visible(row: dict[str, Any]) -> bool:
        if not (
            is_effectively_published(row.get("publication_state"), row.get("release_at"))
            or can_view_unreleased(access, user_id, row.get("created_by"))
        ):
            return False
        if row.get("assessment_id") is not None and not (
            can_view_assessment_key(row, access, user_id)
        ):
            return False
        return True

    scope_kind = scope.get("kind")
    if scope_kind == "all":
        rows = await database.fetch(
            "SELECT n.id,n.title,n.kind,n.extracted_text,n.summary,n.status,n.created_by,"
            "n.publication_state,n.release_at,n.resource_scope,a.id AS assessment_id,a.owner_id AS assessment_owner,"
            "a.has_student_responses,a.score_percent,a.grading_result,"
            "a.answer_key_release_state,a.answer_key_release_at FROM course_notes n "
            "LEFT JOIN uploaded_assessments a ON a.note_id=n.id "
            "WHERE n.class_id=?1 AND n.status='ready' ORDER BY n.created_at",
            class_id,
        )
        rows = [row for row in rows if visible(row)]
    elif scope_kind == "uploads":
        ids = [UUID(value) for value in scope.get("upload_ids", [])]
        placeholders = ", ".join(f"?{index + 2}" for index in range(len(ids)))
        rows = await database.fetch(
            "SELECT n.id,n.title,n.kind,n.extracted_text,n.summary,n.status,n.created_by,"
            "n.publication_state,n.release_at,n.resource_scope,a.id AS assessment_id,a.owner_id AS assessment_owner,"
            "a.has_student_responses,a.score_percent,a.grading_result,"
            "a.answer_key_release_state,a.answer_key_release_at FROM course_notes n "
            "LEFT JOIN uploaded_assessments a ON a.note_id=n.id "
            f"WHERE n.class_id=?1 AND n.id IN ({placeholders}) ORDER BY n.created_at",
            class_id,
            *ids,
        )
        if len(rows) != len(ids) or any(not visible(row) for row in rows):
            raise ApiError(404, "not_found", "The requested resource was not found.")
        if any(row["status"] != "ready" for row in rows):
            raise ApiError(409, "uploads_not_ready", "Wait for the selected uploads to finish processing.")
    elif scope_kind in {"topics", "exam"}:
        syllabus = await database.fetchrow(
            "SELECT n.syllabus_outline,n.created_by,n.publication_state,n.release_at,n.resource_scope,"
            "a.id AS assessment_id,a.owner_id AS assessment_owner,a.has_student_responses,"
            "a.score_percent,a.grading_result,a.answer_key_release_state,"
            "a.answer_key_release_at FROM course_notes n "
            "LEFT JOIN uploaded_assessments a ON a.note_id=n.id "
            "WHERE n.class_id=?1 AND n.kind='syllabus' AND n.status='ready' "
            "AND (n.publication_state='published' OR (n.publication_state='scheduled' "
            "AND n.release_at<=strftime('%Y-%m-%dT%H:%M:%fZ','now')) OR n.created_by=?2 OR ?3=1) "
            "ORDER BY n.updated_at DESC LIMIT 1",
            class_id,
            user_id,
            int(is_class_manager(access)),
        )
        if syllabus is None or not visible(syllabus):
            raise ApiError(409, "syllabus_not_ready", "Process a syllabus before using a syllabus scope.")
        outline = json_value(syllabus["syllabus_outline"], {})
        outline = outline if isinstance(outline, dict) else {}
        topics = outline.get("topics", [])
        exams = outline.get("exams", [])
        if scope_kind == "topics":
            requested = scope.get("topics", [])
            topic_by_id = {str(item.get("id")): item for item in topics if isinstance(item, dict)}
            topic_by_name = {str(item.get("title", "")).casefold(): item for item in topics if isinstance(item, dict)}
            resolved = []
            for value in requested:
                found = topic_by_id.get(value) or topic_by_name.get(value.casefold())
                if found is None:
                    raise ApiError(400, "invalid_scope", "A selected topic is not in this class syllabus.")
                resolved.append(found["title"])
            scope = {"kind": "topics", "topics": resolved}
        else:
            exam_name = scope.get("exam_name", "")
            found_exam = next(
                (item for item in exams if isinstance(item, dict) and str(item.get("name", "")).casefold() == exam_name.casefold()),
                None,
            )
            if found_exam is None:
                raise ApiError(400, "invalid_scope", "The selected exam is not in this class syllabus.")
            scope = {"kind": "exam", "exam_name": found_exam["name"]}
        rows = await database.fetch(
            "SELECT n.id,n.title,n.kind,n.extracted_text,n.summary,n.status,n.created_by,"
            "n.publication_state,n.release_at,n.resource_scope,a.id AS assessment_id,a.owner_id AS assessment_owner,"
            "a.has_student_responses,a.score_percent,a.grading_result,"
            "a.answer_key_release_state,a.answer_key_release_at FROM course_notes n "
            "LEFT JOIN uploaded_assessments a ON a.note_id=n.id "
            "WHERE n.class_id=?1 AND n.status='ready' ORDER BY n.created_at",
            class_id,
        )
        rows = [row for row in rows if visible(row)]
    else:
        raise ApiError(400, "invalid_scope", "The requested study scope is not supported.")
    if not rows:
        raise ApiError(409, "no_ready_uploads", "Add at least one processed course upload before generating study material.")
    return list(rows), scope


async def _create_material_job(
    application: Any,
    material_id: UUID,
) -> None:
    database: SQLiteDatabase | None = getattr(application.state, "database", None)
    service: GeminiContentService | None = getattr(
        application.state, "gemini_content_service", None
    )
    if database is None or service is None:
        return
    generation_stage = "load_sources"
    usage_token = None
    try:
        material = await database.fetchrow(
            "SELECT m.id,m.class_id,m.origin_class_id,m.owner_id,m.created_by,m.resource_scope,"
            "m.kind,m.scope,m.options,m.generation_instructions, "
            "m.title_override, "
            "c.name AS class_name "
            "FROM study_materials m LEFT JOIN course_classes c ON c.id = COALESCE(m.class_id,m.origin_class_id) "
            "WHERE m.id = ?1 AND m.status = 'generating'",
            material_id,
        )
        if material is None:
            return
        material_kind = str(material["kind"])
        class_name = str(material.get("class_name") or "Personal study")
        usage_token = bind_usage_context(
            operation_id=str(material["id"]),
            operation_kind=material_kind,
            operation_label=f"{material_kind.replace('_', ' ').title()} · {class_name}",
        )
        source_rows = await database.fetch(
            "SELECT COALESCE(n.id,s.source_note_id,s.note_id) AS id,"
            "COALESCE(n.title,s.source_title) AS title,COALESCE(n.kind,s.source_kind) AS kind,"
            "COALESCE(n.class_id,s.source_class_id,s.class_id) AS class_id,n.created_by,"
            "n.publication_state,n.release_at,n.resource_scope,n.extracted_text,n.summary,"
            "n.original_filename,n.mime_type,n.source_type,a.id AS assessment_id,"
            "n.storage_key AS _storage_key,a.owner_id AS assessment_owner,a.has_student_responses,"
            "a.score_percent,a.grading_result,"
            "a.answer_key_release_state,a.answer_key_release_at "
            "FROM study_material_sources s LEFT JOIN course_notes n "
            "ON n.id=COALESCE(s.source_note_id,s.note_id) "
            "AND n.class_id=COALESCE(s.source_class_id,s.class_id) "
            "LEFT JOIN uploaded_assessments a ON a.note_id=n.id "
            "WHERE s.material_id = ?1 ORDER BY s.position",
            material_id,
        )
        sources = []
        storage: LocalPrivateStorage | None = getattr(
            application.state, "private_storage", None
        )
        actor_id = material.get("created_by") or material.get("owner_id")
        if actor_id is None:
            actor_class_id = material.get("class_id") or material.get("origin_class_id")
            if actor_class_id is not None:
                actor_id = await database.fetchval(
                    "SELECT owner_id FROM course_classes WHERE id=?1", actor_class_id
                )
        for row in source_rows:
            if actor_id is None or row.get("class_id") is None or row.get("id") is None:
                continue
            try:
                source_access = await class_access(
                    database, row["class_id"], actor_id, required="read"
                )
            except ApiError:
                continue
            if not (
                is_effectively_published(row.get("publication_state"), row.get("release_at"))
                or can_view_unreleased(source_access, actor_id, row.get("created_by"))
            ):
                continue
            if row.get("assessment_id") is not None and not can_view_assessment_key(
                row, source_access, actor_id
            ):
                continue
            source = {key: value for key, value in row.items() if key not in {
                "class_id", "created_by", "publication_state", "release_at", "assessment_id",
                "assessment_owner", "has_student_responses", "score_percent", "grading_result",
                "answer_key_release_state", "answer_key_release_at",
                "_storage_key",
            }}
            if storage is not None and row.get("_storage_key"):
                try:
                    source["original_path"] = storage.path_for(str(row["_storage_key"]))
                except ValueError:
                    # Visual evidence is supplemental; inaccessible originals do not
                    # invalidate otherwise usable extracted source text.
                    pass
            sources.append(source)
        if not sources or len(sources) != len(source_rows):
            raise RuntimeError("no generation sources remain accessible")
        if material["kind"] in {"quiz", "practice_exam"}:
            # Personal assessments are kept owner-private and do not write to
            # the class bank, but they must still pass the same structured
            # source, answer, rubric, leakage, language, and duplicate checks.
            if material.get("resource_scope") != "personal":
                raise RuntimeError(
                    "shared assessments must be generated through the validated bank"
                )
            origin_class_id = material.get("origin_class_id") or material.get("class_id")
            actor_id = material.get("created_by") or material.get("owner_id")
            if origin_class_id is None or actor_id is None:
                raise RuntimeError("private assessment generation lost its source owner")
            generation_stage = "assessment_generation"
            await create_validated_private_assessment(
                database,
                material_id=str(material_id),
                class_id=str(origin_class_id),
                user_id=str(actor_id),
                material_kind=material["kind"],
                options=json_value(material["options"], {}),
                language=str(json_value(material["options"], {}).get("language") or "en"),
                additional_instructions=material.get("generation_instructions"),
                source_rows=sources,
                service=service,
            )
            return
        await database.execute(
            "UPDATE study_materials SET context_truncated = ?2 WHERE id = ?1",
            material_id,
            int(any(excerpt["truncated"] for excerpt in source_excerpts(sources, MAX_NOTE_CONTEXT_CHARS))),
        )
        generation_stage = "material_generation"
        guide_progress_callback = None
        if material["kind"] == "study_guide":
            event_loop = asyncio.get_running_loop()

            def persist_guide_preview(preview: dict) -> None:
                preview_json = json.dumps(preview, ensure_ascii=False)
                if len(preview_json.encode("utf-8")) > MAX_SERIALIZED_BYTES:
                    return
                future = asyncio.run_coroutine_threadsafe(
                    database.execute(
                        "UPDATE study_materials SET preview_content=?2,"
                        "preview_revision=preview_revision+1,"
                        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                        "WHERE id=?1 AND kind='study_guide' AND status='generating'",
                        material_id,
                        preview_json,
                    ),
                    event_loop,
                )
                try:
                    future.result(timeout=10)
                except Exception:
                    future.cancel()
                    logger.warning("study_guide_preview_persist_failed material_id=%s", material_id)

            guide_progress_callback = persist_guide_preview
        # Study-guide authoring already retries malformed batches and splits
        # them to isolate bad sections. Preserve one whole-generation retry for
        # unclassified empty/invalid responses, but don't repeat every batch
        # after the service returns a precise failure from its bounded repairs.
        generation_attempts = 2
        for generation_attempt in range(generation_attempts):
            try:
                generation_kwargs = {
                    "kind": material["kind"],
                    "class_name": material["class_name"] or "Personal study materials",
                    "scope": json_value(material["scope"], {}),
                    "options": json_value(material["options"], {}),
                    "additional_instructions": material["generation_instructions"],
                    "sources": sources,
                }
                if guide_progress_callback is not None:
                    generation_kwargs["guide_progress_callback"] = guide_progress_callback
                title, content = await asyncio.to_thread(
                    service.generate_material,
                    **generation_kwargs,
                )
                if material["kind"] == "study_guide":
                    if material.get("title_override"):
                        title = material["title_override"]
                    else:
                        title_suggester = getattr(service, "suggest_study_guide_title", None)
                        if callable(title_suggester):
                            title = await asyncio.to_thread(
                                title_suggester,
                                class_name=material["class_name"] or "Personal study materials",
                                content=content,
                            )
                break
            except ContentGenerationFailure as exc:
                retryable_failure = exc.code in {
                    "empty_model_response",
                    "invalid_model_response",
                }
                if material["kind"] == "study_guide" and exc.diagnostic_code is not None:
                    retryable_failure = False
                if (
                    not retryable_failure
                    or generation_attempt + 1 >= generation_attempts
                ):
                    raise
        files = {}
        quality_review_version = 0
        if material["kind"] == "study_guide":
            generation_stage = "document_validation_export"
            content, files = await asyncio.to_thread(
                _validate_and_export_study_guide,
                service=service,
                settings=application.state.settings,
                title=title,
                class_name=material["class_name"] or "Personal study materials",
                material_id=UUID(str(material_id)),
                content=content,
                sources=sources,
                requested_section_count=_requested_guide_section_count(material),
            )
            quality_review_version = STUDY_GUIDE_QUALITY_VERSION
        generation_stage = "persist"
        async with database.acquire() as connection:
            async with connection.transaction():
                completed = await connection.fetchrow(
                    "UPDATE study_materials SET title = ?2, content = ?3, status = 'ready', "
                    "preview_content = NULL, "
                    "quality_review_version = ?4, "
                    "failure_code = NULL, generated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE id = ?1 AND status = 'generating' RETURNING id",
                    material_id,
                    title,
                    json.dumps(content),
                    quality_review_version,
                )
                if completed and files:
                    await connection.executemany(
                        "INSERT INTO study_material_files (material_id, format, content) VALUES (?1, ?2, ?3)",
                        [(material_id, format, data) for format, data in files.items()],
                    )
    except Exception as exc:
        failure_code = _study_generation_failure_code(exc)
        failure_detail = (
            exc.diagnostic_code if isinstance(exc, ContentGenerationFailure) else None
        )
        provider_code, provider_status = _provider_error_metadata(exc)
        validation_issues = (
            json.dumps(exc.validation_issues, ensure_ascii=True, separators=(",", ":"))
            if isinstance(exc, ContentGenerationFailure) and exc.validation_issues
            else "none"
        )
        logger.error(
            "study_generation_failed material_id=%s stage=%s category=%s error_code=%s "
            "failure_detail=%s validation_issues=%s provider_code=%s provider_status=%s",
            material_id,
            generation_stage,
            type(exc).__name__,
            failure_code,
            failure_detail,
            validation_issues,
            provider_code,
            provider_status,
        )
        await database.execute(
            "UPDATE study_materials SET status = 'failed', failure_code = ?2, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ?1 AND status = 'generating'",
            material_id,
            failure_code,
        )
    finally:
        if usage_token is not None:
            reset_usage_context(usage_token)


@router.post("/api/classes/{class_id}/study-materials", status_code=202)
async def create_study_material(
    class_id: UUID,
    payload: StudyMaterialCreate,
    request: Request,
    background_tasks: BackgroundTasks,
) -> MaterialSummary:
    user_id, database, settings = await _authorize(request)
    access = await _owned_class(database, class_id, user_id)
    if payload.destination == "class_contribution":
        require_capability(access, "create")
    class_row = access
    if getattr(request.app.state, "gemini_content_service", None) is None:
        raise ApiError(
            503, "content_processing_unavailable", "Study material generation is unavailable."
        )
    if payload.kind == "study_guide" and not compiler_path(settings):
        raise ApiError(503, "pdf_export_unavailable", "Study-guide PDF generation is unavailable.")
    await _rate_limit(request, "generation_rate_limiter", user_id)
    scope = payload.scope.model_dump(mode="json")
    options = payload.options.model_dump(mode="json", exclude_none=True)
    if payload.kind == "study_guide":
        guide_styles = payload.guide_styles or [payload.guide_style or "balanced"]
        options["guide_styles"] = guide_styles
        # Preserve the old summary/options field as the primary style for clients
        # that have not started reading guide_styles yet.
        options["guide_style"] = guide_styles[0]
    if payload.kind in {"quiz", "practice_exam"}:
        options["language"] = payload.language
    if payload.kind != "practice_exam" and options.get("time_limit_minutes") is not None:
        raise ApiError(400, "invalid_options", "A time limit is available only for practice exams.")
    if options.get("question_types") is not None and payload.kind not in {"quiz", "practice_exam"}:
        raise ApiError(
            400,
            "invalid_options",
            "Question types are available only for quizzes and practice exams.",
        )
    sources, scope = await _resolve_material_sources(database, class_id, scope, user_id, access)
    title = payload.title or f"{payload.kind.replace('_', ' ').title()}: {class_row['name']}"
    personal = payload.destination == "personal"
    resource_scope = "personal" if personal else "student_contribution"
    material_class_id = None if personal else class_id
    publication_state = None if personal else "draft"
    feedback_release_policy = payload.feedback_release_policy or (
        "manual" if not personal and payload.kind == "practice_exam" else "after_completion"
    )
    if not personal and payload.kind in {"quiz", "practice_exam"}:
        # Shared assessments must originate in the validated source-linked bank.
        # This preserves the existing Study UI while preventing an arbitrary
        # provider response from becoming a publishable class assessment.
        source_ids = [str(source["id"]) for source in sources]
        placeholders = ",".join(f"?{index + 2}" for index in range(len(source_ids)))
        concepts = await database.fetch(
            "SELECT DISTINCT c.id FROM class_concepts c "
            "JOIN class_concept_source_refs ref ON ref.concept_id=c.id "
            "AND ref.class_id=c.class_id "
            "JOIN source_chunk_revisions cr ON cr.id=ref.chunk_revision_id "
            "JOIN source_revisions sr ON sr.id=cr.revision_id "
            "JOIN source_index_heads h ON h.note_id=sr.note_id AND h.revision_id=sr.id "
            "JOIN course_notes n ON n.id=sr.note_id "
            "WHERE c.class_id=?1 AND c.status='validated' AND n.id IN (" + placeholders + ") "
            "AND n.status='ready' AND n.source_index_status='ready' "
            "ORDER BY c.id LIMIT 50",
            class_id,
            *source_ids,
        )
        if not concepts:
            raise ApiError(
                409,
                "validated_concepts_required",
                "Review and validate source-backed class concepts before sharing a quiz or exam.",
            )
        operation_id = payload.operation_id or uuid4()
        digest_payload = payload.model_dump(mode="json", exclude={"operation_id"})
        operation_digest = hashlib.sha256(
            json.dumps(
                digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        async with database.acquire() as connection:
            async with connection.transaction():
                existing = await connection.fetchrow(
                    "SELECT * FROM class_question_generation_operations "
                    "WHERE class_id=?1 AND requester_id=?2 AND operation_id=?3",
                    class_id,
                    user_id,
                    operation_id,
                )
                if existing is not None:
                    if existing["request_digest"] != operation_digest:
                        raise ApiError(
                            409,
                            "idempotency_conflict",
                            "This operation ID was already used with different "
                            "generation settings.",
                        )
                    if existing["status"] == "complete" and existing.get("material_id"):
                        existing_material = await connection.fetchrow(
                            "SELECT * FROM study_materials WHERE id=?1 AND class_id=?2",
                            existing["material_id"],
                            class_id,
                        )
                        if existing_material is not None:
                            return _material_summary(existing_material)
                    if existing["status"] == "running":
                        raise ApiError(
                            409,
                            "generation_in_progress",
                            "This generation operation is already running.",
                        )
                    raise ApiError(
                        503,
                        "question_generation_failed",
                        "This generation operation failed. Retry with a new operation ID.",
                    )
                await connection.execute(
                    "INSERT INTO class_question_generation_operations "
                    "(id,class_id,requester_id,operation_id,request_digest,status) "
                    "VALUES (?1,?2,?3,?4,?5,'running')",
                    uuid4(),
                    class_id,
                    user_id,
                    operation_id,
                    operation_digest,
                )
        request_payload = QuestionBankGenerationRequest(
            operation_id=operation_id,
            kind=payload.kind,
            concept_ids=[UUID(str(item["id"])) for item in concepts],
            upload_ids=[UUID(source_id) for source_id in source_ids],
            count=payload.options.count,
            level=(1 if payload.options.difficulty == "easier" else
                   3 if payload.options.difficulty == "harder" else None),
            question_types=payload.options.question_types or ["multiple_choice"],
            language=payload.language,
            additional_instructions=payload.additional_instructions,
            feedback_release_policy=feedback_release_policy,
        )
        try:
            generated = await create_generated_assessment(
                database,
                class_id=str(class_id),
                user_id=str(user_id),
                payload=request_payload,
                service=request.app.state.gemini_content_service,
                model_name=getattr(
                    request.app.state.gemini_content_service,
                    "model_name",
                    settings.gemini_text_model,
                ),
            )
        except ApiError as exc:
            await database.execute(
                "UPDATE class_question_generation_operations SET status='failed',error_code=?4,"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE class_id=?1 AND requester_id=?2 AND operation_id=?3 AND status='running'",
                class_id,
                user_id,
                operation_id,
                exc.code[:80],
            )
            raise
        except Exception as exc:
            logger.info("question_generation_failed category=%s", type(exc).__name__)
            await database.execute(
                "UPDATE class_question_generation_operations SET status='failed',"
                "error_code='generation_failed',updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE class_id=?1 AND requester_id=?2 AND operation_id=?3 AND status='running'",
                class_id,
                user_id,
                operation_id,
            )
            raise ApiError(
                503,
                "question_generation_failed",
                "The assessment could not be generated and validated. "
                "Retry with a new operation ID.",
            ) from None
        row = await database.fetchrow(
            "SELECT * FROM study_materials WHERE id=?1", generated["material_id"]
        )
        if row is None:
            raise ApiError(
                503,
                "question_generation_failed",
                "The shared assessment could not be saved.",
            )
        await database.execute(
            "UPDATE class_question_generation_operations SET status='complete',material_id=?4,"
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE class_id=?1 AND requester_id=?2 AND operation_id=?3 AND status='running'",
            class_id,
            user_id,
            operation_id,
            generated["material_id"],
        )
        return _material_summary(row)
    private_assessment_operation = (
        (payload.operation_id or uuid4())
        if personal and payload.kind in {"quiz", "practice_exam"}
        else None
    )
    private_assessment_digest: str | None = None
    if private_assessment_operation is not None:
        private_assessment_digest = hashlib.sha256(
            json.dumps(
                {
                    "request": payload.model_dump(mode="json", exclude={"operation_id"}),
                    "resolved_source_ids": [str(source["id"]) for source in sources],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    async with database.acquire() as connection:
        async with connection.transaction():
            if private_assessment_operation is not None:
                previous = await connection.fetchrow(
                    "SELECT * FROM class_question_generation_operations "
                    "WHERE class_id=?1 AND requester_id=?2 AND operation_id=?3",
                    class_id,
                    user_id,
                    private_assessment_operation,
                )
                if previous is not None:
                    if previous["request_digest"] != private_assessment_digest:
                        raise ApiError(
                            409,
                            "idempotency_conflict",
                            "This operation ID was already used with different "
                            "generation settings.",
                        )
                    if previous["status"] == "complete" and previous.get("material_id"):
                        existing_material = await connection.fetchrow(
                            "SELECT * FROM study_materials WHERE id=?1 AND owner_id=?2 "
                            "AND resource_scope='personal'",
                            previous["material_id"],
                            user_id,
                        )
                        if existing_material is not None:
                            return _material_summary(existing_material)
                    if previous["status"] == "running":
                        raise ApiError(
                            409,
                            "generation_in_progress",
                            "This generation operation is already running.",
                        )
                    raise ApiError(
                        503,
                        "question_generation_failed",
                        "This generation operation failed. Retry with a new operation ID.",
                    )
                await connection.execute(
                    "INSERT INTO class_question_generation_operations "
                    "(id,class_id,requester_id,operation_id,request_digest,status) "
                    "VALUES (?1,?2,?3,?4,?5,'running')",
                    uuid4(),
                    class_id,
                    user_id,
                    private_assessment_operation,
                    private_assessment_digest,
                )
            row = await connection.fetchrow(
                "INSERT INTO study_materials "
                "(id,class_id,origin_class_id,resource_scope,owner_id,created_by,publication_state, "
                "feedback_release_policy,kind,title,title_override,status,scope,options,generation_instructions) "
                "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,'generating',?12,?13,?14) "
                "RETURNING *",
                uuid4(),
                material_class_id,
                class_id,
                resource_scope,
                user_id if personal else None,
                user_id,
                publication_state,
                feedback_release_policy,
                payload.kind,
                title,
                payload.title,
                json.dumps(scope),
                json.dumps(options),
                payload.additional_instructions,
            )
            await connection.executemany(
                "INSERT INTO study_material_sources "
                "(material_id,source_class_id,source_note_id,source_title,source_kind,position,class_id,note_id) "
                "VALUES (?1,?2,?3,?4,?5,?6,?2,?3)",
                [
                    (row["id"], class_id, source["id"], source["title"], source["kind"], index)
                    for index, source in enumerate(sources)
                ],
            )
            if private_assessment_operation is not None:
                await connection.execute(
                    "UPDATE class_question_generation_operations SET status='complete',"
                    "material_id=?4,"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                    "WHERE class_id=?1 AND requester_id=?2 AND operation_id=?3 "
                    "AND status='running'",
                    class_id,
                    user_id,
                    private_assessment_operation,
                    row["id"],
                )
    background_tasks.add_task(_create_material_job, request.app, row["id"])
    return MaterialSummary(
        id=row["id"],
        class_id=row["class_id"],
        origin_class_id=row["origin_class_id"],
        resource_scope=row["resource_scope"],
        created_by=row["created_by"],
        publication_state=row["publication_state"],
        release_at=row["release_at"],
        feedback_release_policy=feedback_release_policy,
        kind=row["kind"],
        title=row["title"],
        scope=json_value(row["scope"], {}),
        options=json_value(row["options"], {}),
        status=row["status"],
        item_count=payload.options.count,
        failure_code=row["failure_code"],
        created_at=row["created_at"],
        generated_at=row["generated_at"],
    )


@router.get("/api/classes/{class_id}/study-materials")
async def list_study_materials(
    class_id: UUID, request: Request
) -> dict[str, list[MaterialSummary]]:
    user_id, database, _ = await _authorize(request)
    access = await _owned_class(database, class_id, user_id)
    rows = await database.fetch(
        "SELECT m.*, COALESCE(json_extract(m.options, '$.count'), 0) AS requested_count, "
        "(SELECT a.score_percent FROM study_material_attempts a "
        "WHERE a.material_id = m.id AND a.owner_id=?2 "
        "ORDER BY a.completed_at DESC LIMIT 1) AS latest_score, "
        "(SELECT a.completed_at FROM study_material_attempts a "
        "WHERE a.material_id = m.id AND a.owner_id=?2 "
        "ORDER BY a.completed_at DESC LIMIT 1) AS latest_completed_at "
        ",(SELECT 1 FROM study_material_feedback_releases f "
        "WHERE f.material_id=m.id) AS feedback_released "
        "FROM study_materials m "
        "WHERE (m.class_id = ?1 OR (m.resource_scope='personal' AND m.origin_class_id=?1 AND m.owner_id=?2)) "
        "ORDER BY m.created_at DESC, m.id",
        class_id,
        user_id,
    )
    visible = []
    for row in rows:
        if row["resource_scope"] == "personal":
            visible.append(_material_summary(row))
        elif (
            is_effectively_published(row["publication_state"], row["release_at"])
            or can_view_unreleased(access, user_id, row["created_by"])
        ):
            visible.append(_material_summary(row))
    return {"items": visible}


@router.get("/api/me/study-materials")
async def list_personal_study_materials(request: Request) -> dict[str, list[MaterialSummary]]:
    user_id, database, _ = await _authorize(request)
    rows = await database.fetch(
        "SELECT m.*,COALESCE(json_extract(m.options,'$.count'),0) AS requested_count, "
        "(SELECT a.score_percent FROM study_material_attempts a "
        "WHERE a.material_id=m.id "
        "AND a.owner_id=?1 ORDER BY a.completed_at DESC LIMIT 1) AS latest_score, "
        "(SELECT a.completed_at FROM study_material_attempts a "
        "WHERE a.material_id=m.id "
        "AND a.owner_id=?1 ORDER BY a.completed_at DESC LIMIT 1) AS latest_completed_at "
        ",(SELECT 1 FROM study_material_feedback_releases f "
        "WHERE f.material_id=m.id) AS feedback_released "
        "FROM study_materials m WHERE m.resource_scope='personal' AND m.owner_id=?1 "
        "ORDER BY m.created_at DESC,m.id",
        user_id,
    )
    return {"items": [_material_summary(row) for row in rows]}


@router.get("/api/study-materials/{material_id}")
async def get_study_material(
    material_id: UUID, request: Request, response: Response
) -> MaterialDetail:
    user_id, database, _ = await _authorize(request)
    material, access = await material_access(database, material_id, user_id)
    row = await database.fetchrow(
        "SELECT m.*,COALESCE(json_extract(m.options,'$.count'),0) AS requested_count, "
        "(SELECT a.score_percent FROM study_material_attempts a "
        "WHERE a.material_id=m.id "
        "AND a.owner_id=?2 ORDER BY a.completed_at DESC LIMIT 1) AS latest_score, "
        "(SELECT a.completed_at FROM study_material_attempts a "
        "WHERE a.material_id=m.id "
        "AND a.owner_id=?2 ORDER BY a.completed_at DESC LIMIT 1) AS latest_completed_at "
        ",(SELECT 1 FROM study_material_feedback_releases f "
        "WHERE f.material_id=m.id) AS feedback_released "
        "FROM study_materials m WHERE m.id=?1", material_id, user_id,
    )
    guide_exports_ready = True
    if row["kind"] == "study_guide" and row["status"] == "ready":
        try:
            row = await _ensure_study_guide_quality(
                request.app,
                database,
                material_id,
                user_id,
                row,
                access,
                row=row,
            )
        except ApiError as exc:
            if exc.code != "study_guide_quality_unavailable":
                raise
            # Keep the authored guide readable even when a legacy export cannot be rebuilt.
            # File downloads still pass through the validation gate and remain unavailable.
            guide_exports_ready = False
    response.headers["Cache-Control"] = "private, no-store"
    visible_source_ids = await _visible_material_source_ids(
        database, material_id, user_id, material, access
    )
    preview_content = None
    preview_revision = 0
    if row["kind"] == "study_guide" and row["status"] in {"generating", "failed"}:
        preview_content = _public_material_content(
            row["kind"],
            row.get("preview_content"),
            visible_source_ids=visible_source_ids,
        )
        preview_revision = int(row.get("preview_revision") or 0)
    feedback_released = bool(
        await database.fetchval(
            "SELECT 1 FROM study_material_feedback_releases WHERE material_id=?1", material_id
        )
    )
    summary = _material_summary(row)
    return MaterialDetail(
        **summary.model_dump(),
        content=_public_material_content(
            row["kind"],
            row["content"],
            visible_source_ids=visible_source_ids,
            include_assessment_feedback=feedback_released,
        ),
        preview_content=preview_content,
        preview_revision=preview_revision,
        download_urls={
            format: f"/api/study-materials/{material_id}/files/{format}"
            for format in ("pdf", "latex")
        }
        if row["kind"] == "study_guide" and row["status"] == "ready" and guide_exports_ready
        else {},
    )


@router.post("/api/study-materials/{material_id}/retry", status_code=202)
async def retry_study_guide_generation(
    material_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
) -> MaterialSummary:
    user_id, database, settings = await _authorize(request)
    material, access = await material_access(database, material_id, user_id)
    if access is not None:
        require_resource_mutation(
            access,
            user_id,
            material["created_by"],
            "create",
            resource_scope=material["resource_scope"],
            publication_state=material["publication_state"],
            release_at=material["release_at"],
        )
    if material["kind"] != "study_guide":
        raise ApiError(409, "retry_not_supported", "Only failed study guides can be retried.")
    if material["status"] != "failed":
        code = "generation_in_progress" if material["status"] == "generating" else "material_not_retryable"
        message = (
            "This study guide is already generating."
            if material["status"] == "generating"
            else "Only failed study guides can be retried."
        )
        raise ApiError(409, code, message)
    if getattr(request.app.state, "gemini_content_service", None) is None:
        raise ApiError(
            503, "content_processing_unavailable", "Study material generation is unavailable."
        )
    if not compiler_path(settings):
        raise ApiError(503, "pdf_export_unavailable", "Study-guide PDF generation is unavailable.")
    await _rate_limit(request, "generation_rate_limiter", user_id)

    async with database.acquire() as connection:
        async with connection.transaction():
            claimed = await connection.fetchrow(
                "UPDATE study_materials SET status='generating',content=NULL,failure_code=NULL,"
                "quality_review_version=0,generated_at=NULL,"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=?1 AND kind='study_guide' AND status='failed' RETURNING id",
                material_id,
            )
            if claimed is None:
                current_status = await connection.fetchval(
                    "SELECT status FROM study_materials WHERE id=?1 AND kind='study_guide'",
                    material_id,
                )
                if current_status == "generating":
                    raise ApiError(
                        409, "generation_in_progress", "This study guide is already generating."
                    )
                raise ApiError(
                    409, "material_not_retryable", "Only failed study guides can be retried."
                )
            await connection.execute(
                "DELETE FROM study_material_files WHERE material_id=?1", material_id
            )
            row = await connection.fetchrow(
                "SELECT m.*,COALESCE(json_extract(m.options,'$.count'),0) AS requested_count "
                "FROM study_materials m WHERE m.id=?1",
                material_id,
            )

    background_tasks.add_task(_create_material_job, request.app, material_id)
    return _material_summary(row)


@router.get("/api/study-materials/{material_id}/files/{format}")
async def download_study_file(
    material_id: UUID,
    format: Literal["pdf", "latex"],
    request: Request,
) -> Response:
    user_id, database, settings = await _authorize(request)
    material = await database.fetchrow(
        "SELECT m.*,c.name AS class_name FROM study_materials m "
        "LEFT JOIN course_classes c ON c.id=COALESCE(m.class_id,m.origin_class_id) WHERE m.id=?1",
        material_id,
    )
    _, access = await material_access(database, material_id, user_id)
    if material is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if material["kind"] != "study_guide":
        raise ApiError(
            409, "export_not_supported", "File downloads are available for study guides."
        )
    if material["status"] != "ready":
        raise ApiError(409, "material_not_ready", "Wait for this study guide to finish generating.")
    material = await _ensure_study_guide_quality(
        request.app,
        database,
        material_id,
        user_id,
        material,
        access,
        row=material,
    )
    file = await database.fetchrow(
        "SELECT content FROM study_material_files WHERE material_id = ?1 AND format = ?2",
        material_id,
        format,
    )
    if file is None:
        # Backfill guides saved before file exports existed, without another model request.
        sources = await database.fetch(
            "SELECT COALESCE(n.id,s.source_note_id) AS id,COALESCE(n.title,s.source_title) AS title "
            "FROM study_material_sources s LEFT JOIN course_notes n "
            "ON n.id=COALESCE(s.source_note_id,s.note_id) "
            "WHERE s.material_id = ?1 ORDER BY s.position",
            material_id,
        )
        try:
            files = await asyncio.to_thread(
                create_guide_files,
                settings,
                strict_math=False,
                title=material["title"],
                class_name=material["class_name"] or "Personal study materials",
                material_id=material_id,
                content=json_value(material["content"], {}),
                sources=sources,
            )
            _validate_guide_pdf(files, json_value(material["content"], {}))
        except StudyFileFailure as exc:
            raise ApiError(
                503,
                "pdf_export_unavailable",
                "Study-guide file generation failed. Try again shortly.",
            ) from exc
        async with database.acquire() as connection:
            async with connection.transaction():
                if not await connection.fetchval(
                    "SELECT id FROM study_materials WHERE id = ?1", material_id
                ):
                    raise ApiError(404, "not_found", "The requested resource was not found.")
                await connection.executemany(
                    "INSERT OR IGNORE INTO study_material_files (material_id, format, content) VALUES (?1, ?2, ?3)",
                    [(material_id, kind, data) for kind, data in files.items()],
                )
                file = await connection.fetchrow(
                    "SELECT content FROM study_material_files WHERE material_id = ?1 AND format = ?2",
                    material_id,
                    format,
                )
    suffix = "pdf" if format == "pdf" else "tex"
    filename = (
        re.sub(r"[^A-Za-z0-9._-]+", "-", material["title"]).strip(".-")[:100] or "study-guide"
    )
    return Response(
        content=file["content"],
        media_type="application/pdf" if format == "pdf" else "application/x-tex",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}.{suffix}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/api/study-materials/{material_id}", status_code=204)
async def delete_study_material(material_id: UUID, request: Request) -> Response:
    user_id, database, _ = await _authorize(request)
    material, access = await material_access(database, material_id, user_id)
    if access is not None:
        require_resource_mutation(
            access,
            user_id,
            material["created_by"],
            "delete",
            resource_scope=material["resource_scope"],
            publication_state=material["publication_state"],
            release_at=material["release_at"],
        )
        if material["kind"] in {"quiz", "practice_exam"}:
            attempts = await database.fetchval(
                "SELECT count(*) FROM quiz_runs WHERE material_id=?1", material_id
            )
            if attempts:
                raise ApiError(
                    409,
                    "assessment_has_attempts",
                    "This shared assessment has student history and cannot be deleted.",
                )
    result = await database.execute("DELETE FROM study_materials WHERE id=?1", material_id)
    if result == "DELETE 0":
        raise ApiError(404, "not_found", "The requested resource was not found.")
    return Response(status_code=204)


def _normalize_answer(value: str) -> str:
    """Normalize text without erasing signs, decimal points, or math operators."""
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s+", " ", normalized)


@router.post("/api/study-materials/{material_id}/attempts")
async def submit_attempt(
    material_id: UUID,
    payload: AttemptSubmission,
    request: Request,
) -> AttemptResult:
    user_id, database, _ = await _authorize(request)
    row, access = await material_access(database, material_id, user_id)
    if row["kind"] not in {"quiz", "practice_exam"}:
        raise ApiError(409, "attempt_not_supported", "Only quizzes and practice exams can be submitted.")
    if row["status"] != "ready":
        raise ApiError(409, "material_not_ready", "Wait for this study material to finish generating.")

    if payload.run_id is not None:
        run = await database.fetchrow(
            "SELECT * FROM quiz_runs WHERE id=?1 AND material_id=?2 AND owner_id=?3",
            payload.run_id, material_id, user_id,
        )
        if run is None:
            raise ApiError(404, "not_found", "The requested quiz run was not found.")
        if run.get("assessment_type") == "mastery_ladder":
            raise ApiError(
                409,
                "ladder_route_required",
                "Use the Mastery Ladder controls to submit this response.",
            )
        if run["mode"] != "standard":
            raise ApiError(
                409,
                "wrong_quiz_mode",
                "Use the Reasoning Mode submission flow for this quiz run.",
            )
        if int(run.get("snapshot_version", 0)) < 1:
            raise ApiError(
                409, "legacy_run_snapshot_unavailable",
                "This older run has no immutable questions. Start a new quiz run.",
            )
        if payload.operation_id is None:
            raise ApiError(400, "operation_id_required", "A submission operation ID is required.")
        answers_digest = hashlib.sha256(
            json.dumps(payload.answers, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        snapshots = await database.fetch(
            "SELECT id,public_snapshot,private_snapshot,family_id,evidence_eligible "
            "FROM quiz_run_questions "
            "WHERE run_id=?1 ORDER BY ordinal,id", payload.run_id,
        )
        snapshot_ids = {str(item["id"]) for item in snapshots}
        if not snapshots or not set(payload.answers).issubset(snapshot_ids):
            raise ApiError(400, "invalid_answers", "The submission contains an unknown question.")
        correct_count = 0
        result_data: list[tuple[dict, dict, str, bool, dict]] = []
        for item in snapshots:
            question_id = str(item["id"])
            question = json_value(item["private_snapshot"], {})
            public = json_value(item["public_snapshot"], {})
            submitted = str(payload.answers.get(question_id, ""))
            if not submitted:
                correct = False
            elif question.get("kind") == "multiple_choice":
                allowed = {str(choice.get("id")) for choice in question.get("choices", [])}
                if submitted not in allowed:
                    raise ApiError(400, "invalid_answer", "Choose one of the listed answers.")
                correct = submitted == str(question.get("answer", ""))
            elif question.get("kind") == "true_false":
                allowed = {str(choice.get("id")) for choice in question.get("choices", [])}
                if submitted not in allowed:
                    raise ApiError(400, "invalid_answer", "Choose True or False.")
                correct = submitted == str(question.get("answer", ""))
            else:
                accepted = [question.get("answer", ""), *(question.get("accepted_answers") or [])]
                correct = any(_normalize_answer(submitted) == _normalize_answer(str(value))
                              for value in accepted)
            correct_count += int(correct)
            result_data.append((question, public, submitted, correct, item))
        score = round(correct_count * 100 / len(snapshots), 2)
        async with database.acquire() as connection:
            async with connection.transaction():
                current = await connection.fetchrow(
                    "SELECT * FROM quiz_runs WHERE id=?1 AND material_id=?2 AND owner_id=?3",
                    payload.run_id, material_id, user_id,
                )
                if current is None:
                    raise ApiError(404, "not_found", "The requested quiz run was not found.")
                if current["status"] == "completed":
                    if (current["submission_operation_id"] != str(payload.operation_id)
                            or current["submission_digest"] != answers_digest):
                        raise ApiError(409, "answer_immutable", "This quiz run already has a submitted answer.")
                elif current["submission_operation_id"] is not None:
                    raise ApiError(
                        409,
                        "idempotency_conflict",
                        "This submission operation ID is already in use.",
                    )
                else:
                    for question, _public, submitted, correct, _snapshot in result_data:
                        question_id = str(question["id"])
                        attempt = await connection.fetchrow(
                            "SELECT id,state FROM quiz_question_attempts "
                            "WHERE run_id=?1 AND question_id=?2",
                            payload.run_id, question_id,
                        )
                        if attempt is None or attempt["state"] not in {
                            "awaiting_reasoning", "reasoning_accepted"
                        }:
                            raise ApiError(
                                409,
                                "attempt_terminal",
                                "A question in this run is already finished.",
                            )
                        await connection.execute(
                            "UPDATE quiz_question_attempts SET state='completed',final_answer=?3,"
                            "answer_status=?4,answer_operation_id=?5,answer_operation_digest=?6,correct=?7,"
                            "submitted_through_turn=0,submitted_snapshot_hash=?8,"
                            "submitted_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                            "completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                            "feedback_status='not_requested',version=version+1,"
                            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?1",
                            attempt["id"], question_id, submitted,
                            "answered" if submitted else "unanswered", str(payload.operation_id),
                            hashlib.sha256(submitted.encode("utf-8")).hexdigest(),
                            int(correct), hashlib.sha256(b"[]").hexdigest(),
                        )
                    await connection.execute(
                        "UPDATE quiz_runs SET submission_operation_id=?2,submission_digest=?3,"
                        "status='completed',score_percent=?4,"
                        "completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                        "WHERE id=?1 AND status='in_progress'",
                        payload.run_id, payload.operation_id, answers_digest, score,
                    )
                    await connection.execute(
                        "INSERT OR IGNORE INTO study_material_attempts "
                        "(id,material_id,owner_id,answers,score_percent) VALUES (?1,?2,?3,?4,?5)",
                        payload.run_id, material_id, user_id, json.dumps(payload.answers), score,
                    )
                    completed_run = await connection.fetchrow(
                        "SELECT * FROM quiz_runs WHERE id=?1 AND owner_id=?2",
                        payload.run_id,
                        user_id,
                    )
                    for question, _public, submitted, correct, snapshot in result_data:
                        question_id = str(question["id"])
                        attempt = await connection.fetchrow(
                            "SELECT * FROM quiz_question_attempts "
                            "WHERE run_id=?1 AND question_id=?2",
                            payload.run_id,
                            question_id,
                        )
                        status = "unanswered" if not submitted else (
                            "correct" if correct else "incorrect"
                        )
                        await record_assessment_outcome(
                            connection,
                            run=completed_run,
                            attempt=attempt,
                            question_row={
                                "id": question_id,
                                "private_snapshot": question,
                                "family_id": snapshot["family_id"],
                                "evidence_eligible": snapshot["evidence_eligible"],
                            },
                            status=status,
                            correctness=(None if not submitted else float(correct)),
                            reasoning_status="none",
                        )
                    answered_count = sum(bool(item[2]) for item in result_data)
                    if answered_count:
                        await award_run_completion(connection, run=completed_run)
                    await materialize_released_evidence_for_run(
                        connection, run_id=str(payload.run_id)
                    )
        released = bool(
            current.get("feedback_release_policy", "after_completion") == "after_completion"
            or await database.fetchval(
                "SELECT 1 FROM study_material_feedback_releases WHERE material_id=?1", material_id
            )
        ) and current.get("feedback_release_policy", "after_completion") != "never"
        results = []
        for question, public, submitted, correct, _snapshot in result_data:
            result = {"question_id": public["id"]}
            if released:
                answer_id = str(question.get("answer", ""))
                answer_choice = next((item for item in public.get("choices", [])
                                      if item.get("id") == answer_id), None)
                selected_choice = next((item for item in public.get("choices", [])
                                        if item.get("id") == submitted), None)
                result.update({
                    "correct": correct if submitted else None,
                    "submitted_answer": selected_choice["text"] if selected_choice else submitted,
                    "correct_answer": (
                        answer_choice["text"]
                        if answer_choice
                        else str(question.get("answer", ""))
                    ),
                    "explanation": str(question.get("explanation", "")),
                    "sources": [],
                })
            results.append(QuestionAttemptResult(**result))
        return AttemptResult(
            attempt_id=payload.run_id,
            score_percent=score if released else None,
            correct_count=correct_count if released else None,
            feedback_available=released,
            question_results=results,
        )

    # Compatibility for old personal quizzes only. Shared resources must use
    # run-pinned snapshots so they cannot be graded against later edits.
    if row["resource_scope"] != "personal" or str(row["owner_id"]) != str(user_id):
        raise ApiError(409, "quiz_run_required", "Start this assessment to submit an answer.")
    if int(row.get("assessment_engine_version") or 0) >= 1:
        raise ApiError(
            409,
            "quiz_run_required",
            "This validated assessment requires an immutable quiz run. Start a new run to submit.",
        )
    if row.get("assessment_validation_state") != "validated":
        raise ApiError(
            409,
            "assessment_unvalidated",
            "This older assessment has no validated grading record. Generate a new quiz "
            "before submitting an answer.",
        )
    content = json_value(row["content"], {})
    questions = content.get("questions", []) if isinstance(content, dict) else []
    question_by_id = {str(question["id"]): question for question in questions}
    if not question_by_id or any(key not in question_by_id for key in payload.answers):
        raise ApiError(400, "invalid_answers", "The submission contains an unknown question.")
    results = []
    correct_count = 0
    for question_id, question in question_by_id.items():
        submitted = payload.answers.get(question_id, "")
        answer = str(question.get("answer", ""))
        if question.get("kind") == "short_answer":
            accepted = [answer, *(question.get("accepted_answers") or [])]
            correct = any(_normalize_answer(submitted) == _normalize_answer(str(value))
                          for value in accepted)
        elif question.get("kind") == "multiple_choice":
            correct = submitted == answer
        else:
            correct = _normalize_answer(submitted) == _normalize_answer(answer)
        correct_count += int(correct)
        results.append((question_id, question, submitted, correct))
    score = round(correct_count * 100 / len(question_by_id), 2)
    attempt_id = await database.fetchval(
        "INSERT INTO study_material_attempts (id,material_id,owner_id,answers,score_percent) "
        "VALUES (?1,?2,?3,?4,?5) RETURNING id",
        uuid4(), material_id, user_id, json.dumps(payload.answers), score,
    )
    released = row.get("feedback_release_policy", "after_completion") == "after_completion"
    if row.get("feedback_release_policy") == "manual":
        released = bool(await database.fetchval(
            "SELECT 1 FROM study_material_feedback_releases WHERE material_id=?1", material_id
        ))
    if row.get("feedback_release_policy") == "never":
        released = False
    return AttemptResult(
        attempt_id=attempt_id,
        score_percent=score if released else None,
        correct_count=correct_count if released else None,
        feedback_available=released,
        question_results=[
            QuestionAttemptResult(
                question_id=question_id,
                correct=correct if released else None,
                submitted_answer=submitted if released else None,
                correct_answer=str(question.get("answer", "")) if released else None,
                explanation=str(question.get("explanation", "")) if released else None,
                sources=[],
            )
            for question_id, question, submitted, correct in results
        ],
    )


@router.post("/api/study-materials/{material_id}/flags", status_code=201)
async def flag_study_item(
    material_id: UUID,
    payload: StudyMaterialFlagCreate,
    request: Request,
) -> dict[str, Any]:
    user_id, database, _ = await _authorize(request)
    row, access = await material_access(database, material_id, user_id)
    if row["class_id"] is None:
        raise ApiError(409, "flag_not_supported", "Private study materials cannot be flagged for class review.")
    content = json_value(row["content"], {})
    items = []
    if isinstance(content, dict):
        items = content.get("questions", []) if row["kind"] in {"quiz", "practice_exam"} else content.get("cards", [])
    if payload.question_id not in {str(item.get("id")) for item in items}:
        raise ApiError(400, "invalid_question", "The selected item is not part of this study material.")
    flag = await database.fetchrow(
        "INSERT INTO study_material_flags (id, material_id, class_id, reporter_id, question_id, reason) "
        "VALUES (?1, ?2, ?3, ?4, ?5, ?6) ON CONFLICT (material_id, reporter_id, question_id) DO NOTHING "
        "RETURNING id, created_at",
        uuid4(),
        material_id,
        row["class_id"],
        user_id,
        payload.question_id,
        payload.reason,
    )
    if flag is None:
        return {"status": "already_flagged"}
    return {"id": flag["id"], "created_at": flag["created_at"]}
