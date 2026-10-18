"""Private model-assisted study-performance summaries."""

from __future__ import annotations

import hashlib
import json
import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response

from app.api.dependencies import database, enforce_sqlite_api_policy
from app.api.errors import ApiError
from app.api.schemas import StudyPerformanceScoreResponse
from app.services.ai_usage_ledger import bind_usage_context, reset_usage_context
from app.services.class_access import class_access, material_access
from app.services.database import json_value
from app.services.study_performance import (
    STUDY_PERFORMANCE_PROMPT_VERSION,
    StudyPerformanceScoringService,
)

logger = logging.getLogger("screen_tutor.study_performance_api")
router = APIRouter(tags=["study-performance"])
MAX_SCORE_INPUTS_PER_CATEGORY = 20
MAX_FLASHCARD_DECKS = 30


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


def _class_for_material(material: dict) -> str:
    class_id = material.get("origin_class_id") or material.get("class_id")
    if not class_id:
        raise ApiError(
            409,
            "study_score_class_required",
            "AI study scores are available for study materials in a class.",
        )
    return str(class_id)


async def _collect_performance_summary(db, *, owner_id: str, class_id: str) -> dict:
    assessment_rows = await db.fetch(
        "WITH eligible_runs AS ("
        "SELECT r.id,r.material_id,r.score_percent,m.kind,r.completed_at,"
        "ROW_NUMBER() OVER (PARTITION BY m.kind "
        "ORDER BY r.completed_at DESC,r.id DESC) AS recent_rank "
        "FROM quiz_runs r JOIN study_materials m ON m.id=r.material_id "
        "WHERE r.owner_id=?1 AND r.status='completed' AND r.assessment_type='quiz' "
        "AND m.kind IN ('quiz','practice_exam') "
        "AND COALESCE(m.origin_class_id,m.class_id)=?2 AND r.score_percent IS NOT NULL "
        "AND (r.feedback_release_policy='after_completion' OR "
        "(r.feedback_release_policy='manual' AND EXISTS (SELECT 1 "
        "FROM study_material_feedback_releases f WHERE f.material_id=m.id))) "
        ") SELECT eligible_runs.kind,eligible_runs.score_percent,"
        "(SELECT COUNT(*) FROM quiz_run_questions q "
        "WHERE q.run_id=eligible_runs.id) AS question_count "
        "FROM eligible_runs WHERE eligible_runs.recent_rank<=?3 "
        "ORDER BY eligible_runs.completed_at DESC,eligible_runs.id DESC",
        owner_id,
        class_id,
        MAX_SCORE_INPUTS_PER_CATEGORY,
    )
    scores: dict[str, list[float]] = {"quiz": [], "exam": []}
    question_counts: dict[str, int] = {"quiz": 0, "exam": 0}
    for row in assessment_rows:
        key = "exam" if row["kind"] == "practice_exam" else "quiz"
        scores[key].append(round(float(row["score_percent"]), 2))
        question_counts[key] += int(row["question_count"] or 0)
        if len(scores[key]) > MAX_SCORE_INPUTS_PER_CATEGORY:
            scores[key].pop()
            question_counts[key] -= int(row["question_count"] or 0)

    quiz = {
        "attempts": len(scores["quiz"]),
        "question_count": question_counts["quiz"],
        "recent_scores": scores["quiz"],
    }
    exam = {
        "attempts": len(scores["exam"]),
        "question_count": question_counts["exam"],
        "recent_scores": scores["exam"],
    }

    flashcard_materials = await db.fetch(
        "SELECT id,content FROM study_materials WHERE kind='flashcards' AND status='ready' "
        "AND COALESCE(origin_class_id,class_id)=?1 "
        "ORDER BY COALESCE(generated_at,created_at) DESC,id DESC LIMIT ?2",
        class_id,
        MAX_FLASHCARD_DECKS,
    )
    flashcards = {
        "decks": 0,
        "rated_cards": 0,
        "recalled": 0,
        "unsure": 0,
        "missed": 0,
    }
    for material in flashcard_materials:
        try:
            await material_access(db, UUID(str(material["id"])), UUID(owner_id))
        except ApiError as exc:
            if exc.status_code not in {403, 404}:
                raise
            continue
        except ValueError:
            continue
        content = json_value(material.get("content"), {})
        cards = content.get("cards", []) if isinstance(content, dict) else []
        valid_ids = {
            str(card.get("id"))
            for card in cards
            if isinstance(card, dict) and isinstance(card.get("id"), str) and card["id"]
        } if isinstance(cards, list) else set()
        if not valid_ids:
            continue
        review_rows = await db.fetch(
            "SELECT card_id,last_rating FROM flashcard_review_state "
            "WHERE owner_id=?1 AND material_id=?2 AND reviewed_at IS NOT NULL "
            "AND last_rating IN ('recalled','unsure','missed')",
            owner_id,
            str(material["id"]),
        )
        found = 0
        for item in review_rows:
            if str(item["card_id"]) not in valid_ids:
                continue
            rating = str(item["last_rating"])
            if rating not in {"recalled", "unsure", "missed"}:
                continue
            flashcards[rating] += 1
            flashcards["rated_cards"] += 1
            found += 1
        if found:
            flashcards["decks"] += 1

    if not quiz["attempts"] and not exam["attempts"] and not flashcards["rated_cards"]:
        raise ApiError(
            409,
            "no_performance_data",
            "Complete a quiz or exam, or rate flashcards, before requesting a study score.",
        )
    return {
        "quiz": quiz,
        "exam": exam,
        "flashcards": flashcards,
        "scoring_rule": {
            "quiz_weight": 0.45,
            "exam_weight": 0.45,
            "flashcard_weight": 0.10,
            "flashcard_formula": "recalled=100, unsure=50, missed=0",
        },
    }


