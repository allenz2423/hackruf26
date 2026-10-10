"""Shared origin, authentication, and rate-limit request policies."""

from __future__ import annotations

import asyncio
import hmac
import logging
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Request

from app.api.errors import ApiError
from app.config import Settings
from app.security.rate_limit import InMemoryRateLimiter
from app.services.database import SQLiteDatabase
from app.services.supabase_auth import (
    SupabaseJWTVerifier,
    SupabaseTokenInvalid,
    SupabaseVerifierUnavailable,
)
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

    origin = _normalized_origin(origin_header)
    if origin is None or origin not in settings.allowed_origins:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.warning("request_rejected request_id=%s category=origin_not_allowed", request_id)
        raise ApiError(403, "origin_not_allowed", "This request origin is not allowed.")


async def require_supabase_user(request: Request, settings: Settings) -> UUID:
    """Validate the bearer JWT and return its authenticated Supabase user UUID."""
    if settings.supabase_url is None:
        raise ApiError(503, "auth_not_configured", "Sign-in is not available right now.")

    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token.strip():
        raise ApiError(401, "authentication_required", "Please sign in to continue.")

    verifier: SupabaseJWTVerifier | None = getattr(request.app.state, "supabase_verifier", None)
    if verifier is None:
        raise ApiError(503, "auth_not_configured", "Sign-in is not available right now.")

    try:
        return await asyncio.to_thread(verifier.verify, token.strip())
    except SupabaseTokenInvalid:
        raise ApiError(401, "authentication_required", "Please sign in to continue.") from None
    except SupabaseVerifierUnavailable:
        raise ApiError(503, "auth_unavailable", "Sign-in is temporarily unavailable.") from None


async def enforce_supabase_api_policy(request: Request, settings: Settings) -> UUID:
    """Apply the browser-origin policy and require a verified Supabase user."""
    await enforce_origin_policy(request, settings, required=request.method not in {"GET", "HEAD"})
    user_id = await require_supabase_user(request, settings)
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
    """Require Supabase Auth when configured, otherwise use the demo gate."""
    await enforce_origin_policy(request, settings, required=True)
    request_id = getattr(request.state, "request_id", "unknown")

    if settings.supabase_url is not None:
        request.state.user_id = await require_supabase_user(request, settings)
        return

    if settings.demo_gate_token is not None:
        authorization = request.headers.get("authorization", "")
        scheme, separator, supplied_token = authorization.partition(" ")
        configured_token = settings.demo_gate_token.get_secret_value()
        authenticated = (
            separator == " "
            and scheme.lower() == "bearer"
            and hmac.compare_digest(supplied_token, configured_token)
        )
        if not authenticated:
            logger.warning(
                "request_rejected request_id=%s category=authentication_required",
                request_id,
            )
            raise ApiError(401, "authentication_required", "A valid app session is required.")
        return

    request_id = getattr(request.state, "request_id", "unknown")
    logger.warning("request_rejected request_id=%s category=auth_not_configured", request_id)
    raise ApiError(503, "auth_not_configured", "Sign-in or a private demo gate must be configured.")


async def enforce_token_request_policy(request: Request, settings: Settings) -> None:
    """Apply app access checks and the Live token issuance rate budget."""
    await enforce_app_request_policy(request, settings)
    request_id = getattr(request.state, "request_id", "unknown")
    limiter: InMemoryRateLimiter = request.app.state.rate_limiter
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
            "Too many token requests. Please wait before trying again.",
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
