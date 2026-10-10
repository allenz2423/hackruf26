"""FastAPI application entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import ApiError
from app.api.routes import router
from app.api.schemas import ErrorResponse
from app.config import Settings, get_settings
from app.middleware.request_context import RequestContextMiddleware
from app.middleware.request_size import RequestSizeLimitMiddleware
from app.security.rate_limit import InMemoryRateLimiter
from app.services.gemini_tokens import GeminiTokenService, create_genai_client

logger = logging.getLogger("screen_tutor.errors")


def create_app(
    *,
    settings_override: Settings | None = None,
    token_service_override: GeminiTokenService | None = None,
) -> FastAPI:
    """Build an application instance; overrides make integration composition explicit."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        settings = settings_override or get_settings()
        # Fail closed during startup. No endpoint is served with a missing API key.
        settings.require_gemini_key()
        application.state.settings = settings
        application.state.rate_limiter = InMemoryRateLimiter(
            settings.token_rate_limit,
            settings.token_rate_window_seconds,
        )
        application.state.calculator_rate_limiter = InMemoryRateLimiter(
            settings.calculator_rate_limit,
            settings.calculator_rate_window_seconds,
        )

        client = None
        if token_service_override is not None:
            application.state.token_service = token_service_override
        else:
            client = create_genai_client(settings)
            application.state.token_service = GeminiTokenService(client, settings)

        try:
            yield
        finally:
            if client is not None:
                client.close()

    application = FastAPI(
        title="Screen Tutor API",
        description=(
            "Constrained Gemini Live token service and numeric calculator. "
            "Media remains browser-to-Gemini."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    application.include_router(router)

    @application.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        headers = {}
        if exc.retry_after_seconds is not None:
            headers["Retry-After"] = str(exc.retry_after_seconds)
        body = ErrorResponse(code=exc.code, message=exc.message, request_id=request_id)
        return JSONResponse(
            status_code=exc.status_code,
            content=body.model_dump(mode="json"),
            headers=headers,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, _: RequestValidationError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        body = ErrorResponse(
            code="invalid_request",
            message="The request is malformed or contains unsupported fields.",
            request_id=request_id,
        )
        return JSONResponse(status_code=400, content=body.model_dump(mode="json"))

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        if exc.status_code == 404:
            code, message = "not_found", "The requested resource was not found."
        elif exc.status_code == 405:
            code, message = "method_not_allowed", "This method is not supported for the resource."
        else:
            code, message = "request_rejected", "The request could not be completed."
        body = ErrorResponse(code=code, message=message, request_id=request_id)
        return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.error(
            "internal_failure request_id=%s category=unhandled_exception type=%s",
            request_id or "unknown",
            type(exc).__name__,
        )
        body = ErrorResponse(
            code="internal_error",
            message="The server could not complete the request.",
            request_id=request_id,
        )
        return JSONResponse(status_code=500, content=body.model_dump(mode="json"))

    # CORS is explicit and credential-free. Browser auth, if configured, uses
    # the Authorization header and never wildcard origins.
    cors_settings = settings_override or get_settings()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )
    application.add_middleware(
        RequestSizeLimitMiddleware,
        max_body_bytes=cors_settings.request_body_max_bytes,
    )
    application.add_middleware(RequestContextMiddleware)

    frontend_path = cors_settings.frontend_dist_path
    if frontend_path.is_dir():
        application.mount(
            "/",
            StaticFiles(directory=frontend_path, html=True),
            name="frontend",
        )

    return application


app = create_app()
