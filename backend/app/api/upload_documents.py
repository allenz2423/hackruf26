"""Saved, source-preserving companion documents for course uploads."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Request, Response

from app.api.dependencies import database, enforce_sqlite_api_policy
from app.api.errors import ApiError
from app.api.schemas import DocumentDetailLevel, UploadDocumentContent, UploadDocumentRequest
from app.services.class_access import (
    can_view_assessment_key,
    can_view_unreleased,
    class_access,
    is_effectively_published,
    is_private_assessment,
)
from app.services.database import SQLiteDatabase, json_value
from app.services.gemini_content import ContentGenerationFailure
from app.services.study_files import compiler_path, create_guide_files

router = APIRouter(tags=["upload-documents"])
logger = logging.getLogger("screen_tutor.upload_documents")
_CHUNK_CHARS = 40_000


def source_chunks(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + _CHUNK_CHARS, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + _CHUNK_CHARS // 2, end)
            if boundary < 0:
                boundary = text.rfind(" ", start + _CHUNK_CHARS // 2, end)
            if boundary >= 0:
                end = boundary + 1
        chunks.append(text[start:end])
        start = end
    return chunks


async def _owned_upload(request: Request, upload_id: UUID) -> tuple[SQLiteDatabase, dict]:
    settings = request.app.state.settings
    owner = await enforce_sqlite_api_policy(request, settings)
    db = database(request)
    note = await db.fetchrow(
        "SELECT n.*,c.name AS class_name FROM course_notes n JOIN course_classes c ON c.id=n.class_id "
        "WHERE n.id=?1",
        upload_id,
    )
    if note is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    access = await class_access(db, note["class_id"], owner, required="read")
    if not is_effectively_published(note["publication_state"], note["release_at"]) and not can_view_unreleased(
        access, owner, note["created_by"]
    ):
        raise ApiError(404, "not_found", "The requested resource was not found.")
    assessment = await db.fetchrow(
        "SELECT a.owner_id,a.answer_key_release_state,a.answer_key_release_at,a.has_student_responses,"
        "a.score_percent,a.grading_result,n.created_by,n.resource_scope "
        "FROM uploaded_assessments a JOIN course_notes n ON n.id=a.note_id WHERE a.note_id=?1",
        upload_id,
    )
    if assessment and is_private_assessment(assessment) and str(assessment["owner_id"]) != str(owner):
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if assessment and not can_view_assessment_key({**assessment, "created_by": assessment["created_by"]}, access, owner):
        raise ApiError(404, "not_found", "The requested resource was not found.")
    note["class_capabilities"] = access["capabilities"]
    note["class_is_manager"] = access["is_owner"] or "manage_permissions" in access["capabilities"]
    return db, note


async def document_detail(db: SQLiteDatabase, upload_id: UUID, level: str = "balanced") -> dict:
    row = await db.fetchrow(
        "SELECT d.status, d.progress, d.content, d.failure_code, d.updated_at "
        "FROM upload_documents d "
        "JOIN course_notes n ON n.id = d.note_id AND n.updated_at = d.source_updated_at "
        "WHERE d.note_id = ?1 AND d.detail_level = ?2",
        upload_id,
        level,
    )
    detail = {
        "upload_id": str(upload_id),
        "detail_level": level,
        "status": "not_created",
        "progress": 0,
        "content": None,
        "failure_code": None,
        "download_urls": {},
    }
    if row:
        detail.update({**row, "content": json_value(row["content"])})
        if row["status"] == "ready":
            detail["download_urls"] = {
                format: f"/api/uploads/{upload_id}/document/files/{format}?detail_level={level}"
                for format in ("pdf", "latex", "original_text")
            }
    return detail


async def _reserve_document(db: SQLiteDatabase, upload_id: UUID, level: str) -> tuple[str, bool]:
    async with db.acquire() as connection:
        async with connection.transaction():
            note = await connection.fetchrow(
                "SELECT extracted_text, updated_at, status FROM course_notes WHERE id = ?1",
                upload_id,
            )
            if not note or note["status"] != "ready" or not note["extracted_text"]:
                raise ApiError(
                    409, "upload_not_ready", "Wait for this upload to finish processing first."
                )
            existing = await connection.fetchrow(
                "SELECT job_id, status, source_updated_at FROM upload_documents WHERE "
                "note_id = ?1 AND detail_level = ?2",
                upload_id,
                level,
            )
            if (
                existing
                and existing["source_updated_at"] == note["updated_at"]
                and existing["status"] in {"generating", "ready"}
            ):
                return existing["job_id"], False
            job_id = str(uuid4())
            await connection.execute(
                "INSERT INTO upload_documents (note_id, detail_level, job_id, "
                "source_updated_at, status, original_text) "
                "VALUES (?1, ?2, ?3, ?4, 'generating', ?5) ON CONFLICT(note_id, "
                "detail_level) DO UPDATE SET "
                "job_id = excluded.job_id, source_updated_at = "
                "excluded.source_updated_at, status = 'generating', "
                "progress = 0, content = NULL, pdf = NULL, latex = NULL, failure_code = NULL, "
                "original_text = excluded.original_text, updated_at = "
                "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
                upload_id,
                level,
                job_id,
                note["updated_at"],
                note["extracted_text"],
            )
            return job_id, True


async def _create_upload_document_job(
    application: Any, upload_id: UUID, level: str, job_id: str
) -> None:
    db = application.state.database
    generation_stage = "load_source"
    try:
        row = await db.fetchrow(
            "SELECT d.original_text, d.source_updated_at, n.title, c.name AS "
            "class_name,n.storage_key,n.mime_type FROM upload_documents d "
            "JOIN course_notes n ON n.id = d.note_id AND n.updated_at = "
            "d.source_updated_at AND n.status = 'ready' "
            "JOIN course_classes c ON c.id = n.class_id "
            "WHERE d.note_id = ?1 AND d.detail_level = ?2 AND d.job_id = ?3 AND "
            "d.status = 'generating'",
            upload_id,
            level,
            job_id,
        )
        if row is None:
            return
        text = row["original_text"]
        # Process every chunk. Never silently discard a long document's later pages.
        chunks = source_chunks(text)
        service = application.state.gemini_content_service
        source_visuals: list[object] = []
        storage = getattr(application.state, "private_storage", None)
        if storage is not None and row.get("storage_key"):
            try:
                original_path = storage.path_for(str(row["storage_key"]))
                source_visuals = await asyncio.to_thread(
                    service._generation_source_visuals,
                    [
                        {
                            "id": str(upload_id),
                            "title": str(row["title"]),
                            "kind": str(row.get("mime_type") or ""),
                            "mime_type": str(row.get("mime_type") or ""),
                            "original_path": original_path,
                            "extracted_text": text,
                        }
                    ],
                    row["title"],
                )
            except ValueError:
                # A missing original file must not block the extracted-text companion.
                source_visuals = []
        visuals_by_chunk: list[list[object]] = [[] for _ in chunks]
        for visual_index, visual in enumerate(source_visuals):
            label = str(getattr(visual, "label", ""))
            locator = re.search(r"\b(page|slide)\s+(\d+)\b", label, re.IGNORECASE)
            matching_chunk = None
            if locator:
                marker = re.compile(
                    rf"^\[{locator.group(1)}\s+{int(locator.group(2))}\]\s*$",
                    re.IGNORECASE | re.MULTILINE,
                )
                matching_chunk = next(
                    (index for index, chunk in enumerate(chunks) if marker.search(chunk)),
                    None,
                )
            target_chunk = (
                matching_chunk
                if matching_chunk is not None
                else visual_index % len(chunks)
            )
            visuals_by_chunk[target_chunk].append(visual)
        sections = []
        generation_stage = "draft_and_revision"
        for part, chunk in enumerate(chunks, 1):
            sections.extend(
                await asyncio.to_thread(
                    application.state.gemini_content_service.generate_upload_document_chunk,
                    text=chunk,
                    upload_id=str(upload_id),
                    title=row["title"],
                    detail_level=level,
                    part=part,
                    total_parts=len(chunks),
                    source_visuals=visuals_by_chunk[part - 1],
                )
            )
            progress = await db.execute(
                "UPDATE upload_documents SET progress = ?4 WHERE note_id = ?1 AND "
                "detail_level = ?2 "
                "AND job_id = ?3 AND status = 'generating' AND EXISTS (SELECT 1 FROM "
                "course_notes n "
                "WHERE n.id = note_id AND n.status = 'ready' AND n.updated_at = source_updated_at)",
                upload_id,
                level,
                job_id,
                int(part * 85 / len(chunks)),
            )
            if progress == "UPDATE 0":
                return
        content = UploadDocumentContent(detail_level=level, sections=sections).model_dump(
            mode="json"
        )
        generation_stage = "typesetting"
        files = await asyncio.to_thread(
            create_guide_files,
            application.state.settings,
            title=f"Study document: {row['title']}",
            class_name=row["class_name"],
            material_id=upload_id,
            content=content,
            sources=[{"id": upload_id, "title": row["title"]}],
            original_text=text,
        )
        generation_stage = "persist"
        await db.execute(
            "UPDATE upload_documents SET status = 'ready', progress = 100, content = "
            "?4, pdf = ?5, latex = ?6, "
            "failure_code = NULL, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE note_id = ?1 AND detail_level = ?2 AND job_id = ?3 AND status = 'generating' "
            "AND EXISTS (SELECT 1 FROM course_notes n WHERE n.id = note_id AND "
            "n.status = 'ready' AND n.updated_at = source_updated_at)",
            upload_id,
            level,
            job_id,
            json.dumps(content),
            files["pdf"],
            files["latex"],
        )
    except Exception as exc:
        failure_code = (
            exc.code
            if isinstance(exc, ContentGenerationFailure)
            and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", exc.code)
            else "document_generation_failed"
        )
        logger.error(
            "upload_document_failed upload_id=%s stage=%s category=%s error_code=%s",
            upload_id,
            generation_stage,
            type(exc).__name__,
            failure_code,
        )
        await db.execute(
            "UPDATE upload_documents SET status = 'failed', failure_code = "
            "?4, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE note_id = ?1 "
            "AND detail_level = ?2 AND job_id = ?3 AND status = 'generating'",
            upload_id,
            level,
            job_id,
            failure_code,
        )


async def create_default_document(application: Any, upload_id: UUID) -> None:
    """An independent companion failure never marks a successfully extracted upload failed."""
    try:
        job_id, created = await _reserve_document(application.state.database, upload_id, "balanced")
        if created:
            await _create_upload_document_job(application, upload_id, "balanced", job_id)
    except Exception as exc:
        logger.error(
            "upload_document_start_failed upload_id=%s category=%s", upload_id, type(exc).__name__
        )


@router.get("/api/uploads/{upload_id}/document")
async def get_upload_document(
    upload_id: UUID, request: Request, detail_level: DocumentDetailLevel = "balanced"
) -> dict:
    db, _ = await _owned_upload(request, upload_id)
    return await document_detail(db, upload_id, detail_level)


@router.post("/api/uploads/{upload_id}/document", status_code=202)
async def create_upload_document(
    upload_id: UUID,
    payload: UploadDocumentRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    db, note = await _owned_upload(request, upload_id)
    existing = await document_detail(db, upload_id, payload.detail_level)
    if existing["status"] in {"ready", "generating"}:
        return existing
    if not note.get("class_is_manager") and "create" not in note.get("class_capabilities", []):
        raise ApiError(403, "insufficient_permission", "You need class create permission to generate a shared study document.")
    if (
        not compiler_path(request.app.state.settings)
        or getattr(request.app.state, "gemini_content_service", None) is None
    ):
        raise ApiError(
            503, "document_generation_unavailable", "Upload document generation is unavailable."
        )
    retry_after = await request.app.state.generation_rate_limiter.check(
        str(await enforce_sqlite_api_policy(request, request.app.state.settings))
    )
    if retry_after is not None:
        raise ApiError(
            429,
            "generation_rate_limited",
            "Too many requests. Please wait before trying again.",
            retry_after,
        )
    job_id, created = await _reserve_document(db, upload_id, payload.detail_level)
    if created:
        background_tasks.add_task(
            _create_upload_document_job, request.app, upload_id, payload.detail_level, job_id
        )
    return await document_detail(db, upload_id, payload.detail_level)


@router.get("/api/uploads/{upload_id}/document/files/{format}")
async def download_upload_document(
    upload_id: UUID,
    format: Literal["pdf", "latex", "original_text"],
    request: Request,
    detail_level: DocumentDetailLevel = "balanced",
) -> Response:
    db, note = await _owned_upload(request, upload_id)
    row = await db.fetchrow(
        "SELECT pdf, latex, original_text FROM upload_documents "
        "WHERE note_id = ?1 AND detail_level = ?2 AND source_updated_at = ?3 AND status = 'ready'",
        upload_id,
        detail_level,
        note["updated_at"],
    )
    if row is None:
        raise ApiError(409, "document_not_ready", "Generate this document before downloading it.")
    extensions = {"pdf": "pdf", "latex": "tex", "original_text": "txt"}
    mime_types = {
        "pdf": "application/pdf",
        "latex": "application/x-tex",
        "original_text": "text/plain; charset=utf-8",
    }
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", note["title"]).strip(".-")[:80] or "upload"
    attachment = f"{filename}-{detail_level}.{extensions[format]}"
    content = row[format].encode("utf-8") if format == "original_text" else row[format]
    return Response(
        content=content,
        media_type=mime_types[format],
        headers={
            "Content-Disposition": f'attachment; filename="{attachment}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
