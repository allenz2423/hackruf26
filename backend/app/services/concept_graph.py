"""Class-scoped, source-backed concepts and relationships with proposal review."""

from __future__ import annotations

import asyncio
import logging
import unicodedata
from typing import Any
from uuid import uuid4

from app.api.errors import ApiError
from app.api.schemas import (
    ConceptCreate,
    ConceptEdgeCreate,
    ConceptGraphAIOutput,
    ConceptMerge,
    ConceptPatch,
)
from app.services.class_access import (
    can_view_assessment_key,
    can_view_unreleased,
    class_access,
    is_class_manager,
    is_effectively_published,
    is_private_assessment,
)
from app.services.concept_graph_prompts import CONCEPT_GRAPH_EXTRACTION_VERSION
from app.services.database import SQLiteConnection, SQLiteDatabase, json_value

logger = logging.getLogger("screen_tutor.concept_graph")

MAX_EXTRACTION_JOBS_PER_BATCH = 2
MAX_EXTRACTION_CHUNKS = 24
MAX_EXTRACTION_CHARS = 32_000
MAX_EXISTING_CONCEPTS = 80
MAX_SOURCE_REFS_PER_ITEM = 8
MAX_EXTRACTION_RETRY_DELAY_SECONDS = 30


def normalize_concept_name(value: str) -> str:
    """Normalize Unicode compatibility forms, case, and whitespace for identity matching."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


async def _manager_access(
    database: SQLiteDatabase | SQLiteConnection,
    class_id: str,
    user_id: str,
) -> dict[str, Any]:
    access = await class_access(database, class_id, user_id, required="read")
    if not is_class_manager(access):
        raise ApiError(403, "insufficient_permission", "A class manager must do that.")
    return access


def _check_version(expected: int, actual: int) -> None:
    if expected != actual:
        raise ApiError(409, "version_conflict", "This graph item changed. Refresh and try again.")


def _is_current_release_visible(access: dict[str, Any], user_id: str, row: dict[str, Any]) -> bool:
    return bool(
        is_effectively_published(row.get("publication_state"), row.get("release_at"))
        or can_view_unreleased(access, user_id, row.get("created_by"))
    )


async def _validate_current_chunk_refs(
    connection: SQLiteConnection,
    *,
    class_id: str,
    user_id: str,
    chunk_revision_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Validate source refs against the current indexed revision and live class policy."""
    if len(chunk_revision_ids) > MAX_SOURCE_REFS_PER_ITEM:
        raise ApiError(400, "too_many_source_references", "Use at most eight source references.")
    access = await class_access(connection, class_id, user_id, required="read")
    result: dict[str, dict[str, Any]] = {}
    for chunk_id in dict.fromkeys(chunk_revision_ids):
        row = await connection.fetchrow(
            "SELECT cr.id AS chunk_revision_id,cr.revision_id,cr.text,cr.locator,cr.provenance,"
            "n.id AS note_id,n.title,n.created_by,n.publication_state,n.release_at,"
            "n.status,n.source_index_status,n.processing_revision,n.resource_scope,"
            "h.processing_revision AS indexed_processing_revision,"
            "h.revision_id AS current_revision_id,a.id AS assessment_id,"
            "a.owner_id AS assessment_owner,"
            "a.has_student_responses,a.score_percent,a.grading_result,a.answer_key_release_state,"
            "a.answer_key_release_at "
            "FROM source_chunk_revisions cr JOIN source_revisions r ON r.id=cr.revision_id "
            "JOIN course_notes n ON n.id=r.note_id "
            "JOIN source_index_heads h ON h.note_id=n.id "
            "LEFT JOIN uploaded_assessments a ON a.note_id=n.id "
            "WHERE cr.id=?1 AND n.class_id=?2",
            chunk_id,
            class_id,
        )
        if row is None:
            cross_class = await connection.fetchrow(
                "SELECT n.class_id FROM source_chunk_revisions cr "
                "JOIN source_revisions r ON r.id=cr.revision_id "
                "JOIN course_notes n ON n.id=r.note_id WHERE cr.id=?1",
                chunk_id,
            )
            if cross_class is not None and str(cross_class["class_id"]) != class_id:
                raise ApiError(
                    400,
                    "cross_class_source_reference",
                    "A source reference must belong to this class.",
                )
            raise ApiError(404, "source_reference_not_found", "A source reference was not found.")
        if row["status"] != "ready" or row["provenance"] != "source":
            raise ApiError(404, "source_reference_not_found", "A source reference was not found.")
        if (
            row["source_index_status"] != "ready"
            or row["indexed_processing_revision"] != row["processing_revision"]
            or row["revision_id"] != row["current_revision_id"]
        ):
            raise ApiError(
                409,
                "stale_source_reference",
                "A source changed. Refresh the citation and try again.",
            )
        if not _is_current_release_visible(access, user_id, row):
            raise ApiError(404, "source_reference_not_found", "A source reference was not found.")
        if row["assessment_id"] is not None:
            assessment = {
                "owner_id": row["assessment_owner"],
                "has_student_responses": row["has_student_responses"],
                "score_percent": row["score_percent"],
                "grading_result": row["grading_result"],
            }
            if is_private_assessment(assessment) or not can_view_assessment_key(
                {
                    **assessment,
                    "answer_key_release_state": row["answer_key_release_state"],
                    "answer_key_release_at": row["answer_key_release_at"],
                    "created_by": row["created_by"],
                    "resource_scope": row["resource_scope"],
                },
                access,
                user_id,
            ):
                raise ApiError(
                    404, "source_reference_not_found", "A source reference was not found."
                )
        result[chunk_id] = row
    return result


async def _add_source_refs(
    connection: SQLiteConnection,
    *,
    class_id: str,
    chunk_ids: list[str],
    concept_id: str | None = None,
    edge_id: str | None = None,
) -> None:
    for chunk_id in dict.fromkeys(chunk_ids):
        await connection.execute(
            "INSERT OR IGNORE INTO class_concept_source_refs "
            "(id,class_id,concept_id,edge_id,chunk_revision_id) VALUES (?1,?2,?3,?4,?5)",
            uuid4(),
            class_id,
            concept_id,
            edge_id,
            chunk_id,
        )


async def _concept_row(
    connection: SQLiteConnection, class_id: str, concept_id: str
) -> dict[str, Any] | None:
    return await connection.fetchrow(
        "SELECT * FROM class_concepts WHERE id=?1 AND class_id=?2",
        concept_id,
        class_id,
    )


async def create_concept(
    database: SQLiteDatabase,
    *,
    class_id: str,
    user_id: str,
    payload: ConceptCreate,
) -> dict[str, Any]:
    normalized = normalize_concept_name(payload.canonical_name)
    if not normalized:
        raise ApiError(400, "invalid_concept_name", "Enter a concept name.")
    async with database.acquire() as connection:
        async with connection.transaction():
            await _manager_access(connection, class_id, user_id)
            await _validate_current_chunk_refs(
                connection,
                class_id=class_id,
                user_id=user_id,
                chunk_revision_ids=[str(value) for value in payload.source_chunk_revision_ids],
            )
            if payload.parent_concept_id is not None:
                parent = await _concept_row(connection, class_id, str(payload.parent_concept_id))
                if parent is None:
                    other_class = await connection.fetchrow(
                        "SELECT class_id FROM class_concepts WHERE id=?1", payload.parent_concept_id
                    )
                    if other_class is not None:
                        raise ApiError(
                            400,
                            "cross_class_concept",
                            "A parent concept must belong to this class.",
                        )
                    raise ApiError(404, "concept_not_found", "The parent concept was not found.")
                if parent["status"] in {"merged", "rejected"}:
                    raise ApiError(409, "concept_unavailable", "Choose an active parent concept.")
            duplicate = await _find_name_owner(connection, class_id, normalized)
            if duplicate is not None:
                raise ApiError(
                    409, "duplicate_concept_name", "A concept with this name already exists."
                )
            concept_id = str(uuid4())
            await connection.execute(
                "INSERT INTO class_concepts "
                "(id,class_id,canonical_name,normalized_name,description,parent_concep"
                "t_id,status,provenance,created_by) "
                "VALUES (?1,?2,?3,?4,?5,?6,'proposed','manual',?7)",
                concept_id,
                class_id,
                payload.canonical_name,
                normalized,
                payload.description,
                payload.parent_concept_id,
                user_id,
            )
            await _add_source_refs(
                connection,
                class_id=class_id,
                concept_id=concept_id,
                chunk_ids=[str(value) for value in payload.source_chunk_revision_ids],
            )
            return await _concept_dto(connection, concept_id, class_id)


