"""Private class-scoped study chat, reminders, and bounded material requests."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import date
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.dependencies import database, enforce_sqlite_api_policy
from app.api.errors import ApiError
from app.services.ai_usage_ledger import bind_usage_context, reset_usage_context
from app.services.class_access import class_access
from app.services.class_study_assistant import (
    ClassAssistantDraft,
    ClassAssistantMessage,
)
from app.services.concept_graph import concept_graph
from app.services.learning_scoring import learning_progress
from app.services.source_index import fetch_authorized_chunk, search_authorized_sources

logger = logging.getLogger("screen_tutor.class_study_assistant_api")
router = APIRouter(tags=["class-study-assistant"])
MAX_SOURCE_CHUNKS = 6
MAX_SOURCE_UPLOADS = 6
MAX_SOURCE_CHUNK_CHARS = 1_600
MAX_CLASS_EVENTS = 50
_WORD = re.compile(r"[\w'-]+", re.UNICODE)
_EVENT_WORD = re.compile(
    r"\b(exam|midterm|final|quiz|test|assignment|homework|paper|project|deadline|due)\b",
    re.IGNORECASE,
)
_DATE_WORD = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{1,2}\s+\w+)\b",
    re.IGNORECASE,
)
_MATERIAL_WORD = re.compile(
    r"\b(study\s+guide|guide|quiz|flashcards?|practice\s+(?:test|exam)|exam|study\s+materials?)\b",
    re.I,
)
_MATERIAL_VERB = re.compile(r"\b(create|make|generate|build|prepare)\b", re.I)
_QUESTION_OPEN = re.compile(
    r"^\s*(?:when|what|which|is|are|was|were|could|would|should|do|does)\b", re.I
)
_QUERY_STOP = {
    "a",
    "about",
    "after",
    "am",
    "an",
    "and",
    "are",
    "as",
    "at",
    "can",
    "class",
    "create",
    "for",
    "from",
    "generate",
    "give",
    "guide",
    "help",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "make",
    "my",
    "of",
    "on",
    "or",
    "please",
    "regarding",
    "show",
    "study",
    "tell",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "would",
    "quiz",
    "quizzes",
    "flashcard",
    "flashcards",
    "practice",
    "exam",
    "exams",
    "test",
    "tests",
    "doing",
    "going",
    "performing",
    "performance",
    "score",
    "scores",
    "weak",
    "weakest",
    "should",
    "review",
    "next",
    "topics",
    "topic",
    "learn",
    "learning",
    "understand",
    "explain",
    "explanation",
    "myself",
    "have",
    "has",
    "had",
    "by",
    "deadline",
    "assignment",
}


class ClassAssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=2_000, strict=True)
    history: list[ClassAssistantMessage] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_history(self) -> ClassAssistantRequest:
        if any(
            item.role != ("user" if index % 2 == 0 else "assistant")
            for index, item in enumerate(self.history)
        ):
            raise ValueError("Chat history must alternate, starting with the user.")
        if self.history and self.history[-1].role != "assistant":
            raise ValueError("Chat history must end with an assistant message.")
        if sum(len(item.text) for item in self.history) + len(self.message) > 12_000:
            raise ValueError("Chat history is too long.")
        return self


class ClassAssistantEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    event_kind: Literal["exam", "quiz", "assignment", "deadline", "class_event"]
    title: str
    event_date: date
    created_at: str


class ClassAssistantSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    upload_id: UUID
    title: str


class ClassAssistantMaterialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    topic: str
    kinds: list[Literal["study_guide", "quiz", "practice_exam", "flashcards"]]
    count: int = Field(ge=4, le=24)
    upload_ids: list[UUID] = Field(min_length=1, max_length=MAX_SOURCE_UPLOADS)
    sources: list[ClassAssistantSource] = Field(min_length=1, max_length=MAX_SOURCE_UPLOADS)
    learner_focus: str | None = None


class ClassAssistantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reply: str
    references: list[dict]
    saved_event: ClassAssistantEvent | None = None
    material_request: ClassAssistantMaterialRequest | None = None


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


def _event_view(row: dict) -> ClassAssistantEvent:
    return ClassAssistantEvent(
        id=UUID(str(row["id"])),
        event_kind=str(row["event_kind"]),
        title=str(row["title"]),
        event_date=date.fromisoformat(str(row["event_date"])),
        created_at=str(row["created_at"]),
    )


async def _events(db, *, owner_id: UUID, class_id: UUID) -> list[ClassAssistantEvent]:
    rows = await db.fetch(
        "SELECT id,event_kind,title,event_date,created_at FROM class_study_assistant_events "
        "WHERE owner_id=?1 AND class_id=?2 "
        "ORDER BY CASE WHEN event_date>=?3 THEN 0 ELSE 1 END, "
        "CASE WHEN event_date>=?3 THEN event_date END ASC, "
        "CASE WHEN event_date<?3 THEN event_date END DESC,id LIMIT ?4",
        owner_id,
        class_id,
        date.today().isoformat(),
        MAX_CLASS_EVENTS,
    )
    return [_event_view(row) for row in rows]


def _topic_query(message: str) -> str:
    words = [word for word in _WORD.findall(message) if word.casefold() not in _QUERY_STOP]
    return " ".join(words[:12]).strip()


def _looks_like_event_declaration(message: str) -> bool:
    return bool(
        not _QUESTION_OPEN.search(message)
        and "?" not in message
        and _EVENT_WORD.search(message)
        and _DATE_WORD.search(message)
    )


def _looks_like_material_request(message: str) -> bool:
    return bool(_MATERIAL_VERB.search(message) and _MATERIAL_WORD.search(message))


def _requested_material_kinds(message: str) -> set[str]:
    """Return only material types explicitly requested in the latest user turn."""
    if not _looks_like_material_request(message):
        return set()
    requested: set[str] = set()
    if re.search(r"\b(?:study\s+)?guide\b", message, re.I):
        requested.add("study_guide")
    if re.search(r"\bquiz(?:zes)?\b", message, re.I):
        requested.add("quiz")
    if re.search(r"\bflashcards?\b", message, re.I):
        requested.add("flashcards")
    if re.search(r"\bpractice\s+(?:tests?|exams?)\b", message, re.I):
        requested.add("practice_exam")
    elif re.search(r"\b(?:test|exam)\b", message, re.I) and not _EVENT_WORD.search(message):
        requested.add("practice_exam")
    if re.search(r"\bstudy\s+materials?\b", message, re.I):
        requested.update({"study_guide", "quiz", "practice_exam", "flashcards"})
    return requested


def _event_title(value: str) -> tuple[str, str]:
    title = " ".join(unicodedata.normalize("NFKC", value).split())
    normalized = " ".join(title.casefold().split())
    if not title or len(title) > 160 or any(ord(char) < 32 for char in title):
        raise ValueError("invalid event title")
    return title, normalized


async def _performance_context(db, *, owner_id: UUID, class_id: UUID) -> dict:
    """Assemble only the requester's released scores and class-specific estimates."""
    runs = await db.fetch(
        "SELECT r.score_percent,m.kind,m.title,r.completed_at FROM quiz_runs r "
        "JOIN study_materials m ON m.id=r.material_id "
        "WHERE r.owner_id=?1 AND r.status='completed' AND r.assessment_type='quiz' "
        "AND m.kind IN ('quiz','practice_exam') "
        "AND COALESCE(m.origin_class_id,m.class_id)=?2 AND r.score_percent IS NOT NULL "
        "AND (r.feedback_release_policy='after_completion' OR "
        "(r.feedback_release_policy='manual' AND EXISTS (SELECT 1 FROM "
        "study_material_feedback_releases f WHERE f.material_id=m.id))) "
        "ORDER BY r.completed_at DESC,r.id DESC LIMIT 20",
        owner_id,
        class_id,
    )
    recent_scores = [
        {
            "kind": "exam" if row["kind"] == "practice_exam" else "quiz",
            "title": str(row["title"])[:120],
            "score_percent": round(float(row["score_percent"]), 1),
            "completed_at": str(row["completed_at"]),
        }
        for row in runs
    ]
    legacy = await db.fetch(
        "SELECT a.score_percent,m.kind,m.title,a.completed_at FROM study_material_attempts a "
        "JOIN study_materials m ON m.id=a.material_id "
        "WHERE a.owner_id=?1 AND COALESCE(m.origin_class_id,m.class_id)=?2 "
        "AND m.kind IN ('quiz','practice_exam') AND "
        "(m.feedback_release_policy='after_completion' OR "
        "(m.feedback_release_policy='manual' AND EXISTS (SELECT 1 FROM "
        "study_material_feedback_releases f WHERE f.material_id=m.id))) "
        "AND NOT EXISTS (SELECT 1 FROM quiz_runs r WHERE r.owner_id=a.owner_id "
        "AND r.material_id=a.material_id) "
        "ORDER BY a.completed_at DESC,a.id DESC LIMIT 20",
        owner_id,
        class_id,
    )
    recent_scores.extend(
        {
            "kind": "exam" if row["kind"] == "practice_exam" else "quiz",
            "title": str(row["title"])[:120],
            "score_percent": round(float(row["score_percent"]), 1),
            "completed_at": str(row["completed_at"]),
        }
        for row in legacy
    )
    recent_scores.sort(key=lambda item: item["completed_at"], reverse=True)
    recent_scores = recent_scores[:20]

    graph = await concept_graph(db, class_id=str(class_id), user_id=str(owner_id))
    concepts = [
        {
            "id": str(item["id"]),
            "class_id": str(class_id),
            "canonical_name": str(item.get("canonical_name") or "")[:240],
        }
        for item in graph.get("concepts", [])
        if isinstance(item, dict) and item.get("id") and item.get("canonical_name")
    ]
    progress = await learning_progress(
        db,
        owner_id=str(owner_id),
        visible_class_ids=[str(class_id)],
        visible_concepts=concepts,
        window_days=30,
    )
    private_concepts = [
        {
            "label": str(item.get("label") or "Concept")[:160],
            "status": item.get("status"),
            "current_level": item.get("current_level"),
            "latest_demonstrated_level": item.get("latest_demonstrated_level"),
            "estimate": item.get("estimate"),
            "review_due": bool(item.get("review_due")),
            "last_assessed_at": item.get("last_assessed_at"),
        }
        for item in progress.get("concepts", [])
    ]
    flashcards = await db.fetch(
        "SELECT s.last_rating,count(*) AS count FROM flashcard_review_state s "
        "JOIN study_materials m ON m.id=s.material_id "
        "WHERE s.owner_id=?1 AND s.reviewed_at IS NOT NULL AND "
        "s.last_rating IN ('recalled','unsure','missed') AND m.kind='flashcards' "
        "AND m.status='ready' AND COALESCE(m.origin_class_id,m.class_id)=?2 "
        "GROUP BY s.last_rating",
        owner_id,
        class_id,
    )
    flashcard_counts = {str(row["last_rating"]): int(row["count"]) for row in flashcards}
    study_scores = await db.fetch(
        "SELECT overall_score,quiz_score,exam_score,flashcard_score,evidence_level,"
        "explanation,updated_at FROM study_performance_scores "
        "WHERE owner_id=?1 AND class_id=?2 AND status='ready' "
        "ORDER BY updated_at DESC,id DESC LIMIT 6",
        owner_id,
        class_id,
    )
    scores_by_kind = {
        name: [item["score_percent"] for item in recent_scores if item["kind"] == name]
        for name in ("quiz", "exam")
    }
    score_summary = {
        name: {
            "attempts": len(values),
            "average_percent": round(sum(values) / len(values), 1) if values else None,
        }
        for name, values in scores_by_kind.items()
    }
    return {
        "as_of": date.today().isoformat(),
        "recent_accuracy_30_days": progress.get("recent_accuracy", {}),
        "quiz_exam_summary": score_summary,
        "recent_released_assessments": recent_scores,
        "previous_ai_study_estimates": [
            {
                "overall_score": row["overall_score"],
                "quiz_score": row["quiz_score"],
                "exam_score": row["exam_score"],
                "flashcard_score": row["flashcard_score"],
                "evidence_level": row["evidence_level"],
                "explanation": str(row["explanation"] or "")[:500],
                "updated_at": str(row["updated_at"]),
            }
            for row in study_scores
        ],
        "flashcard_self_ratings": flashcard_counts,
        "total_xp": int(progress.get("total_xp", 0)),
        "concepts": private_concepts[:30],
        "limitations": [
            "Scores are released assessment observations, not course grades.",
            "Mastery entries are estimates from available validated class concepts.",
            "Chat responses do not change performance, scores, or mastery.",
        ],
    }


