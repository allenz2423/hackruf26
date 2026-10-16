"""Central class membership, capability, and release checks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.api.errors import ApiError
from app.services.database import SQLiteConnection, SQLiteDatabase

CAPABILITIES = frozenset(
    {
        "read", "create", "edit", "delete", "delete_others", "invite",
        "manage_members", "manage_permissions",
    }
)
OWNER_CAPABILITIES = CAPABILITIES


async def class_access(
    connection: SQLiteConnection | SQLiteDatabase,
    class_id: UUID | str,
    user_id: UUID | str,
    *,
    required: str | None = None,
) -> dict[str, Any]:
    """Return class data and effective capabilities, hiding non-member classes."""
    row = await connection.fetchrow(
        "SELECT c.id, c.owner_id, c.name, c.color, c.icon, c.created_at, c.updated_at, "
        "CASE WHEN c.owner_id = ?2 THEN 1 ELSE 0 END AS is_owner "
        "FROM course_classes c LEFT JOIN class_memberships m ON m.class_id = c.id "
        "AND m.user_id = ?2 AND m.status = 'active' "
        "WHERE c.id = ?1 AND (c.owner_id = ?2 OR m.user_id IS NOT NULL)",
        class_id,
        user_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if row["is_owner"]:
        capabilities = set(OWNER_CAPABILITIES)
    else:
        capabilities = {
            item["capability"]
            for item in await connection.fetch(
                "SELECT capability FROM class_membership_capabilities "
                "WHERE class_id = ?1 AND user_id = ?2",
                class_id,
                user_id,
            )
        }
    if required and required not in capabilities:
        raise ApiError(403, "insufficient_permission", "You do not have permission to do that.")
    return {**row, "capabilities": sorted(capabilities)}


def require_capability(access: dict[str, Any], capability: str) -> None:
    if capability not in access["capabilities"]:
        raise ApiError(403, "insufficient_permission", "You do not have permission to do that.")


def is_class_manager(access: dict[str, Any]) -> bool:
    return bool(access["is_owner"] or "manage_permissions" in access["capabilities"])


def is_effectively_published(state: str | None, release_at: str | None) -> bool:
    """Treat a due scheduled release as public without relying on a worker."""
    if state == "published":
        return True
    if state != "scheduled" or not release_at:
        return False
    try:
        release = datetime.fromisoformat(release_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if release.tzinfo is None:
        return False
    return release.astimezone(UTC) <= datetime.now(UTC)


def can_view_unreleased(access: dict[str, Any], user_id: UUID | str, created_by: object) -> bool:
    return bool(
        access["is_owner"]
        or "manage_permissions" in access["capabilities"]
        or (created_by is not None and str(created_by) == str(user_id))
    )


def is_private_assessment(row: dict[str, Any]) -> bool:
    """Legacy or student-submitted assessment results remain owner-private."""
    return bool(
        row.get("has_student_responses")
        or row.get("score_percent") is not None
        or row.get("grading_result") is not None
    )


def can_view_assessment_key(
    row: dict[str, Any], access: dict[str, Any], user_id: UUID | str
) -> bool:
    owner_id = row.get("owner_id", row.get("assessment_owner"))
    resource_scope = row.get("resource_scope", row.get("note_resource_scope"))
    if is_private_assessment(row):
        return owner_id is not None and str(owner_id) == str(user_id)
    key_is_released = is_effectively_published(
        row.get("answer_key_release_state"), row.get("answer_key_release_at")
    )
    if resource_scope == "student_contribution" and not key_is_released:
        return bool(
            (owner_id is not None and str(owner_id) == str(user_id))
            or (row.get("created_by") is not None and str(row["created_by"]) == str(user_id))
        )
    return bool(
        is_class_manager(access)
        or (owner_id is not None and str(owner_id) == str(user_id))
        or (row.get("created_by") is not None and str(row["created_by"]) == str(user_id))
        or key_is_released
    )


async def require_resource_access(
    connection: SQLiteConnection | SQLiteDatabase,
    class_id: UUID | str,
    user_id: UUID | str,
    *,
    capability: str = "read",
    publication_state: str | None = None,
    release_at: str | None = None,
    created_by: object = None,
) -> dict[str, Any]:
    access = await class_access(connection, class_id, user_id)
    require_capability(access, capability)
    if capability == "read" and not is_effectively_published(publication_state, release_at):
        if not can_view_unreleased(access, user_id, created_by):
            raise ApiError(404, "not_found", "The requested resource was not found.")
    return access


async def material_access(
    connection: SQLiteConnection | SQLiteDatabase,
    material_id: UUID | str,
    user_id: UUID | str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return a readable material and class access, preserving private ownership."""
    row = await connection.fetchrow(
        "SELECT id,class_id,origin_class_id,resource_scope,owner_id,created_by,"
        "publication_state,release_at,feedback_release_policy,kind,status,title,content,scope,options,created_at,"
        "assessment_validation_state,assessment_validation_version,generated_at,failure_code,"
        "assessment_engine_version,quality_review_version,"
        "sources_outdated,sources_missing,context_truncated "
        "FROM study_materials WHERE id=?1",
        material_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    if row["resource_scope"] == "personal":
        if str(row["owner_id"]) != str(user_id):
            raise ApiError(404, "not_found", "The requested resource was not found.")
        return row, None
    if row["class_id"] is None:
        raise ApiError(404, "not_found", "The requested resource was not found.")
    access = await class_access(connection, row["class_id"], user_id, required="read")
    if not is_effectively_published(row["publication_state"], row["release_at"]):
        if not can_view_unreleased(access, user_id, row["created_by"]):
            raise ApiError(404, "not_found", "The requested resource was not found.")
    return row, access


def require_resource_mutation(
    access: dict[str, Any],
    user_id: UUID | str,
    created_by: object,
    capability: str,
    *,
    resource_scope: str | None = None,
    publication_state: str | None = None,
    release_at: str | None = None,
) -> None:
    """Keep ordinary mutations author-scoped; allow explicit moderation of released contributions."""
    if access["is_owner"]:
        return
    is_author = created_by is not None and str(created_by) == str(user_id)
    if is_author and capability in access["capabilities"]:
        return
    if (
        capability == "delete"
        and not is_author
        and created_by is not None
        and resource_scope == "student_contribution"
        and "delete_others" in access["capabilities"]
    ):
        if not is_effectively_published(publication_state, release_at):
            raise ApiError(404, "not_found", "The requested resource was not found.")
        return
    raise ApiError(403, "insufficient_permission", "You do not have permission to do that.")
