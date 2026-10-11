"""In-page quiz/practice-test hints with server-owned review context."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, Response

from app.api.dependencies import database, enforce_app_request_policy, enforce_sqlite_api_policy
from app.api.errors import ApiError
from app.api.schemas import StudyTutorRequest, StudyTutorResponse
from app.services.database import json_value
from app.services.source_context import source_excerpts

router = APIRouter(tags=["study-tutor"])


async def _remote_context(request: Request, payload: StudyTutorRequest, owner: UUID) -> dict:
    connection = database(request)
    row = await connection.fetchrow(
        "SELECT m.id, m.class_id, m.kind, m.status, m.content, c.name AS class_name, "
        "m.sources_outdated, m.sources_missing FROM study_materials m "
        "JOIN course_classes c ON c.id = m.class_id WHERE m.id = ?1 AND c.owner_id = ?2",
        payload.material_id, owner,
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested study material was not found.")
    if row["kind"] not in {"quiz", "practice_exam"} or row["status"] != "ready":
        raise ApiError(
            409, "review_not_ready", "Only completed quizzes and practice tests can be reviewed.",
        )
    content = json_value(row["content"], {})
    question = next(
        (item for item in content.get("questions", []) if item.get("id") == payload.question_id),
        None,
    )
    if question is None:
        raise ApiError(404, "not_found", "The requested quiz question was not found.")
    attempt = await connection.fetchrow(
        "SELECT answers FROM study_material_attempts WHERE id = ?1 AND material_id = ?2",
        payload.attempt_id, payload.material_id,
    )
    if attempt is None:
        raise ApiError(404, "not_found", "Submit this quiz before asking for review help.")
    answers = json_value(attempt["answers"], {})
    source_rows = await connection.fetch(
        "SELECT n.id, n.title, n.extracted_text, n.summary FROM study_material_sources s "
        "JOIN course_notes n ON n.id = s.note_id AND n.class_id = s.class_id "
        "WHERE s.material_id = ?1 AND s.class_id = ?2 AND n.status = 'ready' ORDER BY s.position",
        row["id"], row["class_id"],
    )
    cited_ids = {str(source["upload_id"]) for source in question.get("sources", [])}
    sources = [
        dict(source) for source in source_rows
        if not cited_ids or str(source["id"]) in cited_ids
    ]
    return {
        "class_name": row["class_name"],
        "question": question,
        "submitted_answer": answers.get(payload.question_id, ""),
        "course_excerpts": source_excerpts(sources, 24_000),
        "sources_changed": bool(row["sources_outdated"]),
        "sources_missing": bool(row["sources_missing"]) or bool(cited_ids - {
            str(source["id"]) for source in sources
        }),
    }


@router.post("/api/study-tutor", response_model=StudyTutorResponse)
async def study_tutor(
    payload: StudyTutorRequest, request: Request, response: Response,
) -> StudyTutorResponse:
    settings = request.app.state.settings
    if payload.material_id is not None:
        owner = await enforce_sqlite_api_policy(request, settings)
    else:
        await enforce_app_request_policy(request, settings)
        owner = getattr(request.state, "user_id", None)
    identity = str(owner or (request.client.host if request.client else "unknown"))
    retry_after = await request.app.state.study_tutor_rate_limiter.check(identity)
    if retry_after is not None:
        raise ApiError(
            429, "study_tutor_rate_limited", "Too many study tutor requests. Try again shortly.",
            retry_after_seconds=retry_after,
        )
    if payload.material_id is not None:
        context = await _remote_context(request, payload, owner)
    else:
        context = {"question": payload.question.model_dump(), "course_excerpts": []}
    service = getattr(request.app.state, "study_tutor_service", None)
    if service is None:
        raise ApiError(503, "study_tutor_unavailable", "The study tutor is unavailable.")
    response.headers["Cache-Control"] = "no-store, private"
    return await service.reply(
        context, payload.messages, request_id=getattr(request.state, "request_id", "unknown"),
    )
