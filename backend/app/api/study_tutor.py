"""In-page quiz/practice-test hints with server-owned review context."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, Response

from app.api.dependencies import database, enforce_app_request_policy, enforce_sqlite_api_policy
from app.api.errors import ApiError
from app.api.schemas import StudyTutorRequest, StudyTutorResponse
from app.services.class_access import (
    can_view_assessment_key,
    can_view_unreleased,
    class_access,
    is_effectively_published,
    material_access,
)
from app.services.database import json_value
from app.services.source_context import source_excerpts
from app.services.source_index import fetch_authorized_chunk

router = APIRouter(tags=["study-tutor"])


async def _remote_context(request: Request, payload: StudyTutorRequest, owner: UUID) -> dict:
    connection = database(request)
    _, class_permissions = await material_access(connection, payload.material_id, owner)
    row = await connection.fetchrow(
        "SELECT m.id,m.class_id,m.origin_class_id,m.resource_scope,m.owner_id,m.created_by,"
        "m.kind,m.status,m.content,m.feedback_release_policy,"
        "m.sources_outdated,m.sources_missing,c.name AS class_name "
        "FROM study_materials m LEFT JOIN course_classes c "
        "ON c.id=COALESCE(m.class_id,m.origin_class_id) "
        "WHERE m.id=?1",
        payload.material_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested study material was not found.")
    if row["kind"] not in {"quiz", "practice_exam"} or row["status"] != "ready":
        raise ApiError(
            409, "review_not_ready", "Only completed quizzes and practice tests can be reviewed.",
        )
    run = await connection.fetchrow(
        "SELECT * FROM quiz_runs WHERE id=?1 AND material_id=?2 AND owner_id=?3",
        payload.attempt_id, payload.material_id, owner,
    )
    feedback_released = False
    source_question: dict = {}
    submitted_answer = ""
    if run is not None:
        if int(run.get("snapshot_version", 0)) < 1:
            raise ApiError(
                409, "legacy_run_snapshot_unavailable",
                "Review is unavailable for this older quiz run.",
            )
        question_row = await connection.fetchrow(
            "SELECT public_snapshot,private_snapshot FROM quiz_run_questions "
            "WHERE run_id=?1 AND id=?2",
            payload.attempt_id, payload.question_id,
        )
        attempt = await connection.fetchrow(
            "SELECT state,final_answer FROM quiz_question_attempts "
            "WHERE run_id=?1 AND question_id=?2",
            payload.attempt_id, payload.question_id,
        )
        if question_row is None or attempt is None or attempt["state"] != "completed":
            raise ApiError(404, "not_found", "Submit this question before asking for review help.")
        source_question = json_value(question_row["private_snapshot"], {})
        submitted_answer = str(attempt["final_answer"] or "")
        if source_question.get("kind") == "multiple_choice":
            selected = next(
                (item.get("text") for item in source_question.get("choices", [])
                 if item.get("id") == submitted_answer),
                "",
            )
            submitted_answer = str(selected)
        feedback_released = bool(
            (run["feedback_release_policy"] == "after_completion" and run["status"] == "completed")
            or (
                run["feedback_release_policy"] == "manual"
                and await connection.fetchval(
                    "SELECT 1 FROM study_material_feedback_releases WHERE material_id=?1",
                    payload.material_id,
                )
            )
        )
        if run["feedback_release_policy"] == "never":
            feedback_released = False
    else:
        # Historical no-run attempts are usable only by their explicitly
        # attributed owner. Unattributed attempts are never guessed or shared.
        legacy_attempt = await connection.fetchrow(
            "SELECT answers FROM study_material_attempts "
            "WHERE id=?1 AND material_id=?2 AND owner_id=?3",
            payload.attempt_id, payload.material_id, owner,
        )
        if legacy_attempt is None:
            raise ApiError(404, "not_found", "Submit this quiz before asking for review help.")
        content = json_value(row["content"], {})
        source_question = next(
            (item for item in content.get("questions", [])
             if str(item.get("id")) == str(payload.question_id)),
            None,
        )
        if source_question is None:
            raise ApiError(404, "not_found", "The requested quiz question was not found.")
        submitted_answer = str(
            json_value(legacy_attempt["answers"], {}).get(payload.question_id, "")
        )
        feedback_released = row["feedback_release_policy"] == "after_completion"
        if row["feedback_release_policy"] == "manual":
            feedback_released = bool(await connection.fetchval(
                "SELECT 1 FROM study_material_feedback_releases WHERE material_id=?1",
                payload.material_id,
            ))
        if row["feedback_release_policy"] == "never":
            feedback_released = False
    source_class_id = row["class_id"] or row["origin_class_id"]
    sources: list[dict] = []
    missing_pinned_sources = False
    cited_ids = {str(source["upload_id"]) for source in source_question.get("sources", [])}
    if feedback_released and run is not None:
        # A modern run pins exact source chunk revisions with its immutable
        # private snapshot. Resolve only those revisions through the normal
        # current-access check; never substitute a source's latest extracted
        # text when a pinned revision is no longer accessible.
        pinned_ids = source_question.get("source_chunk_revision_ids", [])
        if not isinstance(pinned_ids, list):
            pinned_ids = []
            missing_pinned_sources = True
        if source_class_id:
            seen_revision_ids: set[str] = set()
            for revision_id in pinned_ids:
                revision_key = str(revision_id)
                if revision_key in seen_revision_ids:
                    continue
                seen_revision_ids.add(revision_key)
                try:
                    chunk = await fetch_authorized_chunk(
                        connection,
                        class_id=str(source_class_id),
                        user_id=str(owner),
                        chunk_revision_id=revision_key,
                    )
                except ApiError:
                    missing_pinned_sources = True
                    continue
                if cited_ids and str(chunk["material_id"]) not in cited_ids:
                    missing_pinned_sources = True
                    continue
                sources.append(
                    {
                        "id": str(chunk["material_id"]),
                        "title": str(chunk["title"]),
                        "extracted_text": str(chunk["text"]),
                    }
                )
        elif pinned_ids:
            # A run cannot safely resolve class-indexed citations without its
            # class authorization context.
            missing_pinned_sources = True
    elif feedback_released:
        # Compatibility for owner-attributed legacy attempts, which predate
        # immutable run snapshots and exact chunk citations.
        source_rows = []
        if source_class_id:
            try:
                source_permissions = await class_access(
                    connection, source_class_id, owner, required="read"
                )
            except ApiError:
                source_permissions = None
            if source_permissions is not None:
                source_rows = await connection.fetch(
                    "SELECT n.id,n.title,n.extracted_text,n.summary,n.created_by,"
                    "n.publication_state,"
                    "n.release_at,n.resource_scope,"
                    "a.owner_id,a.has_student_responses,a.score_percent,a.grading_result,"
                    "a.answer_key_release_state,a.answer_key_release_at "
                    "FROM study_material_sources s "
                    "JOIN course_notes n ON n.id=COALESCE(s.source_note_id,s.note_id) "
                    "AND n.class_id=COALESCE(s.source_class_id,s.class_id) "
                    "LEFT JOIN uploaded_assessments a ON a.note_id=n.id "
                    "WHERE s.material_id=?1 AND COALESCE(s.source_class_id,s.class_id)=?2 "
                    "AND n.status='ready' ORDER BY s.position",
                    row["id"], source_class_id,
                )
                source_rows = [
                    source for source in source_rows
                    if (
                        is_effectively_published(source["publication_state"], source["release_at"])
                        or can_view_unreleased(source_permissions, owner, source["created_by"])
                    )
                    and (
                        source.get("owner_id") is None
                        or can_view_assessment_key(source, source_permissions, owner)
                    )
                ]
        sources = [
            dict(source) for source in source_rows
            if not cited_ids or str(source["id"]) in cited_ids
        ]
    if feedback_released:
        question = source_question
    else:
        question = {
            "prompt": str(source_question.get("prompt", "")),
            "kind": str(source_question.get("kind", "short_answer")),
            "choices": [
                {"text": str(item.get("text", ""))}
                for item in source_question.get("choices", [])
                if isinstance(item, dict)
            ],
        }
        sources = []
    correct_answer = str(source_question.get("answer", "")) if feedback_released else ""
    if feedback_released and source_question.get("kind") in {"multiple_choice", "true_false"}:
        correct_answer = next(
            (
                str(choice.get("text", ""))
                for choice in source_question.get("choices", [])
                if isinstance(choice, dict)
                and str(choice.get("id", "")) == correct_answer
            ),
            correct_answer,
        )
    return {
        "class_name": row["class_name"] or "Personal study materials",
        "question": question,
        "submitted_answer": submitted_answer,
        "correct_answer": correct_answer,
        "explanation": str(source_question.get("explanation", "")) if feedback_released else "",
        "course_excerpts": source_excerpts(sources, 24_000) if feedback_released else "",
        "sources_changed": bool(row["sources_outdated"]),
        "sources_missing": bool(row["sources_missing"]) or missing_pinned_sources or bool(
            cited_ids - {str(source["id"]) for source in sources}
        ),
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
