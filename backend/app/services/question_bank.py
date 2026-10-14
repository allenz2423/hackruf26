"""Validated source-grounded class question bank and assessment assembly."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import unicodedata
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.api.errors import ApiError
from app.api.schemas import QuestionBankGenerationRequest
from app.services.class_access import class_access
from app.services.concept_graph import _validate_current_chunk_refs
from app.services.database import SQLiteConnection, SQLiteDatabase, json_value
from app.services.source_index import fetch_authorized_chunk, search_authorized_sources
from app.services.study_prompts import (
    QUESTION_BANK_GENERATION_VERSION,
    QUESTION_BANK_VALIDATION_VERSION,
)

MAX_BANK_SOURCE_CHUNKS = 32
MAX_BANK_SOURCE_CHARS = 36_000
PRIVATE_ASSESSMENT_VALIDATION_ATTEMPTS = 2
QUESTION_BANK_VALIDATOR_VERSION = "jot-question-bank-validator-v2"
FAMILY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "by",
    "does",
    "for",
    "from",
    "how",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "through",
    "to",
    "what",
    "which",
    "with",
}
COGNITIVE_LEVELS = {
    1: "Recall",
    2: "Conceptual explanation",
    3: "Application or multi-step reasoning",
    4: "Analysis, synthesis, or transfer to a new context",
}


def _normalize_family(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[\w]+", normalized, re.UNICODE))


async def current_concept_source_digest(
    database: SQLiteConnection | SQLiteDatabase,
    *,
    class_id: str,
    user_id: str,
    concept_ids: list[str],
) -> str:
    """Hash the currently released source revisions supporting selected concepts."""
    selected = sorted(set(concept_ids))
    if not selected:
        raise ApiError(409, "source_chunks_unavailable", "No current source is available.")
    rows = await database.fetch(
        "SELECT DISTINCT ref.chunk_revision_id FROM class_concept_source_refs ref "
        "JOIN class_concepts c ON c.id=ref.concept_id AND c.class_id=ref.class_id "
        "JOIN source_chunk_revisions scr ON scr.id=ref.chunk_revision_id "
        "JOIN source_index_heads h ON h.revision_id=scr.revision_id "
        "JOIN source_revisions sr ON sr.id=scr.revision_id "
        "JOIN course_notes n ON n.id=sr.note_id AND n.class_id=ref.class_id "
        "WHERE ref.class_id=?1 AND ref.concept_id IN ("
        + ",".join(f"?{index + 2}" for index in range(len(selected)))
        + ") AND c.status='validated' AND scr.provenance='source' AND n.status='ready' "
        "AND n.publication_state='published' AND (n.release_at IS NULL OR "
        "n.release_at<=strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
        "AND h.processing_revision=n.processing_revision ORDER BY ref.chunk_revision_id",
        class_id,
        *selected,
    )
    chunk_ids = sorted({str(row["chunk_revision_id"]) for row in rows})
    if not chunk_ids:
        raise ApiError(409, "source_chunks_unavailable", "No current source is available.")
    await _validate_current_chunk_refs(
        database,
        class_id=class_id,
        user_id=user_id,
        chunk_revision_ids=chunk_ids,
    )
    return hashlib.sha256("\n".join(chunk_ids).encode("utf-8")).hexdigest()


def family_identity(target: str, primary_concept_id: str) -> tuple[str, str]:
    """Return a stable opaque family ID and matching signature for an objective."""
    normalized = _normalize_family(target)
    if not normalized:
        raise ApiError(
            422,
            "invalid_question_family",
            "A question needs a clear assessment target.",
        )
    signature = hashlib.sha256(f"{primary_concept_id}\0{normalized}".encode()).hexdigest()
    return str(uuid5(NAMESPACE_URL, f"jot:question-family:{signature}")), signature


def _family_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for token in _normalize_family(value).split():
        if token in FAMILY_STOPWORDS:
            continue
        if len(token) > 5 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 5 and token.endswith("ing"):
            token = token[:-3]
        elif len(token) > 4 and token.endswith("ed"):
            token = token[:-2]
        elif len(token) > 4 and token.endswith("es"):
            token = token[:-2]
        elif len(token) > 4 and token.endswith("s"):
            token = token[:-1]
        tokens.add(token)
    return tokens


def _near_duplicate(left: str, right: str) -> bool:
    """Conservatively catch close paraphrases of a same-concept objective."""
    left_tokens = _family_tokens(left)
    right_tokens = _family_tokens(right)
    if min(len(left_tokens), len(right_tokens)) < 4:
        return bool(left_tokens and left_tokens == right_tokens)
    union = left_tokens | right_tokens
    return bool(union and len(left_tokens & right_tokens) / len(union) >= 0.75)


def build_coverage_plan(
    concepts: list[dict[str, Any]], payload: QuestionBankGenerationRequest
) -> list[dict[str, Any]]:
    """Allocate concepts, levels, and types before any source retrieval begins."""
    if not concepts:
        raise ApiError(
            409,
            "question_bank_unavailable",
            "Validate at least one class concept before generation.",
        )
    selected = concepts[: min(len(concepts), payload.count)]
    plan: list[dict[str, Any]] = []
    for index in range(payload.count):
        concept = selected[index % len(selected)]
        level = payload.level or (index % 4) + 1
        kind = payload.question_types[index % len(payload.question_types)]
        plan.append(
            {
                "assignment_id": f"slot-{index:03d}",
                "concept_id": str(concept["id"]),
                "concept_name": str(concept["canonical_name"]),
                "level": level,
                "level_description": COGNITIVE_LEVELS[level],
                "kind": kind,
            }
        )
    return plan


async def validate_material_bank_sources(
    connection: SQLiteConnection,
    *,
    material_id: str,
    class_id: str,
    user_id: str,
) -> None:
    """Fail closed if copied bank questions no longer have live source support."""
    material = await connection.fetchrow(
        "SELECT content,resource_scope FROM study_materials WHERE id=?1 AND class_id=?2",
        material_id,
        class_id,
    )
    if material is None:
        raise ApiError(404, "not_found", "The assessment was not found.")
    content = json_value(material["content"], {})
    questions = content.get("questions", []) if isinstance(content, dict) else []
    for question in questions if isinstance(questions, list) else []:
        if not isinstance(question, dict):
            continue
        item = await connection.fetchrow(
            "SELECT state FROM class_question_bank_items WHERE id=?1 AND class_id=?2",
            str(question.get("id", "")),
            class_id,
        )
        if item is None:
            raise ApiError(
                409,
                "assessment_source_unavailable",
                "A question is missing validated bank provenance. Regenerate the assessment.",
            )
        if item["state"] != "validated":
            raise ApiError(
                409,
                "assessment_source_unavailable",
                "A question source is no longer available. Regenerate the assessment.",
            )
        source_rows = await connection.fetch(
            "SELECT chunk_revision_id FROM class_question_bank_sources "
            "WHERE item_id=?1 ORDER BY source_order,chunk_revision_id",
            str(question["id"]),
        )
        source_ids = [str(row["chunk_revision_id"]) for row in source_rows]
        if not source_ids:
            raise ApiError(
                409,
                "assessment_source_unavailable",
                "A question source is no longer available. Regenerate the assessment.",
            )
        material_source_ids = question.get("source_chunk_revision_ids")
        if not isinstance(material_source_ids, list) or set(map(str, material_source_ids)) != set(
            source_ids
        ):
            raise ApiError(
                409,
                "assessment_source_unavailable",
                "A question citation no longer matches its validated bank provenance.",
            )
        await _validate_current_chunk_refs(
            connection,
            class_id=class_id,
            user_id=user_id,
            chunk_revision_ids=source_ids,
        )


async def validate_material_question_sources(
    connection: SQLiteConnection,
    *,
    material_id: str,
    class_id: str,
    user_id: str,
) -> None:
    """Revalidate every exact source citation before creating a new run."""
    material = await connection.fetchrow(
        "SELECT content FROM study_materials WHERE id=?1", material_id
    )
    if material is None:
        raise ApiError(404, "not_found", "The assessment was not found.")
    content = json_value(material["content"], {})
    questions = content.get("questions", []) if isinstance(content, dict) else []
    all_refs: set[str] = set()
    for question in questions if isinstance(questions, list) else []:
        if not isinstance(question, dict):
            continue
        refs = question.get("source_chunk_revision_ids")
        if not isinstance(refs, list) or not refs:
            raise ApiError(
                409,
                "assessment_source_unavailable",
                "This assessment has no current source citations. Regenerate it before "
                "starting a graded run.",
            )
        all_refs.update(str(value) for value in refs)
    if not all_refs:
        raise ApiError(
            409,
            "assessment_source_unavailable",
                "This assessment has no current source citations. Regenerate it before "
                "starting a graded run.",
        )
    await _validate_current_chunk_refs(
        connection,
        class_id=class_id,
        user_id=user_id,
        chunk_revision_ids=sorted(all_refs),
    )


async def _load_concepts(
    database: SQLiteDatabase,
    *,
    class_id: str,
    requested_ids: list[str],
    maximum: int,
) -> list[dict[str, Any]]:
    if requested_ids:
        rows = await database.fetch(
            "SELECT id,canonical_name,description FROM class_concepts "
            "WHERE class_id=?1 AND status='validated' AND id IN ("
            + ",".join(f"?{index + 2}" for index in range(len(requested_ids)))
            + ") ORDER BY canonical_name COLLATE NOCASE,id LIMIT ?"
            + str(len(requested_ids) + 2),
            class_id,
            *requested_ids,
            maximum,
        )
        found = {str(row["id"]) for row in rows}
        if found != set(requested_ids):
            raise ApiError(
                404, "concept_not_found", "One or more selected validated concepts were not found."
            )
        return rows
    return await database.fetch(
        "SELECT id,canonical_name,description FROM class_concepts "
        "WHERE class_id=?1 AND status='validated' "
        "ORDER BY canonical_name COLLATE NOCASE,id LIMIT ?2",
        class_id,
        maximum,
    )


async def _eligible_existing_item(
    database: SQLiteDatabase,
    *,
    class_id: str,
    user_id: str,
    slot: dict[str, Any],
    language: str,
) -> dict[str, Any] | None:
    candidates = await database.fetch(
        "SELECT i.* FROM class_question_bank_items i JOIN class_question_bank_concepts c "
        "ON c.item_id=i.id AND c.class_id=i.class_id WHERE i.class_id=?1 AND i.state='validated' "
        "AND i.kind=?2 AND i.level=?3 AND lower(i.language)=lower(?4) AND c.concept_id=?5 "
        "ORDER BY i.exposure_count ASC,i.created_at ASC,i.id LIMIT 20",
        class_id,
        slot["kind"],
        slot["level"],
        language,
        slot["concept_id"],
    )
    for candidate in candidates:
        source_rows = await database.fetch(
            "SELECT chunk_revision_id FROM class_question_bank_sources "
            "WHERE item_id=?1 ORDER BY source_order,chunk_revision_id",
            candidate["id"],
        )
        source_ids = [str(row["chunk_revision_id"]) for row in source_rows]
        try:
            await _validate_current_chunk_refs(
                database,
                class_id=class_id,
                user_id=user_id,
                chunk_revision_ids=source_ids,
            )
        except ApiError:
            continue
        concepts = await database.fetch(
            "SELECT concept_id FROM class_question_bank_concepts WHERE item_id=?1 "
            "ORDER BY relation,concept_id",
            candidate["id"],
        )
        return {
            "id": str(candidate["id"]),
            "family_id": str(candidate["family_id"]),
            "family_signature": str(candidate["family_signature"]),
            "prompt": str(candidate["prompt"]),
            "kind": str(candidate["kind"]),
            "level": int(candidate["level"]),
            "language": str(candidate["language"]),
            "choices": json_value(candidate["choices_json"], []),
            **json_value(candidate["grading_json"], {}),
            "concept_ids": [str(row["concept_id"]) for row in concepts],
            "source_chunk_revision_ids": source_ids,
            "bank_item_id": str(candidate["id"]),
        }
    return None


async def _retrieve_sources(
    database: SQLiteDatabase,
    *,
    class_id: str,
    user_id: str,
    concepts: list[dict[str, Any]],
    upload_ids: list[str],
) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for concept in concepts:
        selected_uploads: list[str | None] = list(upload_ids) if upload_ids else [None]
        for selected_upload in selected_uploads:
            results = await search_authorized_sources(
                database,
                class_id=class_id,
                user_id=user_id,
                query=str(concept["canonical_name"]),
                upload_id=selected_upload,
                limit=4,
            )
            for hit in results["items"]:
                if len(by_id) >= MAX_BANK_SOURCE_CHUNKS:
                    break
                chunk_id = str(hit["chunk_revision_id"])
                if chunk_id in by_id:
                    continue
                chunk = await fetch_authorized_chunk(
                    database,
                    class_id=class_id,
                    user_id=user_id,
                    chunk_revision_id=chunk_id,
                )
                if chunk["provenance"] != "source":
                    continue
                by_id[chunk_id] = {
                    "chunk_revision_id": chunk_id,
                    "material_id": str(chunk["material_id"]),
                    "title": str(chunk["title"]),
                    "locator": chunk["locator"],
                    "text": str(chunk["text"]),
                    "concept_id": str(concept["id"]),
                }
            if len(by_id) >= MAX_BANK_SOURCE_CHUNKS:
                break
    if not by_id:
        raise ApiError(
            409,
            "source_chunks_unavailable",
            "No current authorized source chunks are available for these concepts.",
        )
    chars = 0
    bounded: dict[str, dict[str, Any]] = {}
    for chunk_id, item in by_id.items():
        remaining = MAX_BANK_SOURCE_CHARS - chars
        if remaining <= 0:
            break
        text = item["text"][:remaining]
        if text.strip():
            bounded[chunk_id] = {**item, "text": text}
            chars += len(text)
    return bounded


def _validate_ai_items(
    generated: list[Any],
    review: list[Any],
    *,
    plan: list[dict[str, Any]],
    source_context: dict[str, dict[str, Any]],
    language: str,
    prior_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    plan_by_id = {slot["assignment_id"]: slot for slot in plan}
    if len(generated) != len(plan) or len(review) != len(plan):
        raise ApiError(
            503, "question_validation_failed", "The generated assessment did not pass validation."
        )
    if {item.assignment_id for item in generated} != set(plan_by_id):
        raise ApiError(
            503, "question_validation_failed", "The generated assessment did not pass validation."
        )
    generated_index = {item.assignment_id: index for index, item in enumerate(generated)}
    generated_by_assignment = {item.assignment_id: item for item in generated}
    review_by_index = {item.index: item for item in review}
    if set(review_by_index) != set(range(len(plan))):
        raise ApiError(
            503, "question_validation_failed", "The generated assessment did not pass validation."
        )
    validated: list[dict[str, Any]] = []
    used_family_signatures: set[str] = set()
    batch_families: list[tuple[str, int, str, str]] = []
    for slot in plan:
        item = generated_by_assignment[slot["assignment_id"]]
        index = generated_index[slot["assignment_id"]]
        checks = review_by_index[index]
        if not all(
            (
                checks.source_supported,
                checks.concept_aligned,
                checks.answer_correct,
                checks.distractors_valid,
                checks.unambiguous,
                checks.level_aligned,
                checks.language_valid,
                checks.leakage_free,
                not checks.duplicate_family,
            )
        ):
            raise ApiError(
                503,
                "question_validation_failed",
                "The generated assessment did not pass validation.",
            )
        if (
            item.level != slot["level"]
            or item.kind != slot["kind"]
            or not _language_matches(item.language, language)
            or slot["concept_id"] not in item.concept_ids
            or not set(item.concept_ids).issubset({s["concept_id"] for s in plan})
            or not set(item.source_chunk_revision_ids).issubset(source_context)
        ):
            raise ApiError(
                503,
                "question_validation_failed",
                "The generated assessment did not pass validation.",
            )
        if not any(
            source_context[source_id]["concept_id"] == slot["concept_id"]
            for source_id in item.source_chunk_revision_ids
        ):
            raise ApiError(
                503,
                "question_validation_failed",
                "The generated assessment did not pass validation.",
            )
        choice_ids = [choice.id for choice in item.choices]
        choice_texts = [_normalize_family(choice.text) for choice in item.choices]
        if item.kind == "multiple_choice":
            structurally_valid = (
                2 <= len(item.choices) <= 6
                and len(choice_ids) == len(set(choice_ids))
                and len(choice_texts) == len(set(choice_texts))
                and item.answer in set(choice_ids)
            )
        elif item.kind == "true_false":
            structurally_valid = not item.choices and item.answer.casefold() in {"true", "false"}
        else:
            accepted = {_normalize_family(value) for value in item.accepted_answers}
            structurally_valid = not item.choices and _normalize_family(item.answer) in accepted
        if not structurally_valid:
            raise ApiError(
                503,
                "question_validation_failed",
                "The generated assessment did not pass validation.",
            )
        if not item.source_chunk_revision_ids or not item.concept_ids:
            raise ApiError(
                503,
                "question_validation_failed",
                "The generated assessment did not pass validation.",
            )
        family_id, signature = family_identity(item.assessment_target, slot["concept_id"])
        candidate_family_text = item.assessment_target
        near_batch_duplicate = any(
            prior_signature == signature
            or (
                prior_concept_id == slot["concept_id"]
                and prior_level == item.level
                and _near_duplicate(candidate_family_text, prior_target)
            )
            for prior_concept_id, prior_level, prior_target, prior_signature in batch_families
        )
        prior_near_duplicate = any(
            entry.get("concept_id") == slot["concept_id"]
            and entry.get("level") == item.level
            and (
                entry.get("family_signature") == signature
                or _near_duplicate(
                    candidate_family_text,
                    str(entry.get("assessment_target") or entry.get("prompt", "")),
                )
            )
            for entry in (prior_items or [])
        )
        if signature in used_family_signatures or near_batch_duplicate or prior_near_duplicate:
            raise ApiError(
                409,
                "duplicate_question_family",
                "The generated set repeats the same assessment objective. Retry generation.",
            )
        used_family_signatures.add(signature)
        batch_families.append((slot["concept_id"], item.level, candidate_family_text, signature))
        validated.append(
            {
                "id": str(uuid4()),
                "family_id": family_id,
                "family_signature": signature,
                "prompt": item.prompt,
                "kind": item.kind,
                "level": item.level,
                "language": item.language,
                "assessment_target": item.assessment_target,
                "choices": [choice.model_dump(mode="json") for choice in item.choices],
                "answer": item.answer,
                "accepted_answers": list(item.accepted_answers),
                "explanation": item.explanation,
                "rubric": item.rubric,
                "concept_ids": list(dict.fromkeys(item.concept_ids)),
                "source_chunk_revision_ids": list(dict.fromkeys(item.source_chunk_revision_ids)),
                "primary_concept_id": slot["concept_id"],
                "bank_item_id": None,
            }
        )
    return validated


def _language_matches(actual: str, requested: str) -> bool:
    if requested.casefold() != "auto":
        return actual.casefold() == requested.casefold()
    return bool(
        re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", actual) and actual.casefold() != "auto"
    )


async def create_validated_private_assessment(
    database: SQLiteDatabase,
    *,
    material_id: str,
    class_id: str,
    user_id: str,
    material_kind: str,
    options: dict[str, Any],
    language: str,
    additional_instructions: str | None,
    source_rows: list[dict[str, Any]],
    service: Any,
) -> dict[str, Any]:
    """Generate a private quiz from exact authorized chunks and fail closed.

    Private generation deliberately uses the structured question generator and
    independent validator used for the class bank, but it never creates shared
    bank rows. Source-associated validated graph concepts are used when they
    exist; otherwise an opaque source-topic key provides a temporary grouping
    solely for validation/family deduplication and is not persisted as a graph
    concept or returned to the student.
    """
    from app.api.schemas import QuestionBankGenerationRequest

    if material_kind not in {"quiz", "practice_exam"}:
        raise ApiError(409, "assessment_kind_unsupported", "This assessment type is unavailable.")
    await class_access(database, class_id, user_id, required="read")
    requested_language = language
    request_payload = QuestionBankGenerationRequest(
        operation_id=uuid4(),
        kind=material_kind,
        upload_ids=[str(row["id"]) for row in source_rows],
        count=int(options.get("count", 10)),
        question_types=options.get("question_types") or ["multiple_choice"],
        additional_instructions=additional_instructions,
        language=requested_language,
    )
    source_context: dict[str, dict[str, Any]] = {}
    topic_rows: dict[str, dict[str, Any]] = {}
    actual_topic_ids: set[str] = set()
    max_chars = MAX_BANK_SOURCE_CHARS
    for source in source_rows:
        note_id = str(source.get("id", ""))
        if not note_id:
            continue
        async with database.acquire() as connection:
            await class_access(connection, class_id, user_id, required="read")
        excerpt = " ".join(
            [str(source.get("title") or ""), str(source.get("summary") or "")[:260]]
        ).strip()
        if not excerpt:
            excerpt = str(source.get("title") or "course material")
        hits = await search_authorized_sources(
            database,
            class_id=class_id,
            user_id=user_id,
            query=excerpt[:500],
            upload_id=note_id,
            limit=4,
        )
        for hit in hits["items"]:
            chunk_id = str(hit["chunk_revision_id"])
            if chunk_id in source_context or len(source_context) >= MAX_BANK_SOURCE_CHUNKS:
                continue
            chunk = await fetch_authorized_chunk(
                database,
                class_id=class_id,
                user_id=user_id,
                chunk_revision_id=chunk_id,
            )
            remaining = max_chars - sum(len(item["text"]) for item in source_context.values())
            if remaining <= 0:
                break
            chunk_text = str(chunk["text"])[:remaining]
            if not chunk_text.strip():
                continue
            concept = await database.fetchrow(
                "SELECT c.id,c.canonical_name,c.description FROM class_concepts c "
                "JOIN class_concept_source_refs r ON r.concept_id=c.id AND r.class_id=c.class_id "
                "WHERE c.class_id=?1 AND c.status='validated' AND r.chunk_revision_id=?2 "
                "ORDER BY c.canonical_name COLLATE NOCASE,c.id LIMIT 1",
                class_id,
                chunk_id,
            )
            if concept is not None:
                topic_id = str(concept["id"])
                actual_topic_ids.add(topic_id)
                topic = {
                    "id": topic_id,
                    "canonical_name": str(concept["canonical_name"]),
                    "description": str(concept["description"] or ""),
                }
            else:
                topic_id = str(
                    uuid5(NAMESPACE_URL, f"jot:private-source-topic:{class_id}:{note_id}")
                )
                topic = {
                    "id": topic_id,
                    "canonical_name": str(
                        chunk.get("section_title") or source.get("title") or "Course topic"
                    ),
                    "description": (
                        "A temporary source-topic grouping used only to validate "
                        "this private assessment."
                    ),
                }
            topic_rows.setdefault(topic_id, topic)
            source_context[chunk_id] = {
                **chunk,
                "text": chunk_text,
                "concept_id": topic_id,
            }
    if not source_context or not topic_rows:
        raise ApiError(
            409,
            "source_chunks_unavailable",
                "The selected materials do not have current, searchable source chunks. "
                "Retry after indexing.",
        )

    plan = build_coverage_plan(list(topic_rows.values()), request_payload)
    difficulty = options.get("difficulty", "mixed")
    if difficulty == "easier":
        for index, slot in enumerate(plan):
            slot["level"] = 1 + (index % 2)
            slot["level_description"] = COGNITIVE_LEVELS[slot["level"]]
    elif difficulty == "harder":
        for index, slot in enumerate(plan):
            slot["level"] = 3 + (index % 2)
            slot["level_description"] = COGNITIVE_LEVELS[slot["level"]]
    assignments = [
        {
            **slot,
            "concept_description": topic_rows[slot["concept_id"]]["description"],
            "source_chunk_revision_ids": [
                chunk_id
                for chunk_id, chunk in source_context.items()
                if chunk["concept_id"] == slot["concept_id"]
            ],
        }
        for slot in plan
    ]
    # When many topics are selected, some may not have searchable excerpts.
    # Allocate those slots only across topics backed by retrieved chunks.
    available_topics = {str(chunk["concept_id"]) for chunk in source_context.values()}
    if len(available_topics) != len(topic_rows):
        supported = [topic_rows[key] for key in sorted(available_topics)]
        plan = build_coverage_plan(supported, request_payload)
        if difficulty == "easier":
            for index, slot in enumerate(plan):
                slot["level"] = 1 + (index % 2)
                slot["level_description"] = COGNITIVE_LEVELS[slot["level"]]
        elif difficulty == "harder":
            for index, slot in enumerate(plan):
                slot["level"] = 3 + (index % 2)
                slot["level_description"] = COGNITIVE_LEVELS[slot["level"]]
        assignments = [
            {
                **slot,
                "concept_description": topic_rows[slot["concept_id"]]["description"],
                "source_chunk_revision_ids": [
                    chunk_id
                    for chunk_id, chunk in source_context.items()
                    if chunk["concept_id"] == slot["concept_id"]
                ],
            }
            for slot in plan
        ]
    if any(not slot["source_chunk_revision_ids"] for slot in assignments):
        raise ApiError(
            409,
            "source_chunks_unavailable",
            "A selected topic has no current supporting source chunks.",
        )
    public_sources = [
        {key: value for key, value in chunk.items() if key != "concept_id"}
        for chunk in source_context.values()
    ]
    generation_context = {
        "kind": material_kind,
        "requested_language": request_payload.language,
        "optional_instructions": additional_instructions or "",
        "assignments": assignments,
        "source_chunks": public_sources,
    }
    for validation_attempt in range(PRIVATE_ASSESSMENT_VALIDATION_ATTEMPTS):
        generated = await asyncio.to_thread(service.generate_question_bank, generation_context)
        reviewed = await asyncio.to_thread(
            service.validate_question_bank,
            {
                "requested_language": request_payload.language,
                "assignments": assignments,
                "source_chunks": public_sources,
                "candidates": [item.model_dump(mode="json") for item in generated.questions],
                "prior_validated_items": [],
            },
        )
        try:
            validated = _validate_ai_items(
                generated.questions,
                reviewed.reviews,
                plan=plan,
                source_context=source_context,
                language=request_payload.language,
            )
            break
        except ApiError as exc:
            retryable_validation_error = exc.code in {
                "question_validation_failed",
                "duplicate_question_family",
            }
            if (
                not retryable_validation_error
                or validation_attempt + 1 >= PRIVATE_ASSESSMENT_VALIDATION_ATTEMPTS
            ):
                raise
    content_questions = []
    actual_concept_ids = set(actual_topic_ids)
    for item in validated:
        current_sources: dict[str, dict[str, Any]] = {}
        for chunk_id in item["source_chunk_revision_ids"]:
            source = source_context[chunk_id]
            current_sources[chunk_id] = source
        content_questions.append(
            {
                "id": item["id"],
                "prompt": item["prompt"],
                "kind": item["kind"],
                "choices": item["choices"],
                "answer": item["answer"],
                "accepted_answers": item["accepted_answers"],
                "explanation": item["explanation"],
                "rubric": item["rubric"],
                "level": item["level"],
                "language": item["language"],
                "family_id": item["family_id"],
                "assessment_target": item["assessment_target"],
                "concept_ids": [
                    concept_id
                    for concept_id in item["concept_ids"]
                    if concept_id in actual_concept_ids
                ],
                "source_chunk_revision_ids": item["source_chunk_revision_ids"],
                "sources": [
                    {
                        "upload_id": source["material_id"],
                        **(
                            {"page": source["locator"]["page"]}
                            if isinstance(source.get("locator"), dict)
                            and isinstance(source["locator"].get("page"), int)
                            and source["locator"]["page"] > 0
                            else {}
                        ),
                        **(
                            {"slide": source["locator"]["slide"]}
                            if isinstance(source.get("locator"), dict)
                            and isinstance(source["locator"].get("slide"), int)
                            and source["locator"]["slide"] > 0
                            else {}
                        ),
                        **(
                            {"timestamp_seconds": source["locator"]["timestamp_start_seconds"]}
                            if isinstance(source.get("locator"), dict)
                            and isinstance(
                                source["locator"].get("timestamp_start_seconds"), (int, float)
                            )
                            else {}
                        ),
                    }
                    for source in current_sources.values()
                ],
            }
        )
    content: dict[str, Any] = {"format": material_kind, "questions": content_questions}
    if material_kind == "practice_exam":
        content["instructions"] = (
            "Answer each question. Feedback follows the assessment release policy."
        )

    async with database.acquire() as connection:
        async with connection.transaction():
            await class_access(connection, class_id, user_id, required="read")
            current_material = await connection.fetchrow(
                "SELECT status,owner_id,origin_class_id,resource_scope FROM study_materials "
                "WHERE id=?1 AND status='generating'",
                material_id,
            )
            if (
                current_material is None
                or current_material["resource_scope"] != "personal"
                or str(current_material["owner_id"]) != user_id
                or str(current_material["origin_class_id"]) != class_id
            ):
                raise ApiError(
                    409, "generation_stale", "This private assessment request is no longer active."
                )
            for item in validated:
                await _validate_current_chunk_refs(
                    connection,
                    class_id=class_id,
                    user_id=user_id,
                    chunk_revision_ids=item["source_chunk_revision_ids"],
                )
            if actual_concept_ids:
                current = await connection.fetch(
                    "SELECT id FROM class_concepts WHERE class_id=?1 "
                    "AND status='validated' AND id IN ("
                    + ",".join(f"?{index + 2}" for index in range(len(actual_concept_ids)))
                    + ")",
                    class_id,
                    *actual_concept_ids,
                )
                if {str(row["id"]) for row in current} != actual_concept_ids:
                    raise ApiError(
                        409,
                        "concept_validation_stale",
                        "A source concept changed while the assessment was being generated.",
                    )
            updated = await connection.fetchrow(
                "UPDATE study_materials SET content=?2,assessment_validation_state='validated',"
                "assessment_validation_version=?3,assessment_engine_version=1,status='ready',failure_code=NULL,"
                "generated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=?1 AND status='generating' RETURNING id",
                material_id,
                json.dumps(content, ensure_ascii=False),
                QUESTION_BANK_VALIDATOR_VERSION,
            )
            if updated is None:
                raise ApiError(
                    409, "generation_stale", "This private assessment request is no longer active."
                )
    return content


async def create_generated_assessment(
    database: SQLiteDatabase,
    *,
    class_id: str,
    user_id: str,
    payload: QuestionBankGenerationRequest,
    service: Any,
    model_name: str,
    persist_assessment: bool = True,
    expected_source_digest: str | None = None,
) -> dict[str, Any]:
    """Generate/reuse validated items and persist a private-key draft material."""
    access = await class_access(database, class_id, user_id, required="create")
    concept_rows = await _load_concepts(
        database,
        class_id=class_id,
        requested_ids=[str(item) for item in payload.concept_ids],
        maximum=50,
    )
    plan = build_coverage_plan(concept_rows, payload)
    if payload.kind == "practice_exam" and not plan:
        raise ApiError(
            409, "exam_coverage_unavailable", "The exam needs validated concepts before generation."
        )
    concepts_by_id = {str(row["id"]): row for row in concept_rows}
    selected_concept_ids = list(concepts_by_id)
    if expected_source_digest is not None:
        source_digest = await current_concept_source_digest(
            database,
            class_id=class_id,
            user_id=user_id,
            concept_ids=selected_concept_ids,
        )
        if source_digest != expected_source_digest:
            raise ApiError(
                409,
                "replenishment_source_superseded",
                "The source changed while this question-bank job was queued.",
            )
    # First reuse eligible validated items; every source is revalidated against
    # current membership, release state, key policy, and indexed source head.
    selected: dict[str, dict[str, Any]] = {}
    used_families: set[str] = set()
    missing_slots: list[dict[str, Any]] = []
    for slot in plan:
        existing = await _eligible_existing_item(
            database,
            class_id=class_id,
            user_id=user_id,
            slot=slot,
            language=payload.language,
        )
        if existing is None or existing["family_signature"] in used_families:
            missing_slots.append(slot)
            continue
        selected[slot["assignment_id"]] = existing
        used_families.add(existing["family_signature"])

    created_items: list[dict[str, Any]] = []
    source_context: dict[str, dict[str, Any]] = {}
    if missing_slots:
        required_concepts = [
            concepts_by_id[cid]
            for cid in dict.fromkeys(slot["concept_id"] for slot in missing_slots)
        ]
        source_context = await _retrieve_sources(
            database,
            class_id=class_id,
            user_id=user_id,
            concepts=required_concepts,
            upload_ids=[str(item) for item in payload.upload_ids],
        )
        assignment_context = [
            {
                **slot,
                "concept_description": concepts_by_id[slot["concept_id"]]["description"],
                "source_chunk_revision_ids": [
                    source_id
                    for source_id, source in source_context.items()
                    if source["concept_id"] == slot["concept_id"]
                ],
            }
            for slot in missing_slots
        ]
        prior_items: list[dict[str, Any]] = []
        concept_level_pairs = list(
            dict.fromkeys((slot["concept_id"], slot["level"]) for slot in missing_slots)
        )
        pair_clauses = " OR ".join(
            f"(c.concept_id=?{index * 2 + 2} AND i.level=?{index * 2 + 3})"
            for index in range(len(concept_level_pairs))
        )
        prior_rows = await database.fetch(
            "SELECT i.prompt,i.level,i.family_signature,i.grading_json,c.concept_id "
            "FROM class_question_bank_items i "
            "JOIN class_question_bank_concepts c ON c.item_id=i.id AND c.class_id=i.class_id "
            "WHERE i.class_id=?1 AND i.state='validated' AND c.relation='primary' AND ("
            + pair_clauses
            + ") ORDER BY i.created_at DESC,i.id LIMIT 100",
            class_id,
            *(value for pair in concept_level_pairs for value in pair),
        )
        for row in prior_rows:
            grading = json_value(row["grading_json"], {})
            prior_items.append(
                {
                    "concept_id": str(row["concept_id"]),
                    "level": int(row["level"]),
                    "family_signature": str(row["family_signature"]),
                    "prompt": str(row["prompt"]),
                    "assessment_target": str(grading.get("assessment_target", "")),
                }
            )
        if any(not slot["source_chunk_revision_ids"] for slot in assignment_context):
            raise ApiError(
                409,
                "source_chunks_unavailable",
                "A concept has no current supporting source chunks.",
            )
        generation_context = {
            "kind": payload.kind,
            "requested_language": payload.language,
            "optional_instructions": payload.additional_instructions or "",
            "assignments": assignment_context,
            "source_chunks": [
                {key: value for key, value in chunk.items() if key != "concept_id"}
                for chunk in source_context.values()
            ],
        }
        generated = await asyncio.to_thread(service.generate_question_bank, generation_context)
        candidate_items = generated.questions
        review_context = {
            "requested_language": payload.language,
            "assignments": assignment_context,
            "source_chunks": [
                {key: value for key, value in chunk.items() if key != "concept_id"}
                for chunk in source_context.values()
            ],
            "candidates": [item.model_dump(mode="json") for item in candidate_items],
            "prior_validated_items": prior_items,
        }
        reviewed = await asyncio.to_thread(service.validate_question_bank, review_context)
        created_items = _validate_ai_items(
            candidate_items,
            reviewed.reviews,
            plan=missing_slots,
            source_context=source_context,
            language=payload.language,
            prior_items=prior_items,
        )
        for item in created_items:
            if item["family_signature"] in used_families:
                raise ApiError(
                    409,
                    "duplicate_question_family",
                    "The generated set repeats an existing assessment objective. Retry generation.",
                )
            prior = await database.fetchval(
                "SELECT id FROM class_question_bank_items WHERE class_id=?1 "
                "AND family_signature=?2 AND state='validated' LIMIT 1",
                class_id,
                item["family_signature"],
            )
            if prior is not None:
                raise ApiError(
                    409,
                    "duplicate_question_family",
                    "This objective already exists in the question bank. Retry generation.",
                )
            used_families.add(item["family_signature"])
        selected.update(
            {
                slot["assignment_id"]: item
                for slot, item in zip(missing_slots, created_items, strict=True)
            }
        )

    ordered_items = [selected[slot["assignment_id"]] for slot in plan]
    release_policy = payload.feedback_release_policy or (
        "manual" if payload.kind == "practice_exam" else "after_completion"
    )
    material_id = str(uuid4())
    content_questions: list[dict[str, Any]] = []
    source_map: dict[str, dict[str, Any]] = {}
    for item in ordered_items:
        chunk_ids = item["source_chunk_revision_ids"]
        if item.get("bank_item_id"):
            refs = await _validate_current_chunk_refs(
                database,
                class_id=class_id,
                user_id=user_id,
                chunk_revision_ids=chunk_ids,
            )
            current_sources = {
                chunk_id: {
                    "chunk_revision_id": chunk_id,
                    "material_id": ref["note_id"],
                    "title": ref["title"],
                    "locator": json_value(ref["locator"], {}),
                    "text": "",
                }
                for chunk_id, ref in refs.items()
            }
        else:
            current_sources = {chunk_id: source_context[chunk_id] for chunk_id in chunk_ids}
        for chunk_id, source in current_sources.items():
            source_map.setdefault(chunk_id, source)
        material_question = {
            "id": item["id"],
            "prompt": item["prompt"],
            "kind": item["kind"],
            "choices": item["choices"],
            "answer": item["answer"],
            "accepted_answers": item["accepted_answers"],
            "explanation": item["explanation"],
            "rubric": item["rubric"],
            "level": item["level"],
            "language": item["language"],
            "family_id": item["family_id"],
            "assessment_target": item.get("assessment_target", ""),
            "concept_ids": item["concept_ids"],
            "source_chunk_revision_ids": chunk_ids,
            "sources": [
                {
                    "upload_id": source["material_id"],
                    **(
                        {"page": source["locator"]["page"]}
                        if isinstance(source["locator"], dict)
                        and isinstance(source["locator"].get("page"), int)
                        and source["locator"]["page"] > 0
                        else {}
                    ),
                    **(
                        {"slide": source["locator"]["slide"]}
                        if isinstance(source["locator"], dict)
                        and isinstance(source["locator"].get("slide"), int)
                        and source["locator"]["slide"] > 0
                        else {}
                    ),
                }
                for source in current_sources.values()
            ],
        }
        content_questions.append(material_question)

    content: dict[str, Any] = {"format": payload.kind, "questions": content_questions}
    if payload.kind == "practice_exam":
        content["instructions"] = (
            "Answer each question. Feedback follows the assessment release policy."
        )
    async with database.acquire() as connection:
        async with connection.transaction():
            # Authorization and citations must still be valid at commit. The
            # model calls above deliberately run without a transaction, so a
            # membership revocation, permission change, source edit, or release
            # change may have happened while they were in flight.
            access = await class_access(connection, class_id, user_id, required="create")
            current_concepts = await connection.fetch(
                "SELECT id FROM class_concepts WHERE class_id=?1 AND status='validated' "
                "AND id IN ("
                + ",".join(f"?{index + 2}" for index in range(len(concepts_by_id)))
                + ")",
                class_id,
                *concepts_by_id.keys(),
            )
            if {str(row["id"]) for row in current_concepts} != set(concepts_by_id):
                raise ApiError(
                    409,
                    "concept_validation_stale",
                    "A selected concept changed while the assessment was being generated.",
                )
            if expected_source_digest is not None:
                source_digest = await current_concept_source_digest(
                    connection,
                    class_id=class_id,
                    user_id=user_id,
                    concept_ids=selected_concept_ids,
                )
                if source_digest != expected_source_digest:
                    raise ApiError(
                        409,
                        "replenishment_source_superseded",
                        "The source changed while questions were being generated.",
                    )
            for item in ordered_items:
                source_ids = [str(value) for value in item["source_chunk_revision_ids"]]
                await _validate_current_chunk_refs(
                    connection,
                    class_id=class_id,
                    user_id=user_id,
                    chunk_revision_ids=source_ids,
                )
                bank_item_id = item.get("bank_item_id")
                if bank_item_id:
                    current_item = await connection.fetchrow(
                        "SELECT state,family_signature FROM class_question_bank_items "
                        "WHERE id=?1 AND class_id=?2",
                        bank_item_id,
                        class_id,
                    )
                    if (
                        current_item is None
                        or current_item["state"] != "validated"
                        or current_item["family_signature"] != item["family_signature"]
                    ):
                        raise ApiError(
                            409,
                            "question_item_stale",
                            "A selected question is no longer available. Retry generation.",
                        )
                else:
                    duplicate = await connection.fetchrow(
                        "SELECT id FROM class_question_bank_items WHERE class_id=?1 "
                        "AND family_signature=?2 AND state='validated' LIMIT 1",
                        class_id,
                        item["family_signature"],
                    )
                    if duplicate is not None:
                        raise ApiError(
                            409,
                            "duplicate_question_family",
                            "This objective was added while the assessment was being generated. "
                            "Retry generation.",
                        )
            if persist_assessment:
                await connection.execute(
                    "INSERT INTO study_materials "
                    "(id,class_id,origin_class_id,resource_scope,owner_id,created_by,publication_state,"
                    "feedback_release_policy,assessment_validation_state,assessment_engine_version,kind,title,status,scope,options,generation_instructions,"
                    "content,generated_at) VALUES (?1,?2,?2,'student_contribution',NULL,?3,'draft',"
                    "?4,'validated',1,?5,?6,'ready','{\"kind\":\"all\"}',?7,?8,?9,"
                    "strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                    material_id,
                    class_id,
                    user_id,
                    release_policy,
                    payload.kind,
                    f"{payload.kind.replace('_', ' ').title()} — {access['name']}",
                    json.dumps({"count": payload.count, "question_types": payload.question_types}),
                    payload.additional_instructions,
                    json.dumps(content, ensure_ascii=False),
                )
            inserted_ids: set[str] = set()
            for item in created_items:
                await connection.execute(
                    "INSERT INTO class_question_bank_items "
                    "(id,class_id,family_id,family_signature,prompt,kind,level,language,choices_json,"
                    "grading_json,state,validator_version,generation_model,generation_version,"
                    "created_by) "
                    "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,'validated',?11,?12,?13,?14)",
                    item["id"],
                    class_id,
                    item["family_id"],
                    item["family_signature"],
                    item["prompt"],
                    item["kind"],
                    item["level"],
                    item["language"],
                    json.dumps(item["choices"], ensure_ascii=False),
                    json.dumps(
                        {
                            key: item[key]
                            for key in (
                                "answer",
                                "accepted_answers",
                                "explanation",
                                "rubric",
                                "assessment_target",
                            )
                        },
                        ensure_ascii=False,
                    ),
                    QUESTION_BANK_VALIDATOR_VERSION,
                    model_name,
                    QUESTION_BANK_GENERATION_VERSION + "+" + QUESTION_BANK_VALIDATION_VERSION,
                    user_id,
                )
                inserted_ids.add(item["id"])
                await connection.executemany(
                    "INSERT INTO class_question_bank_concepts"
                    "(item_id,class_id,concept_id,relation) "
                    "VALUES (?1,?2,?3,?4)",
                    [
                        (
                            item["id"],
                            class_id,
                            concept_id,
                            "primary" if concept_id == item["primary_concept_id"] else "secondary",
                        )
                        for concept_id in item["concept_ids"]
                    ],
                )
                await connection.executemany(
                    "INSERT INTO class_question_bank_sources"
                    "(item_id,class_id,chunk_revision_id,source_order) "
                    "VALUES (?1,?2,?3,?4)",
                    [
                        (item["id"], class_id, chunk_id, index)
                        for index, chunk_id in enumerate(item["source_chunk_revision_ids"])
                    ],
                )
            for item in ordered_items:
                if item.get("bank_item_id"):
                    continue
                # Newly generated item ID is already its bank row ID.
            if persist_assessment:
                await connection.executemany(
                    "INSERT INTO study_material_sources "
                    "(material_id,source_class_id,source_note_id,source_title,source_kind,position,"
                    "class_id,note_id) "
                    "VALUES (?1,?2,?3,?4,'document',?5,?2,?3)",
                    [
                        (material_id, class_id, source["material_id"], source["title"], index)
                        for index, source in enumerate(
                            {
                                str(value["material_id"]): value for value in source_map.values()
                            }.values()
                        )
                    ],
                )
            return {
                "material_id": material_id if persist_assessment else None,
                "kind": payload.kind,
                "status": "ready",
                "item_count": len(ordered_items),
                "publication_state": "draft",
                "feedback_release_policy": release_policy,
                "created_bank_item_count": len(inserted_ids),
                "reused_bank_item_count": len(ordered_items) - len(inserted_ids),
            }