def _weights(summary: dict, scores: dict) -> tuple[float, float, float, float | None]:
    available_assessments = [
        name for name in ("quiz", "exam")
        if summary[name]["attempts"] and scores[f"{name}_score"] is not None
    ]
    has_flashcards = bool(
        summary["flashcards"]["rated_cards"] and scores["flashcard_score"] is not None
    )
    if not available_assessments:
        return 0.0, 0.0, 0.0, None
    flashcard_weight = 0.10 if has_flashcards else 0.0
    assessment_weight = (1.0 - flashcard_weight) / len(available_assessments)
    quiz_weight = assessment_weight if "quiz" in available_assessments else 0.0
    exam_weight = assessment_weight if "exam" in available_assessments else 0.0
    flashcard_weight = flashcard_weight if has_flashcards else 0.0
    weighted = (
        float(scores["quiz_score"] or 0) * quiz_weight
        + float(scores["exam_score"] or 0) * exam_weight
        + float(scores["flashcard_score"] or 0) * flashcard_weight
    )
    return quiz_weight, exam_weight, flashcard_weight, round(weighted, 1)


def _evidence_level(summary: dict) -> str:
    attempts = summary["quiz"]["attempts"] + summary["exam"]["attempts"]
    if attempts >= 10:
        return "high"
    if attempts >= 3:
        return "moderate"
    return "low"


