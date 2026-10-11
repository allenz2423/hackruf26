"""HTTP endpoints exposed by the backend."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request, Response

from app.api.dependencies import (
    database,
    enforce_calculator_request_policy,
    enforce_lean_request_policy,
    enforce_live_session_request_policy,
)
from app.api.errors import ApiError
from app.api.schemas import (
    CalculatorRequest,
    CalculatorResponse,
    HealthResponse,
    LeanProofRequest,
    LeanProofResponse,
    LiveSessionRequest,
    LiveSessionTicketResponse,
)
from app.config import Settings
from app.services.calculator import CalculatorInputError, evaluate_expression
from app.services.lean_proofs import (
    LeanProofBusy,
    LeanProofInputError,
    LeanProofService,
    LeanRuntimeUnavailable,
)
from app.services.live_sessions import LiveSessionTicketStore

logger = logging.getLogger("screen_tutor.api")
router = APIRouter()


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("/healthz", response_model=HealthResponse, tags=["health"])
async def health_check() -> HealthResponse:
    """Report only whether the process completed startup successfully."""
    return HealthResponse()


@router.post(
    "/api/live-sessions",
    response_model=LiveSessionTicketResponse,
    tags=["live"],
    responses={
        400: {"description": "Malformed request or unexpected fields."},
        401: {"description": "The configured demo gate was not satisfied."},
        403: {"description": "The request origin is not allow-listed."},
        413: {"description": "The request body exceeds the configured limit."},
        429: {"description": "The session creation rate limit was reached."},
        503: {"description": "Server configuration is unavailable."},
    },
)
async def create_live_session_ticket(
    request: Request,
    response: Response,
    payload: LiveSessionRequest,
) -> LiveSessionTicketResponse:
    """Issue a one-use ticket for a server-owned Live WebSocket session."""
    settings = _settings(request)
    await enforce_live_session_request_policy(request, settings)

    class_context = None
    if payload.class_id is not None:
        user_id = getattr(request.state, "user_id", None)
        if user_id is None:
            raise ApiError(401, "authentication_required", "Please sign in to use class materials.")
        database_connection = database(request)
        class_row = await database_connection.fetchrow(
            "SELECT id, name FROM course_classes WHERE id = ?1 AND owner_id = ?2",
            payload.class_id,
            user_id,
        )
        if class_row is None:
            raise ApiError(404, "not_found", "The requested resource was not found.")
        notes = await database_connection.fetch(
            "SELECT title, substr(COALESCE(summary, extracted_text, ''), 1, 40000) AS context "
            "FROM course_notes "
            "WHERE class_id = ?1 AND status = 'ready' ORDER BY "
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

    store: LiveSessionTicketStore = request.app.state.live_session_ticket_store
    ticket, expires_at = await store.issue(class_context, mode=payload.mode, language=payload.language)
    return LiveSessionTicketResponse(ticket=ticket, expires_at=expires_at)


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


@router.post(
    "/api/tools/verify-lean",
    response_model=LeanProofResponse,
    tags=["tools"],
    responses={
        400: {"description": "The Lean request contains unsupported syntax."},
        401: {"description": "The configured app gate was not satisfied."},
        403: {"description": "The request origin is not allow-listed."},
        413: {"description": "The request body exceeds the configured Lean limit."},
        429: {"description": "The Lean proof-check rate or concurrency limit was reached."},
        503: {"description": "Lean/Mathlib is not available."},
    },
)
async def verify_lean_proof(
    request: Request,
    response: Response,
    payload: LeanProofRequest,
) -> LeanProofResponse:
    """Check the model's formal proof without logging or storing its source."""
    settings = _settings(request)
    await enforce_lean_request_policy(request, settings)
    if not settings.lean_tool_enabled:
        raise ApiError(503, "lean_disabled", "Formal proof checking is unavailable right now.")

    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    service: LeanProofService | None = getattr(request.app.state, "lean_proof_service", None)
    if service is None:
        raise ApiError(503, "lean_unavailable", "Formal proof checking is unavailable right now.")
    try:
        return await service.verify(payload.proposition, payload.proof)
    except LeanProofInputError:
        raise ApiError(
            400,
            "invalid_lean_input",
            "The proof contains unsupported Lean syntax or exceeds the configured limits.",
        ) from None
    except LeanProofBusy:
        raise ApiError(
            429,
            "lean_busy",
            "The proof checker is busy. Please try again shortly.",
            retry_after_seconds=2,
        ) from None
    except LeanRuntimeUnavailable:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.error(
            "lean_runtime_unavailable request_id=%s",
            request_id,
        )
        raise ApiError(
            503,
            "lean_unavailable",
            "Formal proof checking is unavailable right now.",
        ) from None
