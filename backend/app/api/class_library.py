"""Authenticated class-library, upload, and generated-study endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID

import asyncpg
from fastapi import APIRouter, BackgroundTasks, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse

from app.api.dependencies import (
    database_pool,
    enforce_supabase_api_policy,
    private_storage,
)
from app.api.errors import ApiError
from app.api.schemas import (
    AttemptResult,
    AttemptSubmission,
    ClassCreate,
    ClassPatch,
    ClassSummary,
    MaterialDetail,
    MaterialSummary,
    QuestionAttemptResult,
    StudyMaterialCreate,
    StudyMaterialFlagCreate,
    SyllabusOutline,
    SyllabusOutlineUpdate,
    UploadDetail,
    UploadPatch,
    UploadSummary,
)
from app.config import Settings
from app.services.database import json_value
from app.services.gemini_content import GeminiContentService
from app.services.storage import LocalPrivateStorage, UploadTooLarge

logger = logging.getLogger("screen_tutor.class_library")
router = APIRouter(tags=["class-library"])

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


async def _authorize(request: Request) -> tuple[UUID, asyncpg.Pool, Settings]:
    settings: Settings = request.app.state.settings
    user_id = await enforce_supabase_api_policy(request, settings)
    return user_id, database_pool(request), settings


async def _owned_class(pool: asyncpg.Pool, class_id: UUID, owner_id: UUID) -> asyncpg.Record:
    row = await pool.fetchrow(
        "SELECT id, name, created_at, updated_at FROM public.course_classes "
        "WHERE id = $1 AND owner_id = $2",
        class_id,
        owner_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    return row


async def _class_summary(pool: asyncpg.Pool, row: asyncpg.Record) -> ClassSummary:
    counts = await pool.fetchrow(
        "SELECT "
        "(SELECT count(*) FROM public.course_notes WHERE class_id = $1) AS upload_count, "
        "(SELECT count(*) FROM public.study_materials WHERE class_id = $1) AS material_count",
        row["id"],
    )
    return ClassSummary(
        id=row["id"],
        name=row["name"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        upload_count=counts["upload_count"],
        material_count=counts["material_count"],
    )


def _upload_summary(row: asyncpg.Record) -> UploadSummary:
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
    )


def _material_item_count(content: object, kind: str, fallback: int = 0) -> int:
    data = json_value(content, {})
    if not isinstance(data, dict):
        return fallback
    key = "questions" if kind in {"quiz", "practice_exam"} else "sections" if kind == "study_guide" else "cards"
    items = data.get(key)
    return len(items) if isinstance(items, list) else fallback


def _material_summary(row: asyncpg.Record) -> MaterialSummary:
    values = dict(row)
    latest = None
    if values.get("latest_score") is not None:
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
    )


def _public_material_content(kind: str, value: object) -> dict | None:
    content = json_value(value)
    if not isinstance(content, dict):
        return None
    result = json.loads(json.dumps(content))
    if kind in {"quiz", "practice_exam"}:
        for question in result.get("questions", []):
            for key in ("answer", "accepted_answers", "rubric", "explanation"):
                question.pop(key, None)
    return result


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
    user_id, pool, _ = await _authorize(request)
    rows = await pool.fetch(
        "SELECT c.id, c.name, c.created_at, c.updated_at, "
        "(SELECT count(*) FROM public.course_notes n WHERE n.class_id = c.id) AS upload_count, "
        "(SELECT count(*) FROM public.study_materials m WHERE m.class_id = c.id) AS material_count "
        "FROM public.course_classes c WHERE c.owner_id = $1 ORDER BY c.updated_at DESC, c.id",
        user_id,
    )
    return {"items": [ClassSummary.model_validate(dict(row)) for row in rows]}


@router.post("/api/classes", status_code=201)
async def create_class(payload: ClassCreate, request: Request) -> ClassSummary:
    user_id, pool, settings = await _authorize(request)
    name = payload.name.strip()
    if not name or len(name) > settings.max_class_name_length:
        raise ApiError(400, "invalid_class_name", "Enter a class name within the allowed length.")
    row = await pool.fetchrow(
        "INSERT INTO public.course_classes (owner_id, name) VALUES ($1, $2) "
        "RETURNING id, name, created_at, updated_at",
        user_id,
        name,
    )
    return await _class_summary(pool, row)


@router.patch("/api/classes/{class_id}")
async def rename_class(class_id: UUID, payload: ClassPatch, request: Request) -> ClassSummary:
    user_id, pool, settings = await _authorize(request)
    name = payload.name.strip()
    if not name or len(name) > settings.max_class_name_length:
        raise ApiError(400, "invalid_class_name", "Enter a class name within the allowed length.")
    row = await pool.fetchrow(
        "UPDATE public.course_classes SET name = $3 WHERE id = $1 AND owner_id = $2 "
        "RETURNING id, name, created_at, updated_at",
        class_id,
        user_id,
        name,
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    return await _class_summary(pool, row)


@router.delete("/api/classes/{class_id}", status_code=204)
async def delete_class(class_id: UUID, request: Request) -> Response:
    user_id, pool, _ = await _authorize(request)
    storage = private_storage(request)
    async with pool.acquire() as connection:
        async with connection.transaction():
            rows = await connection.fetch(
                "SELECT n.storage_key FROM public.course_notes n "
                "JOIN public.course_classes c ON c.id = n.class_id "
                "WHERE c.id = $1 AND c.owner_id = $2 AND n.storage_key IS NOT NULL",
                class_id,
                user_id,
            )
            result = await connection.execute(
                "DELETE FROM public.course_classes WHERE id = $1 AND owner_id = $2",
                class_id,
                user_id,
            )
            if result == "DELETE 0":
                raise ApiError(404, "not_found", "The requested resource was not found.")
    for row in rows:
        storage.delete(row["storage_key"])
    return Response(status_code=204)


@router.post("/api/classes/{class_id}/uploads", status_code=202)
async def create_upload(
    class_id: UUID,
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    title: str | None = Form(default=None),
    kind: str | None = Form(default=None),
) -> UploadSummary:
    user_id, pool, settings = await _authorize(request)
    if getattr(request.app.state, "gemini_content_service", None) is None:
        raise ApiError(503, "content_processing_unavailable", "Course material processing is unavailable.")
    await _owned_class(pool, class_id, user_id)
    await _rate_limit(request, "upload_rate_limiter", user_id)
    upload_count = await pool.fetchval(
        "SELECT count(*) FROM public.course_notes WHERE class_id = $1",
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
        row = await pool.fetchrow(
            "INSERT INTO public.course_notes "
            "(class_id, title, kind, source_type, original_filename, mime_type, storage_key, "
            "file_size_bytes, extracted_text, status, processing_progress) "
            "VALUES ($1, $2, $3::course_note_kind, $4::note_source_type, $5, $6, $7, $8, $9, 'processing', 0) "
            "RETURNING *",
            class_id,
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
    background_tasks.add_task(_process_upload_job, request.app, row["id"])
    return _upload_summary(row)


@router.get("/api/classes/{class_id}/uploads")
async def list_uploads(class_id: UUID, request: Request) -> dict[str, list[UploadSummary]]:
    user_id, pool, _ = await _authorize(request)
    await _owned_class(pool, class_id, user_id)
    rows = await pool.fetch(
        "SELECT n.* FROM public.course_notes n "
        "JOIN public.course_classes c ON c.id = n.class_id "
        "WHERE c.id = $1 AND c.owner_id = $2 ORDER BY n.created_at DESC, n.id",
        class_id,
        user_id,
    )
    return {"items": [_upload_summary(row) for row in rows]}


@router.get("/api/uploads/{upload_id}")
async def get_upload(upload_id: UUID, request: Request) -> UploadDetail:
    user_id, pool, _ = await _authorize(request)
    row = await pool.fetchrow(
        "SELECT n.* FROM public.course_notes n "
        "JOIN public.course_classes c ON c.id = n.class_id "
        "WHERE n.id = $1 AND c.owner_id = $2",
        upload_id,
        user_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    file_url = None
    expires_at = None
    if row["storage_key"]:
        storage = private_storage(request)
        file_url, expires = storage.signed_url(str(request.base_url).rstrip("/"), upload_id)
        expires_at = datetime.fromtimestamp(expires, timezone.utc)
    return UploadDetail(
        **_upload_summary(row).model_dump(),
        extracted_text=row["extracted_text"],
        summary=row["summary"],
        key_topics=json_value(row["key_topics"], []),
        transcript_segments=json_value(row["transcript_segments"], []),
        syllabus_outline=json_value(row["syllabus_outline"]),
        file_url=file_url,
        file_url_expires_at=expires_at,
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
    pool = database_pool(request)
    row = await pool.fetchrow(
        "SELECT storage_key, original_filename, mime_type FROM public.course_notes WHERE id = $1",
        upload_id,
    )
    if row is None or not row["storage_key"]:
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
    user_id, pool, _ = await _authorize(request)
    patch = payload.model_dump(exclude_unset=True)
    if not patch or any(value is None for value in patch.values()):
        raise ApiError(400, "invalid_upload_update", "Provide one or more non-empty upload fields.")
    if "extracted_text" in patch and not patch["extracted_text"].strip():
        raise ApiError(400, "invalid_upload_update", "Extracted text cannot be empty.")
    current = await pool.fetchrow(
        "SELECT n.* FROM public.course_notes n "
        "JOIN public.course_classes c ON c.id = n.class_id "
        "WHERE n.id = $1 AND c.owner_id = $2",
        upload_id,
        user_id,
    )
    if current is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    fields: list[str] = []
    values: list[Any] = [upload_id, user_id]
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
            cast = "::course_note_kind" if key == "kind" else ""
            fields.append(f"{key} = ${len(values)}{cast}")
    reprocess = "kind" in patch or "extracted_text" in patch
    if reprocess and getattr(request.app.state, "gemini_content_service", None) is None:
        raise ApiError(503, "content_processing_unavailable", "Course material processing is unavailable.")
    if reprocess:
        fields.extend(["status = 'processing'", "processing_progress = 0", "failure_code = NULL", "processed_at = NULL"])
    query = (
        "UPDATE public.course_notes AS n SET " + ", ".join(fields)
        + " FROM public.course_classes AS c WHERE n.id = $1 AND n.class_id = c.id "
        "AND c.owner_id = $2 RETURNING n.*"
    )
    async with pool.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(query, *values)
            if row is None:
                raise ApiError(404, "not_found", "The requested resource was not found.")
            if reprocess:
                await connection.execute(
                    "DELETE FROM public.study_materials WHERE id IN ("
                    "SELECT material_id FROM public.study_material_sources WHERE note_id = $1)",
                    upload_id,
                )
    if reprocess:
        background_tasks.add_task(
            _reprocess_upload_job,
            request.app,
            upload_id,
            "extracted_text" in patch,
        )
    return _upload_summary(row)


@router.delete("/api/uploads/{upload_id}", status_code=204)
async def delete_upload(upload_id: UUID, request: Request) -> Response:
    user_id, pool, _ = await _authorize(request)
    async with pool.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(
                "SELECT n.storage_key FROM public.course_notes n "
                "JOIN public.course_classes c ON c.id = n.class_id "
                "WHERE n.id = $1 AND c.owner_id = $2 FOR UPDATE OF n",
                upload_id,
                user_id,
            )
            if row is None:
                raise ApiError(404, "not_found", "The requested resource was not found.")
            await connection.execute("DELETE FROM public.course_notes WHERE id = $1", upload_id)
    private_storage(request).delete(row["storage_key"])
    return Response(status_code=204)


@router.post("/api/uploads/{upload_id}/retry", status_code=202)
async def retry_upload(upload_id: UUID, request: Request, background_tasks: BackgroundTasks) -> UploadSummary:
    user_id, pool, _ = await _authorize(request)
    if getattr(request.app.state, "gemini_content_service", None) is None:
        raise ApiError(503, "content_processing_unavailable", "Course material processing is unavailable.")
    row = await pool.fetchrow(
        "UPDATE public.course_notes n SET status = 'processing', processing_progress = 0, "
        "failure_code = NULL, updated_at = now() FROM public.course_classes c "
        "WHERE n.class_id = c.id AND n.id = $1 AND c.owner_id = $2 AND n.status = 'failed' "
        "RETURNING n.*",
        upload_id,
        user_id,
    )
    if row is None:
        current = await pool.fetchrow(
            "SELECT n.status FROM public.course_notes n JOIN public.course_classes c ON c.id = n.class_id "
            "WHERE n.id = $1 AND c.owner_id = $2",
            upload_id,
            user_id,
        )
        if current is None:
            raise ApiError(404, "not_found", "The requested resource was not found.")
        raise ApiError(409, "upload_not_failed", "Only failed uploads can be retried.")
    background_tasks.add_task(_process_upload_job, request.app, upload_id)
    return _upload_summary(row)


async def _process_upload_job(application: Any, upload_id: UUID) -> None:
    """Extract or transcribe one upload, updating only the active processing attempt."""
    pool: asyncpg.Pool | None = getattr(application.state, "database_pool", None)
    content_service = getattr(application.state, "gemini_content_service", None)
    storage: LocalPrivateStorage | None = getattr(application.state, "private_storage", None)
    if pool is None or content_service is None or storage is None:
        return
    try:
        row = await pool.fetchrow(
            "UPDATE public.course_notes SET processing_progress = 10 WHERE id = $1 "
            "AND status = 'processing' RETURNING id, kind, source_type, storage_key, mime_type, extracted_text, updated_at",
            upload_id,
        )
        if row is None:
            return
        if row["source_type"] == "pasted_text":
            result = await asyncio.to_thread(content_service.process_text, row["extracted_text"], row["kind"])
        else:
            path = storage.path_for(row["storage_key"])
            result = await asyncio.to_thread(content_service.process_upload, path, row["kind"], row["mime_type"])
        await pool.execute(
            "UPDATE public.course_notes SET extracted_text = $2, summary = $3, key_topics = $4::jsonb, "
            "transcript_segments = $5::jsonb, duration_seconds = $6, syllabus_outline = $7::jsonb, "
            "status = 'ready', processing_progress = 100, failure_code = NULL, processed_at = now() "
            "WHERE id = $1 AND status = 'processing' AND updated_at = $8",
            upload_id,
            result["extracted_text"],
            result["summary"],
            json.dumps(result["key_topics"]),
            json.dumps(result["transcript_segments"]),
            result["duration_seconds"],
            json.dumps(result["syllabus_outline"]) if result["syllabus_outline"] is not None else None,
            row["updated_at"],
        )
    except Exception as exc:
        logger.error(
            "upload_processing_failed upload_id=%s category=%s",
            upload_id,
            type(exc).__name__,
        )
        try:
            await pool.execute(
                "UPDATE public.course_notes SET status = 'failed', failure_code = 'processing_failed' "
                "WHERE id = $1 AND status = 'processing'",
                upload_id,
            )
        except Exception:
            logger.error("upload_failure_state_update_failed upload_id=%s", upload_id)


async def _reprocess_upload_job(application: Any, upload_id: UUID, use_extracted_text: bool) -> None:
    """Rebuild summaries/outlines after a user edits the text or kind."""
    pool: asyncpg.Pool | None = getattr(application.state, "database_pool", None)
    content_service = getattr(application.state, "gemini_content_service", None)
    storage: LocalPrivateStorage | None = getattr(application.state, "private_storage", None)
    if pool is None or content_service is None or storage is None:
        return
    try:
        row = await pool.fetchrow(
            "UPDATE public.course_notes SET processing_progress = 10 WHERE id = $1 "
            "AND status = 'processing' RETURNING id, kind, source_type, storage_key, mime_type, extracted_text, updated_at",
            upload_id,
        )
        if row is None:
            return
        if use_extracted_text or row["source_type"] == "pasted_text":
            result = await asyncio.to_thread(content_service.process_text, row["extracted_text"], row["kind"])
        else:
            result = await asyncio.to_thread(
                content_service.process_upload,
                storage.path_for(row["storage_key"]),
                row["kind"],
                row["mime_type"],
            )
        await pool.execute(
            "UPDATE public.course_notes SET extracted_text = $2, summary = $3, key_topics = $4::jsonb, "
            "transcript_segments = $5::jsonb, duration_seconds = $6, syllabus_outline = $7::jsonb, "
            "status = 'ready', processing_progress = 100, failure_code = NULL, processed_at = now() "
            "WHERE id = $1 AND status = 'processing' AND updated_at = $8",
            upload_id,
            result["extracted_text"],
            result["summary"],
            json.dumps(result["key_topics"]),
            json.dumps(result["transcript_segments"]),
            result["duration_seconds"],
            json.dumps(result["syllabus_outline"]) if result["syllabus_outline"] is not None else None,
            row["updated_at"],
        )
    except Exception as exc:
        logger.error(
            "upload_reprocessing_failed upload_id=%s category=%s",
            upload_id,
            type(exc).__name__,
        )
        try:
            await pool.execute(
                "UPDATE public.course_notes SET status = 'failed', failure_code = 'processing_failed' "
                "WHERE id = $1 AND status = 'processing'",
                upload_id,
            )
        except Exception:
            logger.error("upload_failure_state_update_failed upload_id=%s", upload_id)


@router.get("/api/classes/{class_id}/syllabus")
async def get_syllabus(class_id: UUID, request: Request) -> SyllabusOutline:
    user_id, pool, _ = await _authorize(request)
    await _owned_class(pool, class_id, user_id)
    row = await pool.fetchrow(
        "SELECT n.id, n.syllabus_outline FROM public.course_notes n "
        "WHERE n.class_id = $1 AND n.kind = 'syllabus' AND n.status = 'ready' "
        "ORDER BY n.updated_at DESC LIMIT 1",
        class_id,
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
    user_id, pool, _ = await _authorize(request)
    await _owned_class(pool, class_id, user_id)
    row = await pool.fetchrow(
        "SELECT id FROM public.course_notes WHERE class_id = $1 AND kind = 'syllabus' "
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
    await pool.execute(
        "UPDATE public.course_notes SET syllabus_outline = $2::jsonb WHERE id = $1",
        row["id"],
        json.dumps(outline),
    )
    return SyllabusOutline(note_id=row["id"], **outline)


async def _resolve_material_sources(
    pool: asyncpg.Pool,
    class_id: UUID,
    scope: dict,
) -> tuple[list[asyncpg.Record], dict]:
    scope_kind = scope.get("kind")
    if scope_kind == "all":
        rows = await pool.fetch(
            "SELECT id, title, kind, extracted_text, summary, status FROM public.course_notes "
            "WHERE class_id = $1 AND status = 'ready' ORDER BY created_at",
            class_id,
        )
    elif scope_kind == "uploads":
        ids = [UUID(value) for value in scope.get("upload_ids", [])]
        rows = await pool.fetch(
            "SELECT id, title, kind, extracted_text, summary, status FROM public.course_notes "
            "WHERE class_id = $1 AND id = ANY($2::uuid[]) ORDER BY created_at",
            class_id,
            ids,
        )
        if len(rows) != len(ids):
            raise ApiError(404, "not_found", "The requested resource was not found.")
        if any(row["status"] != "ready" for row in rows):
            raise ApiError(409, "uploads_not_ready", "Wait for the selected uploads to finish processing.")
    elif scope_kind in {"topics", "exam"}:
        syllabus = await pool.fetchrow(
            "SELECT syllabus_outline FROM public.course_notes WHERE class_id = $1 "
            "AND kind = 'syllabus' AND status = 'ready' ORDER BY updated_at DESC LIMIT 1",
            class_id,
        )
        if syllabus is None:
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
        rows = await pool.fetch(
            "SELECT id, title, kind, extracted_text, summary, status FROM public.course_notes "
            "WHERE class_id = $1 AND status = 'ready' ORDER BY created_at",
            class_id,
        )
    else:
        raise ApiError(400, "invalid_scope", "The requested study scope is not supported.")
    if not rows:
        raise ApiError(409, "no_ready_uploads", "Add at least one processed course upload before generating study material.")
    return list(rows), scope


async def _create_material_job(
    application: Any,
    material_id: UUID,
) -> None:
    pool: asyncpg.Pool | None = getattr(application.state, "database_pool", None)
    service: GeminiContentService | None = getattr(application.state, "gemini_content_service", None)
    if pool is None or service is None:
        return
    try:
        material = await pool.fetchrow(
            "SELECT m.id, m.class_id, m.kind, m.scope, m.options, c.name AS class_name "
            "FROM public.study_materials m JOIN public.course_classes c ON c.id = m.class_id "
            "WHERE m.id = $1",
            material_id,
        )
        if material is None:
            return
        source_rows = await pool.fetch(
            "SELECT n.id, n.title, n.extracted_text, n.summary FROM public.study_material_sources s "
            "JOIN public.course_notes n ON n.id = s.note_id AND n.class_id = s.class_id "
            "WHERE s.material_id = $1 ORDER BY s.position",
            material_id,
        )
        sources = [dict(row) for row in source_rows]
        title, content = await asyncio.to_thread(
            service.generate_material,
            kind=material["kind"],
            class_name=material["class_name"],
            scope=json_value(material["scope"], {}),
            options=json_value(material["options"], {}),
            sources=sources,
        )
        await pool.execute(
            "UPDATE public.study_materials SET title = $2, content = $3::jsonb, status = 'ready', "
            "failure_code = NULL, generated_at = now(), updated_at = now() WHERE id = $1",
            material_id,
            title,
            json.dumps(content),
        )
    except Exception as exc:
        logger.error(
            "study_generation_failed material_id=%s category=%s",
            material_id,
            type(exc).__name__,
        )
        await pool.execute(
            "UPDATE public.study_materials SET status = 'failed', failure_code = 'generation_failed', "
            "updated_at = now() WHERE id = $1 AND status = 'generating'",
            material_id,
        )


@router.post("/api/classes/{class_id}/study-materials", status_code=202)
async def create_study_material(
    class_id: UUID,
    payload: StudyMaterialCreate,
    request: Request,
    background_tasks: BackgroundTasks,
) -> MaterialSummary:
    user_id, pool, _ = await _authorize(request)
    class_row = await _owned_class(pool, class_id, user_id)
    if getattr(request.app.state, "gemini_content_service", None) is None:
        raise ApiError(503, "content_processing_unavailable", "Study material generation is unavailable.")
    await _rate_limit(request, "generation_rate_limiter", user_id)
    scope = payload.scope.model_dump(mode="json")
    options = payload.options.model_dump(mode="json", exclude_none=True)
    if payload.kind != "practice_exam" and options.get("time_limit_minutes") is not None:
        raise ApiError(400, "invalid_options", "A time limit is available only for practice exams.")
    sources, scope = await _resolve_material_sources(pool, class_id, scope)
    title = f"{payload.kind.replace('_', ' ').title()}: {class_row['name']}"
    async with pool.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(
                "INSERT INTO public.study_materials (class_id, kind, title, status, scope, options) "
                "VALUES ($1, $2::study_material_kind, $3, 'generating', $4::jsonb, $5::jsonb) "
                "RETURNING id, class_id, kind, title, scope, options, status, failure_code, created_at, generated_at",
                class_id,
                payload.kind,
                title,
                json.dumps(scope),
                json.dumps(options),
            )
            await connection.executemany(
                "INSERT INTO public.study_material_sources (material_id, class_id, note_id, position) "
                "VALUES ($1, $2, $3, $4)",
                [(row["id"], class_id, source["id"], index) for index, source in enumerate(sources)],
            )
    background_tasks.add_task(_create_material_job, request.app, row["id"])
    return MaterialSummary(
        id=row["id"],
        class_id=row["class_id"],
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
async def list_study_materials(class_id: UUID, request: Request) -> dict[str, list[MaterialSummary]]:
    user_id, pool, _ = await _authorize(request)
    await _owned_class(pool, class_id, user_id)
    rows = await pool.fetch(
        "SELECT m.*, COALESCE((m.options->>'count')::integer, 0) AS requested_count, "
        "latest.score_percent AS latest_score, latest.completed_at AS latest_completed_at "
        "FROM public.study_materials m "
        "LEFT JOIN LATERAL (SELECT a.score_percent, a.completed_at FROM public.study_material_attempts a "
        "WHERE a.material_id = m.id ORDER BY a.completed_at DESC LIMIT 1) latest ON true "
        "WHERE m.class_id = $1 ORDER BY m.created_at DESC, m.id",
        class_id,
    )
    return {"items": [_material_summary(row) for row in rows]}


@router.get("/api/study-materials/{material_id}")
async def get_study_material(material_id: UUID, request: Request) -> MaterialDetail:
    user_id, pool, _ = await _authorize(request)
    row = await pool.fetchrow(
        "SELECT m.*, COALESCE((m.options->>'count')::integer, 0) AS requested_count, "
        "latest.score_percent AS latest_score, latest.completed_at AS latest_completed_at "
        "FROM public.study_materials m JOIN public.course_classes c ON c.id = m.class_id "
        "LEFT JOIN LATERAL (SELECT a.score_percent, a.completed_at FROM public.study_material_attempts a "
        "WHERE a.material_id = m.id ORDER BY a.completed_at DESC LIMIT 1) latest ON true "
        "WHERE m.id = $1 AND c.owner_id = $2",
        material_id,
        user_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    summary = _material_summary(row)
    return MaterialDetail(
        **summary.model_dump(),
        content=_public_material_content(row["kind"], row["content"]),
    )


@router.delete("/api/study-materials/{material_id}", status_code=204)
async def delete_study_material(material_id: UUID, request: Request) -> Response:
    user_id, pool, _ = await _authorize(request)
    result = await pool.execute(
        "DELETE FROM public.study_materials m USING public.course_classes c "
        "WHERE m.class_id = c.id AND m.id = $1 AND c.owner_id = $2",
        material_id,
        user_id,
    )
    if result == "DELETE 0":
        raise ApiError(404, "not_found", "The requested resource was not found.")
    return Response(status_code=204)


def _normalize_answer(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[^\w]+", "", normalized, flags=re.UNICODE)


@router.post("/api/study-materials/{material_id}/attempts")
async def submit_attempt(
    material_id: UUID,
    payload: AttemptSubmission,
    request: Request,
) -> AttemptResult:
    user_id, pool, _ = await _authorize(request)
    row = await pool.fetchrow(
        "SELECT m.id, m.kind, m.status, m.content FROM public.study_materials m "
        "JOIN public.course_classes c ON c.id = m.class_id "
        "WHERE m.id = $1 AND c.owner_id = $2",
        material_id,
        user_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if row["kind"] not in {"quiz", "practice_exam"}:
        raise ApiError(409, "attempt_not_supported", "Only quizzes and practice exams can be submitted.")
    if row["status"] != "ready":
        raise ApiError(409, "material_not_ready", "Wait for this study material to finish generating.")
    content = json_value(row["content"], {})
    questions = content.get("questions", []) if isinstance(content, dict) else []
    question_by_id = {str(question["id"]): question for question in questions}
    if not question_by_id or any(question_id not in question_by_id for question_id in payload.answers):
        raise ApiError(400, "invalid_answers", "The submission contains an unknown question.")

    results: list[QuestionAttemptResult] = []
    correct_count = 0
    for question_id, question in question_by_id.items():
        submitted = payload.answers.get(question_id)
        answer = str(question.get("answer", ""))
        if submitted is None:
            correct = False
        elif question["kind"] == "short_answer":
            accepted = question.get("accepted_answers") or [answer]
            correct = _normalize_answer(submitted) in {_normalize_answer(str(value)) for value in accepted}
        else:
            correct = _normalize_answer(submitted) == _normalize_answer(answer)
        correct_count += int(correct)
        correct_answer = answer
        if question["kind"] == "multiple_choice":
            choice = next((choice for choice in question.get("choices", []) if choice.get("id") == answer), None)
            if choice:
                correct_answer = choice["text"]
        results.append(
            QuestionAttemptResult(
                question_id=question_id,
                correct=correct,
                submitted_answer=submitted,
                correct_answer=correct_answer,
                explanation=question.get("explanation", ""),
                sources=question.get("sources", []),
            )
        )
    score = round(correct_count * 100 / len(question_by_id), 2)
    attempt_id = await pool.fetchval(
        "INSERT INTO public.study_material_attempts (material_id, answers, score_percent) "
        "VALUES ($1, $2::jsonb, $3) RETURNING id",
        material_id,
        json.dumps(payload.answers),
        score,
    )
    return AttemptResult(
        attempt_id=attempt_id,
        score_percent=score,
        correct_count=correct_count,
        question_results=results,
    )


@router.post("/api/study-materials/{material_id}/flags", status_code=201)
async def flag_study_item(
    material_id: UUID,
    payload: StudyMaterialFlagCreate,
    request: Request,
) -> dict[str, Any]:
    user_id, pool, _ = await _authorize(request)
    row = await pool.fetchrow(
        "SELECT m.class_id, m.kind, m.status, m.content FROM public.study_materials m "
        "JOIN public.course_classes c ON c.id = m.class_id "
        "WHERE m.id = $1 AND c.owner_id = $2",
        material_id,
        user_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    content = json_value(row["content"], {})
    items = []
    if isinstance(content, dict):
        items = content.get("questions", []) if row["kind"] in {"quiz", "practice_exam"} else content.get("cards", [])
    if payload.question_id not in {str(item.get("id")) for item in items}:
        raise ApiError(400, "invalid_question", "The selected item is not part of this study material.")
    flag = await pool.fetchrow(
        "INSERT INTO public.study_material_flags (material_id, class_id, reporter_id, question_id, reason) "
        "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (material_id, reporter_id, question_id) DO NOTHING "
        "RETURNING id, created_at",
        material_id,
        row["class_id"],
        user_id,
        payload.question_id,
        payload.reason,
    )
    if flag is None:
        return {"status": "already_flagged"}
    return {"id": flag["id"], "created_at": flag["created_at"]}
