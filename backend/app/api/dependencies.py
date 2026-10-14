"""Shared origin, authentication, and rate-limit request policies."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Request

from app.api.errors import ApiError
from app.config import Settings
from app.security.rate_limit import InMemoryRateLimiter
from app.services.auth import SESSION_COOKIE, user_for_token
from app.services.database import SQLiteDatabase
from app.services.storage import LocalPrivateStorage

logger = logging.getLogger("screen_tutor.security")


def _normalized_origin(value: str) -> str | None:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def origin_is_allowed(value: str | None, settings: Settings) -> bool:
    """Return whether a supplied browser origin exactly matches configuration."""
    if not value:
        return False
    origin = _normalized_origin(value)
    return origin is not None and origin in settings.allowed_origins


def _client_identity(request: Request) -> str:
    """Use the socket peer; never trust arbitrary forwarded headers here."""
    if request.client is None or not request.client.host:
        return "unknown"
    return request.client.host


async def enforce_origin_policy(request: Request, settings: Settings, *, required: bool) -> None:
    """Reject untrusted browser origins; allow origin-less safe same-origin GETs."""
    origin_header = request.headers.get("origin")
    if not origin_header:
        if required:
            request_id = getattr(request.state, "request_id", "unknown")
            logger.warning("request_rejected request_id=%s category=origin_missing", request_id)
            raise ApiError(403, "origin_not_allowed", "This request origin is not allowed.")
        return

    if not origin_is_allowed(origin_header, settings):
        request_id = getattr(request.state, "request_id", "unknown")
        logger.warning("request_rejected request_id=%s category=origin_not_allowed", request_id)
        raise ApiError(403, "origin_not_allowed", "This request origin is not allowed.")


def session_token(request: Request) -> str | None:
    """The raw login-session cookie, if the browser sent one."""
    return request.cookies.get(SESSION_COOKIE) or None


async def session_user(request: Request) -> dict | None:
    """Resolve the login cookie to an account; None means the visitor is a guest."""
    token = session_token(request)
    connection: SQLiteDatabase | None = getattr(request.app.state, "database", None)
    if token is None or connection is None:
        return None
    return await user_for_token(connection, token)


async def enforce_sqlite_api_policy(request: Request, settings: Settings) -> UUID:
    """Apply the browser-origin policy and require a logged-in account."""
    await enforce_origin_policy(request, settings, required=request.method not in {"GET", "HEAD"})
    user = await session_user(request)
    if user is None:
        raise ApiError(401, "authentication_required", "Please log in to use your class library.")
    user_id = UUID(str(user["id"]))
    request.state.user_id = user_id
    return user_id


def database(request: Request) -> SQLiteDatabase:
    """Return the initialized SQLite database or a stable service error."""
    connection: SQLiteDatabase | None = getattr(request.app.state, "database", None)
    if connection is None:
        raise ApiError(503, "database_unavailable", "The class library is temporarily unavailable.")
    return connection


def private_storage(request: Request) -> LocalPrivateStorage:
    """Return the application-owned private file store."""
    storage: LocalPrivateStorage | None = getattr(request.app.state, "private_storage", None)
    if storage is None:
        raise ApiError(503, "storage_not_configured", "File storage is temporarily unavailable.")
    return storage


async def enforce_app_request_policy(request: Request, settings: Settings) -> None:
    """Apply origin checks and enforce the configured guest-access policy."""
    await enforce_origin_policy(request, settings, required=True)
    request_id = getattr(request.state, "request_id", "unknown")
    user = await session_user(request)
    request.state.user_id = UUID(str(user["id"])) if user else None
    if user is None and not settings.allow_guest_access:
        logger.warning(
            "request_rejected request_id=%s category=authentication_required",
            request_id,
        )
        raise ApiError(401, "authentication_required", "Please log in to use Jot.")


async def enforce_live_session_request_policy(request: Request, settings: Settings) -> None:
    """Apply app access checks and the Live session ticket rate budget."""
    await enforce_app_request_policy(request, settings)
    request_id = getattr(request.state, "request_id", "unknown")
    limiter: InMemoryRateLimiter = request.app.state.live_session_rate_limiter
    identity = str(getattr(request.state, "user_id", None) or _client_identity(request))
    retry_after = await limiter.check(identity)
    if retry_after is not None:
        logger.warning(
            "request_rejected request_id=%s category=rate_limited",
            request_id,
        )
        raise ApiError(
            429,
            "rate_limited",
            "Too many tutoring sessions were started. Please wait before trying again.",
            retry_after_seconds=retry_after,
        )


async def enforce_calculator_request_policy(request: Request, settings: Settings) -> None:
    """Apply app access checks and the calculator request rate budget."""
    await enforce_app_request_policy(request, settings)
    limiter: InMemoryRateLimiter = request.app.state.calculator_rate_limiter
    identity = str(getattr(request.state, "user_id", None) or _client_identity(request))
    retry_after = await limiter.check(identity)
    if retry_after is not None:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.warning(
            "request_rejected request_id=%s category=calculator_rate_limited",
            request_id,
        )
        raise ApiError(
            429,
            "calculator_rate_limited",
            "Too many calculator requests. Please wait before trying again.",
            retry_after_seconds=retry_after,
        )


async def enforce_lean_request_policy(request: Request, settings: Settings) -> None:
    """Apply app access checks and the more restrictive Lean CPU budget."""
    await enforce_app_request_policy(request, settings)
    limiter: InMemoryRateLimiter = request.app.state.lean_rate_limiter
    identity = str(getattr(request.state, "user_id", None) or _client_identity(request))
    retry_after = await limiter.check(identity)
    if retry_after is not None:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.warning(
            "request_rejected request_id=%s category=lean_rate_limited",
            request_id,
        )
        raise ApiError(
            429,
            "lean_rate_limited",
            "Too many proof checks. Please wait before trying again.",
            retry_after_seconds=retry_after,
        )
