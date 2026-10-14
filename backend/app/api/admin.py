"""Administrator-only signup request review endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, Response

from app.api.dependencies import database, enforce_origin_policy, session_user
from app.api.errors import ApiError
from app.api.schemas import (
    SignupRequestDecision,
    SignupRequestsResponse,
    SignupRequestSummary,
)
from app.services import auth as accounts

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def _require_admin(request: Request) -> dict:
    settings = request.app.state.settings
    await enforce_origin_policy(request, settings, required=request.method not in {"GET", "HEAD"})
    user = await session_user(request)
    if user is None:
        raise ApiError(401, "authentication_required", "Please log in to use the admin page.")
    if not user.get("is_admin"):
        raise ApiError(403, "admin_required", "Administrator access is required.")
    return user


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"


@router.get("/signup-requests", response_model=SignupRequestsResponse)
async def list_signup_requests(request: Request, response: Response) -> SignupRequestsResponse:
    """List pending requests without exposing password hashes."""
    await _require_admin(request)
    rows = await accounts.pending_signup_requests(database(request))
    _no_store(response)
    return SignupRequestsResponse(requests=[SignupRequestSummary(**row) for row in rows])


@router.post("/signup-requests/{request_id}/approve", response_model=SignupRequestDecision)
async def approve_signup_request(
    request_id: UUID, request: Request, response: Response
) -> SignupRequestDecision:
    admin = await _require_admin(request)
    result = await accounts.decide_signup_request(
        database(request), request_id, admin["id"], approve=True
    )
    _no_store(response)
    return SignupRequestDecision(**result)


@router.post("/signup-requests/{request_id}/reject", response_model=SignupRequestDecision)
async def reject_signup_request(
    request_id: UUID, request: Request, response: Response
) -> SignupRequestDecision:
    admin = await _require_admin(request)
    result = await accounts.decide_signup_request(
        database(request), request_id, admin["id"], approve=False
    )
    _no_store(response)
    return SignupRequestDecision(**result)
