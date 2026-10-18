"""Owner-private learning progress and dated mastery history APIs."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.admin import _require_admin
from app.api.dependencies import database, enforce_sqlite_api_policy
from app.api.errors import ApiError
from app.services.class_access import (
    class_access,
    material_access,
    require_resource_access,
)
from app.services.concept_graph import concept_graph
from app.services.database import json_value
from app.services.learning_scoring import learning_progress

router = APIRouter(tags=["learning-progress"])
GOALS = {"balanced", "build_foundation", "exam_prep", "review"}


class LearningPreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    chosen_goal: Literal["balanced", "build_foundation", "exam_prep", "review"]


class RecommendationStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    state: Literal["active", "completed", "dismissed"]


class LearningReviewAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    choice_id: str = Field(min_length=1, max_length=160)


class FlashcardReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: UUID
    card_id: str = Field(min_length=1, max_length=160)
    rating: Literal["recalled", "unsure", "missed", "snooze", "reset"]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


async def _visible_learning_context(
    db: Any, owner_id: UUID
) -> tuple[list[str], list[dict[str, Any]]]:
    rows = await db.fetch(
        "SELECT c.id FROM course_classes c WHERE c.owner_id=?1 OR EXISTS ("
        "SELECT 1 FROM class_memberships m WHERE m.class_id=c.id AND m.user_id=?1 "
        "AND m.status='active') ORDER BY c.id",
        owner_id,
    )
    class_ids: list[str] = []
    concepts: list[dict[str, Any]] = []
    for row in rows:
        class_id = str(row["id"])
        try:
            await class_access(db, class_id, owner_id, required="read")
        except ApiError:
            continue
        graph = await concept_graph(db, class_id=class_id, user_id=str(owner_id))
        class_ids.append(class_id)
        concepts.extend(
            {
                "id": str(item["id"]),
                "class_id": class_id,
                "canonical_name": str(item.get("canonical_name") or ""),
            }
            for item in graph.get("concepts", [])
            if isinstance(item, dict) and item.get("id")
        )
    return class_ids, concepts


async def _learning_goal(db: Any, owner_id: str) -> str:
    goal = await db.fetchval(
        "SELECT chosen_goal FROM learning_preferences WHERE owner_id=?1", owner_id
    )
    return str(goal) if goal in GOALS else "balanced"


async def _authorized_citations(
    db: Any, *, owner_id: str, class_id: str, private_snapshot: dict[str, Any]
) -> list[dict[str, Any]]:
    chunk_ids = private_snapshot.get("source_chunk_revision_ids", [])
    if not isinstance(chunk_ids, list):
        return []
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chunk_id in chunk_ids[:10]:
        if not isinstance(chunk_id, str) or chunk_id in seen:
            continue
        seen.add(chunk_id)
        source = await db.fetchrow(
            "SELECT n.id,n.class_id,n.title,n.created_by,n.publication_state,n.release_at,"
            "n.status,cr.locator FROM source_chunk_revisions cr "
            "JOIN source_revisions r ON r.id=cr.revision_id "
            "JOIN course_notes n ON n.id=r.note_id WHERE cr.id=?1",
            chunk_id,
        )
        if source is None or str(source["class_id"]) != class_id or source["status"] != "ready":
            continue
        try:
            await require_resource_access(
                db,
                class_id,
                owner_id,
                capability="read",
                publication_state=source["publication_state"],
                release_at=source["release_at"],
                created_by=source["created_by"],
            )
        except ApiError:
            continue
        citations.append(
            {
                "material_id": str(source["id"]),
                "title": str(source["title"]),
                "locator": json_value(source["locator"], {}),
            }
        )
    return citations


def _learning_summary(progress: dict[str, Any], concept_id: str) -> dict[str, Any] | None:
    return next((item for item in progress.get("concepts", []) if item["id"] == concept_id), None)


async def _eligible_resource(
    db: Any, *, owner_id: str, class_id: str, concept_id: str
) -> dict[str, str] | None:
    rows = await db.fetch(
        "SELECT DISTINCT n.id,n.title,n.created_by,n.publication_state,n.release_at "
        "FROM class_concept_source_refs s JOIN source_chunk_revisions c "
        "ON c.id=s.chunk_revision_id "
        "JOIN source_revisions r ON r.id=c.revision_id JOIN course_notes n ON n.id=r.note_id "
        "WHERE s.class_id=?1 AND s.concept_id=?2 AND n.class_id=?1 AND n.status='ready' "
        "ORDER BY n.updated_at DESC,n.id LIMIT 12",
        class_id,
        concept_id,
    )
    for row in rows:
        try:
            await require_resource_access(
                db,
                class_id,
                owner_id,
                capability="read",
                publication_state=row["publication_state"],
                release_at=row["release_at"],
                created_by=row["created_by"],
            )
        except ApiError:
            continue
        return {"type": "source", "id": str(row["id"]), "title": str(row["title"])[:240]}
    return None


async def _candidate_recommendation(
    db: Any,
    *,
    owner_id: str,
    concept: dict[str, Any],
    progress_concept: dict[str, Any],
    goal: str,
) -> dict[str, Any]:
    class_id = str(concept["class_id"])
    concept_id = str(concept["id"])
    evidence = await db.fetch(
        "SELECT id,correctness FROM mastery_evidence WHERE owner_id=?1 AND class_id=?2 "
        "AND concept_id=?3 ORDER BY created_at DESC,id DESC LIMIT 5",
        owner_id,
        class_id,
        concept_id,
    )
    evidence_ids = [str(row["id"]) for row in evidence]
    recent_errors = sum(float(row["correctness"]) < 0.5 for row in evidence)
    cursor = evidence_ids[0] if evidence_ids else "none"
    review_state = await db.fetchrow(
        "SELECT due_at,last_session_id,last_family_id FROM learning_review_state "
        "WHERE owner_id=?1 AND class_id=?2 AND concept_id=?3",
        owner_id,
        class_id,
        concept_id,
    )
    review_due = bool(progress_concept.get("review_due"))
    if review_state is not None:
        try:
            due_at = datetime.fromisoformat(str(review_state["due_at"]).replace("Z", "+00:00"))
            review_due = due_at <= datetime.now(UTC)
        except (TypeError, ValueError):
            review_due = True
        if review_state.get("last_session_id"):
            cursor = f"review:{review_state['last_session_id']}"
    related = await db.fetch(
        "SELECT e.from_concept_id,e.to_concept_id,e.necessity,c.id,c.canonical_name "
        "FROM class_concept_edges e JOIN class_concepts c ON c.class_id=e.class_id "
        "AND c.id=CASE WHEN e.to_concept_id=?2 THEN e.from_concept_id ELSE e.to_concept_id END "
        "WHERE e.class_id=?1 AND e.status='validated' AND e.type='prerequisite_for' "
        "AND (e.from_concept_id=?2 OR e.to_concept_id=?2) AND c.status='validated' "
        "ORDER BY e.necessity DESC,c.canonical_name COLLATE NOCASE LIMIT 8",
        class_id,
        concept_id,
    )
    prerequisite = None
    if recent_errors:
        for row in related:
            if str(row["to_concept_id"]) != concept_id:
                continue
            observations = int(
                await db.fetchval(
                    "SELECT count(*) FROM mastery_evidence WHERE owner_id=?1 AND class_id=?2 "
                    "AND concept_id=?3",
                    owner_id,
                    class_id,
                    row["id"],
                )
                or 0
            )
            if observations == 0:
                prerequisite = row
                break
    if prerequisite is not None:
        rec_type = "diagnostic"
        label = f"Check your foundation: {prerequisite['canonical_name']}"
        reason = (
            f"You had a recent challenge with {concept['canonical_name']}. This short check of "
            f"{prerequisite['canonical_name']} can show whether that foundation needs review; "
            "the downstream result alone does not establish a prerequisite gap."
        )
    elif review_due or (goal == "review" and review_state is None):
        rec_type = "review"
        label = f"Review {concept['canonical_name']}"
        if review_state is not None:
            reason = (
                "A different source-linked check is due from your review schedule. This reminder "
                "does not lower your mastery estimate."
            )
        elif progress_concept.get("review_due"):
            reason = (
                "Your evidence is old enough for a reminder to revisit this topic. Time alone "
                "does not lower your mastery estimate."
            )
        else:
            reason = (
                "Your selected goal is to revisit material. Try one short question from a "
                "different validated question family."
            )
    else:
        rec_type = "practice"
        label = f"Practice {concept['canonical_name']}"
        if goal == "build_foundation":
            reason = (
                "Your goal is to strengthen foundations; start with a focused check "
                "of this concept."
            )
        elif goal == "exam_prep":
            reason = (
                "Your goal is exam preparation; practice the most recently challenging "
                "part of this concept."
            )
        elif recent_errors:
            reason = (
                f"Your recent evidence includes {recent_errors} challenging response(s); "
                "a fresh item can help you check your next step."
            )
        elif progress_concept["status"] == "unassessed":
            reason = (
                "There is not enough evidence yet. A short assessment will establish "
                "a starting point."
            )
        else:
            reason = (
                "A different validated question family can add independent evidence "
                "for this estimate."
            )
    resource = await _eligible_resource(
        db, owner_id=owner_id, class_id=class_id, concept_id=concept_id
    )
    return {
        "class_id": class_id,
        "concept_id": concept_id,
        "type": rec_type,
        "goal": goal,
        "label": label[:240],
        "reason": reason[:1000],
        "resource": resource,
        "evidence_ids": evidence_ids,
        "evidence_cursor": cursor,
        "sort": (
            0 if rec_type == "review" else 1 if progress_concept["status"] == "unassessed" else 2,
            -recent_errors,
            float(progress_concept["estimate"].get("mean") or 0),
            concept_id,
        ),
    }


async def _record_recommendation_event(
    db: Any,
    *,
    recommendation_id: str,
    owner_id: str,
    event_type: str,
    related_attempt_id: str | None = None,
) -> None:
    event_day = datetime.now(UTC).date().isoformat()
    await db.execute(
        "INSERT OR IGNORE INTO learning_recommendation_events "
        "(id,recommendation_id,owner_id,event_type,related_attempt_id,event_date) "
        "VALUES (?1,?2,?3,?4,?5,?6)",
        str(uuid4()),
        recommendation_id,
        owner_id,
        event_type,
        related_attempt_id,
        event_day,
    )


async def _learning_review_payload(db: Any, session: dict[str, Any]) -> dict[str, Any]:
    public = json_value(session["public_snapshot"], {})
    private = json_value(session["private_snapshot"], {})
    class_id = str(session["class_id"])
    owner_id = str(session["owner_id"])
    await class_access(db, class_id, owner_id, required="read")
    citations = await _authorized_citations(
        db,
        owner_id=owner_id,
        class_id=class_id,
        private_snapshot=private,
    )
    if not citations:
        raise ApiError(404, "not_found", "The source for this review is no longer available.")

    result: dict[str, Any] = {
        "id": str(session["id"]),
        "status": str(session["status"]),
        "concept_id": str(session["concept_id"]),
        "prompt": str(public.get("prompt") or ""),
        "kind": str(public.get("kind") or "multiple_choice"),
        "choices": public.get("choices") if isinstance(public.get("choices"), list) else [],
        "citations": citations if session["status"] == "completed" else [],
        "mastery_updated": False,
    }
    if session["status"] == "completed":
        result.update(
            {
                "correct": bool(session.get("correctness")),
                "selected_choice_id": session.get("submitted_choice_id"),
                "correct_answer": str(private.get("correct_answer") or ""),
                "explanation": str(private.get("explanation") or ""),
                "completed_at": session.get("completed_at"),
                "next_due_at": await db.fetchval(
                    "SELECT due_at FROM learning_review_state WHERE owner_id=?1 "
                    "AND class_id=?2 AND concept_id=?3",
                    owner_id,
                    class_id,
                    str(session["concept_id"]),
                ),
            }
        )
    return result


@router.post("/api/learning/recommendations/{recommendation_id}/review")
async def start_learning_review(
    recommendation_id: UUID, request: Request, response: Response
) -> dict[str, Any]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    _no_store(response)
    db = database(request)
    owner_key = str(owner_id)
    recommendation = await db.fetchrow(
        "SELECT * FROM learning_recommendations WHERE id=?1 AND owner_id=?2",
        recommendation_id,
        owner_key,
    )
    if recommendation is None or recommendation["recommendation_type"] != "review":
        raise ApiError(404, "not_found", "The requested review suggestion was not found.")
    if recommendation["state"] in {"completed", "dismissed"}:
        raise ApiError(409, "recommendation_terminal", "This review suggestion is already closed.")
    class_id = str(recommendation["class_id"])
    concept_id = str(recommendation["concept_id"])
    await class_access(db, class_id, owner_key, required="read")
    scheduled = await db.fetchrow(
        "SELECT due_at FROM learning_review_state "
        "WHERE owner_id=?1 AND class_id=?2 AND concept_id=?3",
        owner_key,
        class_id,
        concept_id,
    )
    if scheduled is not None:
        try:
            not_due = datetime.fromisoformat(
                str(scheduled["due_at"]).replace("Z", "+00:00")
            ) > datetime.now(UTC)
        except (TypeError, ValueError):
            not_due = False
        if not_due:
            raise ApiError(
                409, "review_not_due", "This fresh review is scheduled for a later date."
            )

    # Clear a stale in-progress row in its own committed statement. If it were
    # changed inside the later creation transaction, a missing replacement item
    # could roll the abandonment back and trap the learner in the same session.
    active = await db.fetchrow(
        "SELECT * FROM learning_review_sessions WHERE owner_id=?1 AND class_id=?2 "
        "AND concept_id=?3 AND status='in_progress'",
        owner_key,
        class_id,
        concept_id,
    )
    if active is not None:
        try:
            return await _learning_review_payload(db, active)
        except ApiError as exc:
            if exc.status_code != 404:
                raise
            await db.execute(
                "UPDATE learning_review_sessions SET status='abandoned' "
                "WHERE id=?1 AND status='in_progress'",
                active["id"],
            )

    async with db.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                "SELECT * FROM learning_recommendations WHERE id=?1 AND owner_id=?2",
                recommendation_id,
                owner_key,
            )
            if current is None or current["state"] in {"completed", "dismissed"}:
                raise ApiError(
                    409, "recommendation_terminal", "This review suggestion is already closed."
                )
            active = await conn.fetchrow(
                "SELECT * FROM learning_review_sessions WHERE owner_id=?1 AND class_id=?2 "
                "AND concept_id=?3 AND status='in_progress'",
                owner_key,
                class_id,
                concept_id,
            )
            if active is not None:
                return await _learning_review_payload(conn, active)

            current_schedule = await conn.fetchrow(
                "SELECT due_at,last_family_id FROM learning_review_state "
                "WHERE owner_id=?1 AND class_id=?2 AND concept_id=?3",
                owner_key,
                class_id,
                concept_id,
            )
            if current_schedule is not None:
                try:
                    still_not_due = datetime.fromisoformat(
                        str(current_schedule["due_at"]).replace("Z", "+00:00")
                    ) > datetime.now(UTC)
                except (TypeError, ValueError):
                    still_not_due = False
                if still_not_due:
                    raise ApiError(
                        409, "review_not_due", "This fresh review is scheduled for a later date."
                    )
            last_family_id = (
                str(current_schedule.get("last_family_id") or "") if current_schedule else ""
            )
            candidates = await conn.fetch(
                "SELECT DISTINCT i.* FROM class_question_bank_items i "
                "JOIN class_question_bank_concepts c ON c.item_id=i.id AND c.class_id=i.class_id "
                "WHERE i.class_id=?1 AND i.state='validated' "
                "AND i.kind IN ('multiple_choice','true_false') "
                "AND c.concept_id=?2 AND (?3='' OR i.family_id<>?3) "
                "ORDER BY i.exposure_count ASC,i.created_at ASC,i.id LIMIT 32",
                class_id,
                concept_id,
                last_family_id,
            )
            selected: dict[str, Any] | None = None
            for candidate in candidates:
                source_rows = await conn.fetch(
                    "SELECT chunk_revision_id FROM class_question_bank_sources "
                    "WHERE item_id=?1 AND class_id=?2 ORDER BY source_order,chunk_revision_id",
                    candidate["id"],
                    class_id,
                )
                source_ids = [str(row["chunk_revision_id"]) for row in source_rows]
                private_source = {"source_chunk_revision_ids": source_ids}
                citations = await _authorized_citations(
                    conn,
                    owner_id=owner_key,
                    class_id=class_id,
                    private_snapshot=private_source,
                )
                if not citations:
                    continue
                choices = json_value(candidate["choices_json"], [])
                grading = json_value(candidate["grading_json"], {})
                if not isinstance(choices, list) or not isinstance(grading, dict):
                    continue
                original_answer = str(grading.get("answer") or "")
                if not original_answer:
                    continue
                new_choices: list[dict[str, str]] = []
                correct_choice: dict[str, str] | None = None
                for choice in choices:
                    if not isinstance(choice, dict):
                        continue
                    original_id = str(choice.get("id") or "")
                    text = str(choice.get("text") or "").strip()
                    if not original_id or not text:
                        continue
                    normalized_id = secrets.token_urlsafe(12)
                    normalized = {"id": normalized_id, "text": text[:1200]}
                    new_choices.append(normalized)
                    if original_id == original_answer or (
                        candidate["kind"] == "true_false"
                        and (
                            original_id.casefold() == original_answer.casefold()
                            or text.casefold() == original_answer.casefold()
                        )
                    ):
                        correct_choice = normalized
                if correct_choice is None or len(new_choices) < 2:
                    continue
                secrets.SystemRandom().shuffle(new_choices)
                selected = {
                    "bank_item": candidate,
                    "source_ids": source_ids,
                    "choices": new_choices,
                    "correct_choice": correct_choice,
                    "grading": grading,
                }
                break
            if selected is None:
                raise ApiError(
                    409,
                    "review_item_unavailable",
                    "There is no unused, source-checked question for this topic yet. "
                    "You can review the linked course source or try again after the question "
                    "bank is expanded.",
                )

            session_id = str(uuid4())
            public = {
                "prompt": str(selected["bank_item"]["prompt"])[:5000],
                "kind": str(selected["bank_item"]["kind"]),
                "choices": selected["choices"],
            }
            private = {
                "source_chunk_revision_ids": selected["source_ids"],
                "correct_choice_id": selected["correct_choice"]["id"],
                "correct_answer": selected["correct_choice"]["text"],
                "explanation": str(selected["grading"].get("explanation") or "")[:5000],
            }
            await conn.execute(
                "INSERT INTO learning_review_sessions "
                "(id,recommendation_id,owner_id,class_id,concept_id,bank_item_id,family_id,"
                "public_snapshot,private_snapshot) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)",
                session_id,
                recommendation_id,
                owner_key,
                class_id,
                concept_id,
                selected["bank_item"]["id"],
                selected["bank_item"]["family_id"],
                json.dumps(public, ensure_ascii=False, separators=(",", ":")),
                json.dumps(private, ensure_ascii=False, separators=(",", ":")),
            )
            if current["state"] in {"suggested", "pending"}:
                await conn.execute(
                    "UPDATE learning_recommendations SET state='active',updated_at="
                    "strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?1 AND owner_id=?2",
                    recommendation_id,
                    owner_key,
                )
                await _record_recommendation_event(
                    conn,
                    recommendation_id=str(recommendation_id),
                    owner_id=owner_key,
                    event_type="engaged",
                )
            session = await conn.fetchrow(
                "SELECT * FROM learning_review_sessions WHERE id=?1", session_id
            )
    return await _learning_review_payload(db, session)


@router.post("/api/learning/reviews/{session_id}/answer")
async def submit_learning_review_answer(
    session_id: UUID,
    payload: LearningReviewAnswer,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    _no_store(response)
    db = database(request)
    owner_key = str(owner_id)
    source_revoked = False
    saved: dict[str, Any] | None = None
    async with db.acquire() as conn:
        async with conn.transaction():
            session = await conn.fetchrow(
                "SELECT * FROM learning_review_sessions WHERE id=?1 AND owner_id=?2",
                session_id,
                owner_key,
            )
            if session is None:
                raise ApiError(404, "not_found", "The requested review was not found.")
            if session["status"] == "abandoned":
                raise ApiError(
                    404, "not_found", "The source for this review is no longer available."
                )
            public = json_value(session["public_snapshot"], {})
            private = json_value(session["private_snapshot"], {})
            await class_access(conn, str(session["class_id"]), owner_key, required="read")
            citations = await _authorized_citations(
                conn,
                owner_id=owner_key,
                class_id=str(session["class_id"]),
                private_snapshot=private,
            )
            if not citations:
                if session["status"] == "in_progress":
                    await conn.execute(
                        "UPDATE learning_review_sessions SET status='abandoned' "
                        "WHERE id=?1 AND status='in_progress'",
                        session_id,
                    )
                source_revoked = True
            else:
                choice_ids = {
                    str(choice.get("id"))
                    for choice in public.get("choices", [])
                    if isinstance(choice, dict) and choice.get("id")
                }
                if payload.choice_id not in choice_ids:
                    raise ApiError(
                        422, "invalid_review_choice", "Choose one of the listed answers."
                    )
                if session["status"] == "completed":
                    if session["submitted_choice_id"] != payload.choice_id:
                        raise ApiError(
                            409, "review_already_submitted", "This review answer was already saved."
                        )
                    saved = session

                if saved is None:
                    correct = payload.choice_id == private.get("correct_choice_id")
                    now = datetime.now(UTC)
                    previous = await conn.fetchrow(
                        "SELECT interval_days,completed_count FROM learning_review_state "
                        "WHERE owner_id=?1 AND class_id=?2 AND concept_id=?3",
                        owner_key,
                        session["class_id"],
                        session["concept_id"],
                    )
                    if correct:
                        previous_interval = int(previous["interval_days"]) if previous else 1
                        interval_days = (
                            7
                            if not previous or int(previous["completed_count"]) == 0
                            else min(30, max(7, previous_interval * 2))
                        )
                    else:
                        interval_days = 1
                    due_at = (
                        (now + timedelta(days=interval_days))
                        .isoformat(timespec="milliseconds")
                        .replace("+00:00", "Z")
                    )
                    completed_at = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
                    await conn.execute(
                        "UPDATE learning_review_sessions SET status='completed', "
                        "submitted_choice_id=?2,"
                        "correctness=?3,completed_at=?4 WHERE id=?1 AND status='in_progress'",
                        session_id,
                        payload.choice_id,
                        int(correct),
                        completed_at,
                    )
                    await conn.execute(
                        "INSERT INTO learning_review_state "
                        "(owner_id,class_id,concept_id,due_at,interval_days,completed_count,last_session_id,"
                        "last_family_id,last_correct) VALUES (?1,?2,?3,?4,?5,1,?6,?7,?8) "
                        "ON CONFLICT(owner_id,class_id,concept_id) DO UPDATE SET "
                        "due_at=excluded.due_at,"
                        "interval_days=excluded.interval_days, "
                        "completed_count=learning_review_state.completed_count+1,"
                        "last_session_id=excluded.last_session_id,last_family_id=excluded.last_family_id,"
                        "last_correct=excluded.last_correct,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
                        owner_key,
                        session["class_id"],
                        session["concept_id"],
                        due_at,
                        interval_days,
                        session_id,
                        session["family_id"],
                        int(correct),
                    )
                    await conn.execute(
                        "UPDATE learning_recommendations SET state='completed',updated_at="
                        "strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?1 AND owner_id=?2 "
                        "AND state IN ('active','suggested','pending')",
                        session["recommendation_id"],
                        owner_key,
                    )
                    await _record_recommendation_event(
                        conn,
                        recommendation_id=str(session["recommendation_id"]),
                        owner_id=owner_key,
                        event_type="completed",
                    )
                    saved = await conn.fetchrow(
                        "SELECT * FROM learning_review_sessions WHERE id=?1", session_id
                    )
    if source_revoked:
        raise ApiError(404, "not_found", "The source for this review is no longer available.")
    if saved is None:
        raise ApiError(500, "review_not_saved", "The review answer could not be saved.")
    return await _learning_review_payload(db, saved)


def _flashcard_ids(material: dict[str, Any]) -> set[str]:
    content = json_value(material.get("content"), {})
    cards = content.get("cards", []) if isinstance(content, dict) else []
    return (
        {
            str(card["id"])
            for card in cards
            if isinstance(card, dict) and isinstance(card.get("id"), str) and card["id"]
        }
        if isinstance(cards, list)
        else set()
    )


async def _owned_flashcard_material(db: Any, material_id: UUID, owner_id: UUID) -> dict[str, Any]:
    material, _ = await material_access(db, material_id, owner_id)
    if material["kind"] != "flashcards":
        raise ApiError(404, "not_found", "The requested flashcard set was not found.")
    if material["status"] != "ready":
        raise ApiError(
            409, "material_not_ready", "Wait for this flashcard set to finish generating."
        )
    return material


@router.get("/api/study-materials/{material_id}/flashcard-review")
async def get_flashcard_review_state(
    material_id: UUID, request: Request, response: Response
) -> dict[str, Any]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    _no_store(response)
    db = database(request)
    material = await _owned_flashcard_material(db, material_id, owner_id)
    valid_ids = _flashcard_ids(material)
    rows = await db.fetch(
        "SELECT card_id,last_rating,next_due_at,reviewed_at,review_count,recalled_streak "
        "FROM flashcard_review_state WHERE owner_id=?1 AND material_id=?2 ORDER BY card_id",
        owner_id,
        material_id,
    )
    stale_ids = [str(row["card_id"]) for row in rows if str(row["card_id"]) not in valid_ids]
    if stale_ids:
        async with db.acquire() as conn:
            async with conn.transaction():
                for card_id in stale_ids:
                    await conn.execute(
                        "DELETE FROM flashcard_review_state "
                        "WHERE owner_id=?1 AND material_id=?2 AND card_id=?3",
                        owner_id,
                        material_id,
                        card_id,
                    )
    items = [
        {
            "card_id": str(row["card_id"]),
            "rating": row["last_rating"],
            "next_due_at": row["next_due_at"],
            "reviewed_at": row["reviewed_at"],
            "review_count": int(row["review_count"]),
            "recalled_streak": int(row["recalled_streak"]),
        }
        for row in rows
        if str(row["card_id"]) in valid_ids
    ]
    now = datetime.now(UTC)
    due_count = sum(
        1
        for item in items
        if datetime.fromisoformat(str(item["next_due_at"]).replace("Z", "+00:00")) <= now
    )
    return {"items": items, "due_count": due_count, "mastery_updated": False}


@router.put("/api/study-materials/{material_id}/flashcard-review")
async def update_flashcard_review_state(
    material_id: UUID,
    payload: FlashcardReviewUpdate,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    _no_store(response)
    db = database(request)
    material = await _owned_flashcard_material(db, material_id, owner_id)
    if payload.card_id not in _flashcard_ids(material):
        raise ApiError(404, "not_found", "The requested flashcard was not found.")
    owner_key = str(owner_id)
    material_key = str(material_id)
    async with db.acquire() as conn:
        async with conn.transaction():
            previous = await conn.fetchrow(
                "SELECT * FROM flashcard_review_state "
                "WHERE owner_id=?1 AND material_id=?2 AND card_id=?3",
                owner_key,
                material_key,
                payload.card_id,
            )
            if previous and previous.get("last_operation_id") == str(payload.operation_id):
                return {
                    "card_id": payload.card_id,
                    "rating": previous["last_rating"],
                    "next_due_at": previous["next_due_at"],
                    "reviewed_at": previous["reviewed_at"],
                    "review_count": int(previous["review_count"]),
                    "recalled_streak": int(previous["recalled_streak"]),
                    "mastery_updated": False,
                }
            now = datetime.now(UTC)
            reviewed_at = previous.get("reviewed_at") if previous else None
            review_count = int(previous["review_count"]) if previous else 0
            recalled_streak = int(previous["recalled_streak"]) if previous else 0
            rating = previous.get("last_rating") if previous else None
            if payload.rating == "reset":
                due_at = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
                rating = None
                reviewed_at = None
                review_count = 0
                recalled_streak = 0
            elif payload.rating == "snooze":
                due_at = (
                    (now + timedelta(days=1))
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z")
                )
            else:
                reviewed_at = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
                review_count += 1
                rating = payload.rating
                if payload.rating == "missed":
                    interval_days = 1
                    recalled_streak = 0
                elif payload.rating == "unsure":
                    interval_days = 3
                    recalled_streak = 0
                else:
                    recalled_streak = min(10, recalled_streak + 1)
                    interval_days = min(30, 7 * (2 ** (recalled_streak - 1)))
                due_at = (
                    (now + timedelta(days=interval_days))
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z")
                )
            await conn.execute(
                "INSERT INTO flashcard_review_state "
                "(owner_id,material_id,card_id,last_rating,next_due_at,reviewed_at,review_count,"
                "recalled_streak,last_operation_id) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9) "
                "ON CONFLICT(owner_id,material_id,card_id) DO UPDATE SET "
                "last_rating=excluded.last_rating,next_due_at=excluded.next_due_at,"
                "reviewed_at=excluded.reviewed_at,review_count=excluded.review_count,"
                "recalled_streak=excluded.recalled_streak,last_operation_id=excluded.last_operation_id,"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
                owner_key,
                material_key,
                payload.card_id,
                rating,
                due_at,
                reviewed_at,
                review_count,
                recalled_streak,
                str(payload.operation_id),
            )
    return {
        "card_id": payload.card_id,
        "rating": rating,
        "next_due_at": due_at,
        "reviewed_at": reviewed_at,
        "review_count": review_count,
        "recalled_streak": recalled_streak,
        "mastery_updated": False,
    }


@router.delete("/api/study-materials/{material_id}/flashcard-review", status_code=204)
async def clear_flashcard_review_state(
    material_id: UUID, request: Request, response: Response
) -> Response:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    _no_store(response)
    db = database(request)
    await _owned_flashcard_material(db, material_id, owner_id)
    await db.execute(
        "DELETE FROM flashcard_review_state WHERE owner_id=?1 AND material_id=?2",
        owner_id,
        material_id,
    )
    return Response(status_code=204, headers={"Cache-Control": "private, no-store"})


@router.get("/api/learning/progress")
async def get_learning_progress(
    request: Request,
    response: Response,
    window_days: int = Query(default=30, ge=7, le=90),
) -> dict[str, Any]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    response.headers["Cache-Control"] = "private, no-store"
    db = database(request)
    class_ids, concepts = await _visible_learning_context(db, owner_id)
    return await learning_progress(
        db,
        owner_id=str(owner_id),
        visible_class_ids=class_ids,
        visible_concepts=concepts,
        window_days=window_days,
    )


@router.get("/api/learning/progress/history")
async def get_learning_progress_history(
    request: Request,
    response: Response,
    days: int = Query(default=90, ge=7, le=365),
    concept_id: UUID | None = None,
) -> dict[str, Any]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    response.headers["Cache-Control"] = "private, no-store"
    db = database(request)
    class_ids, concepts = await _visible_learning_context(db, owner_id)
    visible_by_id = {str(item["id"]): item for item in concepts}
    if concept_id is not None and str(concept_id) not in visible_by_id:
        raise ApiError(404, "not_found", "The requested learning history was not found.")
    visible_ids = [str(concept_id)] if concept_id is not None else sorted(visible_by_id)
    if not class_ids or not visible_ids:
        return {
            "as_of": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "days": days,
            "concept_id": str(concept_id) if concept_id else None,
            "snapshots": [],
        }
    concept_slots = ",".join(f"?{index + 3}" for index in range(len(visible_ids)))
    class_slots = ",".join(f"?{index + 3 + len(visible_ids)}" for index in range(len(class_ids)))
    cutoff = (
        (datetime.now(UTC) - timedelta(days=days))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    rows = await db.fetch(
        "SELECT s.* FROM mastery_snapshots s WHERE s.owner_id=?1 AND s.as_of>=?2 "
        f"AND s.concept_id IN ({concept_slots}) AND s.class_id IN ({class_slots}) "
        "ORDER BY s.as_of,s.id LIMIT 2000",
        str(owner_id),
        cutoff,
        *visible_ids,
        *class_ids,
    )
    snapshots = [
        {
            "concept_id": str(row["concept_id"]),
            "as_of": row["as_of"],
            "mean": row["mean"],
            "lower_90": row["lower_90"],
            "upper_90": row["upper_90"],
            "evidence_count": row["evidence_count"],
            "level": row["level"],
            "current_level": row["current_level"],
            "status": "confirmed" if row["current_level"] is not None else "provisional",
        }
        for row in rows
    ]
    return {
        "as_of": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "days": days,
        "concept_id": str(concept_id) if concept_id else None,
        "snapshots": snapshots,
    }


@router.get("/api/learning/preferences")
async def get_learning_preferences(request: Request, response: Response) -> dict[str, str]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    _no_store(response)
    return {"chosen_goal": await _learning_goal(database(request), str(owner_id))}


@router.put("/api/learning/preferences")
async def update_learning_preferences(
    payload: LearningPreferenceUpdate, request: Request, response: Response
) -> dict[str, str]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    _no_store(response)
    await database(request).execute(
        "INSERT INTO learning_preferences(owner_id,chosen_goal) VALUES (?1,?2) "
        "ON CONFLICT(owner_id) DO UPDATE SET chosen_goal=excluded.chosen_goal,"
        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
        owner_id,
        payload.chosen_goal,
    )
    return {"chosen_goal": payload.chosen_goal}


@router.get("/api/learning/concepts/{concept_id}")
async def get_learning_concept_detail(
    concept_id: UUID, request: Request, response: Response
) -> dict[str, Any]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    _no_store(response)
    db = database(request)
    class_ids, concepts = await _visible_learning_context(db, owner_id)
    catalog = next((item for item in concepts if item["id"] == str(concept_id)), None)
    if catalog is None:
        raise ApiError(404, "not_found", "The requested learning concept was not found.")
    class_id = str(catalog["class_id"])
    graph_row = await db.fetchrow(
        "SELECT id,canonical_name,description FROM class_concepts "
        "WHERE id=?1 AND class_id=?2 AND status='validated'",
        concept_id,
        class_id,
    )
    if graph_row is None:
        raise ApiError(404, "not_found", "The requested learning concept was not found.")
    progress = await learning_progress(
        db,
        owner_id=str(owner_id),
        visible_class_ids=class_ids,
        visible_concepts=concepts,
        window_days=30,
    )
    summary = _learning_summary(progress, str(concept_id))
    if summary is None:
        raise ApiError(404, "not_found", "The requested learning concept was not found.")
    evidence_rows = await db.fetch(
        "SELECT e.*,q.public_snapshot,q.private_snapshot FROM mastery_evidence e "
        "JOIN quiz_run_questions q ON q.run_id=e.run_id AND q.id=e.question_id "
        "WHERE e.owner_id=?1 AND e.class_id=?2 AND e.concept_id=?3 "
        "ORDER BY e.created_at DESC,e.id DESC LIMIT 12",
        owner_id,
        class_id,
        concept_id,
    )
    recent_evidence: list[dict[str, Any]] = []
    for item in evidence_rows:
        public = json_value(item["public_snapshot"], {})
        private = json_value(item["private_snapshot"], {})
        reasoning_result = str(item["reasoning_result"])
        if reasoning_result in {"sufficient", "rubric_passed"}:
            reasoning_view = "accepted"
        elif reasoning_result in {"insufficient", "rubric_failed"}:
            reasoning_view = "rejected"
        else:
            reasoning_view = None
        recent_evidence.append(
            {
                "id": str(item["id"]),
                "assessed_at": item["created_at"],
                "level": int(item["level"]),
                "correctness": "correct" if float(item["correctness"]) >= 0.5 else "incorrect",
                "reasoning_result": reasoning_view,
                "grading_policy": str(item["grading_policy"]),
                "weight": float(item["quality_weight"]),
                "question_label": str(public.get("prompt") or "Assessment response")[:240],
                "source_citations": await _authorized_citations(
                    db,
                    owner_id=str(owner_id),
                    class_id=class_id,
                    private_snapshot=private,
                ),
            }
        )
    related_rows = await db.fetch(
        "SELECT e.id,e.type,e.necessity,e.from_concept_id,e.to_concept_id,c.id AS related_id,"
        "c.canonical_name FROM class_concept_edges e JOIN class_concepts c "
        "ON c.class_id=e.class_id AND c.id=CASE WHEN e.from_concept_id=?2 "
        "THEN e.to_concept_id ELSE e.from_concept_id END "
        "WHERE e.class_id=?1 AND e.status='validated' AND c.status='validated' "
        "AND (e.from_concept_id=?2 OR e.to_concept_id=?2) "
        "ORDER BY e.type,c.canonical_name COLLATE NOCASE LIMIT 24",
        class_id,
        concept_id,
    )
    await class_access(db, class_id, owner_id, required="read")
    route = f"#/classes/{class_id}/study"
    return {
        "concept": {
            "id": str(concept_id),
            "label": str(graph_row["canonical_name"]),
            "description": str(graph_row["description"] or ""),
            "status": summary["status"],
            "current_level": summary["current_level"],
            "latest_demonstrated_level": summary["latest_demonstrated_level"],
            "estimate": summary["estimate"],
            "evidence_sufficiency": summary["evidence_sufficiency"],
            "historical_peak": summary["historical_peak"],
            "recent_growth": summary["recent_growth"],
            "last_assessed_at": summary["last_assessed_at"],
            "review_due": summary["review_due"],
        },
        "recent_evidence": recent_evidence,
        "related": [
            {
                "id": str(row["related_id"]),
                "label": str(row["canonical_name"]),
                "type": str(row["type"]),
                "necessity": str(row["necessity"]),
                "direction": (
                    "outgoing" if str(row["from_concept_id"]) == str(concept_id) else "incoming"
                ),
            }
            for row in related_rows
        ],
        "available_practice": [
            {"type": "quiz", "label": "Generate a quiz from this class", "href": route},
            {"type": "ladder", "label": "Start a Mastery Ladder", "href": route},
        ],
    }


@router.get("/api/learning/recommendations")
async def get_learning_recommendations(request: Request, response: Response) -> dict[str, Any]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    _no_store(response)
    db = database(request)
    owner_key = str(owner_id)
    goal = await _learning_goal(db, owner_key)
    class_ids, concepts = await _visible_learning_context(db, owner_id)
    if class_ids and concepts:
        progress = await learning_progress(
            db,
            owner_id=owner_key,
            visible_class_ids=class_ids,
            visible_concepts=concepts,
            window_days=30,
        )
        active_assessment = await db.fetchval(
            "SELECT 1 FROM quiz_runs r JOIN study_materials m ON m.id=r.material_id "
            "WHERE r.owner_id=?1 AND r.status='in_progress' AND "
            "NOT EXISTS (SELECT 1 FROM mastery_ladder_sessions l WHERE l.run_id=r.id "
            "AND l.status='unavailable') AND "
            "COALESCE(m.class_id,m.origin_class_id) IN ("
            + ",".join(f"?{i + 2}" for i in range(len(class_ids)))
            + ") LIMIT 1",
            owner_key,
            *class_ids,
        )
        if not active_assessment:
            candidates = []
            for concept in concepts:
                summary = _learning_summary(progress, str(concept["id"]))
                if summary is not None:
                    candidates.append(
                        await _candidate_recommendation(
                            db,
                            owner_id=owner_key,
                            concept=concept,
                            progress_concept=summary,
                            goal=goal,
                        )
                    )
            for candidate in sorted(candidates, key=lambda item: item["sort"])[:5]:
                resource = candidate["resource"]
                await db.execute(
                    "INSERT OR IGNORE INTO learning_recommendations "
                    "(id,owner_id,class_id,concept_id,recommendation_type,chosen_goal,label,reason,"
                    "resource_type,resource_id,resource_title,supporting_evidence_ids,"
                    "evidence_cursor) "
                    "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13)",
                    str(uuid4()),
                    owner_key,
                    candidate["class_id"],
                    candidate["concept_id"],
                    candidate["type"],
                    goal,
                    candidate["label"],
                    candidate["reason"],
                    resource["type"] if resource else None,
                    resource["id"] if resource else None,
                    resource["title"] if resource else None,
                    json.dumps(candidate["evidence_ids"], separators=(",", ":")),
                    candidate["evidence_cursor"],
                )
    if not class_ids:
        return {"chosen_goal": goal, "items": []}
    rows = await db.fetch(
        "SELECT * FROM learning_recommendations WHERE owner_id=?1 AND chosen_goal=?2 "
        "AND class_id IN (" + ",".join(f"?{i + 3}" for i in range(len(class_ids))) + ") "
        "AND state IN ('suggested','pending','active') ORDER BY created_at DESC,id DESC LIMIT 100",
        owner_key,
        goal,
        *class_ids,
    )
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        identity = (str(row["class_id"]), str(row["concept_id"]))
        if identity in seen:
            continue
        seen.add(identity)
        resource = None
        if row["resource_id"] and row["resource_type"] == "source":
            note = await db.fetchrow(
                "SELECT id,title,created_by,publication_state,release_at,status FROM course_notes "
                "WHERE id=?1 AND class_id=?2",
                row["resource_id"],
                row["class_id"],
            )
            if note is not None and note["status"] == "ready":
                try:
                    await require_resource_access(
                        db,
                        row["class_id"],
                        owner_key,
                        capability="read",
                        publication_state=note["publication_state"],
                        release_at=note["release_at"],
                        created_by=note["created_by"],
                    )
                    resource = {
                        "type": "source",
                        "id": str(note["id"]),
                        "title": str(note["title"]),
                    }
                except ApiError:
                    resource = None
        await _record_recommendation_event(
            db,
            recommendation_id=str(row["id"]),
            owner_id=owner_key,
            event_type="shown",
        )
        items.append(
            {
                "id": str(row["id"]),
                "class_id": str(row["class_id"]),
                "href": f"#/classes/{row['class_id']}/study",
                "type": str(row["recommendation_type"]),
                "concept_id": str(row["concept_id"]),
                "label": str(row["label"]),
                "reason": str(row["reason"]),
                "resource": resource,
                "state": str(row["state"]),
                "created_at": row["created_at"],
            }
        )
    return {"chosen_goal": goal, "items": items}


@router.patch("/api/learning/recommendations/{recommendation_id}/state")
async def update_learning_recommendation_state(
    recommendation_id: UUID,
    payload: RecommendationStateUpdate,
    request: Request,
    response: Response,
) -> dict[str, str]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    _no_store(response)
    db = database(request)
    row = await db.fetchrow(
        "SELECT class_id,state FROM learning_recommendations WHERE id=?1 AND owner_id=?2",
        recommendation_id,
        owner_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested recommendation was not found.")
    await class_access(db, row["class_id"], owner_id, required="read")
    old_state = str(row["state"])
    new_state = payload.state
    if old_state in {"completed", "dismissed"}:
        if old_state == new_state:
            return {"id": str(recommendation_id), "state": old_state}
        raise ApiError(409, "recommendation_terminal", "This recommendation is already closed.")
    if new_state == "active" and old_state in {"suggested", "pending"}:
        event_type = "engaged"
    elif new_state in {"completed", "dismissed"}:
        event_type = new_state
    else:
        if old_state == new_state:
            return {"id": str(recommendation_id), "state": old_state}
        raise ApiError(
            409, "recommendation_state_conflict", "This recommendation cannot change to that state."
        )
    async with db.acquire() as conn:
        async with conn.transaction():
            await class_access(conn, row["class_id"], owner_id, required="read")
            result = await conn.fetchrow(
                "UPDATE learning_recommendations SET state=?3,updated_at="
                "strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?1 AND owner_id=?2 "
                "AND state=?4 RETURNING id,state",
                recommendation_id,
                owner_id,
                new_state,
                old_state,
            )
            if result is None:
                raise ApiError(
                    409,
                    "recommendation_state_conflict",
                    "This recommendation changed. Refresh and retry.",
                )
            await _record_recommendation_event(
                conn,
                recommendation_id=str(recommendation_id),
                owner_id=str(owner_id),
                event_type=event_type,
            )
    return {"id": str(recommendation_id), "state": new_state}


@router.get("/api/admin/learning-calibration")
async def get_learning_calibration_summary(request: Request, response: Response) -> dict[str, Any]:
    await _require_admin(request)
    _no_store(response)
    db = database(request)
    question_flags = await db.fetch(
        "SELECT f.class_id,f.bank_item_id,f.flag_code,f.response_count,f.adjusted_residual,"
        "f.state,f.created_at,i.level,i.language FROM question_calibration_flags f "
        "JOIN class_question_bank_items i ON i.id=f.bank_item_id AND i.class_id=f.class_id "
        "WHERE f.state='open' ORDER BY f.created_at DESC LIMIT 500"
    )
    graph_flags = await db.fetch(
        "SELECT f.class_id,f.edge_id,f.flag_code,f.observed_learners,f.prerequisite_rate,"
        "f.downstream_rate,f.state,f.created_at,e.from_concept_id,e.to_concept_id "
        "FROM calibration_graph_flags f JOIN class_concept_edges e "
        "ON e.id=f.edge_id AND e.class_id=f.class_id WHERE f.state='open' "
        "ORDER BY f.created_at DESC LIMIT 500"
    )
    replenishment = await db.fetch(
        "SELECT status,count(*) AS count FROM question_bank_replenishment_jobs GROUP BY status"
    )
    calibration_jobs = await db.fetch(
        "SELECT status,count(*) AS count FROM learning_calibration_jobs GROUP BY status"
    )
    latest_calibration_job = await db.fetchrow(
        "SELECT status,attempts,error_code,created_at,completed_at "
        "FROM learning_calibration_jobs ORDER BY created_at DESC,id DESC LIMIT 1"
    )
    return {
        "question_flags": question_flags,
        "graph_flags": graph_flags,
        "replenishment_jobs": replenishment,
        "calibration_jobs": calibration_jobs,
        "latest_calibration_job": latest_calibration_job,
    }