def _response(row: dict, *, material_id: UUID) -> StudyPerformanceScoreResponse:
    evidence = json_value(row.get("evidence_summary"), {})
    return StudyPerformanceScoreResponse(
        material_id=material_id,
        quiz_score=row.get("quiz_score"),
        exam_score=row.get("exam_score"),
        flashcard_score=row.get("flashcard_score"),
        overall_score=row.get("overall_score"),
        quiz_weight=float(evidence.get("quiz_weight", 0)),
        exam_weight=float(evidence.get("exam_weight", 0)),
        flashcard_weight=float(evidence.get("flashcard_weight", 0)),
        evidence_level=str(row.get("evidence_level") or "low"),
        explanation=str(row.get("explanation") or ""),
        quiz_attempts=int(evidence.get("quiz_attempts", 0)),
        exam_attempts=int(evidence.get("exam_attempts", 0)),
        flashcard_decks=int(evidence.get("flashcard_decks", 0)),
        flashcards_rated=int(evidence.get("flashcards_rated", 0)),
        model_identifier=str(row.get("model_identifier") or "unknown"),
        prompt_version=str(row.get("prompt_version") or STUDY_PERFORMANCE_PROMPT_VERSION),
        created_at=row["updated_at"],
    )


async def _claim_score(db, *, owner_id: str, class_id: str, material_id: str, digest: str):
    score_id = str(uuid4())
    async with db.acquire() as connection:
        async with connection.transaction():
            inserted = await connection.fetchrow(
                "INSERT INTO study_performance_scores "
                "(id,owner_id,class_id,triggering_material_id,input_digest,status,prompt_version) "
                "VALUES (?1,?2,?3,?4,?5,'pending',?6) "
                "ON CONFLICT(owner_id,class_id,input_digest) DO NOTHING RETURNING id",
                score_id,
                owner_id,
                class_id,
                material_id,
                digest,
                STUDY_PERFORMANCE_PROMPT_VERSION,
            )
            if inserted:
                return score_id, None
            existing = await connection.fetchrow(
                "SELECT * FROM study_performance_scores WHERE owner_id=?1 AND class_id=?2 "
                "AND input_digest=?3",
                owner_id,
                class_id,
                digest,
            )
            if existing and existing["status"] == "ready":
                return None, existing
            claimed = await connection.fetchrow(
                "UPDATE study_performance_scores SET status='pending',error_code=NULL,"
                "triggering_material_id=?4,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE owner_id=?1 AND class_id=?2 AND input_digest=?3 AND "
                "(status='failed' OR (status='pending' AND updated_at < "
                "strftime('%Y-%m-%dT%H:%M:%fZ','now','-2 minutes'))) RETURNING id",
                owner_id,
                class_id,
                digest,
                material_id,
            )
            if claimed:
                return str(claimed["id"]), None
            raise ApiError(
                409,
                "study_score_in_progress",
                "An AI study score is already being prepared. Retry shortly.",
                retry_after_seconds=5,
            )


async def _finish_failed_score(db, score_id: str, code: str) -> None:
    await db.execute(
        "UPDATE study_performance_scores SET status='failed',error_code=?2,"
        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?1 AND status='pending'",
        score_id,
        code[:80],
    )