async def _source_context(
    db, *, owner_id: UUID, class_id: UUID, query: str
) -> tuple[list[dict], list[dict]]:
    if not query:
        return [], []
    found = await search_authorized_sources(
        db,
        class_id=str(class_id),
        user_id=str(owner_id),
        query=query,
        upload_id=None,
        limit=MAX_SOURCE_CHUNKS,
    )
    context_chunks: list[dict] = []
    candidates: dict[str, dict] = {}
    for result in found.get("items", []):
        chunk = await fetch_authorized_chunk(
            db,
            class_id=str(class_id),
            user_id=str(owner_id),
            chunk_revision_id=str(result["chunk_revision_id"]),
        )
        upload_id = str(chunk["material_id"])
        title = str(chunk.get("title") or "Course source")[:200]
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        locator = chunk.get("chunk_locator") or {}
        locator = locator if isinstance(locator, dict) else {}
        context_chunks.append(
            {
                "upload_id": upload_id,
                "title": title,
                "locator": locator,
                "text": text[:MAX_SOURCE_CHUNK_CHARS],
            }
        )
        if upload_id not in candidates and len(candidates) < MAX_SOURCE_UPLOADS:
            candidates[upload_id] = {"upload_id": upload_id, "title": title, "excerpts": []}
        if upload_id in candidates and not candidates[upload_id]["excerpts"]:
            candidates[upload_id]["excerpts"].append(text[:600])
    return context_chunks[:MAX_SOURCE_CHUNKS], list(candidates.values())


