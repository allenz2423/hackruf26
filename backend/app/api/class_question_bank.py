"""Source-grounded validated question-bank generation and release controls."""

from __future__ import annotations

import hashlib
import json
import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import database, enforce_sqlite_api_policy
from app.api.errors import ApiError
from app.api.schemas import QuestionBankGenerationRequest
from app.services.class_access import class_access, is_class_manager, material_access
from app.services.database import json_value
from app.services.learning_scoring import materialize_released_evidence_for_run
from app.services.question_bank import create_generated_assessment

logger = logging.getLogger("screen_tutor.question_bank")
router = APIRouter(tags=["question-bank"])


class FeedbackReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: UUID


def _payload_digest(payload: QuestionBankGenerationRequest) -> str:
    encoded = json.dumps(
        payload.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _user(request: Request) -> tuple[UUID, object]:
    user_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    return user_id, database(request)


@router.post("/api/classes/{class_id}/question-bank/generate", status_code=201)
async def generate_class_assessment(
    class_id: UUID, payload: QuestionBankGenerationRequest, request: Request
) -> dict:
    user_id, db = await _user(request)
    await class_access(db, class_id, user_id, required="create")
    service = getattr(request.app.state, "gemini_content_service", None)
    if service is None:
        raise ApiError(
            503, "question_generation_unavailable", "Question generation is unavailable."
        )
    limiter = getattr(request.app.state, "generation_rate_limiter", None)
    if limiter is not None:
        retry_after = await limiter.check(f"user:{user_id}")
        if retry_after is not None:
            raise ApiError(
                429,
                "generation_rate_limited",
                "Too many generation requests. Try again shortly.",
                retry_after,
            )

    digest = _payload_digest(payload)
    async with db.acquire() as connection:
        async with connection.transaction():
            existing = await connection.fetchrow(
                "SELECT * FROM class_question_generation_operations WHERE class_id=?1 "
                "AND requester_id=?2 AND operation_id=?3",
                class_id,
                user_id,
                payload.operation_id,
            )
            if existing is not None:
                if existing["request_digest"] != digest:
                    raise ApiError(
                        409,
                        "idempotency_conflict",
                        "This operation ID was already used with different generation settings.",
                    )
                if existing["status"] == "complete":
                    material = await connection.fetchrow(
                        "SELECT id,kind,status,title,publication_state,feedback_release_policy,"
                        "content "
                        "FROM study_materials WHERE id=?1",
                        existing["material_id"],
                    )
                    if material is not None:
                        content = json_value(material["content"], {})
                        return {
                            "operation_id": str(payload.operation_id),
                            "material_id": material["id"],
                            "kind": material["kind"],
                            "status": material["status"],
                            "title": material["title"],
                            "publication_state": material["publication_state"],
                            "feedback_release_policy": material["feedback_release_policy"],
                            "item_count": len(content.get("questions", [])),
                            "replayed": True,
                        }
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
                payload.operation_id,
                digest,
            )

    try:
        result = await _run_generation(
            db,
            class_id=str(class_id),
            user_id=str(user_id),
            payload=payload,
            service=service,
            model_name=getattr(
                service,
                "model_name",
                request.app.state.settings.gemini_text_model,
            ),
        )
    except ApiError as exc:
        await db.execute(
            "UPDATE class_question_generation_operations SET status='failed',error_code=?4,"
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE class_id=?1 AND requester_id=?2 "
            "AND operation_id=?3 AND status='running'",
            class_id,
            user_id,
            payload.operation_id,
            exc.code[:80],
        )
        raise
    except Exception as exc:
        logger.info("question_generation_failed category=%s", type(exc).__name__)
        await db.execute(
            "UPDATE class_question_generation_operations SET status='failed',"
            "error_code='generation_failed',updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE class_id=?1 AND requester_id=?2 AND operation_id=?3 AND status='running'",
            class_id,
            user_id,
            payload.operation_id,
        )
        raise ApiError(
            503,
            "question_generation_failed",
            "The assessment could not be generated and validated. Retry with a new operation ID.",
        ) from None
    await db.execute(
        "UPDATE class_question_generation_operations SET status='complete',material_id=?4,"
        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE class_id=?1 AND requester_id=?2 "
        "AND operation_id=?3 AND status='running'",
        class_id,
        user_id,
        payload.operation_id,
        result["material_id"],
    )
    return {"operation_id": str(payload.operation_id), **result, "replayed": False}


