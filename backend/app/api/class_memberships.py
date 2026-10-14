"""Class membership, capability, and temporary invitation endpoints."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response

from app.api.dependencies import database, enforce_sqlite_api_policy
from app.api.errors import ApiError
from app.api.schemas import (
    InvitationCreate,
    InvitationRedeem,
    MemberCapabilitiesUpdate,
)
from app.security.rate_limit import InMemoryRateLimiter
from app.services.class_access import class_access

router = APIRouter(tags=["class-memberships"])


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def _user(request: Request) -> tuple[UUID, object]:
    user_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    return user_id, database(request)


async def _rate_limit(request: Request, identity: str) -> None:
    limiter: InMemoryRateLimiter = request.app.state.invitation_rate_limiter
    retry_after = await limiter.check(identity)
    if retry_after is not None:
        raise ApiError(
            429,
            "invitation_rate_limited",
            "Too many invitation attempts. Please wait before trying again.",
            retry_after_seconds=retry_after,
        )


@router.post("/api/classes/{class_id}/invitations", status_code=201)
async def create_invitation(
    class_id: UUID, payload: InvitationCreate, request: Request
) -> dict:
    user_id, db = await _user(request)
    access = await class_access(db, class_id, user_id, required="invite")
    requested = set(payload.capabilities)
    if not requested <= set(access["capabilities"]):
        raise ApiError(
            403,
            "capability_not_delegable",
            "You can only grant capabilities that you hold.",
        )
    code = secrets.token_urlsafe(32)
    digest = hashlib.sha256(code.encode("ascii")).hexdigest()
    invitation_id = uuid4()
    now = datetime.now(UTC)
    expires_at = _timestamp(now + timedelta(days=payload.expires_in_days))
    async with db.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO class_invitations "
                "(id,class_id,code_digest,created_by,expires_at,max_uses) "
                "VALUES (?1,?2,?3,?4,?5,?6)",
                invitation_id,
                class_id,
                digest,
                user_id,
                expires_at,
                payload.max_uses,
            )
            await connection.executemany(
                "INSERT INTO class_invitation_capabilities (invitation_id,capability) "
                "VALUES (?1,?2)",
                [(str(invitation_id), capability) for capability in sorted(requested)],
            )
    return {
        "id": str(invitation_id),
        "class_id": str(class_id),
        "code": code,
        "expires_at": expires_at,
        "max_uses": payload.max_uses,
        "successful_uses": 0,
        "capabilities": sorted(requested),
    }


@router.get("/api/classes/{class_id}/invitations")
async def list_invitations(class_id: UUID, request: Request) -> dict:
    user_id, db = await _user(request)
    await class_access(db, class_id, user_id, required="invite")
    rows = await db.fetch(
        "SELECT id,class_id,created_by,created_at,expires_at,max_uses,successful_uses,revoked_at "
        "FROM class_invitations WHERE class_id=?1 ORDER BY created_at DESC",
        class_id,
    )
    for row in rows:
        caps = await db.fetch(
            "SELECT capability FROM class_invitation_capabilities WHERE invitation_id=?1 "
            "ORDER BY capability",
            row["id"],
        )
        row["capabilities"] = [item["capability"] for item in caps]
        row["active"] = (
            row["revoked_at"] is None
            and row["successful_uses"] < row["max_uses"]
            and row["expires_at"] > _timestamp(datetime.now(UTC))
        )
    return {"items": rows}


@router.delete("/api/classes/{class_id}/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(
    class_id: UUID, invitation_id: UUID, request: Request
) -> Response:
    user_id, db = await _user(request)
    access = await class_access(db, class_id, user_id, required="invite")
    invitation = await db.fetchrow(
        "SELECT created_by FROM class_invitations WHERE id=?1 AND class_id=?2",
        invitation_id,
        class_id,
    )
    if invitation is not None and not access["is_owner"] and str(invitation["created_by"]) != str(user_id):
        granted = {
            row["capability"] for row in await db.fetch(
                "SELECT capability FROM class_invitation_capabilities WHERE invitation_id=?1",
                invitation_id,
            )
        }
        if not granted <= set(access["capabilities"]):
            raise ApiError(
                403,
                "capability_not_delegable",
                "You cannot revoke an invitation with capabilities beyond your own.",
            )
    row = await db.fetchrow(
        "UPDATE class_invitations SET revoked_at=COALESCE(revoked_at,?3) "
        "WHERE id=?1 AND class_id=?2 RETURNING id",
        invitation_id,
        class_id,
        _timestamp(datetime.now(UTC)),
    )
    if row is None:
        raise ApiError(404, "not_found", "The requested invitation was not found.")
    return Response(status_code=204)


@router.post("/api/class-invitations/redeem")
async def redeem_invitation(payload: InvitationRedeem, request: Request) -> dict:
    user_id, db = await _user(request)
    await _rate_limit(request, f"user:{user_id}")
    peer = request.client.host if request.client else "unknown"
    await _rate_limit(request, f"ip:{peer}")
    digest = hashlib.sha256(payload.code.encode("ascii")).hexdigest()
    now = _timestamp(datetime.now(UTC))
    async with db.acquire() as connection:
        async with connection.transaction():
            invite = await connection.fetchrow(
                "SELECT i.*,c.owner_id,c.name AS class_name FROM class_invitations i "
                "JOIN course_classes c ON c.id=i.class_id WHERE i.code_digest=?1",
                digest,
            )
            if invite is None or invite["revoked_at"] is not None:
                raise ApiError(404, "invitation_invalid", "This invitation is invalid or revoked.")
            if str(invite["owner_id"]) == str(user_id):
                return {"status": "already_member", "class_id": invite["class_id"]}

            membership = await connection.fetchrow(
                "SELECT status FROM class_memberships WHERE class_id=?1 AND user_id=?2",
                invite["class_id"],
                user_id,
            )
            if membership and membership["status"] == "active":
                return {"status": "already_member", "class_id": invite["class_id"]}
            if invite["expires_at"] <= now:
                raise ApiError(410, "invitation_expired", "This invitation has expired.")
            if invite["successful_uses"] >= invite["max_uses"]:
                raise ApiError(410, "invitation_exhausted", "This invitation has reached its use limit.")

            uses = await connection.fetchrow(
                "UPDATE class_invitations SET successful_uses=successful_uses+1 "
                "WHERE id=?1 AND revoked_at IS NULL AND expires_at>?2 "
                "AND successful_uses<max_uses RETURNING successful_uses",
                invite["id"],
                now,
            )
            if uses is None:
                raise ApiError(410, "invitation_exhausted", "This invitation is no longer available.")

            await connection.execute(
                "INSERT INTO class_memberships (class_id,user_id,status,added_by,joined_at,ended_at) "
                "VALUES (?1,?2,'active',?3,?4,NULL) ON CONFLICT(class_id,user_id) DO UPDATE SET "
                "status='active',added_by=excluded.added_by,joined_at=excluded.joined_at,ended_at=NULL",
                invite["class_id"],
                user_id,
                invite["created_by"],
                now,
            )
            await connection.execute(
                "DELETE FROM class_membership_capabilities WHERE class_id=?1 AND user_id=?2",
                invite["class_id"],
                user_id,
            )
            await connection.execute(
                "INSERT INTO class_membership_capabilities (class_id,user_id,capability,granted_by) "
                "SELECT ?1,?2,capability,?4 FROM class_invitation_capabilities "
                "WHERE invitation_id=?3",
                invite["class_id"],
                user_id,
                invite["id"],
                invite["created_by"],
            )
    return {
        "status": "joined",
        "class_id": invite["class_id"],
        "class_name": invite["class_name"],
        "capabilities": [row["capability"] for row in await db.fetch(
            "SELECT capability FROM class_membership_capabilities WHERE class_id=?1 AND user_id=?2 "
            "ORDER BY capability", invite["class_id"], user_id,
        )],
    }


@router.get("/api/classes/{class_id}/members")
async def list_members(class_id: UUID, request: Request) -> dict:
    user_id, db = await _user(request)
    await class_access(db, class_id, user_id, required="read")
    owner_id = await db.fetchval("SELECT owner_id FROM course_classes WHERE id=?1", class_id)
    rows = await db.fetch(
        "SELECT m.user_id,u.username,u.display_name,m.status,m.joined_at,m.added_by,m.ended_at "
        "FROM class_memberships m JOIN users u ON u.id=m.user_id "
        "WHERE m.class_id=?1 AND m.status='active' ORDER BY m.joined_at,m.user_id",
        class_id,
    )
    for row in rows:
        row["is_owner"] = str(row["user_id"]) == str(owner_id)
        row["capabilities"] = [item["capability"] for item in await db.fetch(
            "SELECT capability FROM class_membership_capabilities WHERE class_id=?1 AND user_id=?2 "
            "ORDER BY capability", class_id, row["user_id"],
        )]
    return {"items": rows}


@router.patch("/api/classes/{class_id}/members/{member_id}/capabilities")
async def update_member_capabilities(
    class_id: UUID,
    member_id: UUID,
    payload: MemberCapabilitiesUpdate,
    request: Request,
) -> dict:
    user_id, db = await _user(request)
    access = await class_access(db, class_id, user_id, required="manage_permissions")
    if str(member_id) == str(user_id) and not access["is_owner"]:
        raise ApiError(403, "self_permission_change", "You cannot change your own class permissions.")
    target = await db.fetchrow(
        "SELECT status FROM class_memberships WHERE class_id=?1 AND user_id=?2",
        class_id,
        member_id,
    )
    owner_id = await db.fetchval("SELECT owner_id FROM course_classes WHERE id=?1", class_id)
    if target is None or target["status"] != "active":
        raise ApiError(404, "not_found", "The requested class member was not found.")
    if str(member_id) == str(owner_id):
        raise ApiError(409, "owner_permissions_fixed", "The class owner's permissions cannot be changed.")
    requested = set(payload.capabilities)
    if not requested <= set(access["capabilities"]):
        raise ApiError(403, "capability_not_delegable", "You can only grant capabilities that you hold.")
    async with db.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "DELETE FROM class_membership_capabilities WHERE class_id=?1 AND user_id=?2",
                class_id,
                member_id,
            )
            await connection.executemany(
                "INSERT INTO class_membership_capabilities (class_id,user_id,capability,granted_by) "
                "VALUES (?1,?2,?3,?4)",
                [(str(class_id), str(member_id), capability, str(user_id))
                 for capability in sorted(requested)],
            )
    return {"user_id": str(member_id), "capabilities": sorted(requested)}


@router.delete("/api/classes/{class_id}/members/{member_id}", status_code=204)
async def remove_member(class_id: UUID, member_id: UUID, request: Request) -> Response:
    user_id, db = await _user(request)
    access = await class_access(db, class_id, user_id, required="manage_members")
    owner_id = await db.fetchval("SELECT owner_id FROM course_classes WHERE id=?1", class_id)
    if str(member_id) == str(owner_id):
        raise ApiError(409, "owner_cannot_be_removed", "The class owner cannot be removed.")
    target_caps = {
        row["capability"] for row in await db.fetch(
            "SELECT capability FROM class_membership_capabilities WHERE class_id=?1 AND user_id=?2",
            class_id, member_id,
        )
    }
    if not target_caps <= set(access["capabilities"]):
        raise ApiError(
            403,
            "capability_not_delegable",
            "You cannot remove a member with permissions beyond your own.",
        )
    async with db.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(
                "UPDATE class_memberships SET status='removed',ended_at=?3 "
                "WHERE class_id=?1 AND user_id=?2 AND status='active' RETURNING user_id",
                class_id,
                member_id,
                _timestamp(datetime.now(UTC)),
            )
            if row is None:
                raise ApiError(404, "not_found", "The requested class member was not found.")
            await connection.execute(
                "DELETE FROM class_membership_capabilities WHERE class_id=?1 AND user_id=?2",
                class_id,
                member_id,
            )
    await request.app.state.live_session_ticket_store.revoke_member(class_id, member_id)
    return Response(status_code=204)


@router.post("/api/classes/{class_id}/leave", status_code=204)
async def leave_class(class_id: UUID, request: Request) -> Response:
    user_id, db = await _user(request)
    access = await class_access(db, class_id, user_id)
    if access["is_owner"]:
        raise ApiError(409, "owner_cannot_leave", "The class owner cannot leave their class.")
    async with db.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(
                "UPDATE class_memberships SET status='left',ended_at=?3 "
                "WHERE class_id=?1 AND user_id=?2 AND status='active' RETURNING user_id",
                class_id,
                user_id,
                _timestamp(datetime.now(UTC)),
            )
            if row is None:
                raise ApiError(404, "not_found", "The requested class was not found.")
            await connection.execute(
                "DELETE FROM class_membership_capabilities WHERE class_id=?1 AND user_id=?2",
                class_id,
                user_id,
            )
    await request.app.state.live_session_ticket_store.revoke_member(class_id, user_id)
    return Response(status_code=204)
