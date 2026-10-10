"""HTTP endpoints exposed by the backend."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Body, Request, Response

from app.api.dependencies import (
    database_pool,
    enforce_calculator_request_policy,
    enforce_token_request_policy,
)
from app.api.errors import ApiError
from app.api.schemas import (
    CalculatorRequest,
    CalculatorResponse,
    HealthResponse,
    LiveTokenRequest,
    LiveTokenResponse,
)
from app.config import Settings
from app.services.calculator import CalculatorInputError, evaluate_expression
from app.services.gemini_tokens import GeminiTokenService

logger = logging.getLogger("screen_tutor.api")
router = APIRouter()


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("/healthz", response_model=HealthResponse, tags=["health"])
async def health_check() -> HealthResponse:
    """Report only whether the process completed startup successfully."""
    return HealthResponse()


@router.post(
    "/api/live-token",
    response_model=LiveTokenResponse,
    tags=["live"],
    responses={
        400: {"description": "Malformed request or unexpected fields."},
        401: {"description": "The configured demo gate was not satisfied."},
        403: {"description": "The request origin is not allow-listed."},
        413: {"description": "The request body exceeds the configured limit."},
        429: {"description": "The token issuance rate limit was reached."},
        502: {"description": "Gemini token provisioning failed."},
        503: {"description": "Server configuration is unavailable."},
    },
)
async def create_live_token(
    request: Request,
    response: Response,
    payload: LiveTokenRequest = Body(default=LiveTokenRequest()),
) -> LiveTokenResponse:
    """Issue one constrained token; media continues directly from browser to Gemini."""
    settings = _settings(request)
    await enforce_token_request_policy(request, settings)

    class_context = None
    if payload.class_id is not None:
        user_id = getattr(request.state, "user_id", None)
        if user_id is None:
            raise ApiError(401, "authentication_required", "Please sign in to use class materials.")
        pool = database_pool(request)
        class_row = await pool.fetchrow(
            "SELECT id, name FROM public.course_classes WHERE id = $1 AND owner_id = $2",
            payload.class_id,
            user_id,
        )
        if class_row is None:
            raise ApiError(404, "not_found", "The requested resource was not found.")
        notes = await pool.fetch(
            "SELECT title, left(COALESCE(summary, extracted_text, ''), 40000) AS context "
            "FROM public.course_notes "
            "WHERE class_id = $1 AND status = 'ready' ORDER BY "
            "CASE WHEN kind = 'syllabus' THEN 0 ELSE 1 END, created_at",
            payload.class_id,
        )
        context_parts = [f"Class: {class_row['name']}\n"]
        remaining = 40_000
        for note in notes:
            excerpt = str(note["context"] or "")[:remaining]
            if excerpt:
                context_parts.append(f"Source: {note['title']}\n{excerpt}")
                remaining -= len(excerpt)
            if remaining <= 0:
                break
        class_context = "\n\n".join(context_parts)

    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"

    service: GeminiTokenService = request.app.state.token_service
    try:
        result = await service.create_token(class_context=class_context)
    except Exception as exc:  # noqa: BLE001 - upstream details must not reach clients or logs
        request_id = getattr(request.state, "request_id", "unknown")
        logger.error(
            "upstream_failure request_id=%s category=gemini_token_provisioning_failed type=%s",
            request_id,
            type(exc).__name__,
        )
        raise ApiError(
            502,
            "token_provisioning_failed",
            "A Live session token could not be created. Please try again.",
        ) from None

    return LiveTokenResponse(
        access_token=result.access_token,
        model=result.model,
        expires_at=result.expires_at,
    )


@router.post(
    "/api/tools/calculate",
    response_model=CalculatorResponse,
    tags=["tools"],
    responses={
        400: {"description": "The expression is invalid or outside calculator limits."},
        401: {"description": "The configured app gate was not satisfied."},
        403: {"description": "The request origin is not allow-listed."},
        413: {"description": "The request body exceeds the configured limit."},
        429: {"description": "The calculator rate limit was reached."},
    },
)
async def calculate_expression(
    request: Request,
    response: Response,
    payload: CalculatorRequest,
) -> CalculatorResponse:
    """Evaluate bounded symbolic math for a Live function call; do not log inputs."""
    await enforce_calculator_request_policy(request, _settings(request))
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    try:
        result = await asyncio.to_thread(evaluate_expression, payload.expression)
    except CalculatorInputError:
        raise ApiError(
            400,
            "invalid_expression",
            "The calculator accepts only bounded numeric or symbolic math expressions.",
        ) from None
    return CalculatorResponse(result=result)
