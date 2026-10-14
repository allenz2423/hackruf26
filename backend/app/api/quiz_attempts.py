"""Owner-scoped quiz run and reasoning-mode state transitions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import database, enforce_sqlite_api_policy
from app.api.errors import ApiError
from app.services.database import SQLiteConnection, SQLiteDatabase, json_value
from app.services.study_prompts import (
    QUIZ_REASONING_EVALUATOR_VERSION,
    QUIZ_REASONING_FEEDBACK_VERSION,
)

logger = logging.getLogger("screen_tutor.quiz_reasoning")
router = APIRouter(tags=["quiz-attempts"])
PROVIDER_DEADLINE_SECONDS = 35


class QuizRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: UUID
    mode: Literal["standard", "reasoning"]


class ReasoningSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    operation_id: UUID
    text: str = Field(min_length=1, max_length=12_000)
    source: Literal["text", "voice"] = "text"


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: UUID


class FinalAnswerSubmission(OperationRequest):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    answer: str = Field(min_length=1, max_length=5_000)


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _authorize(request: Request) -> tuple[UUID, SQLiteDatabase]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    return owner_id, database(request)


async def _owned_material(
    db: SQLiteDatabase, material_id: UUID, owner_id: UUID, *, require_ready: bool = True
) -> dict[str, Any]:
    row = await db.fetchrow(
        "SELECT m.id, m.kind, m.status, m.content, c.owner_id "
        "FROM study_materials m JOIN course_classes c ON c.id = m.class_id "
        "WHERE m.id = ?1 AND c.owner_id = ?2",
        material_id,
        owner_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if row["kind"] not in {"quiz", "practice_exam"}:
        raise ApiError(
            409, "attempt_not_supported", "Only quizzes and practice tests can be started."
        )
    if require_ready and row["status"] != "ready":
        raise ApiError(
            409, "material_not_ready", "Wait for this study material to finish generating."
        )
    return row


def _questions(material: dict[str, Any]) -> list[dict[str, Any]]:
    content = json_value(material.get("content"), {})
    questions = content.get("questions", []) if isinstance(content, dict) else []
    if not isinstance(questions, list) or not questions:
        raise ApiError(409, "quiz_empty", "This quiz does not contain any questions.")
    if any(
        not isinstance(q, dict) or not isinstance(q.get("id"), str) or not q["id"]
        for q in questions
    ):
        raise ApiError(409, "quiz_invalid", "This quiz contains an invalid question.")
    return questions


def _question(material: dict[str, Any], question_id: str) -> dict[str, Any]:
    if len(question_id) > 200:
        raise ApiError(404, "not_found", "The requested quiz question was not found.")
    found = next((item for item in _questions(material) if item["id"] == question_id), None)
    if found is None:
        raise ApiError(404, "not_found", "The requested quiz question was not found.")
    return found


async def _owned_run(
    db: SQLiteDatabase, material_id: UUID, run_id: UUID, owner_id: UUID
) -> dict[str, Any]:
    row = await db.fetchrow(
        "SELECT r.* FROM quiz_runs r JOIN study_materials m ON m.id = r.material_id "
        "JOIN course_classes c ON c.id = m.class_id "
        "WHERE r.id = ?1 AND r.material_id = ?2 AND r.owner_id = ?3 AND c.owner_id = ?3",
        run_id,
        material_id,
        owner_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested quiz run was not found.")
    return row


async def _attempt_payload(
    db: SQLiteConnection | SQLiteDatabase, attempt_id: str, question: dict[str, Any]
) -> dict[str, Any]:
    row = await db.fetchrow("SELECT * FROM quiz_question_attempts WHERE id = ?1", attempt_id)
    if row is None:
        raise ApiError(404, "not_found", "The question attempt was not found.")
    turns = await db.fetch(
        "SELECT sequence_number, operation_id, source, text, created_at "
        "FROM quiz_reasoning_turns "
        "WHERE attempt_id = ?1 ORDER BY sequence_number",
        attempt_id,
    )
    evaluation = await db.fetchrow(
        "SELECT operation_id, status, result, follow_up, evaluator_version, created_at "
        "FROM quiz_reasoning_evaluations WHERE attempt_id = ?1 "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        attempt_id,
    )
    result: dict[str, Any] = {
        "attempt_id": row["id"],
        "question_id": row["question_id"],
        "state": row["state"],
        "reasoning_turns": turns,
        "accepted_through_turn": row["accepted_through_turn"],
        "submitted_through_turn": row["submitted_through_turn"],
        "evaluation": (
            {
                "operation_id": evaluation["operation_id"],
                "status": evaluation["status"],
                "result": evaluation["result"],
                "follow_up": evaluation["follow_up"],
                "evaluator_version": evaluation["evaluator_version"],
                "created_at": evaluation["created_at"],
            }
            if evaluation
            else None
        ),
        "skipped_at": row["skipped_at"],
        "feedback_status": row["feedback_status"],
        "feedback_operation_id": row["feedback_operation_id"],
    }
    if row["state"] in {"submitted", "completed"}:
        result.update(
            {
                "final_answer": row["final_answer"],
                "correct": bool(row["correct"]),
                "correct_answer": _display_answer(question),
                "explanation": str(question.get("explanation", "")),
                "answer_feedback": row["answer_feedback"],
                "reasoning_feedback": row["reasoning_feedback"],
                "submitted_at": row["submitted_at"],
                "completed_at": row["completed_at"],
            }
        )
    return result


def _display_answer(question: dict[str, Any]) -> str:
    answer = str(question.get("answer", ""))
    if question.get("kind") == "multiple_choice":
        option = next(
            (
                choice
                for choice in question.get("choices", [])
                if isinstance(choice, dict) and choice.get("id") == answer
            ),
            None,
        )
        if option:
            return str(option.get("text", answer))
    if question.get("kind") == "true_false":
        return "True" if answer.casefold() == "true" else "False"
    return answer


async def _run_payload(
    db: SQLiteDatabase, run: dict[str, Any], material: dict[str, Any]
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "run_id": run["id"],
        "material_id": run["material_id"],
        "mode": run["mode"],
        "status": run["status"],
        "score_percent": run["score_percent"],
        "created_at": run["created_at"],
        "completed_at": run["completed_at"],
        "attempts": [],
    }
    if run["mode"] == "standard":
        legacy = await db.fetchrow(
            "SELECT answers, score_percent, completed_at FROM study_material_attempts "
            "WHERE id = ?1",
            run["id"],
        )
        if legacy:
            response["answers"] = json_value(legacy["answers"], {})
        return response
    by_id = {str(item["id"]): item for item in _questions(material)}
    attempts = await db.fetch(
        "SELECT id, question_id FROM quiz_question_attempts WHERE run_id = ?1 ORDER BY rowid",
        run["id"],
    )
    response["attempts"] = [
        await _attempt_payload(db, item["id"], by_id[item["question_id"]])
        for item in attempts
        if item["question_id"] in by_id
    ]
    return response


async def _record_metric(
    conn: SQLiteConnection,
    *,
    latency_ms: int = 0,
    first: bool = False,
    accepted: bool = False,
    turns: int = 0,
    failed: bool = False,
    retry: bool = False,
) -> None:
    day = datetime.now(UTC).date().isoformat()
    bucket = (
        "latency_le_1s"
        if latency_ms <= 1000
        else "latency_le_3s"
        if latency_ms <= 3000
        else "latency_le_10s"
        if latency_ms <= 10_000
        else "latency_le_30s"
        if latency_ms <= 30_000
        else "latency_gt_30s"
    )
    await conn.execute(
        "INSERT INTO quiz_reasoning_metrics "
        "(metric_date, evaluation_count, first_evaluation_count, "
        "first_accepted_count, followup_turn_count, accepted_attempt_count, "
        "evaluation_failure_count, "
        "retry_count, latency_total_ms, " + bucket + ") "
        "VALUES (?1, 1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 1) "
        "ON CONFLICT(metric_date) DO UPDATE SET evaluation_count = evaluation_count + 1, "
        "first_evaluation_count = first_evaluation_count + excluded.first_evaluation_count, "
        "first_accepted_count = first_accepted_count + excluded.first_accepted_count, "
        "followup_turn_count = followup_turn_count + excluded.followup_turn_count, "
        "accepted_attempt_count = accepted_attempt_count + excluded.accepted_attempt_count, "
        "evaluation_failure_count = evaluation_failure_count + excluded.evaluation_failure_count, "
        "retry_count = retry_count + excluded.retry_count, "
        "latency_total_ms = latency_total_ms + excluded.latency_total_ms, "
        + bucket
        + " = "
        + bucket
        + " + 1",
        day,
        int(first),
        int(first and accepted),
        max(0, turns - 1) if accepted else 0,
        int(accepted),
        int(failed),
        int(retry),
        latency_ms,
    )


async def _complete_run_if_ready(
    conn: SQLiteConnection, run_id: str, material: dict[str, Any]
) -> None:
    open_count = await conn.fetchval(
        "SELECT count(*) FROM quiz_question_attempts WHERE run_id = ?1 "
        "AND state NOT IN ('completed', 'skipped')",
        run_id,
    )
    if open_count:
        return
    questions = _questions(material)
    count = await conn.fetchval(
        "SELECT count(*) FROM quiz_question_attempts WHERE run_id = ?1",
        run_id,
    )
    if count != len(questions):
        return
    correct = await conn.fetchval(
        "SELECT count(*) FROM quiz_question_attempts WHERE run_id = ?1 "
        "AND state = 'completed' AND correct = 1",
        run_id,
    )
    score = round(int(correct) * 100 / max(1, len(questions)), 2)
    completed = await conn.fetchrow(
        "UPDATE quiz_runs SET status = 'completed', score_percent = ?2, "
        "completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
        "WHERE id = ?1 AND status = 'in_progress' RETURNING id",
        run_id,
        score,
    )
    if completed is None:
        return
    answers = await conn.fetch(
        "SELECT question_id, final_answer FROM quiz_question_attempts "
        "WHERE run_id = ?1 AND final_answer IS NOT NULL",
        run_id,
    )
    run = await conn.fetchrow("SELECT material_id FROM quiz_runs WHERE id = ?1", run_id)
    await conn.execute(
        "INSERT OR IGNORE INTO study_material_attempts (id, material_id, answers, score_percent) "
        "VALUES (?1, ?2, ?3, ?4)",
        run_id,
        run["material_id"],
        json.dumps({row["question_id"]: row["final_answer"] for row in answers}),
        score,
    )


async def _evaluation_context(
    db: SQLiteDatabase, attempt_id: str, question: dict[str, Any]
) -> tuple[dict[str, Any], str, int]:
    turns = await db.fetch(
        "SELECT sequence_number, source, text FROM quiz_reasoning_turns "
        "WHERE attempt_id = ?1 ORDER BY sequence_number",
        attempt_id,
    )
    previous = await db.fetch(
        "SELECT follow_up FROM quiz_reasoning_evaluations WHERE attempt_id = ?1 "
        "AND status = 'available' AND result = 'insufficient' AND follow_up IS NOT NULL "
        "ORDER BY created_at, id",
        attempt_id,
    )
    digest = _digest([(turn["sequence_number"], turn["source"], turn["text"]) for turn in turns])
    choices = question.get("choices")
    context = {
        "question": {
            "id": question["id"],
            "text": str(question.get("prompt", ""))[:8_000],
            "type": str(question.get("kind", "short_answer")),
            "choices": [
                {"id": str(item.get("id", "")), "text": str(item.get("text", ""))[:1_000]}
                for item in choices
                if isinstance(item, dict)
            ][:8]
            if isinstance(choices, list)
            else [],
            "complexity": str(question.get("complexity", ""))[:500],
            "instructional_metadata": str(question.get("instructional_metadata", ""))[:1_000],
        },
        "reasoning_turns": turns,
        "latest_turn": turns[-1],
        "previous_follow_up_prompts": [str(item["follow_up"])[:300] for item in previous[-10:]],
    }
    return context, digest, len(turns)


async def _run_evaluation(
    request: Request,
    db: SQLiteDatabase,
    attempt_id: str,
    operation_id: str,
    question: dict[str, Any],
    context: dict[str, Any],
    snapshot_hash: str,
    through_turn: int,
    *,
    first: bool,
    retry: bool,
) -> dict[str, Any]:
    started = time.monotonic()
    failed = False
    decision = None
    try:
        limiter = request.app.state.quiz_reasoning_rate_limiter
        delay = await limiter.check(f"user:{request.state.user_id}")
        if delay is not None:
            raise ApiError(
                429,
                "reasoning_rate_limited",
                "Please wait before checking more explanations.",
                delay,
            )
        service = request.app.state.quiz_reasoning_service
        decision = await asyncio.wait_for(
            asyncio.to_thread(service.evaluate, context), timeout=PROVIDER_DEADLINE_SECONDS
        )
    except ApiError:
        failed = True
        async with db.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE quiz_reasoning_evaluations SET status = 'failed', "
                    "completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE attempt_id = ?1 AND operation_id = ?2 AND status = 'pending'",
                    attempt_id,
                    operation_id,
                )
                await conn.execute(
                    "UPDATE quiz_question_attempts SET state = 'reasoning_failed', "
                    "active_evaluation_operation_id = NULL, version = version + 1, "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE id = ?1 AND state = 'evaluating_reasoning' "
                    "AND active_evaluation_operation_id = ?2",
                    attempt_id,
                    operation_id,
                )
                await _record_metric(
                    conn,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    first=first,
                    failed=True,
                    retry=retry,
                    turns=through_turn,
                )
        raise
    except Exception as exc:
        failed = True
        logger.info("quiz_reasoning_evaluation_failed category=%s", type(exc).__name__)
    latency_ms = int((time.monotonic() - started) * 1000)
    async with db.acquire() as conn:
        async with conn.transaction():
            if failed or decision is None:
                await conn.execute(
                    "UPDATE quiz_reasoning_evaluations SET status = 'failed', "
                    "completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE attempt_id = ?1 AND operation_id = ?2 AND status = 'pending'",
                    attempt_id,
                    operation_id,
                )
                await conn.execute(
                    "UPDATE quiz_question_attempts SET state = 'reasoning_failed', "
                    "active_evaluation_operation_id = NULL, version = version + 1, "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE id = ?1 AND state = 'evaluating_reasoning' "
                    "AND active_evaluation_operation_id = ?2",
                    attempt_id,
                    operation_id,
                )
                await _record_metric(
                    conn,
                    latency_ms=latency_ms,
                    first=first,
                    failed=True,
                    retry=retry,
                    turns=through_turn,
                )
            else:
                await conn.execute(
                    "UPDATE quiz_reasoning_evaluations SET status = 'available', result = ?3, "
                    "internal_reason = ?4, follow_up = ?5, "
                    "completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE attempt_id = ?1 AND operation_id = ?2 AND status = 'pending'",
                    attempt_id,
                    operation_id,
                    decision.status,
                    decision.reason,
                    decision.follow_up,
                )
                target = (
                    "reasoning_accepted"
                    if decision.status == "sufficient"
                    else "reasoning_insufficient"
                )
                accepted = decision.status == "sufficient"
                updated = await conn.fetchrow(
                    "UPDATE quiz_question_attempts SET state = ?3, "
                    "accepted_through_turn = CASE WHEN ?4 "
                    "THEN COALESCE(accepted_through_turn, ?5) ELSE accepted_through_turn END, "
                    "accepted_snapshot_hash = CASE WHEN ?4 "
                    "THEN COALESCE(accepted_snapshot_hash, ?6) ELSE accepted_snapshot_hash END, "
                    "active_evaluation_operation_id = NULL, version = version + 1, "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE id = ?1 AND state = 'evaluating_reasoning' "
                    "AND active_evaluation_operation_id = ?2 "
                    "AND EXISTS (SELECT 1 FROM quiz_reasoning_evaluations e "
                    "WHERE e.attempt_id = ?1 "
                    "AND e.operation_id = ?2 AND e.snapshot_hash = ?6 AND e.status = 'available') "
                    "RETURNING id",
                    attempt_id,
                    operation_id,
                    target,
                    accepted,
                    through_turn,
                    snapshot_hash,
                )
                if updated is None:
                    await conn.execute(
                        "UPDATE quiz_reasoning_evaluations SET status = 'superseded' "
                        "WHERE attempt_id = ?1 AND operation_id = ?2 AND status = 'available'",
                        attempt_id,
                        operation_id,
                    )
                await _record_metric(
                    conn,
                    latency_ms=latency_ms,
                    first=first,
                    accepted=accepted and updated is not None,
                    retry=retry,
                    turns=through_turn,
                )
    if failed:
        raise ApiError(
            503,
            "reasoning_evaluation_failed",
            "We could not check this explanation. Your submitted reasoning is saved; "
            "retry the evaluation or skip the question.",
        )
    return await _attempt_payload(db, attempt_id, question)


@router.post("/api/study-materials/{material_id}/runs", status_code=201)
async def create_quiz_run(
    material_id: UUID, payload: QuizRunCreate, request: Request
) -> dict[str, Any]:
    owner_id, db = await _authorize(request)
    material = await _owned_material(db, material_id, owner_id)
    questions = _questions(material)
    async with db.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT * FROM quiz_runs WHERE material_id = ?1 "
                "AND owner_id = ?2 AND operation_id = ?3",
                material_id,
                owner_id,
                payload.operation_id,
            )
            if existing is not None:
                if existing["mode"] != payload.mode:
                    raise ApiError(
                        409,
                        "idempotency_conflict",
                        "This run request ID was already used for another quiz mode.",
                    )
                run = existing
            else:
                run_id = uuid4()
                await conn.execute(
                    "INSERT INTO quiz_runs (id, material_id, owner_id, operation_id, mode) "
                    "VALUES (?1, ?2, ?3, ?4, ?5)",
                    run_id,
                    material_id,
                    owner_id,
                    payload.operation_id,
                    payload.mode,
                )
                if payload.mode == "reasoning":
                    await conn.executemany(
                        "INSERT INTO quiz_question_attempts (id, run_id, question_id) "
                        "VALUES (?1, ?2, ?3)",
                        [
                            (str(uuid4()), str(run_id), str(question["id"]))
                            for question in questions
                        ],
                    )
                    await conn.execute(
                        "INSERT INTO quiz_reasoning_metrics (metric_date, question_attempt_count) "
                        "VALUES (?1, ?2) ON CONFLICT(metric_date) DO UPDATE SET "
                        "question_attempt_count = question_attempt_count "
                        "+ excluded.question_attempt_count",
                        datetime.now(UTC).date().isoformat(),
                        len(questions),
                    )
                run = await conn.fetchrow("SELECT * FROM quiz_runs WHERE id = ?1", run_id)
    return await _run_payload(db, run, material)


@router.get("/api/study-materials/{material_id}/runs")
async def quiz_run_history(material_id: UUID, request: Request) -> dict[str, Any]:
    owner_id, db = await _authorize(request)
    await _owned_material(db, material_id, owner_id, require_ready=False)
    rows = await db.fetch(
        "SELECT id, mode, status, score_percent, created_at, completed_at FROM quiz_runs "
        "WHERE material_id = ?1 AND owner_id = ?2 ORDER BY created_at DESC LIMIT 50",
        material_id,
        owner_id,
    )
    return {"items": rows}


@router.get("/api/study-materials/{material_id}/runs/{run_id}")
async def get_quiz_run(material_id: UUID, run_id: UUID, request: Request) -> dict[str, Any]:
    owner_id, db = await _authorize(request)
    material = await _owned_material(db, material_id, owner_id, require_ready=False)
    run = await _owned_run(db, material_id, run_id, owner_id)
    return await _run_payload(db, run, material)


@router.post("/api/study-materials/{material_id}/runs/{run_id}/questions/{question_id}/reasoning")
async def submit_reasoning(
    material_id: UUID,
    run_id: UUID,
    question_id: str,
    payload: ReasoningSubmission,
    request: Request,
) -> dict[str, Any]:
    owner_id, db = await _authorize(request)
    material = await _owned_material(db, material_id, owner_id)
    question = _question(material, question_id)
    run = await _owned_run(db, material_id, run_id, owner_id)
    if run["mode"] != "reasoning":
        raise ApiError(
            409, "wrong_quiz_mode", "Reasoning actions are available only in Reasoning Mode."
        )
    attempt = await db.fetchrow(
        "SELECT a.* FROM quiz_question_attempts a WHERE a.run_id = ?1 AND a.question_id = ?2",
        run_id,
        question_id,
    )
    if attempt is None:
        raise ApiError(404, "not_found", "The requested question attempt was not found.")
    operation_id = str(payload.operation_id)
    content_digest = _digest({"text": payload.text, "source": payload.source})
    context = snapshot_hash = ""
    through_turn = 0
    first = False
    should_evaluate = False
    async with db.acquire() as conn:
        async with conn.transaction():
            attempt = await conn.fetchrow(
                "SELECT * FROM quiz_question_attempts WHERE id = ?1", attempt["id"]
            )
            existing = await conn.fetchrow(
                "SELECT * FROM quiz_reasoning_turns WHERE attempt_id = ?1 AND operation_id = ?2",
                attempt["id"],
                operation_id,
            )
            if existing is not None:
                if existing["content_digest"] != content_digest:
                    raise ApiError(
                        409,
                        "idempotency_conflict",
                        "This reasoning request ID was already used with different content.",
                    )
                return await _attempt_payload(conn, attempt["id"], question)
            if attempt["state"] in {"submitted", "completed", "skipped"}:
                raise ApiError(
                    409, "attempt_terminal", "This question attempt is already finished."
                )
            if attempt["state"] == "evaluating_reasoning":
                raise ApiError(
                    409,
                    "evaluation_in_progress",
                    "A reasoning check is already running. Keep this explanation "
                    "and check its status.",
                )
            if attempt["state"] not in {
                "awaiting_reasoning",
                "reasoning_insufficient",
                "reasoning_failed",
                "reasoning_accepted",
            }:
                raise ApiError(
                    409,
                    "invalid_attempt_state",
                    "This question is not ready for another reasoning turn.",
                )
            through_turn = (
                int(
                    await conn.fetchval(
                        "SELECT count(*) FROM quiz_reasoning_turns WHERE attempt_id = ?1",
                        attempt["id"],
                    )
                )
                + 1
            )
            await conn.execute(
                "INSERT INTO quiz_reasoning_turns "
                "(id, attempt_id, sequence_number, operation_id, content_digest, source, text) "
                "VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                uuid4(),
                attempt["id"],
                through_turn,
                operation_id,
                content_digest,
                payload.source,
                payload.text,
            )
            if attempt["state"] == "reasoning_accepted":
                # Acceptance is an entitlement; later turns are appended for final feedback.
                await conn.execute(
                    "UPDATE quiz_question_attempts SET version = version + 1, "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE id = ?1 AND state = 'reasoning_accepted'",
                    attempt["id"],
                )
                return await _attempt_payload(conn, attempt["id"], question)
            turns = await conn.fetch(
                "SELECT sequence_number, source, text FROM quiz_reasoning_turns "
                "WHERE attempt_id = ?1 ORDER BY sequence_number",
                attempt["id"],
            )
            snapshot_hash = _digest([(t["sequence_number"], t["source"], t["text"]) for t in turns])
            previous = await conn.fetchval(
                "SELECT count(*) FROM quiz_reasoning_evaluations WHERE attempt_id = ?1",
                attempt["id"],
            )
            first = int(previous) == 0
            await conn.execute(
                "INSERT INTO quiz_reasoning_evaluations "
                "(id, attempt_id, operation_id, snapshot_hash, through_turn, "
                "status, evaluator_version) "
                "VALUES (?1, ?2, ?3, ?4, ?5, 'pending', ?6)",
                uuid4(),
                attempt["id"],
                operation_id,
                snapshot_hash,
                through_turn,
                QUIZ_REASONING_EVALUATOR_VERSION,
            )
            await conn.execute(
                "UPDATE quiz_question_attempts SET state = 'evaluating_reasoning', "
                "active_evaluation_operation_id = ?2, version = version + 1, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?1",
                attempt["id"],
                operation_id,
            )
            context, actual_hash, through_turn = await _evaluation_context(
                conn, attempt["id"], question
            )
            snapshot_hash = actual_hash
            should_evaluate = True
    if should_evaluate:
        return await _run_evaluation(
            request,
            db,
            attempt["id"],
            operation_id,
            question,
            context,
            snapshot_hash,
            through_turn,
            first=first,
            retry=False,
        )
    raise ApiError(500, "internal_error", "The reasoning check could not be started.")


@router.post(
    "/api/study-materials/{material_id}/runs/{run_id}/questions/{question_id}/reasoning/retry"
)
async def retry_reasoning(
    material_id: UUID, run_id: UUID, question_id: str, payload: OperationRequest, request: Request
) -> dict[str, Any]:
    owner_id, db = await _authorize(request)
    material = await _owned_material(db, material_id, owner_id)
    question = _question(material, question_id)
    run = await _owned_run(db, material_id, run_id, owner_id)
    if run["mode"] != "reasoning":
        raise ApiError(
            409, "wrong_quiz_mode", "Reasoning actions are available only in Reasoning Mode."
        )
    attempt = await db.fetchrow(
        "SELECT * FROM quiz_question_attempts WHERE run_id = ?1 AND question_id = ?2",
        run_id,
        question_id,
    )
    if attempt is None:
        raise ApiError(404, "not_found", "The requested question attempt was not found.")
    operation_id = str(payload.operation_id)
    context, snapshot_hash, through_turn = await _evaluation_context(db, attempt["id"], question)
    first = False
    async with db.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT * FROM quiz_reasoning_evaluations "
                "WHERE attempt_id = ?1 AND operation_id = ?2",
                attempt["id"],
                operation_id,
            )
            if existing is not None:
                if existing["snapshot_hash"] != snapshot_hash:
                    raise ApiError(
                        409,
                        "idempotency_conflict",
                        "This retry ID was already used for another reasoning snapshot.",
                    )
                if existing["status"] == "available":
                    return await _attempt_payload(conn, attempt["id"], question)
                if existing["status"] == "pending":
                    raise ApiError(
                        409, "evaluation_in_progress", "This reasoning check is already running."
                    )
                raise ApiError(
                    503,
                    "reasoning_evaluation_failed",
                    "This retry did not complete. Start a new retry.",
                )
            current = await conn.fetchrow(
                "SELECT * FROM quiz_question_attempts WHERE id = ?1", attempt["id"]
            )
            if current["state"] == "skipped":
                raise ApiError(409, "attempt_terminal", "This question was skipped.")
            if current["state"] == "evaluating_reasoning":
                raise ApiError(
                    409, "evaluation_in_progress", "A reasoning check is already running."
                )
            if current["state"] != "reasoning_failed":
                raise ApiError(
                    409, "retry_not_available", "Retry is available after a failed reasoning check."
                )
            await conn.execute(
                "INSERT INTO quiz_reasoning_evaluations "
                "(id, attempt_id, operation_id, snapshot_hash, through_turn, "
                "status, evaluator_version) "
                "VALUES (?1, ?2, ?3, ?4, ?5, 'pending', ?6)",
                uuid4(),
                attempt["id"],
                operation_id,
                snapshot_hash,
                through_turn,
                QUIZ_REASONING_EVALUATOR_VERSION,
            )
            await conn.execute(
                "UPDATE quiz_question_attempts SET state = 'evaluating_reasoning', "
                "active_evaluation_operation_id = ?2, version = version + 1, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?1 AND state = 'reasoning_failed'",
                attempt["id"],
                operation_id,
            )
    return await _run_evaluation(
        request,
        db,
        attempt["id"],
        operation_id,
        question,
        context,
        snapshot_hash,
        through_turn,
        first=first,
        retry=True,
    )


@router.post("/api/study-materials/{material_id}/runs/{run_id}/questions/{question_id}/skip")
async def skip_question(
    material_id: UUID, run_id: UUID, question_id: str, payload: OperationRequest, request: Request
) -> dict[str, Any]:
    owner_id, db = await _authorize(request)
    material = await _owned_material(db, material_id, owner_id)
    question = _question(material, question_id)
    run = await _owned_run(db, material_id, run_id, owner_id)
    if run["mode"] != "reasoning":
        raise ApiError(
            409, "wrong_quiz_mode", "Skip confirmation is available only in Reasoning Mode."
        )
    attempt = await db.fetchrow(
        "SELECT id FROM quiz_question_attempts WHERE run_id = ?1 AND question_id = ?2",
        run_id,
        question_id,
    )
    if attempt is None:
        raise ApiError(404, "not_found", "The question attempt was not found.")
    operation_id = str(payload.operation_id)
    async with db.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                "SELECT * FROM quiz_question_attempts WHERE id = ?1", attempt["id"]
            )
            if current["state"] == "skipped":
                if current["skip_operation_id"] != operation_id:
                    # A distinct confirmation cannot change an already recorded skip.
                    return await _attempt_payload(conn, attempt["id"], question)
                return await _attempt_payload(conn, attempt["id"], question)
            if current["state"] in {"submitted", "completed"}:
                raise ApiError(409, "attempt_terminal", "A submitted question cannot be skipped.")
            pending_operation = current["active_evaluation_operation_id"]
            if pending_operation:
                await conn.execute(
                    "UPDATE quiz_reasoning_evaluations SET status = 'superseded', "
                    "completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE attempt_id = ?1 AND operation_id = ?2 AND status = 'pending'",
                    attempt["id"],
                    pending_operation,
                )
            await conn.execute(
                "UPDATE quiz_question_attempts SET state = 'skipped', skip_operation_id = ?2, "
                "skipped_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                "active_evaluation_operation_id = NULL, version = version + 1, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?1",
                attempt["id"],
                operation_id,
            )
            await conn.execute(
                "INSERT INTO quiz_reasoning_metrics (metric_date, skip_count, followup_turn_count) "
                "VALUES (?1, 1, max(?2 - 1, 0)) ON CONFLICT(metric_date) DO UPDATE SET "
                "skip_count = skip_count + 1, "
                "followup_turn_count = followup_turn_count + excluded.followup_turn_count",
                datetime.now(UTC).date().isoformat(),
                await conn.fetchval(
                    "SELECT count(*) FROM quiz_reasoning_turns WHERE attempt_id = ?1", attempt["id"]
                ),
            )
            await _complete_run_if_ready(conn, str(run_id), material)
    return await _attempt_payload(db, attempt["id"], question)


def _is_correct(question: dict[str, Any], submitted: str) -> bool:
    expected = str(question.get("answer", ""))
    kind = question.get("kind")
    if kind == "multiple_choice":
        choices = question.get("choices", [])
        if submitted not in {str(item.get("id", "")) for item in choices if isinstance(item, dict)}:
            raise ApiError(
                400, "invalid_answer", "Choose one of the listed answers before submitting."
            )
        return submitted == expected
    if kind == "true_false":
        if submitted.casefold() not in {"true", "false"}:
            raise ApiError(400, "invalid_answer", "Choose True or False before submitting.")
        return submitted.casefold() == expected.casefold()
    return _normalize_answer(submitted) in {
        _normalize_answer(str(value))
        for value in [expected, *(question.get("accepted_answers") or [])]
    }


def _normalize_answer(value: str) -> str:
    import unicodedata

    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


@router.post("/api/study-materials/{material_id}/runs/{run_id}/questions/{question_id}/answer")
async def submit_final_answer(
    material_id: UUID,
    run_id: UUID,
    question_id: str,
    payload: FinalAnswerSubmission,
    request: Request,
) -> dict[str, Any]:
    owner_id, db = await _authorize(request)
    material = await _owned_material(db, material_id, owner_id)
    question = _question(material, question_id)
    run = await _owned_run(db, material_id, run_id, owner_id)
    if run["mode"] != "reasoning":
        raise ApiError(
            409,
            "wrong_quiz_mode",
            "Reasoning answer submission is available only in Reasoning Mode.",
        )
    attempt = await db.fetchrow(
        "SELECT * FROM quiz_question_attempts WHERE run_id = ?1 AND question_id = ?2",
        run_id,
        question_id,
    )
    if attempt is None:
        raise ApiError(404, "not_found", "The question attempt was not found.")
    operation_id = str(payload.operation_id)
    answer_digest = _digest(payload.answer)
    correct = False
    feedback_operation_id = str(uuid4())
    final_context: dict[str, Any] | None = None
    async with db.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                "SELECT * FROM quiz_question_attempts WHERE id = ?1", attempt["id"]
            )
            if current["answer_operation_id"] is not None:
                if (
                    current["answer_operation_id"] != operation_id
                    or current["answer_operation_digest"] != answer_digest
                ):
                    raise ApiError(
                        409, "answer_immutable", "This question already has a submitted answer."
                    )
                return await _attempt_payload(conn, attempt["id"], question)
            if current["state"] != "reasoning_accepted":
                raise ApiError(
                    409,
                    "reasoning_required",
                    "Submit meaningful reasoning and wait for backend acceptance "
                    "before submitting your answer.",
                )
            correct = _is_correct(question, payload.answer)
            turns = await conn.fetch(
                "SELECT sequence_number, source, text FROM quiz_reasoning_turns "
                "WHERE attempt_id = ?1 ORDER BY sequence_number",
                attempt["id"],
            )
            through_turn = len(turns)
            snapshot_hash = _digest([(t["sequence_number"], t["source"], t["text"]) for t in turns])
            answer_text = payload.answer
            # Freeze answer and submitted reasoning before feedback generation.
            await conn.execute(
                "UPDATE quiz_question_attempts SET state = 'submitted', final_answer = ?2, "
                "answer_operation_id = ?3, answer_operation_digest = ?4, correct = ?5, "
                "submitted_through_turn = ?6, submitted_snapshot_hash = ?7, "
                "submitted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                "feedback_status = 'pending', "
                "feedback_operation_id = ?8, version = version + 1, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?1",
                attempt["id"],
                answer_text,
                operation_id,
                answer_digest,
                int(correct),
                through_turn,
                snapshot_hash,
                feedback_operation_id,
            )
            await conn.execute(
                "INSERT INTO quiz_feedback_operations "
                "(id, attempt_id, operation_id, status, feedback_version) "
                "VALUES (?1, ?2, ?3, 'pending', ?4)",
                uuid4(),
                attempt["id"],
                feedback_operation_id,
                QUIZ_REASONING_FEEDBACK_VERSION,
            )
            await conn.execute(
                "UPDATE quiz_question_attempts SET state = 'completed', "
                "completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?1 AND state = 'submitted'",
                attempt["id"],
            )
            await _complete_run_if_ready(conn, str(run_id), material)
            final_context = {
                "question": {
                    "id": question["id"],
                    "text": str(question.get("prompt", ""))[:8_000],
                    "type": question.get("kind", "short_answer"),
                },
                "trusted_answer": {
                    "answer": _display_answer(question),
                    "explanation": str(question.get("explanation", ""))[:3_000],
                },
                "student_answer": answer_text,
                "authoritative_grade": "correct" if correct else "incorrect",
                "reasoning_turns": turns,
            }
    await _generate_feedback(request, db, attempt["id"], feedback_operation_id, final_context)
    return await _attempt_payload(db, attempt["id"], question)


async def _generate_feedback(
    request: Request,
    db: SQLiteDatabase,
    attempt_id: str,
    operation_id: str,
    context: dict[str, Any],
) -> None:
    service = request.app.state.quiz_reasoning_service
    try:
        feedback = await asyncio.wait_for(
            asyncio.to_thread(service.feedback, context), timeout=PROVIDER_DEADLINE_SECONDS
        )
    except Exception as exc:
        logger.info("quiz_reasoning_feedback_failed category=%s", type(exc).__name__)
        async with db.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE quiz_feedback_operations SET status = 'failed', "
                    "completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE attempt_id = ?1 AND operation_id = ?2 AND status = 'pending'",
                    attempt_id,
                    operation_id,
                )
                await conn.execute(
                    "UPDATE quiz_question_attempts SET feedback_status = 'failed', "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE id = ?1 AND state = 'completed' AND feedback_operation_id = ?2 "
                    "AND feedback_status = 'pending'",
                    attempt_id,
                    operation_id,
                )
        return
    async with db.acquire() as conn:
        async with conn.transaction():
            op = await conn.fetchrow(
                "UPDATE quiz_feedback_operations SET status = 'available', "
                "completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE attempt_id = ?1 AND operation_id = ?2 AND status = 'pending' RETURNING id",
                attempt_id,
                operation_id,
            )
            if op is not None:
                await conn.execute(
                    "UPDATE quiz_question_attempts SET answer_feedback = ?3, "
                    "reasoning_feedback = ?4, feedback_status = 'available', "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE id = ?1 AND state = 'completed' AND feedback_operation_id = ?2 "
                    "AND feedback_status = 'pending'",
                    attempt_id,
                    operation_id,
                    feedback.answer_feedback,
                    feedback.reasoning_feedback,
                )


@router.post(
    "/api/study-materials/{material_id}/runs/{run_id}/questions/{question_id}/feedback/retry"
)
async def retry_feedback(
    material_id: UUID, run_id: UUID, question_id: str, payload: OperationRequest, request: Request
) -> dict[str, Any]:
    owner_id, db = await _authorize(request)
    material = await _owned_material(db, material_id, owner_id)
    question = _question(material, question_id)
    run = await _owned_run(db, material_id, run_id, owner_id)
    if run["mode"] != "reasoning":
        raise ApiError(
            409, "wrong_quiz_mode", "Reasoning feedback is available only in Reasoning Mode."
        )
    attempt = await db.fetchrow(
        "SELECT a.* FROM quiz_question_attempts a WHERE a.run_id = ?1 AND a.question_id = ?2",
        run_id,
        question_id,
    )
    if attempt is None or attempt["state"] != "completed":
        raise ApiError(
            409, "feedback_not_available", "Feedback retry is available after answer grading."
        )
    operation_id = str(payload.operation_id)
    context = await _feedback_context(db, attempt, question)
    async with db.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT status FROM quiz_feedback_operations "
                "WHERE attempt_id = ?1 AND operation_id = ?2",
                attempt["id"],
                operation_id,
            )
            if existing:
                if existing["status"] == "pending":
                    return await _attempt_payload(conn, attempt["id"], question)
                if existing["status"] == "available":
                    return await _attempt_payload(conn, attempt["id"], question)
                raise ApiError(
                    503, "feedback_failed", "This feedback retry failed. Start another retry."
                )
            current = await conn.fetchrow(
                "SELECT * FROM quiz_question_attempts WHERE id = ?1", attempt["id"]
            )
            if current["feedback_status"] != "failed":
                raise ApiError(
                    409,
                    "feedback_retry_not_available",
                    "Feedback can be retried after a failed attempt.",
                )
            await conn.execute(
                "INSERT INTO quiz_feedback_operations "
                "(id, attempt_id, operation_id, status, feedback_version) "
                "VALUES (?1, ?2, ?3, 'pending', ?4)",
                uuid4(),
                attempt["id"],
                operation_id,
                QUIZ_REASONING_FEEDBACK_VERSION,
            )
            await conn.execute(
                "UPDATE quiz_question_attempts SET feedback_status = 'pending', "
                "feedback_operation_id = ?2, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?1 AND feedback_status = 'failed'",
                attempt["id"],
                operation_id,
            )
    await _generate_feedback(request, db, attempt["id"], operation_id, context)
    return await _attempt_payload(db, attempt["id"], question)


async def _feedback_context(
    db: SQLiteDatabase, attempt: dict[str, Any], question: dict[str, Any]
) -> dict[str, Any]:
    turns = await db.fetch(
        "SELECT sequence_number, source, text FROM quiz_reasoning_turns "
        "WHERE attempt_id = ?1 AND sequence_number <= ?2 ORDER BY sequence_number",
        attempt["id"],
        attempt["submitted_through_turn"],
    )
    return {
        "question": {
            "id": question["id"],
            "text": str(question.get("prompt", ""))[:8_000],
            "type": question.get("kind", "short_answer"),
        },
        "trusted_answer": {
            "answer": _display_answer(question),
            "explanation": str(question.get("explanation", ""))[:3_000],
        },
        "student_answer": attempt["final_answer"],
        "authoritative_grade": "correct" if attempt["correct"] else "incorrect",
        "reasoning_turns": turns,
    }