async def _save_event(
    db, *, owner_id: UUID, class_id: UUID, draft: ClassAssistantDraft
) -> ClassAssistantEvent:
    try:
        title, normalized = _event_title(draft.event_title or "")
    except ValueError:
        raise ApiError(
            503, "class_assistant_unavailable", "The reminder could not be saved safely."
        ) from None
    event_date = draft.event_date
    if event_date is None or event_date < date.today():
        raise ApiError(
            409,
            "class_event_date_past",
            "That date is in the past; give me the upcoming date and I can add it.",
        )
    event_id = str(uuid4())
    await db.execute(
        "INSERT INTO class_study_assistant_events "
        "(id,owner_id,class_id,event_kind,title,normalized_title,event_date) "
        "VALUES (?1,?2,?3,?4,?5,?6,?7) "
        "ON CONFLICT(owner_id,class_id,event_date,normalized_title) DO NOTHING",
        event_id,
        owner_id,
        class_id,
        draft.event_kind,
        title,
        normalized,
        event_date.isoformat(),
    )
    row = await db.fetchrow(
        "SELECT id,event_kind,title,event_date,created_at FROM class_study_assistant_events "
        "WHERE owner_id=?1 AND class_id=?2 AND event_date=?3 AND normalized_title=?4",
        owner_id,
        class_id,
        event_date.isoformat(),
        normalized,
    )
    if row is None:
        raise ApiError(
            503, "class_event_save_failed", "The reminder could not be saved. Try again."
        )
    return _event_view(row)