@router.post(
    "/api/study-materials/{material_id}/performance-score",
    response_model=StudyPerformanceScoreResponse,
)
async def create_study_performance_score(
    material_id: UUID, request: Request, response: Response
) -> StudyPerformanceScoreResponse:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    retry_after = await request.app.state.study_performance_rate_limiter.check(str(owner_id))
    if retry_after is not None:
        raise ApiError(
            429,
            "study_score_rate_limited",
            "Too many study-score requests. Try again shortly.",
            retry_after_seconds=retry_after,
        )
    db = database(request)
    material, _access = await material_access(db, material_id, owner_id)
    if material["kind"] not in {"quiz", "practice_exam", "flashcards"}:
        raise ApiError(
            409,
            "study_score_not_supported",
            "AI study scoring is available after quizzes, practice tests, and flashcard review.",
        )
    if material["status"] != "ready":
        raise ApiError(409, "material_not_ready", "This study material is not ready for scoring.")
    class_id = _class_for_material(material)
    await class_access(db, class_id, owner_id, required="read")
    summary = await _collect_performance_summary(db, owner_id=str(owner_id), class_id=class_id)
    serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(
        f"{STUDY_PERFORMANCE_PROMPT_VERSION}\n{serialized}".encode()
    ).hexdigest()
    score_id, cached = await _claim_score(
        db,
        owner_id=str(owner_id),
        class_id=class_id,
        material_id=str(material_id),
        digest=digest,
    )
    if cached is not None:
        _no_store(response)
        return _response(cached, material_id=material_id)

    service: StudyPerformanceScoringService | None = getattr(
        request.app.state, "study_performance_scoring_service", None
    )
    if service is None:
        await _finish_failed_score(db, score_id, "study_score_unavailable")
        raise ApiError(503, "study_score_unavailable", "The AI study scorer is not configured.")
    config = request.app.state.ai_provider_config.get()
    model_identifier = config.model if config is not None else "unknown"
    usage_token = bind_usage_context(
        operation_id=str(material_id),
        operation_kind="study_performance_score",
        operation_label="AI study performance score",
    )
    try:
        draft = await service.score(
            summary, request_id=getattr(request.state, "request_id", "unknown")
        )
        score_values = draft.model_dump(mode="json")
        quiz_weight, exam_weight, flashcard_weight, overall_score = _weights(summary, score_values)
        evidence_summary = {
            "quiz_attempts": summary["quiz"]["attempts"],
            "quiz_questions": summary["quiz"]["question_count"],
            "exam_attempts": summary["exam"]["attempts"],
            "exam_questions": summary["exam"]["question_count"],
            "flashcard_decks": summary["flashcards"]["decks"],
            "flashcards_rated": summary["flashcards"]["rated_cards"],
            "quiz_weight": quiz_weight,
            "exam_weight": exam_weight,
            "flashcard_weight": flashcard_weight,
        }
        await db.execute(
            "UPDATE study_performance_scores SET status='ready',quiz_score=?2,exam_score=?3,"
            "flashcard_score=?4,overall_score=?5,evidence_level=?6,explanation=?7,evidence_summary=?8,"
            "model_identifier=?9,error_code=NULL,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE id=?1 AND status='pending'",
            score_id,
            score_values["quiz_score"],
            score_values["exam_score"],
            score_values["flashcard_score"],
            overall_score,
            _evidence_level(summary),
            draft.explanation,
            json.dumps(evidence_summary, separators=(",", ":")),
            model_identifier[:200],
        )
        row = await db.fetchrow(
            "SELECT * FROM study_performance_scores WHERE id=?1 AND owner_id=?2",
            score_id,
            owner_id,
        )
        if row is None:
            raise ApiError(
                503, "study_score_unavailable", "The score could not be saved. Retry it."
            )
        _no_store(response)
        return _response(row, material_id=material_id)
    except ApiError as exc:
        await _finish_failed_score(db, score_id, exc.code)
        raise
    except Exception as exc:
        logger.warning(
            "study_performance_save_failed material_id=%s category=%s",
            material_id,
            type(exc).__name__,
        )
        await _finish_failed_score(db, score_id, "study_score_save_failed")
        raise ApiError(
            503, "study_score_unavailable", "The AI study score could not be saved. Retry it."
        ) from None
    finally:
        reset_usage_context(usage_token)


@router.delete("/api/study-materials/{material_id}/performance-score", status_code=204)
async def clear_study_performance_scores(
    material_id: UUID, request: Request
) -> Response:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db = database(request)
    material, _access = await material_access(db, material_id, owner_id)
    if material["kind"] not in {"quiz", "practice_exam", "flashcards"}:
        raise ApiError(409, "study_score_not_supported", "This material has no AI study score.")
    class_id = _class_for_material(material)
    await class_access(db, class_id, owner_id, required="read")
    await db.execute(
        "DELETE FROM study_performance_scores WHERE owner_id=?1 AND class_id=?2",
        owner_id,
        class_id,
    )
    return Response(
        status_code=204,
        headers={"Cache-Control": "private, no-store", "Pragma": "no-cache"},
    )
