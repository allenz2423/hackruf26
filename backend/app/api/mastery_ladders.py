"""Owner-private Mastery Ladder sessions built on the shared quiz engine."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.dependencies import database, enforce_sqlite_api_policy
from app.api.errors import ApiError
from app.api.quiz_attempts import (
    FinalAnswerSubmission,
    OperationRequest,
    ReasoningSubmission,
    _complete_run_if_ready,
    _owned_material,
    _owned_run,
    _pin_run_questions,
    retry_reasoning,
    submit_final_answer,
    submit_reasoning,
)
from app.services.class_access import class_access
from app.services.concept_graph import concept_graph
from app.services.database import SQLiteConnection, SQLiteDatabase, json_value
from app.services.learning_scoring import award_ladder_completion, record_assessment_outcome
from app.services.question_bank import _validate_current_chunk_refs
from app.services.quiz_reasoning import ReasoningRubricResult
from app.services.study_prompts import QUIZ_REASONING_RUBRIC_VERSION

router = APIRouter(tags=["mastery-ladder"])
RUBRIC_DEADLINE_SECONDS = 35
MAX_LADDER_CONCEPTS = 12


class LadderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    operation_id: UUID
    class_id: UUID
    concept_ids: list[UUID] = Field(min_length=1, max_length=MAX_LADDER_CONCEPTS)
    language: str = Field(min_length=2, max_length=35)
    grading_policy: Literal["objective_only", "reasoning_required"]

    @field_validator("concept_ids")
    @classmethod
    def unique_concepts(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("Concept selections must be unique.")
        return value


class LadderAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    operation_id: UUID
    question_id: UUID
    answer: str = Field(min_length=1, max_length=5_000)
    expected_version: int = Field(ge=0)


class LadderReasoning(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    operation_id: UUID
    expected_version: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=12_000)


class LadderOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID
    expected_version: int = Field(ge=0)


def _request_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def _ladder_context(
    db: SQLiteDatabase, ladder_id: UUID | str, owner_id: UUID | str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    ladder = await db.fetchrow(
        "SELECT * FROM mastery_ladder_sessions WHERE id=?1 AND owner_id=?2",
        ladder_id,
        owner_id,
    )
    if ladder is None:
        raise ApiError(404, "not_found", "The requested Mastery Ladder was not found.")
    await class_access(db, ladder["class_id"], owner_id, required="read")
    material = await _owned_material(
        db, UUID(str(ladder["material_id"])), UUID(str(owner_id)), require_ready=False
    )
    run = await _owned_run(
        db,
        UUID(str(ladder["material_id"])),
        UUID(str(ladder["run_id"])),
        UUID(str(owner_id)),
    )
    return ladder, material, run


async def _visible_concepts(
    db: SQLiteDatabase, *, class_id: str, owner_id: str, requested_ids: list[str]
) -> list[dict[str, Any]]:
    graph = await concept_graph(db, class_id=class_id, user_id=owner_id)
    visible = {
        str(row["id"]): row
        for row in graph.get("concepts", [])
        if isinstance(row, dict) and row.get("id")
    }
    if set(visible).intersection(requested_ids) != set(requested_ids):
        raise ApiError(404, "concept_not_found", "A selected validated concept was not found.")
    return [visible[item] for item in requested_ids]


async def _eligible_ladder_items(
    db: SQLiteDatabase,
    *,
    class_id: str,
    owner_id: str,
    concept_id: str,
    level: int,
    language: str,
) -> list[dict[str, Any]]:
    candidates = await db.fetch(
        "SELECT i.* FROM class_question_bank_items i "
        "JOIN class_question_bank_concepts c ON c.item_id=i.id AND c.class_id=i.class_id "
        "WHERE i.class_id=?1 AND i.state='validated' AND c.concept_id=?2 "
        "AND c.relation='primary' AND i.level=?3 "
        "AND lower(i.language)=lower(?4) "
        "ORDER BY i.exposure_count,i.created_at,i.id LIMIT 50",
        class_id,
        concept_id,
        level,
        language,
    )
    result = []
    for row in candidates:
        source_rows = await db.fetch(
            "SELECT chunk_revision_id FROM class_question_bank_sources "
            "WHERE item_id=?1 ORDER BY source_order,chunk_revision_id",
            row["id"],
        )
        source_ids = [str(item["chunk_revision_id"]) for item in source_rows]
        if not source_ids:
            continue
        try:
            await _validate_current_chunk_refs(
                db,
                class_id=class_id,
                user_id=owner_id,
                chunk_revision_ids=source_ids,
            )
        except ApiError:
            continue
        grading = json_value(row["grading_json"], {})
        result.append(
            {
                "id": str(row["id"]),
                "family_id": str(row["family_id"]),
                "prompt": str(row["prompt"]),
                "kind": str(row["kind"]),
                "level": int(row["level"]),
                "language": str(row["language"]),
                "choices": json_value(row["choices_json"], []),
                "answer": str(grading.get("answer", "")),
                "accepted_answers": grading.get("accepted_answers", []),
                "explanation": str(grading.get("explanation", "")),
                "rubric": str(grading.get("rubric", "")),
                "assessment_target": str(grading.get("assessment_target", "")),
                "concept_ids": [concept_id],
                "source_chunk_revision_ids": source_ids,
            }
        )
    return result


async def _ensure_attempt(
    conn: SQLiteConnection,
    *,
    run_id: str,
    question_id: str,
) -> str:
    existing = await conn.fetchrow(
        "SELECT id FROM quiz_question_attempts WHERE run_id=?1 AND question_id=?2",
        run_id,
        question_id,
    )
    if existing is not None:
        return str(existing["id"])
    attempt_id = str(uuid4())
    await conn.execute(
        "INSERT INTO quiz_question_attempts(id,run_id,question_id) VALUES (?1,?2,?3)",
        attempt_id,
        run_id,
        question_id,
    )
    return attempt_id


@router.post("/api/mastery-ladders", status_code=201)
async def create_mastery_ladder(payload: LadderCreate, request: Request) -> dict[str, Any]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db = database(request)
    class_id = str(payload.class_id)
    concept_ids = [str(item) for item in payload.concept_ids]
    request_data = {
        "class_id": class_id,
        "concept_ids": concept_ids,
        "language": payload.language.casefold(),
        "grading_policy": payload.grading_policy,
    }
    digest = _request_digest(request_data)
    async with db.acquire() as conn:
        await class_access(conn, class_id, owner_id, required="read")
    existing = await db.fetchrow(
        "SELECT id,request_digest FROM mastery_ladder_sessions "
        "WHERE owner_id=?1 AND operation_id=?2",
        owner_id,
        payload.operation_id,
    )
    if existing is not None:
        if existing["request_digest"] != digest:
            raise ApiError(
                409,
                "idempotency_conflict",
                "This ladder operation ID was used for another request.",
            )
        return await get_mastery_ladder(UUID(existing["id"]), request)

    visible_concepts = await _visible_concepts(
        db, class_id=class_id, owner_id=str(owner_id), requested_ids=concept_ids
    )
    concept_label = {
        str(item["id"]): str(item.get("canonical_name") or "") for item in visible_concepts
    }
    selected_items: list[dict[str, Any]] = []
    assignments: dict[str, dict[int, dict[str, str | None]]] = {}
    for concept_id in concept_ids:
        assignments[concept_id] = {}
        for level in range(1, 5):
            items = await _eligible_ladder_items(
                db,
                class_id=class_id,
                owner_id=str(owner_id),
                concept_id=concept_id,
                level=level,
                language=payload.language,
            )
            primary = items[0] if items else None
            recovery = (
                next(
                    (item for item in items[1:] if item["family_id"] != primary["family_id"]),
                    None,
                )
                if primary
                else None
            )
            if primary:
                selected_items.append(primary)
            if recovery:
                selected_items.append(recovery)
            assignments[concept_id][level] = {
                "primary_bank_id": str(primary["id"]) if primary else None,
                "recovery_bank_id": str(recovery["id"]) if recovery else None,
            }

    now_material_id, run_id, ladder_id = str(uuid4()), str(uuid4()), str(uuid4())
    ladder_state = (
        "active"
        if any(assignments[concept_id][1]["primary_bank_id"] for concept_id in concept_ids)
        else "unavailable"
    )
    mode = "reasoning" if payload.grading_policy == "reasoning_required" else "standard"
    content = json.dumps({"questions": selected_items}, ensure_ascii=False)
    async with db.acquire() as conn:
        async with conn.transaction():
            # Recheck membership, concept state, and exact source heads after selection.
            await class_access(conn, class_id, owner_id, required="read")
            # The request can race another identical create after its initial
            # lookup. BEGIN IMMEDIATE serializes this check with the insertion.
            duplicate = await conn.fetchrow(
                "SELECT * FROM mastery_ladder_sessions WHERE owner_id=?1 AND operation_id=?2",
                owner_id,
                payload.operation_id,
            )
            if duplicate is not None:
                if duplicate["request_digest"] != digest:
                    raise ApiError(
                        409,
                        "idempotency_conflict",
                        "This ladder operation ID was used for another request.",
                    )
                return await _ladder_payload(conn, duplicate)
            await _visible_concepts(
                conn, class_id=class_id, owner_id=str(owner_id), requested_ids=concept_ids
            )
            for item in selected_items:
                await _validate_current_chunk_refs(
                    conn,
                    class_id=class_id,
                    user_id=str(owner_id),
                    chunk_revision_ids=item["source_chunk_revision_ids"],
                )
                current = await conn.fetchval(
                    "SELECT 1 FROM class_question_bank_items WHERE id=?1 AND class_id=?2 "
                    "AND state='validated'",
                    item["id"],
                    class_id,
                )
                if not current:
                    raise ApiError(
                        409,
                        "question_source_unavailable",
                        "A selected question is no longer available.",
                    )
            # Each ladder has a private assessment material and an immutable quiz run.
            await conn.execute(
                "INSERT INTO study_materials "
                "(id,origin_class_id,resource_scope,owner_id,created_by,kind,title,status,content,"
                "feedback_release_policy,assessment_validation_state,assessment_validation_version,"
                "assessment_engine_version) VALUES (?1,?2,'personal',?3,?3,'quiz','Mastery Ladder',"
                "'ready',?4,'after_completion','validated','mastery-ladder-v1',1)",
                now_material_id,
                class_id,
                owner_id,
                content,
            )
            await conn.execute(
                "INSERT INTO quiz_runs "
                "(id,material_id,owner_id,operation_id,mode,snapshot_version,assessment_type,"
                "grading_policy,feedback_release_policy) "
                "VALUES (?1,?2,?3,?4,?5,1,'mastery_ladder',?6,'after_completion')",
                run_id,
                now_material_id,
                owner_id,
                f"mastery-ladder:{payload.operation_id}",
                mode,
                payload.grading_policy,
            )
            await conn.execute(
                "INSERT INTO mastery_ladder_sessions "
                "(id,owner_id,class_id,material_id,run_id,operation_id,request_digest,language,"
                "grading_policy,status) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)",
                ladder_id,
                owner_id,
                class_id,
                now_material_id,
                run_id,
                payload.operation_id,
                digest,
                payload.language,
                payload.grading_policy,
                ladder_state,
            )
            material = {
                "id": now_material_id,
                "class_id": None,
                "origin_class_id": class_id,
                "assessment_validation_state": "validated",
                "content": content,
            }
            await _pin_run_questions(
                conn,
                run_id=run_id,
                material=material,
                questions=selected_items,
            )
            pinned = await conn.fetch(
                "SELECT id,bank_item_id FROM quiz_run_questions WHERE run_id=?1",
                run_id,
            )
            question_by_bank = {str(row["bank_item_id"]): str(row["id"]) for row in pinned}
            for concept_id in concept_ids:
                level_map = assignments[concept_id]
                first_primary = level_map[1]["primary_bank_id"]
                current_q = question_by_bank.get(first_primary) if first_primary else None
                await conn.execute(
                    "INSERT INTO mastery_ladder_concepts "
                    "(ladder_id,class_id,concept_id,concept_label,state,current_level,"
                    "recovery_state,current_question_id,recovery_question_id) "
                    "VALUES (?1,?2,?3,?4,?5,1,'not_offered',?6,?7)",
                    ladder_id,
                    class_id,
                    concept_id,
                    concept_label[concept_id],
                    "active" if current_q else "unavailable",
                    current_q,
                    question_by_bank.get(level_map[1]["recovery_bank_id"])
                    if level_map[1]["recovery_bank_id"]
                    else None,
                )
                for level, mapped in level_map.items():
                    for role, bank_key in (
                        ("primary", "primary_bank_id"),
                        ("recovery", "recovery_bank_id"),
                    ):
                        bank_id = mapped[bank_key]
                        question_id = question_by_bank.get(bank_id) if bank_id else None
                        if question_id:
                            item = next(
                                item for item in selected_items if str(item["id"]) == bank_id
                            )
                            await conn.execute(
                                "INSERT INTO mastery_ladder_items "
                                "(ladder_id,class_id,concept_id,level,role,question_id,family_id) "
                                "VALUES (?1,?2,?3,?4,?5,?6,?7)",
                                ladder_id,
                                class_id,
                                concept_id,
                                level,
                                role,
                                question_id,
                                item["family_id"],
                            )
                if current_q:
                    await _ensure_attempt(conn, run_id=run_id, question_id=current_q)
    return await get_mastery_ladder(UUID(ladder_id), request)


async def _ladder_payload(db: SQLiteDatabase, ladder: dict[str, Any]) -> dict[str, Any]:
    concepts = await db.fetch(
        "SELECT * FROM mastery_ladder_concepts WHERE ladder_id=?1 "
        "ORDER BY concept_label COLLATE NOCASE,concept_id",
        ladder["id"],
    )
    public_concepts = []
    for concept in concepts:
        question = None
        if concept["current_question_id"]:
            row = await db.fetchrow(
                "SELECT public_snapshot FROM quiz_run_questions WHERE run_id=?1 AND id=?2",
                ladder["run_id"],
                concept["current_question_id"],
            )
            if row is not None:
                question = json_value(row["public_snapshot"], None)
        public_concepts.append(
            {
                "concept_id": str(concept["concept_id"]),
                "label": str(concept["concept_label"]),
                "state": str(concept["state"]),
                "current_level": int(concept["current_level"]),
                "recovery_state": str(concept["recovery_state"]),
                "current_question": question,
            }
        )
    run = await db.fetchrow(
        "SELECT r.id,r.status,r.feedback_release_policy,r.feedback_released_at,"
        "r.material_id,m.feedback_release_policy AS material_feedback_policy "
        "FROM quiz_runs r JOIN study_materials m ON m.id=r.material_id WHERE r.id=?1",
        ladder["run_id"],
    )
    feedback_policy = str(
        (run or {}).get("feedback_release_policy")
        or (run or {}).get("material_feedback_policy")
        or "after_completion"
    )
    manually_released = bool(
        run
        and (
            run.get("feedback_released_at")
            or await db.fetchval(
                "SELECT 1 FROM study_material_feedback_releases WHERE material_id=?1",
                run["material_id"],
            )
        )
    )
    feedback_available = bool(
        run
        and (
            (feedback_policy == "after_completion" and run["status"] == "completed")
            or (feedback_policy == "manual" and manually_released)
        )
    )
    attempts = await db.fetch(
        "SELECT a.id,a.question_id,a.state,a.answer_status,a.version,a.accepted_snapshot_hash,"
        "a.active_evaluation_operation_id,a.correct,j.status AS rubric_status,"
        "j.result AS rubric_result,"
        "(SELECT e.status FROM quiz_reasoning_evaluations e WHERE e.attempt_id=a.id "
        "ORDER BY e.created_at DESC,e.id DESC LIMIT 1) AS evaluation_status,"
        "(SELECT e.result FROM quiz_reasoning_evaluations e WHERE e.attempt_id=a.id "
        "ORDER BY e.created_at DESC,e.id DESC LIMIT 1) AS evaluation_result,"
        "(SELECT e.follow_up FROM quiz_reasoning_evaluations e WHERE e.attempt_id=a.id "
        "ORDER BY e.created_at DESC,e.id DESC LIMIT 1) AS reasoning_follow_up,"
        "o.reasoning_status,COALESCE((SELECT sum(x.amount) FROM learning_xp_events x "
        "WHERE x.attempt_id=a.id),0) AS xp_awarded "
        "FROM quiz_question_attempts a JOIN quiz_runs r ON r.id=a.run_id "
        "LEFT JOIN mastery_ladder_rubric_jobs j ON j.attempt_id=a.id "
        "LEFT JOIN assessment_outcomes o ON o.attempt_id=a.id "
        "WHERE a.run_id=?1 ORDER BY a.created_at,a.id",
        ladder["run_id"],
    )
    public_attempts = []
    for attempt in attempts:
        reasoning_status = None
        if ladder["grading_policy"] == "reasoning_required":
            if attempt["accepted_snapshot_hash"]:
                # A later revision or failed rubric cannot revoke the gate entitlement.
                reasoning_status = "accepted"
            elif (
                attempt["active_evaluation_operation_id"]
                or attempt["evaluation_status"] == "pending"
            ):
                reasoning_status = "pending"
            elif attempt["state"] == "reasoning_failed" or attempt["evaluation_status"] == "failed":
                reasoning_status = "failed"
            elif attempt["evaluation_status"] == "available":
                reasoning_status = (
                    "accepted" if attempt["evaluation_result"] == "sufficient" else "insufficient"
                )
        rubric_status = None
        rubric_result = None
        if ladder["grading_policy"] == "reasoning_required":
            if attempt["rubric_status"] == "pending":
                rubric_status = "pending"
            elif attempt["rubric_status"] == "failed":
                rubric_status = "failed"
            elif attempt["rubric_status"] == "available":
                rubric_status = "available"
                if feedback_available:
                    rubric_result = attempt["rubric_result"]
        reasoning_follow_up = (
            attempt["reasoning_follow_up"]
            if reasoning_status == "insufficient"
            and isinstance(attempt["reasoning_follow_up"], str)
            else None
        )
        correctness = None
        if feedback_available and attempt["state"] == "completed":
            correctness = "correct" if bool(attempt["correct"]) else "incorrect"
        elif attempt["state"] in {"evaluating_reasoning", "submitted"}:
            correctness = "pending"
        public_attempts.append(
            {
                "question_id": str(attempt["question_id"]),
                "state": str(attempt["state"]),
                "answer_status": str(attempt["answer_status"]),
                "correctness": correctness,
                "version": int(attempt["version"]),
                "xp_awarded": int(attempt["xp_awarded"] or 0) if feedback_available else None,
                "reasoning_status": reasoning_status,
                "reasoning_follow_up": reasoning_follow_up,
                "rubric_status": rubric_status,
                "rubric_result": rubric_result,
            }
        )
    return {
        "ladder_id": str(ladder["id"]),
        "status": str(ladder["status"]),
        "grading_policy": str(ladder["grading_policy"]),
        "version": int(ladder["version"]),
        "concepts": public_concepts,
        "attempts": public_attempts,
        "run_status": str(run["status"]) if run else "unavailable",
        "feedback_policy": {
            "release": feedback_policy,
            "feedback_available": feedback_available,
        },
    }


@router.get("/api/mastery-ladders/{ladder_id}")
async def get_mastery_ladder(ladder_id: UUID, request: Request) -> dict[str, Any]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db = database(request)
    ladder, _material, _run = await _ladder_context(db, ladder_id, owner_id)
    return await _ladder_payload(db, ladder)


async def _begin_operation(
    db: SQLiteDatabase,
    *,
    ladder: dict[str, Any],
    concept_id: str | None,
    operation_id: UUID | str,
    operation_kind: str,
    request_digest: str,
    expected_version: int,
) -> tuple[bool, dict[str, Any] | None]:
    """Persist a content-bound operation before mutating the shared attempt engine."""
    async with db.acquire() as conn:
        async with conn.transaction():
            await class_access(conn, ladder["class_id"], ladder["owner_id"], required="read")
            current = await conn.fetchrow(
                "SELECT * FROM mastery_ladder_sessions WHERE id=?1 AND owner_id=?2",
                ladder["id"],
                ladder["owner_id"],
            )
            if current is None:
                raise ApiError(404, "not_found", "The requested Mastery Ladder was not found.")
            prior = await conn.fetchrow(
                "SELECT * FROM mastery_ladder_operations WHERE ladder_id=?1 AND operation_id=?2",
                ladder["id"],
                operation_id,
            )
            if prior is not None:
                if (
                    prior["operation_kind"] != operation_kind
                    or prior["request_digest"] != request_digest
                    or prior["concept_id"] != concept_id
                ):
                    raise ApiError(
                        409,
                        "idempotency_conflict",
                        "This operation ID was already used with different content.",
                    )
                if prior["status"] == "complete":
                    return False, json_value(prior["result_json"], {})
                if prior["status"] == "pending":
                    raise ApiError(
                        409,
                        "operation_in_progress",
                        "This ladder operation is still being processed. Refresh before retrying.",
                    )
                await conn.execute(
                    "UPDATE mastery_ladder_operations SET status='pending',error_code=NULL,"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?1",
                    prior["id"],
                )
                return True, None
            if current["status"] != "active":
                raise ApiError(409, "ladder_terminal", "This Mastery Ladder is already finished.")
            if int(current["version"]) != expected_version:
                raise ApiError(
                    409,
                    "version_conflict",
                    f"This ladder changed (version {current['version']}).",
                )
            op_id = str(uuid4())
            await conn.execute(
                "INSERT INTO mastery_ladder_operations "
                "(id,ladder_id,concept_id,operation_id,operation_kind,request_digest,status) "
                "VALUES (?1,?2,?3,?4,?5,?6,'pending')",
                op_id,
                ladder["id"],
                concept_id,
                operation_id,
                operation_kind,
                request_digest,
            )
            await conn.execute(
                "UPDATE mastery_ladder_sessions SET version=version+1,"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?1",
                ladder["id"],
            )
            return True, None


async def _replay_completed_operation(
    db: SQLiteDatabase,
    *,
    ladder_id: str,
    concept_id: str,
    operation_id: str,
    operation_kind: str,
    request_digest: str | None,
) -> dict[str, Any] | None:
    """Recover a completed operation before resolving a now-advanced question.

    The ladder may already have moved to another question (or become terminal)
    when a browser retries after losing the original response. Authorization is
    checked by `_ladder_context` before this lookup; the operation record then
    binds the retry to its original type, concept, and submitted content.
    """
    prior = await db.fetchrow(
        "SELECT * FROM mastery_ladder_operations WHERE ladder_id=?1 AND operation_id=?2",
        ladder_id,
        operation_id,
    )
    if prior is None:
        return None
    if (
        prior["operation_kind"] != operation_kind
        or prior["concept_id"] != concept_id
        or (request_digest is not None and prior["request_digest"] != request_digest)
    ):
        raise ApiError(
            409,
            "idempotency_conflict",
            "This operation ID was already used with different content.",
        )
    if prior["status"] == "pending":
        raise ApiError(
            409,
            "operation_in_progress",
            "This ladder operation is still being processed. Refresh before retrying.",
        )
    if prior["status"] == "complete":
        return json_value(prior["result_json"], {})
    return None


async def _operation_replay(
    db: SQLiteDatabase,
    *,
    ladder_id: str,
    concept_id: str,
    operation_id: str,
    operation_kind: str,
    request_digest: str,
) -> tuple[bool, dict[str, Any] | None]:
    """Resolve a completed retry before checking the now-advanced active question."""
    prior = await db.fetchrow(
        "SELECT * FROM mastery_ladder_operations WHERE ladder_id=?1 AND operation_id=?2",
        ladder_id,
        operation_id,
    )
    if prior is None:
        return False, None
    if (
        prior["operation_kind"] != operation_kind
        or prior["request_digest"] != request_digest
        or str(prior["concept_id"] or "") != concept_id
    ):
        raise ApiError(
            409,
            "idempotency_conflict",
            "This operation ID was already used with different content.",
        )
    if prior["status"] == "complete":
        return True, json_value(prior["result_json"], {})
    if prior["status"] == "pending":
        raise ApiError(
            409, "operation_in_progress", "This ladder operation is still being processed."
        )
    return False, None


async def _fail_operation(
    db: SQLiteDatabase, *, ladder_id: str, operation_id: str, error_code: str
) -> None:
    await db.execute(
        "UPDATE mastery_ladder_operations SET status='failed',error_code=?3,"
        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
        "WHERE ladder_id=?1 AND operation_id=?2 AND status='pending'",
        ladder_id,
        operation_id,
        error_code[:80],
    )


async def _finish_operation(
    conn: SQLiteConnection,
    *,
    ladder_id: str,
    operation_id: str,
    result: dict[str, Any],
) -> None:
    await conn.execute(
        "UPDATE mastery_ladder_operations SET status='complete',error_code=NULL,result_json=?3,"
        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
        "WHERE ladder_id=?1 AND operation_id=?2",
        ladder_id,
        operation_id,
        json.dumps(result, ensure_ascii=False, separators=(",", ":")),
    )


async def _active_ladder_question(
    db: SQLiteDatabase, *, ladder_id: str, concept_id: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    concept = await db.fetchrow(
        "SELECT * FROM mastery_ladder_concepts WHERE ladder_id=?1 AND concept_id=?2",
        ladder_id,
        concept_id,
    )
    if concept is None:
        raise ApiError(404, "not_found", "The requested ladder concept was not found.")
    if concept["state"] != "active" or not concept["current_question_id"]:
        raise ApiError(409, "concept_terminal", "This concept has no active ladder question.")
    question = await db.fetchrow(
        "SELECT q.*,i.role,i.level AS ladder_level,i.family_id AS ladder_family_id "
        "FROM quiz_run_questions q JOIN mastery_ladder_items i ON i.question_id=q.id "
        "WHERE i.ladder_id=?1 AND i.concept_id=?2 AND q.id=?3",
        ladder_id,
        concept_id,
        concept["current_question_id"],
    )
    if question is None:
        raise ApiError(409, "question_unavailable", "This ladder question is no longer available.")
    attempt = await db.fetchrow(
        "SELECT * FROM quiz_question_attempts WHERE run_id=?1 AND question_id=?2",
        question["run_id"],
        question["id"],
    )
    if attempt is None:
        raise ApiError(
            409, "attempt_unavailable", "This ladder question is not ready for a response."
        )
    return concept, question, attempt


async def _run_rubric(
    request: Request,
    db: SQLiteDatabase,
    *,
    ladder: dict[str, Any],
    question_id: str,
    attempt: dict[str, Any],
    operation_id: str,
) -> dict[str, Any]:
    private = json_value(
        await db.fetchval(
            "SELECT private_snapshot FROM quiz_run_questions WHERE run_id=?1 AND id=?2",
            ladder["run_id"],
            question_id,
        ),
        {},
    )
    if not isinstance(private.get("rubric"), str) or not private["rubric"].strip():
        raise ApiError(
            409, "rubric_unavailable", "This question has no validated reasoning rubric."
        )
    previous = await db.fetchrow(
        "SELECT * FROM mastery_ladder_rubric_jobs WHERE attempt_id=?1",
        attempt["id"],
    )
    answer = str(attempt.get("final_answer") or "")
    choices = private.get("choices", [])
    answer_label = next(
        (
            str(choice.get("text", ""))
            for choice in choices
            if isinstance(choice, dict) and str(choice.get("id")) == answer
        ),
        answer,
    )
    turns = await db.fetch(
        "SELECT sequence_number,source,text FROM quiz_reasoning_turns "
        "WHERE attempt_id=?1 AND sequence_number<=?2 ORDER BY sequence_number",
        attempt["id"],
        int(attempt.get("submitted_through_turn") or 0),
    )
    answer_digest = _request_digest(
        {
            "answer": answer,
            "submitted_snapshot_hash": attempt.get("submitted_snapshot_hash"),
            "submitted_through_turn": attempt.get("submitted_through_turn"),
        }
    )
    if previous is not None and previous["answer_digest"] != answer_digest:
        raise ApiError(
            409,
            "rubric_snapshot_conflict",
            "This answer is already bound to a different submitted reasoning snapshot.",
        )
    if previous and previous["status"] == "available":
        return {"status": previous["result"], "feedback": previous["feedback"] or ""}
    job_id = str(previous["id"]) if previous else str(uuid4())
    await db.execute(
        "INSERT INTO mastery_ladder_rubric_jobs "
        "(id,ladder_id,attempt_id,operation_id,answer_digest,status,prompt_version) "
        "VALUES (?1,?2,?3,?4,?5,'pending',?6) ON CONFLICT(attempt_id) DO UPDATE SET "
        "operation_id=excluded.operation_id,answer_digest=excluded.answer_digest,status='pending',"
        "result=NULL,feedback=NULL,error_code=NULL,prompt_version=excluded.prompt_version,"
        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),completed_at=NULL",
        job_id,
        ladder["id"],
        attempt["id"],
        operation_id,
        answer_digest,
        QUIZ_REASONING_RUBRIC_VERSION,
    )
    service = getattr(request.app.state, "quiz_reasoning_service", None)
    evaluate = getattr(service, "evaluate_rubric", None)
    if evaluate is None:
        result = None
    else:
        context = {
            "question": {
                "prompt": str(private.get("prompt", ""))[:5_000],
                "kind": str(private.get("kind", "")),
            },
            "trusted_rubric": str(private["rubric"])[:3_000],
            "student_answer": answer_label[:5_000],
            "submitted_reasoning_turns": [
                {
                    "sequence_number": row["sequence_number"],
                    "source": row["source"],
                    "text": row["text"],
                }
                for row in turns
            ],
        }
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(evaluate, context), timeout=RUBRIC_DEADLINE_SECONDS
            )
            if not isinstance(result, ReasoningRubricResult):
                result = ReasoningRubricResult.model_validate(result)
        except Exception:
            result = None
    access_revoked = False
    snapshot_stale = False
    async with db.acquire() as conn:
        async with conn.transaction():
            current_ladder = await conn.fetchrow(
                "SELECT class_id FROM mastery_ladder_sessions WHERE id=?1 AND owner_id=?2",
                ladder["id"],
                ladder["owner_id"],
            )
            if current_ladder is None:
                access_revoked = True
            else:
                try:
                    await class_access(
                        conn,
                        current_ladder["class_id"],
                        ladder["owner_id"],
                        required="read",
                    )
                except ApiError:
                    access_revoked = True
            current_attempt = await conn.fetchrow(
                "SELECT state,final_answer,submitted_snapshot_hash,submitted_through_turn "
                "FROM quiz_question_attempts WHERE id=?1 AND run_id=?2",
                attempt["id"],
                ladder["run_id"],
            )
            if (
                current_attempt is None
                or current_attempt["state"] != "completed"
                or _request_digest(
                    {
                        "answer": current_attempt["final_answer"],
                        "submitted_snapshot_hash": current_attempt["submitted_snapshot_hash"],
                        "submitted_through_turn": current_attempt["submitted_through_turn"],
                    }
                )
                != answer_digest
            ):
                snapshot_stale = True
            job = await conn.fetchrow(
                "SELECT status,operation_id,answer_digest FROM mastery_ladder_rubric_jobs "
                "WHERE attempt_id=?1",
                attempt["id"],
            )
            if (
                job is None
                or job["status"] != "pending"
                or job["operation_id"] != operation_id
                or job["answer_digest"] != answer_digest
            ):
                snapshot_stale = True
            if access_revoked or snapshot_stale or result is None:
                await conn.execute(
                    "UPDATE mastery_ladder_rubric_jobs SET status='failed',"
                    "error_code=?2,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                    "completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                    "WHERE attempt_id=?1 AND status='pending' AND operation_id=?3 "
                    "AND answer_digest=?4",
                    attempt["id"],
                    "access_revoked"
                    if access_revoked
                    else ("snapshot_changed" if snapshot_stale else "provider_failure"),
                    operation_id,
                    answer_digest,
                )
            else:
                await conn.execute(
                    "UPDATE mastery_ladder_rubric_jobs SET status='available',result=?2,"
                    "feedback=?3,error_code=NULL,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                    "completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                    "WHERE attempt_id=?1 AND status='pending' AND operation_id=?4 "
                    "AND answer_digest=?5",
                    attempt["id"],
                    result.status,
                    result.feedback,
                    operation_id,
                    answer_digest,
                )
                await conn.execute(
                    "UPDATE assessment_outcomes SET reasoning_status=?2 WHERE attempt_id=?1",
                    attempt["id"],
                    "rubric_passed" if result.status == "passed" else "rubric_failed",
                )
    if access_revoked:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if snapshot_stale:
        raise ApiError(
            409, "rubric_snapshot_conflict", "The submitted answer changed. Refresh and retry."
        )
    if result is None:
        raise ApiError(
            503,
            "rubric_evaluation_failed",
            "Your answer and reasoning are saved, but rubric grading failed. "
            "Retry this same response.",
        )
    return {"status": result.status, "feedback": result.feedback}


async def _progress_after_answer(
    conn: SQLiteConnection,
    *,
    ladder: dict[str, Any],
    concept: dict[str, Any],
    current_question: dict[str, Any],
    attempt: dict[str, Any],
    rubric_status: str | None,
    operation_id: str,
) -> dict[str, Any]:
    correct = bool(attempt.get("correct"))
    pass_attempt = correct and (
        ladder["grading_policy"] == "objective_only" or rubric_status == "passed"
    )
    role = str(current_question["role"])
    progression = "failed"
    new_question_id: str | None = None
    state = str(concept["state"])
    recovery_state = str(concept["recovery_state"])
    recovery_id: str | None = None
    level = int(concept["current_level"])
    if pass_attempt:
        if role == "recovery":
            recovery_state = "used"
        if level == 4:
            state = "passed"
            progression = "complete"
        else:
            next_level = level + 1
            next_step = await conn.fetchrow(
                "SELECT question_id FROM mastery_ladder_items WHERE ladder_id=?1 "
                "AND concept_id=?2 AND level=?3 AND role='primary'",
                ladder["id"],
                concept["concept_id"],
                next_level,
            )
            if next_step is None:
                state = "unavailable"
                recovery_state = "unavailable"
                progression = "unavailable"
            else:
                level = next_level
                new_question_id = str(next_step["question_id"])
                recovery_id = await conn.fetchval(
                    "SELECT question_id FROM mastery_ladder_items WHERE ladder_id=?1 "
                    "AND concept_id=?2 AND level=?3 AND role='recovery'",
                    ladder["id"],
                    concept["concept_id"],
                    level,
                )
                recovery_state = "not_offered"
                progression = "advanced"
    elif role == "primary":
        recovery_id = concept.get("recovery_question_id")
        if recovery_id:
            new_question_id = str(recovery_id)
            recovery_state = "available"
            progression = "recovery_offered"
        else:
            state = "unavailable"
            recovery_state = "unavailable"
            progression = "unavailable"
    else:
        state = "failed"
        recovery_state = "used"
        progression = "failed"
    if new_question_id:
        await _ensure_attempt(
            conn,
            run_id=str(ladder["run_id"]),
            question_id=new_question_id,
        )
    if ladder["grading_policy"] == "reasoning_required":
        question_row = await conn.fetchrow(
            "SELECT * FROM quiz_run_questions WHERE run_id=?1 AND id=?2",
            ladder["run_id"],
            current_question["id"],
        )
        run_row = await conn.fetchrow(
            "SELECT * FROM quiz_runs WHERE id=?1 AND owner_id=?2",
            ladder["run_id"],
            ladder["owner_id"],
        )
        if question_row is not None and run_row is not None:
            await record_assessment_outcome(
                conn,
                run=run_row,
                attempt={"id": attempt["id"]},
                question_row=question_row,
                status="correct" if correct else "incorrect",
                correctness=1.0 if correct else 0.0,
                reasoning_status=(
                    "rubric_passed" if rubric_status == "passed" else "rubric_failed"
                ),
            )
    await conn.execute(
        "UPDATE mastery_ladder_concepts SET state=?3,current_level=?4,recovery_state=?5,"
        "current_question_id=?6,recovery_question_id=?7,version=version+1,"
        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE ladder_id=?1 AND concept_id=?2",
        ladder["id"],
        concept["concept_id"],
        state,
        level,
        recovery_state,
        new_question_id,
        recovery_id,
    )
    all_terminal = (
        int(
            await conn.fetchval(
                "SELECT count(*) FROM mastery_ladder_concepts "
                "WHERE ladder_id=?1 AND state='active'",
                ladder["id"],
            )
            or 0
        )
        == 0
    )
    await conn.execute(
        "UPDATE mastery_ladder_sessions SET version=version+1,status=?2,"
        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),completed_at=CASE WHEN ?2='completed' "
        "THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE completed_at END WHERE id=?1",
        ladder["id"],
        "completed" if all_terminal else "active",
    )
    updated_ladder = await conn.fetchrow(
        "SELECT * FROM mastery_ladder_sessions WHERE id=?1", ladder["id"]
    )
    result = {
        "progression": progression,
        "version": int(updated_ladder["version"]),
    }
    if all_terminal:
        material = await conn.fetchrow(
            "SELECT * FROM study_materials WHERE id=?1", ladder["material_id"]
        )
        if material:
            await _complete_run_if_ready(conn, str(ladder["run_id"]), material)
        passed_count = await conn.fetchval(
            "SELECT count(*) FROM mastery_ladder_concepts WHERE ladder_id=?1 AND state='passed'",
            ladder["id"],
        )
        total_count = await conn.fetchval(
            "SELECT count(*) FROM mastery_ladder_concepts WHERE ladder_id=?1",
            ladder["id"],
        )
        if total_count and passed_count == total_count:
            await award_ladder_completion(
                conn, owner_id=str(ladder["owner_id"]), run_id=str(ladder["run_id"])
            )
    run = await conn.fetchrow(
        "SELECT r.status,r.feedback_release_policy,r.feedback_released_at,r.material_id "
        "FROM quiz_runs r WHERE r.id=?1",
        ladder["run_id"],
    )
    release_policy = str(run["feedback_release_policy"]) if run else "after_completion"
    manually_released = bool(
        run
        and (
            run["feedback_released_at"]
            or await conn.fetchval(
                "SELECT 1 FROM study_material_feedback_releases WHERE material_id=?1",
                run["material_id"],
            )
        )
    )
    feedback_available = bool(
        run
        and (
            (release_policy == "after_completion" and run["status"] == "completed")
            or (release_policy == "manual" and manually_released)
        )
    )
    if feedback_available:
        result["correctness"] = "correct" if correct else "incorrect"
        if ladder["grading_policy"] == "reasoning_required":
            result["reasoning_quality"] = rubric_status
        result["xp_awarded"] = int(
            await conn.fetchval(
                "SELECT COALESCE(sum(amount),0) FROM learning_xp_events WHERE run_id=?1",
                ladder["run_id"],
            )
            or 0
        )
    await _finish_operation(
        conn,
        ladder_id=str(ladder["id"]),
        operation_id=operation_id,
        result=result,
    )
    return result


@router.post("/api/mastery-ladders/{ladder_id}/concepts/{concept_id}/response")
async def submit_ladder_response(
    ladder_id: UUID, concept_id: UUID, payload: LadderAnswer, request: Request
) -> dict[str, Any]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db = database(request)
    ladder, material, run = await _ladder_context(db, ladder_id, owner_id)
    operation_id = str(payload.operation_id)
    request_digest = _request_digest(
        {"question_id": str(payload.question_id), "answer": payload.answer}
    )
    replay = await _replay_completed_operation(
        db,
        ladder_id=str(ladder_id),
        concept_id=str(concept_id),
        operation_id=operation_id,
        operation_kind="response",
        request_digest=request_digest,
    )
    if replay is not None:
        return replay
    concept, question_row, attempt = await _active_ladder_question(
        db, ladder_id=str(ladder_id), concept_id=str(concept_id)
    )
    if str(question_row["id"]) != str(payload.question_id):
        raise ApiError(
            409, "stale_question", "This is no longer the active question for this concept."
        )
    should_run, prior_result = await _begin_operation(
        db,
        ladder=ladder,
        concept_id=str(concept_id),
        operation_id=operation_id,
        operation_kind="response",
        request_digest=request_digest,
        expected_version=payload.expected_version,
    )
    if not should_run:
        return prior_result or {}
    request.state.mastery_ladder_internal = True
    try:
        await submit_final_answer(
            UUID(str(ladder["material_id"])),
            UUID(str(ladder["run_id"])),
            str(question_row["id"]),
            FinalAnswerSubmission(
                operation_id=payload.operation_id,
                answer=payload.answer,
                expected_version=int(attempt["version"]),
            ),
            request,
            BackgroundTasks(),
        )
    except ApiError as error:
        await _fail_operation(
            db, ladder_id=str(ladder_id), operation_id=operation_id, error_code=error.code
        )
        raise
    updated_attempt = await db.fetchrow(
        "SELECT * FROM quiz_question_attempts WHERE id=?1", attempt["id"]
    )
    rubric_status = None
    if ladder["grading_policy"] == "reasoning_required":
        try:
            rubric = await _run_rubric(
                request,
                db,
                ladder=ladder,
                question_id=str(question_row["id"]),
                attempt=updated_attempt,
                operation_id=operation_id,
            )
            rubric_status = rubric["status"]
        except ApiError as error:
            await _fail_operation(
                db, ladder_id=str(ladder_id), operation_id=operation_id, error_code=error.code
            )
            raise
    async with db.acquire() as conn:
        async with conn.transaction():
            current_ladder = await conn.fetchrow(
                "SELECT * FROM mastery_ladder_sessions WHERE id=?1 AND owner_id=?2",
                ladder_id,
                owner_id,
            )
            if current_ladder is None:
                raise ApiError(404, "not_found", "The requested Mastery Ladder was not found.")
            await class_access(conn, current_ladder["class_id"], owner_id, required="read")
            if current_ladder["status"] != "active":
                raise ApiError(409, "ladder_terminal", "This Mastery Ladder is already finished.")
            return await _progress_after_answer(
                conn,
                ladder=current_ladder,
                concept=concept,
                current_question={
                    "id": str(question_row["id"]),
                    "role": question_row["role"],
                },
                attempt=updated_attempt,
                rubric_status=rubric_status,
                operation_id=operation_id,
            )


@router.post("/api/mastery-ladders/{ladder_id}/concepts/{concept_id}/reasoning")
async def submit_ladder_reasoning(
    ladder_id: UUID, concept_id: UUID, payload: LadderReasoning, request: Request
) -> dict[str, Any]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db = database(request)
    ladder, material, run = await _ladder_context(db, ladder_id, owner_id)
    if ladder["grading_policy"] != "reasoning_required":
        raise ApiError(409, "reasoning_not_required", "This ladder does not require reasoning.")
    operation_id = str(payload.operation_id)
    digest = _request_digest({"text": payload.text, "source": "text"})
    replay = await _replay_completed_operation(
        db,
        ladder_id=str(ladder_id),
        concept_id=str(concept_id),
        operation_id=operation_id,
        operation_kind="reasoning",
        request_digest=digest,
    )
    if replay is not None:
        return replay
    concept, question_row, attempt = await _active_ladder_question(
        db, ladder_id=str(ladder_id), concept_id=str(concept_id)
    )
    should_run, prior_result = await _begin_operation(
        db,
        ladder=ladder,
        concept_id=str(concept_id),
        operation_id=operation_id,
        operation_kind="reasoning",
        request_digest=digest,
        expected_version=payload.expected_version,
    )
    if not should_run:
        return prior_result or {}
    request.state.mastery_ladder_internal = True
    try:
        await submit_reasoning(
            UUID(str(ladder["material_id"])),
            UUID(str(ladder["run_id"])),
            str(question_row["id"]),
            ReasoningSubmission(
                operation_id=payload.operation_id,
                text=payload.text,
                source="text",
                expected_version=int(attempt["version"]),
            ),
            request,
        )
    except ApiError as error:
        await _fail_operation(
            db, ladder_id=str(ladder_id), operation_id=operation_id, error_code=error.code
        )
        raise
    updated = await db.fetchrow("SELECT * FROM quiz_question_attempts WHERE id=?1", attempt["id"])
    evaluation = await db.fetchrow(
        "SELECT status,result,follow_up FROM quiz_reasoning_evaluations "
        "WHERE attempt_id=?1 ORDER BY created_at DESC,id DESC LIMIT 1",
        attempt["id"],
    )
    result = {
        "question_id": str(question_row["id"]),
        "state": str(updated["state"]),
        "evaluation_status": str(evaluation["status"]) if evaluation else None,
        "reasoning_status": (
            "accepted"
            if updated["state"] == "reasoning_accepted"
            else "failed"
            if updated["state"] == "reasoning_failed"
            else "pending"
            if updated["state"] == "evaluating_reasoning"
            else "insufficient"
            if updated["state"] == "reasoning_insufficient"
            else None
        ),
        "follow_up": evaluation["follow_up"] if evaluation else None,
        "attempt_version": int(updated["version"]),
    }
    result["status"] = result["reasoning_status"]
    if updated["state"] == "reasoning_failed":
        await _fail_operation(
            db,
            ladder_id=str(ladder_id),
            operation_id=operation_id,
            error_code="reasoning_evaluation_failed",
        )
        # Return an actionable retry status while retaining the submitted turn.
        result["retry_available"] = True
        return result
    async with db.acquire() as conn:
        async with conn.transaction():
            current_ladder = await conn.fetchrow(
                "SELECT version FROM mastery_ladder_sessions WHERE id=?1 AND owner_id=?2",
                ladder_id,
                owner_id,
            )
            result["version"] = int(current_ladder["version"])
            await _finish_operation(
                conn,
                ladder_id=str(ladder_id),
                operation_id=operation_id,
                result=result,
            )
    return result


@router.post("/api/mastery-ladders/{ladder_id}/concepts/{concept_id}/reasoning/retry")
async def retry_ladder_reasoning(
    ladder_id: UUID, concept_id: UUID, payload: LadderOperation, request: Request
) -> dict[str, Any]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db = database(request)
    ladder, _material, _run = await _ladder_context(db, ladder_id, owner_id)
    if ladder["grading_policy"] != "reasoning_required":
        raise ApiError(409, "reasoning_not_required", "This ladder does not require reasoning.")
    operation_id = str(payload.operation_id)
    replay = await _replay_completed_operation(
        db,
        ladder_id=str(ladder_id),
        concept_id=str(concept_id),
        operation_id=operation_id,
        operation_kind="retry",
        request_digest=None,
    )
    if replay is not None:
        return replay
    concept, question_row, attempt = await _active_ladder_question(
        db, ladder_id=str(ladder_id), concept_id=str(concept_id)
    )
    digest = _request_digest({"action": "retry_reasoning", "concept_id": str(concept_id)})
    should_run, prior_result = await _begin_operation(
        db,
        ladder=ladder,
        concept_id=str(concept_id),
        operation_id=operation_id,
        operation_kind="retry",
        request_digest=digest,
        expected_version=payload.expected_version,
    )
    if not should_run:
        return prior_result or {}
    request.state.mastery_ladder_internal = True
    try:
        await retry_reasoning(
            UUID(str(ladder["material_id"])),
            UUID(str(ladder["run_id"])),
            str(question_row["id"]),
            OperationRequest(
                operation_id=payload.operation_id,
                expected_version=int(attempt["version"]),
            ),
            request,
        )
    except ApiError as error:
        await _fail_operation(
            db, ladder_id=str(ladder_id), operation_id=operation_id, error_code=error.code
        )
        raise
    updated = await db.fetchrow("SELECT * FROM quiz_question_attempts WHERE id=?1", attempt["id"])
    evaluation = await db.fetchrow(
        "SELECT status,result,follow_up FROM quiz_reasoning_evaluations "
        "WHERE attempt_id=?1 ORDER BY created_at DESC,id DESC LIMIT 1",
        attempt["id"],
    )
    result = {
        "question_id": str(question_row["id"]),
        "state": str(updated["state"]),
        "evaluation_status": str(evaluation["status"]) if evaluation else None,
        "reasoning_status": (
            "accepted"
            if updated["state"] == "reasoning_accepted"
            else "failed"
            if updated["state"] == "reasoning_failed"
            else "pending"
            if updated["state"] == "evaluating_reasoning"
            else "insufficient"
            if updated["state"] == "reasoning_insufficient"
            else None
        ),
        "follow_up": evaluation["follow_up"] if evaluation else None,
        "attempt_version": int(updated["version"]),
        "retry_available": updated["state"] == "reasoning_failed",
    }
    result["status"] = result["reasoning_status"]
    async with db.acquire() as conn:
        async with conn.transaction():
            current_ladder = await conn.fetchrow(
                "SELECT version FROM mastery_ladder_sessions WHERE id=?1 AND owner_id=?2",
                ladder_id,
                owner_id,
            )
            result["version"] = int(current_ladder["version"])
            if updated["state"] == "reasoning_failed":
                await _fail_operation(
                    db,
                    ladder_id=str(ladder_id),
                    operation_id=operation_id,
                    error_code="reasoning_evaluation_failed",
                )
            else:
                await _finish_operation(
                    conn,
                    ladder_id=str(ladder_id),
                    operation_id=operation_id,
                    result=result,
                )
    if updated["state"] == "reasoning_failed":
        raise ApiError(
            503,
            "reasoning_evaluation_failed",
            "Your reasoning is saved, but the check failed. Retry it without "
            "re-entering your explanation.",
        )
    return result


@router.post("/api/mastery-ladders/{ladder_id}/concepts/{concept_id}/skip")
async def skip_ladder_concept(
    ladder_id: UUID, concept_id: UUID, payload: LadderOperation, request: Request
) -> dict[str, Any]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db = database(request)
    ladder, material, run = await _ladder_context(db, ladder_id, owner_id)
    operation_id = str(payload.operation_id)
    digest = _request_digest({"action": "skip", "concept_id": str(concept_id)})
    replay = await _replay_completed_operation(
        db,
        ladder_id=str(ladder_id),
        concept_id=str(concept_id),
        operation_id=operation_id,
        operation_kind="skip",
        request_digest=digest,
    )
    if replay is not None:
        return replay
    concept, question_row, attempt = await _active_ladder_question(
        db, ladder_id=str(ladder_id), concept_id=str(concept_id)
    )
    should_run, prior_result = await _begin_operation(
        db,
        ladder=ladder,
        concept_id=str(concept_id),
        operation_id=operation_id,
        operation_kind="skip",
        request_digest=digest,
        expected_version=payload.expected_version,
    )
    if not should_run:
        return prior_result or {}
    result: dict[str, Any]
    async with db.acquire() as conn:
        async with conn.transaction():
            await class_access(conn, ladder["class_id"], owner_id, required="read")
            current = await conn.fetchrow(
                "SELECT * FROM quiz_question_attempts WHERE id=?1", attempt["id"]
            )
            if current["state"] in {"submitted", "completed", "skipped"}:
                await conn.execute(
                    "UPDATE mastery_ladder_operations SET status='failed',"
                    "error_code='attempt_terminal',updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                    "WHERE ladder_id=?1 AND operation_id=?2 AND status='pending'",
                    ladder_id,
                    operation_id,
                )
                result = {"error_code": "attempt_terminal"}
            else:
                active_eval = current["active_evaluation_operation_id"]
                if active_eval:
                    await conn.execute(
                        "UPDATE quiz_reasoning_evaluations SET status='superseded',"
                        "completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                        "WHERE attempt_id=?1 AND operation_id=?2 AND status='pending'",
                        attempt["id"],
                        active_eval,
                    )
                await conn.execute(
                    "UPDATE quiz_question_attempts SET state='skipped',skip_operation_id=?2,"
                    "active_evaluation_operation_id=NULL,"
                    "skipped_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),version=version+1,"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?1",
                    attempt["id"],
                    operation_id,
                )
                question = await conn.fetchrow(
                    "SELECT * FROM quiz_run_questions WHERE run_id=?1 AND id=?2",
                    ladder["run_id"],
                    question_row["id"],
                )
                await record_assessment_outcome(
                    conn,
                    run=run,
                    attempt={"id": attempt["id"]},
                    question_row=question,
                    status="skipped",
                    correctness=None,
                    reasoning_status="none",
                )
                await conn.execute(
                    "UPDATE mastery_ladder_concepts SET state='skipped',recovery_state='used',"
                    "current_question_id=NULL,recovery_question_id=NULL,version=version+1,"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                    "WHERE ladder_id=?1 AND concept_id=?2 AND state='active'",
                    ladder_id,
                    concept_id,
                )
                active = int(
                    await conn.fetchval(
                        "SELECT count(*) FROM mastery_ladder_concepts "
                        "WHERE ladder_id=?1 AND state='active'",
                        ladder_id,
                    )
                    or 0
                )
                session_status = "active" if active else "completed"
                await conn.execute(
                    "UPDATE mastery_ladder_sessions SET version=version+1,status=?2,"
                    "completed_at=CASE WHEN ?2='completed' THEN "
                    "strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE completed_at END,"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?1",
                    ladder_id,
                    session_status,
                )
                await _complete_run_if_ready(conn, str(ladder["run_id"]), material)
                updated = await conn.fetchrow(
                    "SELECT version FROM mastery_ladder_sessions WHERE id=?1", ladder_id
                )
                result = {
                    "progression": "skipped",
                    "correctness": "unanswered",
                    "reasoning_quality": None,
                    "version": int(updated["version"]),
                    "xp_awarded": 0,
                }
                await _finish_operation(
                    conn,
                    ladder_id=str(ladder_id),
                    operation_id=operation_id,
                    result=result,
                )
    if result.get("error_code"):
        raise ApiError(409, "attempt_terminal", "This question can no longer be skipped.")
    return result