@router.post("/api/classes/{class_id}/study-assistant")
async def chat_with_class_study_assistant(
    class_id: UUID,
    payload: ClassAssistantRequest,
    request: Request,
    response: Response,
) -> ClassAssistantResponse:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db = database(request)
    access = await class_access(db, class_id, owner_id, required="read")
    _no_store(response)
    retry_after = await request.app.state.class_study_assistant_rate_limiter.check(str(owner_id))
    if retry_after is not None:
        raise ApiError(
            429,
            "class_assistant_rate_limited",
            "Please wait before sending another class question.",
            retry_after_seconds=retry_after,
        )
    service = getattr(request.app.state, "class_study_assistant_service", None)
    if service is None:
        raise ApiError(
            503,
            "class_assistant_unavailable",
            "Class study help is unavailable. Try again shortly.",
        )

    query = _topic_query(payload.message)
    source_chunks, source_candidates = await _source_context(
        db, owner_id=owner_id, class_id=class_id, query=query
    )
    class_name = str(access.get("name") or "Class")[:160]
    context = {
        "class": {"id": str(class_id), "name": class_name},
        "today": date.today().isoformat(),
        "performance": await _performance_context(db, owner_id=owner_id, class_id=class_id),
        "private_events": [
            event.model_dump(mode="json")
            for event in (await _events(db, owner_id=owner_id, class_id=class_id))[:20]
        ],
        "source_excerpts": source_chunks,
        "source_candidates": source_candidates,
    }
    if len(json.dumps(context, ensure_ascii=False)) > 21_000:
        performance = context["performance"]
        performance["concepts"] = performance.get("concepts", [])[:16]
        performance["recent_released_assessments"] = performance.get(
            "recent_released_assessments", []
        )[:12]
        performance["previous_ai_study_estimates"] = performance.get(
            "previous_ai_study_estimates", []
        )[:4]
        context["private_events"] = context["private_events"][-12:]
        context["source_excerpts"] = [
            {**item, "text": str(item.get("text") or "")[:1_200]}
            for item in context["source_excerpts"][:6]
        ]
        context["source_candidates"] = [
            {
                **item,
                "excerpts": [str(text)[:300] for text in item.get("excerpts", [])[:1]],
            }
            for item in context["source_candidates"][:MAX_SOURCE_UPLOADS]
        ]
    usage_token = bind_usage_context(
        operation_id=str(uuid4()),
        operation_kind="class_study_assistant",
        operation_label=f"Class study assistant · {class_name}",
    )
    try:
        draft: ClassAssistantDraft = await service.reply(
            context,
            payload.history,
            payload.message,
            request_id=str(getattr(request.state, "request_id", "unknown")),
        )
    finally:
        reset_usage_context(usage_token)

    saved_event = None
    material_request = None
    reply = draft.reply
    if draft.action == "add_event":
        if not _looks_like_event_declaration(payload.message):
            reply = (
                "I can add a reminder when you state the event and date, for example: "
                "“Exam on Oct 19.” I haven't saved anything yet."
            )
        else:
            try:
                saved_event = await _save_event(
                    db, owner_id=owner_id, class_id=class_id, draft=draft
                )
            except ApiError as exc:
                if exc.code == "class_event_date_past":
                    reply = exc.message
                else:
                    raise
            else:
                formatted_date = saved_event.event_date.strftime("%b %-d, %Y")
                reply = (
                    f"Added {saved_event.title} to your private {class_name} reminders "
                    f"for {formatted_date}."
                )
    elif draft.action == "create_materials":
        requested_kinds = _requested_material_kinds(payload.message)
        if not requested_kinds:
            reply = (
                "Tell me which study material you'd like me to create, such as a study "
                "guide or quiz. I haven't started generation."
            )
        elif not set(draft.material_kinds).issubset(requested_kinds):
            reply = (
                "I only create the material types you asked for. Tell me which study "
                "guide, quiz, practice test, or flashcards you want. I haven't started "
                "generation."
            )
        elif not source_candidates:
            reply = (
                f"I couldn't find readable class-source passages about "
                f"{draft.material_topic}. Add or process relevant notes, then ask me again."
            )
        else:
            candidate_ids = {item["upload_id"] for item in source_candidates}
            selected_ids = list(dict.fromkeys(draft.source_upload_ids))
            if (
                not selected_ids
                or len(selected_ids) > MAX_SOURCE_UPLOADS
                or not set(selected_ids).issubset(candidate_ids)
            ):
                reply = (
                    "I couldn't verify the source selection for that request, so I didn't "
                    "start generation. Try asking again with a more specific topic."
                )
            else:
                selected = [item for item in source_candidates if item["upload_id"] in selected_ids]
                material_request = ClassAssistantMaterialRequest(
                    topic=draft.material_topic or "",
                    kinds=draft.material_kinds,
                    count=draft.count,
                    upload_ids=[UUID(value) for value in selected_ids],
                    sources=[
                        ClassAssistantSource(upload_id=UUID(item["upload_id"]), title=item["title"])
                        for item in selected
                    ],
                    learner_focus=draft.learner_focus,
                )
    return ClassAssistantResponse(
        reply=reply,
        references=[] if material_request is not None else references_from(source_chunks),
        saved_event=saved_event,
        material_request=material_request,
    )