async def _run_generation(db, *, class_id, user_id, payload, service, model_name):
    return await create_generated_assessment(
        db,
        class_id=class_id,
        user_id=user_id,
        payload=payload,
        service=service,
        model_name=model_name,
    )


@router.post("/api/study-materials/{material_id}/feedback-release")
async def release_assessment_feedback(
    material_id: UUID, payload: FeedbackReleaseRequest, request: Request
) -> dict:
    user_id, db = await _user(request)
    material, access = await material_access(db, material_id, user_id)
    if material["kind"] not in {"quiz", "practice_exam"}:
        raise ApiError(409, "feedback_not_supported", "This material has no assessment feedback.")
    if access is not None and not is_class_manager(access):
        raise ApiError(
            403, "insufficient_permission", "A class manager must release shared feedback."
        )
    if material["resource_scope"] == "personal" and str(material["owner_id"]) != str(user_id):
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if material["feedback_release_policy"] != "manual":
        raise ApiError(
            409,
            "feedback_release_policy_conflict",
            "Only assessments configured for manual feedback release can be released here.",
        )
    async with db.acquire() as connection:
        async with connection.transaction():
            # Recheck ownership/capability after reserving SQLite's write lock.
            # A membership revocation cannot race between this check and release.
            current_material, current_access = await material_access(
                connection, material_id, user_id
            )
            if current_material["kind"] not in {"quiz", "practice_exam"}:
                raise ApiError(
                    409,
                    "feedback_not_supported",
                    "This material has no assessment feedback.",
                )
            if current_access is not None and not is_class_manager(current_access):
                raise ApiError(
                    403,
                    "insufficient_permission",
                    "A class manager must release shared feedback.",
                )
            if current_material["resource_scope"] == "personal" and str(
                current_material["owner_id"]
            ) != str(user_id):
                raise ApiError(404, "not_found", "The requested resource was not found.")
            if current_material["feedback_release_policy"] != "manual":
                raise ApiError(
                    409,
                    "feedback_release_policy_conflict",
                    "Only assessments configured for manual feedback release can be released here.",
                )
            used_operation = await connection.fetchrow(
                "SELECT material_id FROM study_material_feedback_releases WHERE operation_id=?1",
                payload.operation_id,
            )
            if used_operation and str(used_operation["material_id"]) != str(material_id):
                raise ApiError(
                    409,
                    "idempotency_conflict",
                    "This release operation ID was used for another assessment.",
                )
            result = await connection.fetchrow(
                "INSERT INTO study_material_feedback_releases(material_id,operation_id,"
                "released_by) "
                "VALUES (?1,?2,?3) ON CONFLICT(material_id) DO NOTHING "
                "RETURNING material_id,operation_id,released_by,released_at",
                material_id,
                payload.operation_id,
                user_id,
            )
            already_released = result is None
            if result is None:
                result = await connection.fetchrow(
                    "SELECT material_id,operation_id,released_by,released_at "
                    "FROM study_material_feedback_releases WHERE material_id=?1",
                    material_id,
                )
            completed_runs = await connection.fetch(
                "SELECT id FROM quiz_runs WHERE material_id=?1 AND status='completed' "
                "ORDER BY created_at,id",
                material_id,
            )
            for run in completed_runs:
                await materialize_released_evidence_for_run(connection, run_id=str(run["id"]))
    return {**result, "already_released": already_released}
