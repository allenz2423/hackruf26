"""Account sign-up, login, logout, and current-user endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, Response

from app.api.dependencies import database, enforce_origin_policy, session_token, session_user
from app.api.errors import ApiError
from app.api.schemas import (
    AuthUser,
    LoginRequest,
    MeResponse,
    PasswordChange,
    ProfileUpdate,
    SignupRequest,
    SignupResponse,
)
from app.config import Settings
from app.security.rate_limit import InMemoryRateLimiter
from app.services import auth as accounts

logger = logging.getLogger("screen_tutor.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client_host(request: Request) -> str:
    return request.client.host if request.client and request.client.host else "unknown"


async def _limit_attempts(request: Request, identity: str) -> None:
    limiter: InMemoryRateLimiter = request.app.state.auth_rate_limiter
    retry_after = await limiter.check(identity)
    if retry_after is not None:
        logger.warning(
            "request_rejected request_id=%s category=auth_rate_limited",
            getattr(request.state, "request_id", "unknown"),
        )
        raise ApiError(
            429,
            "auth_rate_limited",
            "Too many attempts. Please wait a few minutes and try again.",
            retry_after_seconds=retry_after,
        )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        accounts.SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "production",
        path="/",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        accounts.SESSION_COOKIE,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "production",
        path="/",
    )


@router.post("/signup", response_model=SignupResponse, status_code=201)
async def signup(request: Request, response: Response, payload: SignupRequest) -> SignupResponse:
    """Create an account or submit an administrator-reviewed signup request."""
    settings: Settings = request.app.state.settings
    await enforce_origin_policy(request, settings, required=True)
    await _limit_attempts(request, f"signup:{_client_host(request)}")

    db = database(request)
    if settings.signup_mode == "closed":
        raise ApiError(403, "signup_closed", "New account requests are currently closed.")

    if settings.signup_mode == "approval":
        await accounts.create_signup_request(
            db,
            username=payload.username,
            password=payload.password,
            email=payload.email,
            display_name=payload.display_name,
        )
        response.status_code = 202
        _no_store(response)
        return SignupResponse(
            status="pending",
            message="Your account request was sent for administrator review.",
        )

    user = await accounts.create_user(
        db,
        username=payload.username,
        password=payload.password,
        email=payload.email,
        display_name=payload.display_name,
    )
    token, _ = await accounts.create_session(db, user["id"], settings.session_ttl_days)
    _set_session_cookie(response, token, settings)
    _no_store(response)
    return SignupResponse(status="active", user=AuthUser(**accounts.public_user(user)))


@router.post("/login", response_model=MeResponse)
async def login(request: Request, response: Response, payload: LoginRequest) -> MeResponse:
    """Verify a username-or-email login and start a session."""
    settings: Settings = request.app.state.settings
    await enforce_origin_policy(request, settings, required=True)
    client_host = _client_host(request)
    # The identifier-specific bucket prevents repeated guessing at one account;
    # the IP bucket prevents bypassing it by cycling through usernames.
    await _limit_attempts(request, f"login-ip:{client_host}")
    await _limit_attempts(request, f"login:{client_host}:{payload.identifier.lower()}")

    db = database(request)
    user = await accounts.authenticate(db, payload.identifier, payload.password)
    if user is None:
        raise ApiError(401, "invalid_credentials", "Incorrect username or password.")

    token, _ = await accounts.create_session(db, user["id"], settings.session_ttl_days)
    _set_session_cookie(response, token, settings)
    _no_store(response)
    return MeResponse(
        user=AuthUser(**accounts.public_user(user)),
        guest_access_enabled=settings.allow_guest_access,
        signup_mode=settings.signup_mode,
    )


@router.post("/logout", status_code=204)
async def logout(request: Request) -> Response:
    """End the current session. Safe to call when already logged out."""
    settings: Settings = request.app.state.settings
    await enforce_origin_policy(request, settings, required=True)

    token = session_token(request)
    db = getattr(request.app.state, "database", None)
    if token and db is not None:
        await accounts.delete_session(db, token)

    response = Response(status_code=204)
    _clear_session_cookie(response, settings)
    _no_store(response)
    return response


@router.get("/me", response_model=MeResponse)
async def me(request: Request, response: Response) -> MeResponse:
    """Report who is logged in; a guest gets a null user, not an error."""
    settings: Settings = request.app.state.settings
    await enforce_origin_policy(request, settings, required=False)
    _no_store(response)

    user = await session_user(request)
    if user is None:
        if session_token(request):
            _clear_session_cookie(response, settings)
        return MeResponse(
            user=None,
            guest_access_enabled=settings.allow_guest_access,
            signup_mode=settings.signup_mode,
        )
    return MeResponse(
        user=AuthUser(**accounts.public_user(user)),
        guest_access_enabled=settings.allow_guest_access,
        signup_mode=settings.signup_mode,
    )


async def _require_user(request: Request) -> dict:
    """Origin-checked, logged-in account for endpoints that change data."""
    settings: Settings = request.app.state.settings
    await enforce_origin_policy(request, settings, required=True)
    user = await session_user(request)
    if user is None:
        raise ApiError(401, "authentication_required", "Please log in to change your account.")
    return user


@router.patch("/me", response_model=MeResponse)
async def update_me(request: Request, response: Response, payload: ProfileUpdate) -> MeResponse:
    """Change the logged-in account's username and/or email."""
    user = await _require_user(request)

    changes: dict[str, Any] = {}
    if "username" in payload.model_fields_set:
        if payload.username is None:
            raise ApiError(400, "invalid_request", "Enter a username.")
        changes["username"] = payload.username
    if "email" in payload.model_fields_set:
        changes["email"] = payload.email
    if "avatar" in payload.model_fields_set:
        changes["avatar"] = payload.avatar.model_dump() if payload.avatar else None
    if not changes:
        raise ApiError(400, "invalid_request", "Nothing to update.")

    updated = await accounts.update_profile(database(request), user["id"], changes)
    _no_store(response)
    settings: Settings = request.app.state.settings
    return MeResponse(
        user=AuthUser(**accounts.public_user(updated)),
        guest_access_enabled=settings.allow_guest_access,
        signup_mode=settings.signup_mode,
    )


@router.post("/password", status_code=204)
async def change_password(request: Request, payload: PasswordChange) -> Response:
    """Change the password. Other devices are logged out; this session stays."""
    user = await _require_user(request)
    await _limit_attempts(request, f"password:{user['id']}")

    await accounts.change_password(
        database(request),
        user["id"],
        payload.current_password,
        payload.new_password,
        keep_session_token=session_token(request),
    )
    response = Response(status_code=204)
    _no_store(response)
    return response