def references_from(source_chunks: list[dict]) -> list[dict]:
    references: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for chunk in source_chunks:
        locator = chunk.get("locator") if isinstance(chunk.get("locator"), dict) else {}
        key = (str(chunk.get("upload_id") or ""), str(locator))
        if key in seen:
            continue
        seen.add(key)
        reference = {
            "upload_id": chunk["upload_id"],
            "label": str(chunk.get("title") or "Course source"),
        }
        for name, locator_name in (
            ("page", "page"),
            ("slide", "slide"),
            ("timestamp_s", "timestamp_start_seconds"),
        ):
            if locator.get(locator_name) is not None:
                reference[name] = locator[locator_name]
        references.append(reference)
        if len(references) >= 8:
            break
    return references


@router.get("/api/classes/{class_id}/study-assistant/events")
async def list_class_study_assistant_events(
    class_id: UUID, request: Request, response: Response
) -> dict[str, list[ClassAssistantEvent]]:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db = database(request)
    await class_access(db, class_id, owner_id, required="read")
    _no_store(response)
    return {"events": await _events(db, owner_id=owner_id, class_id=class_id)}


@router.delete("/api/classes/{class_id}/study-assistant/events/{event_id}", status_code=204)
async def delete_class_study_assistant_event(
    class_id: UUID, event_id: UUID, request: Request, response: Response
) -> Response:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db = database(request)
    await class_access(db, class_id, owner_id, required="read")
    _no_store(response)
    result = await db.execute(
        "DELETE FROM class_study_assistant_events WHERE id=?1 AND class_id=?2 AND owner_id=?3",
        event_id,
        class_id,
        owner_id,
    )
    if result != "DELETE 1":
        raise ApiError(404, "not_found", "The requested reminder was not found.")
    return Response(status_code=204, headers={"Cache-Control": "private, no-store"})
