"""Request policy dependencies for token issuance."""

from __future__ import annotations

import hmac
import logging
from urllib.parse import urlsplit

from fastapi import Request

from app.api.errors import ApiError
from app.config import Settings
from app.security.rate_limit import InMemoryRateLimiter

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


async def enforce_token_request_policy(request: Request, settings: Settings) -> None:
    """Require an allow-listed browser origin, configured gate, and rate budget."""
    request_id = getattr(request.state, "request_id", "unknown")
    origin_header = request.headers.get("origin")
    origin = _normalized_origin(origin_header) if origin_header else None
    if origin is None or origin not in settings.allowed_origins:
        logger.warning(
            "request_rejected request_id=%s category=origin_not_allowed",
            request_id,
        )
        raise ApiError(403, "origin_not_allowed", "This request origin is not allowed.")

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

    limiter: InMemoryRateLimiter = request.app.state.rate_limiter
    retry_after = await limiter.check(_client_identity(request))
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