async def _find_name_owner(
    connection: SQLiteConnection, class_id: str, normalized: str, *, exclude_id: str | None = None
) -> dict[str, Any] | None:
    row = await connection.fetchrow(
        "SELECT id,status FROM class_concepts WHERE class_id=?1 AND normalized_name=?2 "
        "AND status IN ('proposed','validated','deprecated') AND (?3 IS NULL OR id<>?3) LIMIT 1",
        class_id,
        normalized,
        exclude_id,
    )
    if row is not None:
        return row
    return await connection.fetchrow(
        "SELECT a.concept_id AS id,c.status FROM class_concept_aliases a "
        "JOIN class_concepts c ON c.id=a.concept_id AND c.class_id=a.class_id "
        "WHERE a.class_id=?1 AND a.normalized_name=?2 AND a.status IN ('proposed','validated') "
        "AND c.status IN ('proposed','validated','deprecated') "
        "AND (?3 IS NULL OR a.concept_id<>?3) LIMIT 1",
        class_id,
        normalized,
        exclude_id,
    )


async def _concept_dto(
    connection: SQLiteConnection, concept_id: str, class_id: str
) -> dict[str, Any]:
    row = await _concept_row(connection, class_id, concept_id)
    if row is None:
        raise ApiError(404, "concept_not_found", "The concept was not found.")
    aliases = await connection.fetch(
        "SELECT id,name,language,status FROM class_concept_aliases WHERE concept_id=?1 "
        "AND status IN ('proposed','validated') ORDER BY normalized_name",
        concept_id,
    )
    return {
        "id": row["id"],
        "class_id": row["class_id"],
        "canonical_name": row["canonical_name"],
        "description": row["description"],
        "parent_concept_id": row["parent_concept_id"],
        "status": row["status"],
        "state": row["status"],
        "provenance": row["provenance"],
        "version": row["version"],
        "merged_into_id": row["merged_into_id"],
        "aliases": aliases,
        "alias_proposals": [alias for alias in aliases if alias["status"] == "proposed"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def update_concept(
    database: SQLiteDatabase,
    *,
    class_id: str,
    user_id: str,
    concept_id: str,
    payload: ConceptPatch,
) -> dict[str, Any]:
    patch = payload.model_dump(exclude_unset=True)
    patch.pop("expected_version")
    async with database.acquire() as connection:
        async with connection.transaction():
            await _manager_access(connection, class_id, user_id)
            row = await _concept_row(connection, class_id, concept_id)
            if row is None:
                raise ApiError(404, "concept_not_found", "The concept was not found.")
            _check_version(payload.expected_version, row["version"])
            if row["status"] in {"merged", "rejected"}:
                raise ApiError(409, "concept_unavailable", "This concept can no longer be edited.")
            updates: dict[str, Any] = {}
            if "canonical_name" in patch:
                normalized = normalize_concept_name(payload.canonical_name or "")
                if not normalized:
                    raise ApiError(400, "invalid_concept_name", "Enter a concept name.")
                if await _find_name_owner(connection, class_id, normalized, exclude_id=concept_id):
                    raise ApiError(
                        409, "duplicate_concept_name", "A concept with this name already exists."
                    )
                own_alias = await connection.fetchrow(
                    "SELECT id FROM class_concept_aliases WHERE concept_id=?1 AND "
                    "normalized_name=?2 "
                    "AND status IN ('proposed','validated') LIMIT 1",
                    concept_id,
                    normalized,
                )
                if own_alias:
                    raise ApiError(
                        409,
                        "duplicate_concept_name",
                        "That name is already an alias of this concept.",
                    )
                updates["canonical_name"] = payload.canonical_name
                updates["normalized_name"] = normalized
            if "description" in patch:
                updates["description"] = payload.description
            if "parent_concept_id" in patch:
                parent_id = str(payload.parent_concept_id) if payload.parent_concept_id else None
                if parent_id == concept_id:
                    raise ApiError(
                        400, "concept_parent_cycle", "A concept cannot be its own parent."
                    )
                if parent_id is not None:
                    parent = await _concept_row(connection, class_id, parent_id)
                    if parent is None:
                        other = await connection.fetchrow(
                            "SELECT class_id FROM class_concepts WHERE id=?1", parent_id
                        )
                        if other is not None:
                            raise ApiError(
                                400,
                                "cross_class_concept",
                                "A parent concept must belong to this class.",
                            )
                        raise ApiError(
                            404, "concept_not_found", "The parent concept was not found."
                        )
                    if parent["status"] in {"merged", "rejected"}:
                        raise ApiError(
                            409, "concept_unavailable", "Choose an active parent concept."
                        )
                    if await _would_parent_cycle(connection, class_id, concept_id, parent_id):
                        raise ApiError(
                            409,
                            "concept_parent_cycle",
                            "This parent would create a concept hierarchy cycle.",
                        )
                updates["parent_concept_id"] = parent_id
            assignments = ",".join(f"{key}=?{index + 2}" for index, key in enumerate(updates))
            if not assignments:
                raise ApiError(400, "empty_update", "Provide at least one field to update.")
            await connection.execute(
                f"UPDATE class_concepts SET {assignments},version=version+1,"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                f"WHERE id=?1 AND class_id=?{len(updates) + 2} AND version=?{len(updates) + 3}",
                concept_id,
                *updates.values(),
                class_id,
                payload.expected_version,
            )
            return await _concept_dto(connection, concept_id, class_id)


async def _would_parent_cycle(
    connection: SQLiteConnection, class_id: str, concept_id: str, parent_id: str
) -> bool:
    return bool(
        await connection.fetchval(
            "WITH RECURSIVE ancestors(id) AS (SELECT ?1 UNION "
            "SELECT c.parent_concept_id FROM class_concepts c JOIN ancestors a ON c.id=a.id "
            "WHERE c.class_id=?2 AND c.parent_concept_id IS NOT NULL) "
            "SELECT EXISTS(SELECT 1 FROM ancestors WHERE id=?3)",
            parent_id,
            class_id,
            concept_id,
        )
    )


async def _is_parent_ancestor(
    connection: SQLiteConnection, class_id: str, ancestor_id: str, concept_id: str
) -> bool:
    return bool(
        await connection.fetchval(
            "WITH RECURSIVE ancestors(id) AS (SELECT parent_concept_id FROM class_concepts "
            "WHERE id=?1 AND class_id=?2 AND parent_concept_id IS NOT NULL UNION "
            "SELECT c.parent_concept_id FROM class_concepts c JOIN ancestors a ON c.id=a.id "
            "WHERE c.class_id=?2 AND c.parent_concept_id IS NOT NULL) "
            "SELECT EXISTS(SELECT 1 FROM ancestors WHERE id=?3)",
            concept_id,
            class_id,
            ancestor_id,
        )
    )


async def add_concept_alias(
    database: SQLiteDatabase,
    *,
    class_id: str,
    user_id: str,
    concept_id: str,
    name: str,
    language: str,
) -> dict[str, Any]:
    normalized = normalize_concept_name(name)
    if not normalized:
        raise ApiError(400, "invalid_concept_name", "Enter an alias.")
    async with database.acquire() as connection:
        async with connection.transaction():
            await _manager_access(connection, class_id, user_id)
            concept = await _concept_row(connection, class_id, concept_id)
            if concept is None:
                raise ApiError(404, "concept_not_found", "The concept was not found.")
            if concept["status"] in {"merged", "rejected"}:
                raise ApiError(409, "concept_unavailable", "This concept can no longer be edited.")
            if normalized == concept["normalized_name"] or await _find_name_owner(
                connection, class_id, normalized
            ):
                raise ApiError(
                    409, "duplicate_concept_name", "That name is already used in this class."
                )
            alias_id = str(uuid4())
            await connection.execute(
                "INSERT INTO class_concept_aliases "
                "(id,class_id,concept_id,name,normalized_name,language,status,created_by) "
                "VALUES (?1,?2,?3,?4,?5,?6,?7,?8)",
                alias_id,
                class_id,
                concept_id,
                name,
                normalized,
                language,
                "validated" if concept["status"] == "validated" else "proposed",
                user_id,
            )
            await connection.execute(
                "UPDATE class_concepts SET "
                "version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=?1 AND class_id=?2",
                concept_id,
                class_id,
            )
            return await _concept_dto(connection, concept_id, class_id)


async def delete_concept_alias(
    database: SQLiteDatabase,
    *,
    class_id: str,
    user_id: str,
    concept_id: str,
    alias_id: str,
    expected_version: int,
) -> dict[str, Any]:
    async with database.acquire() as connection:
        async with connection.transaction():
            await _manager_access(connection, class_id, user_id)
            concept = await _concept_row(connection, class_id, concept_id)
            if concept is None:
                raise ApiError(404, "concept_not_found", "The concept was not found.")
            _check_version(expected_version, concept["version"])
            result = await connection.execute(
                "DELETE FROM class_concept_aliases WHERE id=?1 AND class_id=?2 AND concept_id=?3",
                alias_id,
                class_id,
                concept_id,
            )
            if result.endswith(" 0"):
                raise ApiError(404, "concept_alias_not_found", "The alias was not found.")
            await connection.execute(
                "UPDATE class_concepts SET "
                "version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=?1 AND class_id=?2",
                concept_id,
                class_id,
            )
            return await _concept_dto(connection, concept_id, class_id)


async def decide_alias_proposal(
    database: SQLiteDatabase,
    *,
    class_id: str,
    user_id: str,
    concept_id: str,
    alias_id: str,
    decision: str,
    expected_version: int,
) -> dict[str, Any]:
    async with database.acquire() as connection:
        async with connection.transaction():
            await _manager_access(connection, class_id, user_id)
            concept = await _concept_row(connection, class_id, concept_id)
            if concept is None:
                raise ApiError(404, "concept_not_found", "The concept was not found.")
            _check_version(expected_version, concept["version"])
            if concept["status"] not in {"validated", "proposed"}:
                raise ApiError(409, "concept_unavailable", "This concept can no longer be edited.")
            alias = await connection.fetchrow(
                "SELECT id,status FROM class_concept_aliases WHERE id=?1 AND "
                "concept_id=?2 AND class_id=?3",
                alias_id,
                concept_id,
                class_id,
            )
            if alias is None or alias["status"] != "proposed":
                raise ApiError(404, "concept_alias_not_found", "The alias proposal was not found.")
            status = (
                "validated"
                if decision == "approve"
                else "rejected"
                if decision == "reject"
                else None
            )
            if status is None:
                raise ApiError(400, "invalid_decision", "Choose approve or reject.")
            if status == "validated":
                # Extracted aliases inherit the matched concept's source support.
                # Revalidate that support at approval time so an alias proposal
                # cannot outlive a source revision or release/permission change.
                refs = await connection.fetch(
                    "SELECT chunk_revision_id FROM class_concept_source_refs "
                    "WHERE concept_id=?1 ORDER BY chunk_revision_id",
                    concept_id,
                )
                if refs:
                    await _validate_current_chunk_refs(
                        connection,
                        class_id=class_id,
                        user_id=user_id,
                        chunk_revision_ids=[item["chunk_revision_id"] for item in refs],
                    )
            await connection.execute(
                "UPDATE class_concept_aliases SET status=?1 WHERE id=?2 AND class_id=?3",
                status,
                alias_id,
                class_id,
            )
            await connection.execute(
                "UPDATE class_concepts SET "
                "version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=?1 AND class_id=?2 AND version=?3",
                concept_id,
                class_id,
                expected_version,
            )
            return await _concept_dto(connection, concept_id, class_id)


async def decide_concept(
    database: SQLiteDatabase,
    *,
    class_id: str,
    user_id: str,
    concept_id: str,
    decision: str,
    expected_version: int,
) -> dict[str, Any]:
    async with database.acquire() as connection:
        async with connection.transaction():
            await _manager_access(connection, class_id, user_id)
            row = await _concept_row(connection, class_id, concept_id)
            if row is None:
                raise ApiError(404, "concept_not_found", "The concept was not found.")
            _check_version(expected_version, row["version"])
            if decision == "deprecate":
                if row["status"] != "validated":
                    raise ApiError(
                        409, "concept_not_validated", "Only a validated concept can be deprecated."
                    )
                status = "deprecated"
            elif row["status"] != "proposed":
                raise ApiError(
                    409, "concept_not_proposed", "Only a proposed concept can be reviewed."
                )
            elif decision == "approve":
                refs = await connection.fetch(
                    "SELECT chunk_revision_id FROM class_concept_source_refs WHERE concept_id=?1",
                    concept_id,
                )
                if refs:
                    await _validate_current_chunk_refs(
                        connection,
                        class_id=class_id,
                        user_id=user_id,
                        chunk_revision_ids=[item["chunk_revision_id"] for item in refs],
                    )
                if row["parent_concept_id"] and await _would_parent_cycle(
                    connection, class_id, concept_id, row["parent_concept_id"]
                ):
                    raise ApiError(
                        409, "concept_parent_cycle", "This concept hierarchy contains a cycle."
                    )
                status = "validated"
            elif decision == "reject":
                status = "rejected"
            else:
                raise ApiError(400, "invalid_decision", "Choose approve or reject.")
            await connection.execute(
                "UPDATE class_concepts SET status=?1,version=version+1,"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=?2 AND class_id=?3 AND version=?4",
                status,
                concept_id,
                class_id,
                expected_version,
            )
            await connection.execute(
                "UPDATE class_concept_aliases SET status=?1 WHERE concept_id=?2 AND "
                "status='proposed'",
                "validated" if status == "validated" else "rejected",
                concept_id,
            )
            return await _concept_dto(connection, concept_id, class_id)


async def merge_concept(
    database: SQLiteDatabase,
    *,
    class_id: str,
    user_id: str,
    concept_id: str,
    payload: ConceptMerge,
) -> dict[str, Any]:
    target_id = str(payload.target_concept_id)
    if target_id == concept_id:
        raise ApiError(400, "self_merge", "A concept cannot be merged into itself.")
    async with database.acquire() as connection:
        async with connection.transaction():
            await _manager_access(connection, class_id, user_id)
            source = await _concept_row(connection, class_id, concept_id)
            target = await _concept_row(connection, class_id, target_id)
            if source is None:
                raise ApiError(404, "concept_not_found", "The source concept was not found.")
            if target is None:
                other = await connection.fetchrow(
                    "SELECT class_id FROM class_concepts WHERE id=?1", target_id
                )
                if other is not None:
                    raise ApiError(
                        400, "cross_class_concept", "Concepts can only merge within one class."
                    )
                raise ApiError(404, "concept_not_found", "The target concept was not found.")
            _check_version(payload.expected_version, source["version"])
            if source["status"] in {"merged", "rejected"} or target["status"] in {
                "merged",
                "rejected",
            }:
                raise ApiError(409, "concept_unavailable", "Choose active concepts to merge.")
            if await _is_parent_ancestor(connection, class_id, concept_id, target_id):
                raise ApiError(
                    409,
                    "concept_parent_cycle",
                    "A concept cannot be merged into one of its descendants.",
                )
            aliases = await connection.fetch(
                "SELECT * FROM class_concept_aliases WHERE concept_id=?1 ORDER BY normalized_name",
                concept_id,
            )
            for alias in aliases:
                collision = await _find_name_owner(
                    connection, class_id, alias["normalized_name"], exclude_id=concept_id
                )
                if collision and collision["id"] != target_id:
                    raise ApiError(
                        409, "duplicate_concept_name", "An alias conflicts with another concept."
                    )
            await connection.execute(
                "INSERT INTO class_concept_merge_history "
                "(id,class_id,source_concept_id,target_concept_id,source_name,source_d"
                "escription,source_version,merged_by) "
                "VALUES (?1,?2,?3,?4,?5,?6,?7,?8)",
                uuid4(),
                class_id,
                concept_id,
                target_id,
                source["canonical_name"],
                source["description"],
                source["version"],
                user_id,
            )
            await connection.execute(
                "UPDATE class_concepts SET parent_concept_id=?1,version=version+1 "
                "WHERE class_id=?2 AND parent_concept_id=?3",
                target_id,
                class_id,
                concept_id,
            )
            touching = await connection.fetch(
                "SELECT * FROM class_concept_edges WHERE class_id=?1 AND status IN "
                "('proposed','validated') "
                "AND (from_concept_id=?2 OR to_concept_id=?2) ORDER BY created_at,id",
                class_id,
                concept_id,
            )
            for edge in touching:
                new_from = (
                    target_id if edge["from_concept_id"] == concept_id else edge["from_concept_id"]
                )
                new_to = target_id if edge["to_concept_id"] == concept_id else edge["to_concept_id"]
                if new_from == new_to:
                    await connection.execute(
                        "UPDATE class_concept_edges SET status='rejected',version=version+1 "
                        "WHERE id=?1 AND class_id=?2",
                        edge["id"],
                        class_id,
                    )
                    continue
                duplicate = await connection.fetchrow(
                    "SELECT id FROM class_concept_edges WHERE class_id=?1 AND from_concept_id=?2 "
                    "AND to_concept_id=?3 AND type=?4 AND status IN "
                    "('proposed','validated') AND id<>?5",
                    class_id,
                    new_from,
                    new_to,
                    edge["type"],
                    edge["id"],
                )
                if duplicate:
                    await connection.execute(
                        "UPDATE class_concept_edges SET status='rejected',version=version+1 "
                        "WHERE id=?1 AND class_id=?2",
                        edge["id"],
                        class_id,
                    )
                    continue
                await connection.execute(
                    "UPDATE class_concept_edges SET "
                    "from_concept_id=?1,to_concept_id=?2,version=version+1,"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?3 AND class_id=?4",
                    new_from,
                    new_to,
                    edge["id"],
                    class_id,
                )
            if await _has_validated_prerequisite_cycle(connection, class_id):
                raise ApiError(
                    409, "prerequisite_cycle", "This merge would create a prerequisite cycle."
                )
            for alias in aliases:
                same = await connection.fetchrow(
                    "SELECT id FROM class_concept_aliases WHERE class_id=?1 AND normalized_name=?2 "
                    "AND id<>?3 AND status IN ('proposed','validated')",
                    class_id,
                    alias["normalized_name"],
                    alias["id"],
                )
                target_canonical = normalize_concept_name(target["canonical_name"])
                if same or alias["normalized_name"] == target_canonical:
                    await connection.execute(
                        "DELETE FROM class_concept_aliases WHERE id=?1", alias["id"]
                    )
                else:
                    await connection.execute(
                        "UPDATE class_concept_aliases SET concept_id=?1 WHERE id=?2",
                        target_id,
                        alias["id"],
                    )
            await connection.execute(
                "UPDATE class_concepts SET status='merged',merged_into_id=?1,version=version+1,"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=?2 AND class_id=?3 AND version=?4",
                target_id,
                concept_id,
                class_id,
                payload.expected_version,
            )
            await connection.execute(
                "UPDATE class_concepts SET "
                "version=version+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=?1 AND class_id=?2",
                target_id,
                class_id,
            )
            return await _concept_dto(connection, target_id, class_id)


async def _edge_review_flags(connection: SQLiteConnection, edge: dict[str, Any]) -> list[str]:
    if edge["type"] != "prerequisite_for":
        return []
    reverse = await connection.fetchval(
        "SELECT EXISTS(SELECT 1 FROM class_concept_edges WHERE class_id=?1 AND "
        "from_concept_id=?2 AND to_concept_id=?3 AND type='prerequisite_for' "
        "AND status IN ('proposed','validated'))",
        edge["class_id"],
        edge["to_concept_id"],
        edge["from_concept_id"],
    )
    return ["opposite_prerequisite"] if reverse else []


async def _edge_dto(connection: SQLiteConnection, edge_id: str, class_id: str) -> dict[str, Any]:
    row = await connection.fetchrow(
        "SELECT * FROM class_concept_edges WHERE id=?1 AND class_id=?2", edge_id, class_id
    )
    if row is None:
        raise ApiError(404, "concept_edge_not_found", "The relationship was not found.")
    return {
        "id": row["id"],
        "class_id": row["class_id"],
        "from_concept_id": row["from_concept_id"],
        "to_concept_id": row["to_concept_id"],
        "type": row["type"],
        "necessity": row["necessity"],
        "provenance": row["provenance"],
        "explanation": row["explanation"],
        "status": row["status"],
        "state": row["status"],
        "version": row["version"],
        "review_flags": await _edge_review_flags(connection, row),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def create_edge(
    database: SQLiteDatabase, *, class_id: str, user_id: str, payload: ConceptEdgeCreate
) -> dict[str, Any]:
    from_id, to_id = str(payload.from_concept_id), str(payload.to_concept_id)
    if from_id == to_id:
        raise ApiError(400, "self_edge", "A concept cannot relate to itself.")
    async with database.acquire() as connection:
        async with connection.transaction():
            await _manager_access(connection, class_id, user_id)
            for concept_id in (from_id, to_id):
                concept = await _concept_row(connection, class_id, concept_id)
                if concept is None:
                    other = await connection.fetchrow(
                        "SELECT class_id FROM class_concepts WHERE id=?1", concept_id
                    )
                    if other is not None:
                        raise ApiError(
                            400,
                            "cross_class_concept",
                            "Relationship endpoints must belong to this class.",
                        )
                    raise ApiError(
                        404, "concept_not_found", "A relationship endpoint was not found."
                    )
                if concept["status"] in {"merged", "rejected"}:
                    raise ApiError(
                        409, "concept_unavailable", "A relationship endpoint is inactive."
                    )
            await _validate_current_chunk_refs(
                connection,
                class_id=class_id,
                user_id=user_id,
                chunk_revision_ids=[str(value) for value in payload.source_chunk_revision_ids],
            )
            if payload.type == "prerequisite_for" and await _would_prerequisite_cycle(
                connection, class_id, from_id, to_id
            ):
                raise ApiError(409, "prerequisite_cycle", "This prerequisite would create a cycle.")
            duplicate = await connection.fetchrow(
                "SELECT id FROM class_concept_edges WHERE class_id=?1 AND from_concept_id=?2 "
                "AND to_concept_id=?3 AND type=?4 AND status IN ('proposed','validated') LIMIT 1",
                class_id,
                from_id,
                to_id,
                payload.type,
            )
            if duplicate:
                raise ApiError(409, "duplicate_concept_edge", "That relationship already exists.")
            edge_id = str(uuid4())
            await connection.execute(
                "INSERT INTO class_concept_edges "
                "(id,class_id,from_concept_id,to_concept_id,type,necessity,provenance,"
                "explanation,created_by) "
                "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)",
                edge_id,
                class_id,
                from_id,
                to_id,
                payload.type,
                payload.necessity or "none",
                payload.provenance,
                payload.explanation,
                user_id,
            )
            await _add_source_refs(
                connection,
                class_id=class_id,
                edge_id=edge_id,
                chunk_ids=[str(value) for value in payload.source_chunk_revision_ids],
            )
            return await _edge_dto(connection, edge_id, class_id)


async def _would_prerequisite_cycle(
    connection: SQLiteConnection, class_id: str, from_id: str, to_id: str
) -> bool:
    # A -> B is cyclic exactly when validated prerequisites already contain B -> ... -> A.
    return bool(
        await connection.fetchval(
            "WITH RECURSIVE reachable(id) AS (SELECT ?1 UNION "
            "SELECT e.to_concept_id FROM class_concept_edges e JOIN reachable r ON "
            "e.from_concept_id=r.id "
            "WHERE e.class_id=?2 AND e.type='prerequisite_for' AND e.status='validated') "
            "SELECT EXISTS(SELECT 1 FROM reachable WHERE id=?3)",
            to_id,
            class_id,
            from_id,
        )
    )


async def _has_validated_prerequisite_cycle(connection: SQLiteConnection, class_id: str) -> bool:
    return bool(
        await connection.fetchval(
            "WITH RECURSIVE reach(start,id) AS ("
            "SELECT from_concept_id,to_concept_id FROM class_concept_edges "
            "WHERE class_id=?1 AND type='prerequisite_for' AND status='validated' UNION "
            "SELECT r.start,e.to_concept_id FROM reach r JOIN class_concept_edges e "
            "ON e.from_concept_id=r.id WHERE e.class_id=?1 AND e.type='prerequisite_for' "
            "AND e.status='validated') SELECT EXISTS(SELECT 1 FROM reach WHERE start=id)",
            class_id,
        )
    )


async def decide_edge(
    database: SQLiteDatabase,
    *,
    class_id: str,
    user_id: str,
    edge_id: str,
    decision: str,
    expected_version: int,
) -> dict[str, Any]:
    async with database.acquire() as connection:
        async with connection.transaction():
            await _manager_access(connection, class_id, user_id)
            edge = await connection.fetchrow(
                "SELECT * FROM class_concept_edges WHERE id=?1 AND class_id=?2", edge_id, class_id
            )
            if edge is None:
                raise ApiError(404, "concept_edge_not_found", "The relationship was not found.")
            _check_version(expected_version, edge["version"])
            if edge["status"] != "proposed":
                raise ApiError(
                    409,
                    "concept_edge_not_proposed",
                    "Only a proposed relationship can be reviewed.",
                )
            if decision == "approve":
                refs = await connection.fetch(
                    "SELECT chunk_revision_id FROM class_concept_source_refs WHERE edge_id=?1",
                    edge_id,
                )
                await _validate_current_chunk_refs(
                    connection,
                    class_id=class_id,
                    user_id=user_id,
                    chunk_revision_ids=[item["chunk_revision_id"] for item in refs],
                )
                endpoints = await connection.fetch(
                    "SELECT id,status FROM class_concepts WHERE class_id=?1 AND id IN (?2,?3)",
                    class_id,
                    edge["from_concept_id"],
                    edge["to_concept_id"],
                )
                if len(endpoints) != 2 or any(item["status"] != "validated" for item in endpoints):
                    raise ApiError(
                        409,
                        "concept_endpoint_not_validated",
                        "Approve both concepts before this relationship.",
                    )
                if edge["type"] == "prerequisite_for" and await _would_prerequisite_cycle(
                    connection, class_id, edge["from_concept_id"], edge["to_concept_id"]
                ):
                    raise ApiError(
                        409, "prerequisite_cycle", "This prerequisite would create a cycle."
                    )
                status = "validated"
            elif decision == "reject":
                status = "rejected"
            else:
                raise ApiError(400, "invalid_decision", "Choose approve or reject.")
            await connection.execute(
                "UPDATE class_concept_edges SET status=?1,version=version+1,"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=?2 AND class_id=?3 AND version=?4",
                status,
                edge_id,
                class_id,
                expected_version,
            )
            return await _edge_dto(connection, edge_id, class_id)


async def delete_edge(
    database: SQLiteDatabase,
    *,
    class_id: str,
    user_id: str,
    edge_id: str,
    expected_version: int,
) -> None:
    async with database.acquire() as connection:
        async with connection.transaction():
            await _manager_access(connection, class_id, user_id)
            edge = await connection.fetchrow(
                "SELECT version FROM class_concept_edges WHERE id=?1 AND class_id=?2",
                edge_id,
                class_id,
            )
            if edge is None:
                raise ApiError(404, "concept_edge_not_found", "The relationship was not found.")
            _check_version(expected_version, edge["version"])
            await connection.execute(
                "DELETE FROM class_concept_edges WHERE id=?1 AND class_id=?2", edge_id, class_id
            )


async def _visible_refs(
    connection: SQLiteConnection,
    *,
    class_id: str,
    user_id: str,
    refs: list[dict[str, Any]],
    include_text: bool,
) -> list[dict[str, Any]]:
    if not refs:
        return []
    access = await class_access(connection, class_id, user_id, required="read")
    visible: list[dict[str, Any]] = []
    for ref in refs:
        row = await connection.fetchrow(
            "SELECT "
            "cr.id,cr.revision_id,cr.chunk_order,cr.locator,cr.text,cr.extraction_met"
            "hod,cr.provenance,"
            "r.note_id,n.title,n.status,n.source_index_status,n.processing_revision,"
            "h.processing_revision AS indexed_processing_revision,"
            "n.created_by,n.publication_state,n.release_at,n.resource_scope,"
            "h.revision_id AS current_revision_id,"
            "a.id AS assessment_id,a.owner_id AS assessment_owner,a.has_student_responses,"
            "a.score_percent,a.grading_result,a.answer_key_release_state,a.answer_key_release_at "
            "FROM source_chunk_revisions cr JOIN source_revisions r ON r.id=cr.revision_id "
            "JOIN course_notes n ON n.id=r.note_id JOIN source_index_heads h ON h.note_id=n.id "
            "LEFT JOIN uploaded_assessments a ON a.note_id=n.id "
            "WHERE cr.id=?1 AND n.class_id=?2",
            ref["chunk_revision_id"],
            class_id,
        )
        if (
            row is None
            or row["status"] != "ready"
            or row["source_index_status"] != "ready"
            or row["indexed_processing_revision"] != row["processing_revision"]
            or row["provenance"] != "source"
            or not (
                is_effectively_published(row["publication_state"], row["release_at"])
                or can_view_unreleased(access, user_id, row["created_by"])
            )
        ):
            continue
        if row["revision_id"] != row["current_revision_id"]:
            continue
        if row["assessment_id"] is not None:
            assessment = {
                "owner_id": row["assessment_owner"],
                "has_student_responses": row["has_student_responses"],
                "score_percent": row["score_percent"],
                "grading_result": row["grading_result"],
            }
            if is_private_assessment(assessment) or not can_view_assessment_key(
                {
                    **assessment,
                    "answer_key_release_state": row["answer_key_release_state"],
                    "answer_key_release_at": row["answer_key_release_at"],
                    "created_by": row["created_by"],
                    "resource_scope": row["resource_scope"],
                },
                access,
                user_id,
            ):
                continue
        item = {
            "chunk_revision_id": row["id"],
            "revision_id": row["revision_id"],
            "material_id": row["note_id"],
            "title": row["title"],
            "order": row["chunk_order"],
            "locator": json_value(row["locator"], {}),
            "extraction_method": row["extraction_method"],
            "provenance": row["provenance"],
        }
        if include_text:
            text = " ".join(row["text"].split())
            item["excerpt"] = text[:500] + ("…" if len(text) > 500 else "")
        visible.append(item)
    return visible


async def concept_graph(
    database: SQLiteDatabase, *, class_id: str, user_id: str, review: bool = False
) -> dict[str, Any]:
    access = await class_access(database, class_id, user_id, required="read")
    if review and not is_class_manager(access):
        raise ApiError(
            403, "insufficient_permission", "A class manager must review the concept graph."
        )
    if review:
        concepts = await database.fetch(
            "SELECT * FROM class_concepts WHERE class_id=?1 AND status='proposed' "
            "ORDER BY canonical_name,id",
            class_id,
        )
        edges = await database.fetch(
            "SELECT * FROM class_concept_edges WHERE class_id=?1 AND "
            "status='proposed' ORDER BY created_at,id",
            class_id,
        )
    else:
        concepts = await database.fetch(
            "SELECT * FROM class_concepts WHERE class_id=?1 AND status='validated' "
            "ORDER BY canonical_name,id",
            class_id,
        )
        edges = await database.fetch(
            "SELECT * FROM class_concept_edges WHERE class_id=?1 AND "
            "status='validated' ORDER BY type,created_at,id",
            class_id,
        )
    out_concepts: list[dict[str, Any]] = []
    visible_ids: set[str] = set()
    for row in concepts:
        refs = await database.fetch(
            "SELECT chunk_revision_id FROM class_concept_source_refs WHERE "
            "concept_id=?1 ORDER BY chunk_revision_id",
            row["id"],
        )
        source_refs = await _visible_refs(
            database, class_id=class_id, user_id=user_id, refs=refs, include_text=review
        )
        # Source-backed graph facts are hidden if their backing material is no
        # longer visible. Explicit manager-created nodes may stand without citations.
        if refs and not source_refs:
            continue
        if not refs and row["provenance"] != "manual":
            continue
        visible_ids.add(row["id"])
        dto = await _concept_dto(database, row["id"], class_id)
        if not review:
            dto["aliases"] = [alias for alias in dto["aliases"] if alias["status"] == "validated"]
            dto["alias_proposals"] = []
        dto["sources"] = source_refs
        dto["source_chunk_revision_ids"] = [ref["chunk_revision_id"] for ref in source_refs]
        out_concepts.append(dto)
    # Do not reveal the identifier of a parent whose own source-backed node was
    # filtered by publication, current-revision, or answer-key visibility.
    for dto in out_concepts:
        if dto["parent_concept_id"] not in visible_ids:
            dto["parent_concept_id"] = None
    out_edges: list[dict[str, Any]] = []
    for row in edges:
        if row["from_concept_id"] not in visible_ids or row["to_concept_id"] not in visible_ids:
            continue
        refs = await database.fetch(
            "SELECT chunk_revision_id FROM class_concept_source_refs WHERE edge_id=?1 "
            "ORDER BY chunk_revision_id",
            row["id"],
        )
        source_refs = await _visible_refs(
            database, class_id=class_id, user_id=user_id, refs=refs, include_text=review
        )
        if refs and not source_refs:
            continue
        if not refs:
            continue
        dto = await _edge_dto(database, row["id"], class_id)
        dto["sources"] = source_refs
        dto["source_chunk_revision_ids"] = [ref["chunk_revision_id"] for ref in source_refs]
        out_edges.append(dto)
    response = {"concepts": out_concepts, "edges": out_edges}
    if review:
        alias_rows = await database.fetch(
            "SELECT a.id,a.name,a.language,a.status AS state,c.id AS concept_id,"
            "c.canonical_name AS concept_name,c.version AS concept_version "
            "FROM class_concept_aliases a JOIN class_concepts c ON c.id=a.concept_id "
            "WHERE a.class_id=?1 AND a.status='proposed' AND c.status IN ('proposed','validated') "
            "ORDER BY a.created_at,a.id LIMIT 100",
            class_id,
        )
        aliases: list[dict[str, Any]] = []
        for alias in alias_rows:
            refs = await database.fetch(
                "SELECT chunk_revision_id FROM class_concept_source_refs WHERE concept_id=?1 "
                "ORDER BY chunk_revision_id",
                alias["concept_id"],
            )
            sources = await _visible_refs(
                database, class_id=class_id, user_id=user_id, refs=refs, include_text=True
            )
            if refs and not sources:
                continue
            aliases.append(
                {
                    "id": alias["id"],
                    "name": alias["name"],
                    "language": alias["language"],
                    "state": alias["state"],
                    "concept_id": alias["concept_id"],
                    "concept_name": alias["concept_name"],
                    "concept_version": alias["concept_version"],
                    "sources": sources,
                }
            )
        response["alias_proposals"] = aliases
    return response


async def enqueue_missing_concept_extractions(database: SQLiteDatabase, *, limit: int = 50) -> int:
    """Queue current indexed sources omitted by upgrade/backfill or a failed old trigger."""
    async with database.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT OR IGNORE INTO class_concept_extraction_jobs "
                "(id,class_id,note_id,source_revision_id,requested_by,status) "
                "SELECT lower(hex(randomblob(16))),n.class_id,n.id,h.revision_id,"
                "c.owner_id,'queued' FROM course_notes n "
                "JOIN source_index_heads h ON h.note_id=n.id "
                "JOIN source_revisions r ON r.id=h.revision_id "
                "JOIN course_classes c ON c.id=n.class_id "
                "WHERE n.status='ready' AND n.source_index_status='ready' "
                "AND h.processing_revision=n.processing_revision "
                "AND (n.resource_scope<>'student_contribution' OR n.publication_state='published' "
                "OR (n.publication_state='scheduled' AND "
                "julianday(n.release_at)<=julianday('now'))) "
                "AND NOT EXISTS (SELECT 1 FROM uploaded_assessments a WHERE a.note_id=n.id) "
                "AND NOT EXISTS (SELECT 1 FROM class_concept_extraction_jobs j "
                "WHERE j.note_id=n.id AND j.source_revision_id=h.revision_id) "
                "ORDER BY n.updated_at,n.id LIMIT ?1",
                limit,
            )
            return int(await connection.fetchval("SELECT changes()") or 0)


async def recover_concept_extraction_jobs(database: SQLiteDatabase) -> None:
    async with database.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "UPDATE class_concept_extraction_jobs SET status=CASE "
                "WHEN attempts<max_attempts "
                "THEN 'queued' ELSE 'failed' END,error_code='extraction_interrupted',"
                "available_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE status='running'"
            )


async def enqueue_concept_extraction(
    database: SQLiteDatabase, *, class_id: str, user_id: str, upload_id: str
) -> dict[str, Any]:
    async with database.acquire() as connection:
        async with connection.transaction():
            access = await _manager_access(connection, class_id, user_id)
            note = await connection.fetchrow(
                "SELECT n.id,n.status,n.source_index_status,n.processing_revision AS "
                "current_processing_revision,"
                "n.created_by,n.resource_scope,n.publication_state,n.release_at,"
                "h.revision_id,h.processing_revision AS "
                "indexed_processing_revision,a.id AS assessment_id "
                "FROM course_notes n LEFT JOIN source_index_heads h ON h.note_id=n.id "
                "LEFT JOIN source_revisions r ON r.id=h.revision_id "
                "LEFT JOIN uploaded_assessments a ON a.note_id=n.id "
                "WHERE n.id=?1 AND n.class_id=?2",
                upload_id,
                class_id,
            )
            if note is None or note["status"] != "ready" or note["source_index_status"] != "ready":
                raise ApiError(404, "source_not_ready", "The source is not ready for extraction.")
            if note["assessment_id"] is not None:
                raise ApiError(
                    404,
                    "source_not_available",
                    "This source is not available for graph extraction.",
                )
            if note["revision_id"] is None or note["current_processing_revision"] is None:
                raise ApiError(409, "source_index_missing", "The source index is not ready yet.")
            if note["indexed_processing_revision"] != note["current_processing_revision"]:
                raise ApiError(409, "stale_source_revision", "The source changed. Try again.")
            if note["resource_scope"] == "student_contribution" and not is_effectively_published(
                note["publication_state"], note["release_at"]
            ):
                raise ApiError(404, "source_not_available", "The source was not found.")
            if not _is_current_release_visible(access, user_id, note):
                raise ApiError(404, "source_not_available", "The source was not found.")
            await connection.execute(
                "INSERT OR IGNORE INTO class_concept_extraction_jobs "
                "(id,class_id,note_id,source_revision_id,requested_by,status) "
                "VALUES (lower(hex(randomblob(16))),?1,?2,?3,?4,'queued')",
                class_id,
                upload_id,
                note["revision_id"],
                user_id,
            )
            job = await connection.fetchrow(
                "SELECT id,status,attempts,max_attempts FROM class_concept_extraction_jobs "
                "WHERE note_id=?1 AND source_revision_id=?2",
                upload_id,
                note["revision_id"],
            )
            if job is None:
                raise ApiError(503, "extraction_unavailable", "The extraction could not be queued.")
            if job["status"] == "failed":
                await connection.execute(
                    "UPDATE class_concept_extraction_jobs SET "
                    "status='queued',attempts=0,error_code=NULL,"
                    "available_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),requested_by=?1,"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?2 AND "
                    "status='failed'",
                    user_id,
                    job["id"],
                )
                job = {**job, "status": "queued"}
            return {"operation_id": job["id"], "status": job["status"]}


async def concept_extraction_status(
    database: SQLiteDatabase, *, class_id: str, user_id: str, operation_id: str
) -> dict[str, Any]:
    async with database.acquire() as connection:
        await _manager_access(connection, class_id, user_id)
        row = await connection.fetchrow(
            "SELECT "
            "id,note_id,source_revision_id,status,error_code,prompt_version,model_name,"
            "created_at,updated_at,completed_at "
            "FROM class_concept_extraction_jobs WHERE id=?1 AND class_id=?2",
            operation_id,
            class_id,
        )
        if row is None:
            raise ApiError(404, "extraction_not_found", "The extraction operation was not found.")
        return {
            "operation_id": row["id"],
            "upload_id": row["note_id"],
            "source_revision_id": row["source_revision_id"],
            "status": row["status"],
            "error_code": row["error_code"],
            "prompt_version": row["prompt_version"],
            "model_name": row["model_name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }


async def concept_merge_history(
    database: SQLiteDatabase, *, class_id: str, user_id: str, limit: int = 100
) -> dict[str, Any]:
    """Return bounded merge audit history to class managers only."""
    async with database.acquire() as connection:
        await _manager_access(connection, class_id, user_id)
        rows = await connection.fetch(
            "SELECT h.source_concept_id,h.target_concept_id,h.source_name,h.source_description,"
            "h.source_version,h.merged_by,h.created_at,target.canonical_name AS target_name "
            "FROM class_concept_merge_history h JOIN class_concepts target "
            "ON target.id=h.target_concept_id AND target.class_id=h.class_id "
            "WHERE h.class_id=?1 ORDER BY h.created_at DESC,h.id DESC LIMIT ?2",
            class_id,
            max(1, min(limit, 200)),
        )
        return {"items": rows}


async def _mark_extraction_job(
    database: SQLiteDatabase,
    job_id: str,
    *,
    status: str,
    error_code: str | None = None,
    delay_seconds: int = 0,
) -> None:
    async with database.acquire() as connection:
        await connection.execute(
            "UPDATE class_concept_extraction_jobs SET status=?1,error_code=?2,"
            "available_at=strftime('%Y-%m-%dT%H:%M:%fZ','now',?3),"
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
            "completed_at=CASE WHEN ?1 IN ('complete','failed','superseded') "
            "THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE NULL END "
            "WHERE id=?4 AND status='running'",
            status,
            error_code,
            f"+{max(0, delay_seconds)} seconds",
            job_id,
        )


async def _create_extraction_proposals(
    connection: SQLiteConnection,
    *,
    job: dict[str, Any],
    output: ConceptGraphAIOutput,
    allowed_chunk_ids: set[str],
    context_concepts: dict[str, dict[str, Any]],
) -> None:
    seen_keys: set[str] = set()
    key_ids: dict[str, str] = {}
    refs_by_key: dict[str, list[str]] = {}
    models_by_key: dict[str, Any] = {}
    for item in output.concepts:
        if item.key in seen_keys:
            raise ValueError("duplicate concept key")
        seen_keys.add(item.key)
        refs = [str(ref) for ref in item.source_chunk_revision_ids]
        if not refs or any(ref not in allowed_chunk_ids for ref in refs):
            raise ValueError("concept source reference is outside the authorized input")
        normalized = normalize_concept_name(item.canonical_name)
        if not normalized:
            raise ValueError("empty normalized concept name")
        concept_id: str | None = None
        if item.existing_concept_id is not None:
            existing_id = str(item.existing_concept_id)
            existing = context_concepts.get(existing_id)
            if existing is None:
                raise ValueError("existing concept id was not supplied to the model")
            concept_id = existing_id
        else:
            duplicate = await _find_name_owner(connection, job["class_id"], normalized)
            if duplicate:
                concept_id = duplicate["id"]
            else:
                concept_id = str(uuid4())
                await connection.execute(
                    "INSERT INTO class_concepts "
                    "(id,class_id,canonical_name,normalized_name,description,status,pr"
                    "ovenance,created_by) "
                    "VALUES (?1,?2,?3,?4,?5,'proposed',?6,?7)",
                    concept_id,
                    job["class_id"],
                    item.canonical_name,
                    normalized,
                    item.description,
                    item.provenance,
                    job["requested_by"],
                )
        existing_canonical = await connection.fetchval(
            "SELECT normalized_name FROM class_concepts WHERE id=?1 AND class_id=?2",
            concept_id,
            job["class_id"],
        )
        if item.existing_concept_id is not None:
            existing = context_concepts[str(item.existing_concept_id)]
            matches_known_name = normalized == normalize_concept_name(
                existing["canonical_name"]
            ) or any(normalized == normalize_concept_name(alias) for alias in existing["aliases"])
            if not matches_known_name:
                raise ValueError("existing concept id does not match the supplied concept name")
        # Never silently add a model-proposed alias to a validated concept.
        # It remains a separate proposal until a manager approves it.
        for alias in item.aliases:
            alias_normalized = normalize_concept_name(alias.name)
            if (
                not alias_normalized
                or alias_normalized == existing_canonical
                or await _find_name_owner(connection, job["class_id"], alias_normalized)
            ):
                continue
            await connection.execute(
                "INSERT OR IGNORE INTO class_concept_aliases "
                "(id,class_id,concept_id,name,normalized_name,language,status,created_by) "
                "VALUES (?1,?2,?3,?4,?5,?6,'proposed',?7)",
                uuid4(),
                job["class_id"],
                concept_id,
                alias.name,
                alias_normalized,
                alias.language,
                job["requested_by"],
            )
        key_ids[item.key] = concept_id
        refs_by_key[item.key] = refs
        models_by_key[item.key] = item
        await _add_source_refs(
            connection,
            class_id=job["class_id"],
            concept_id=concept_id,
            chunk_ids=refs,
        )
    for key, item in models_by_key.items():
        if item.parent_key is None:
            continue
        parent_id = key_ids.get(item.parent_key)
        if parent_id is None:
            raise ValueError("unknown parent concept key")
        if parent_id == key_ids[key]:
            raise ValueError("self parent")
        current = await _concept_row(connection, job["class_id"], key_ids[key])
        if current and current["parent_concept_id"] is None and current["status"] == "proposed":
            if await _would_parent_cycle(connection, job["class_id"], key_ids[key], parent_id):
                raise ValueError("parent hierarchy cycle")
            await connection.execute(
                "UPDATE class_concepts SET parent_concept_id=?1 WHERE id=?2 AND class_id=?3",
                parent_id,
                key_ids[key],
                job["class_id"],
            )
    for edge in output.edges:
        from_id, to_id = key_ids.get(edge.from_key), key_ids.get(edge.to_key)
        refs = [str(ref) for ref in edge.source_chunk_revision_ids]
        if from_id is None or to_id is None or from_id == to_id:
            raise ValueError("invalid concept edge endpoint")
        if not refs or any(ref not in allowed_chunk_ids for ref in refs):
            raise ValueError("edge source reference is outside the authorized input")
        duplicate = await connection.fetchrow(
            "SELECT id FROM class_concept_edges WHERE class_id=?1 AND from_concept_id=?2 "
            "AND to_concept_id=?3 AND type=?4 AND status IN ('proposed','validated') LIMIT 1",
            job["class_id"],
            from_id,
            to_id,
            edge.type,
        )
        if duplicate:
            continue
        edge_id = str(uuid4())
        await connection.execute(
            "INSERT INTO class_concept_edges "
            "(id,class_id,from_concept_id,to_concept_id,type,necessity,provenance,exp"
            "lanation,created_by) "
            "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)",
            edge_id,
            job["class_id"],
            from_id,
            to_id,
            edge.type,
            edge.necessity or "none",
            edge.provenance,
            edge.explanation,
            job["requested_by"],
        )
        await _add_source_refs(
            connection, class_id=job["class_id"], edge_id=edge_id, chunk_ids=refs
        )


async def process_queued_concept_extractions(
    application: Any, *, limit: int = MAX_EXTRACTION_JOBS_PER_BATCH
) -> int:
    """Claim a bounded batch, call the model outside transactions, then persist proposals."""
    database: SQLiteDatabase | None = getattr(application.state, "database", None)
    service = getattr(application.state, "gemini_content_service", None)
    if database is None:
        return 0
    if service is None:
        async with database.acquire() as connection:
            await connection.execute(
                "UPDATE class_concept_extraction_jobs SET "
                "status='failed',error_code='provider_unavailable',"
                "completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE status='queued'"
            )
        return 0
    processed = 0
    for _ in range(max(1, min(limit, MAX_EXTRACTION_JOBS_PER_BATCH))):
        async with database.acquire() as connection:
            async with connection.transaction():
                claimed = await connection.fetchrow(
                    "UPDATE class_concept_extraction_jobs SET status='running',"
                    "prompt_version=?1,model_name=?2,attempts=attempts+1,"
                    "started_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),error_code=NULL "
                    "WHERE id=(SELECT id FROM class_concept_extraction_jobs WHERE status='queued' "
                    "AND attempts<max_attempts AND "
                    "available_at<=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                    "ORDER BY created_at,id LIMIT 1) RETURNING *",
                    CONCEPT_GRAPH_EXTRACTION_VERSION,
                    getattr(service, "model_name", None),
                )
        if claimed is None:
            break
        processed += 1
        job_id = claimed["id"]
        try:
            # All reads are rechecked against the current head and class policy.
            note = await database.fetchrow(
                "SELECT "
                "n.id,n.class_id,n.status,n.source_index_status,n.processing_revision,"
                "n.created_by,"
                "n.resource_scope,n.publication_state,n.release_at,h.revision_id,r.pro"
                "cessing_revision AS indexed_revision,"
                "a.id AS assessment_id FROM course_notes n "
                "JOIN source_index_heads h ON h.note_id=n.id JOIN source_revisions r "
                "ON r.id=h.revision_id "
                "LEFT JOIN uploaded_assessments a ON a.note_id=n.id "
                "WHERE n.id=?1 AND n.class_id=?2",
                claimed["note_id"],
                claimed["class_id"],
            )
            if (
                note is None
                or note["status"] != "ready"
                or note["source_index_status"] != "ready"
                or note["revision_id"] != claimed["source_revision_id"]
                or note["indexed_revision"] != note["processing_revision"]
                or note["assessment_id"] is not None
            ):
                await _mark_extraction_job(
                    database, job_id, status="superseded", error_code="source_changed"
                )
                continue
            try:
                access = await _manager_access(
                    database, claimed["class_id"], claimed["requested_by"]
                )
            except ApiError:
                await _mark_extraction_job(
                    database, job_id, status="superseded", error_code="authorization_changed"
                )
                continue
            if not _is_current_release_visible(access, claimed["requested_by"], note):
                await _mark_extraction_job(
                    database, job_id, status="superseded", error_code="source_unavailable"
                )
                continue
            if note["resource_scope"] == "student_contribution" and not is_effectively_published(
                note["publication_state"], note["release_at"]
            ):
                await _mark_extraction_job(
                    database, job_id, status="superseded", error_code="source_unavailable"
                )
                continue
            chunks = await database.fetch(
                "SELECT "
                "cr.id,cr.text,cr.locator,cr.extraction_method,cr.provenance,sec.title"
                ",sec.heading_path "
                "FROM source_chunk_revisions cr JOIN source_section_revisions sec "
                "ON sec.revision_id=cr.revision_id AND sec.section_id=cr.section_id "
                "WHERE cr.revision_id=?1 AND cr.provenance='source' ORDER BY "
                "cr.chunk_order LIMIT ?2",
                claimed["source_revision_id"],
                MAX_EXTRACTION_CHUNKS,
            )
            bounded_chunks: list[dict[str, Any]] = []
            char_budget = MAX_EXTRACTION_CHARS
            for chunk in chunks:
                if char_budget <= 0:
                    break
                text = chunk["text"][:char_budget]
                char_budget -= len(text)
                bounded_chunks.append(
                    {
                        "chunk_revision_id": chunk["id"],
                        "title": chunk["title"],
                        "heading_path": json_value(chunk["heading_path"], []),
                        "locator": json_value(chunk["locator"], {}),
                        "text": text,
                    }
                )
            if not bounded_chunks:
                await _mark_extraction_job(
                    database, job_id, status="failed", error_code="no_source_text"
                )
                continue
            existing_rows = await database.fetch(
                "SELECT id,canonical_name,description,status FROM class_concepts WHERE class_id=?1 "
                "AND status IN ('proposed','validated','deprecated') ORDER BY "
                "normalized_name,id LIMIT ?2",
                claimed["class_id"],
                MAX_EXISTING_CONCEPTS,
            )
            context_concepts = {
                row["id"]: {
                    "id": row["id"],
                    "canonical_name": row["canonical_name"],
                    "description": row["description"],
                    "status": row["status"],
                    "aliases": [
                        item["name"]
                        for item in await database.fetch(
                            "SELECT name FROM class_concept_aliases WHERE concept_id=?1 "
                            "AND status='validated' ORDER BY normalized_name LIMIT 8",
                            row["id"],
                        )
                    ],
                }
                for row in existing_rows
            }
            context = {
                "source_revision_id": claimed["source_revision_id"],
                "source_chunks": bounded_chunks,
                "existing_concepts": list(context_concepts.values()),
            }
            output = await asyncio.to_thread(service.extract_concept_graph, context)
            allowed_ids = {chunk["chunk_revision_id"] for chunk in bounded_chunks}
            async with database.acquire() as connection:
                async with connection.transaction():
                    current = await connection.fetchrow(
                        "SELECT n.status,n.source_index_status,n.processing_revision,n.created_by,"
                        "n.resource_scope,n.publication_state,n.release_at,h.revision_"
                        "id,a.id AS assessment_id "
                        "FROM course_notes n JOIN source_index_heads h ON h.note_id=n.id "
                        "LEFT JOIN uploaded_assessments a ON a.note_id=n.id "
                        "WHERE n.id=?1 AND n.class_id=?2",
                        claimed["note_id"],
                        claimed["class_id"],
                    )
                    if (
                        current is None
                        or current["status"] != "ready"
                        or current["source_index_status"] != "ready"
                        or current["processing_revision"] is None
                        or current["revision_id"] != claimed["source_revision_id"]
                        or current["assessment_id"] is not None
                    ):
                        await connection.execute(
                            "UPDATE class_concept_extraction_jobs SET "
                            "status='superseded',error_code='source_changed',"
                            "completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE "
                            "id=?1 AND status='running'",
                            job_id,
                        )
                        continue
                    try:
                        current_access = await _manager_access(
                            connection, claimed["class_id"], claimed["requested_by"]
                        )
                    except ApiError:
                        await connection.execute(
                            "UPDATE class_concept_extraction_jobs SET "
                            "status='superseded',error_code='authorization_changed',"
                            "completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE "
                            "id=?1 AND status='running'",
                            job_id,
                        )
                        continue
                    if not _is_current_release_visible(
                        current_access, claimed["requested_by"], current
                    ) or (
                        current["resource_scope"] == "student_contribution"
                        and not is_effectively_published(
                            current["publication_state"], current["release_at"]
                        )
                    ):
                        await connection.execute(
                            "UPDATE class_concept_extraction_jobs SET "
                            "status='superseded',error_code='source_unavailable',"
                            "completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE "
                            "id=?1 AND status='running'",
                            job_id,
                        )
                        continue
                    await _create_extraction_proposals(
                        connection,
                        job=claimed,
                        output=output,
                        allowed_chunk_ids=allowed_ids,
                        context_concepts=context_concepts,
                    )
                    await connection.execute(
                        "UPDATE class_concept_extraction_jobs SET "
                        "status='complete',error_code=NULL,"
                        "completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?1 "
                        "AND status='running'",
                        job_id,
                    )
        except Exception as exc:
            error_code = getattr(exc, "code", None)
            diagnostic_code = getattr(exc, "diagnostic_code", None)
            validation_issues = getattr(exc, "validation_issues", None)
            validation_issue_count = (
                len(validation_issues) if isinstance(validation_issues, list) else 0
            )
            logger.warning(
                "concept_extraction_failed operation_id=%s category=%s error_code=%s "
                "diagnostic_code=%s validation_issue_count=%s",
                job_id,
                type(exc).__name__,
                error_code,
                diagnostic_code,
                validation_issue_count,
            )
            attempts = int(claimed["attempts"])
            if attempts < int(claimed["max_attempts"]):
                await _mark_extraction_job(
                    database,
                    job_id,
                    status="queued",
                    error_code="provider_or_output_failure",
                    delay_seconds=MAX_EXTRACTION_RETRY_DELAY_SECONDS,
                )
            else:
                await _mark_extraction_job(
                    database,
                    job_id,
                    status="failed",
                    error_code="provider_or_output_failure",
                )
    return processed
