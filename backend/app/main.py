"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.class_concept_graph import router as class_concept_graph_router
from app.api.class_library import router as class_library_router
from app.api.class_memberships import router as class_memberships_router
from app.api.class_question_bank import router as class_question_bank_router
from app.api.errors import ApiError
from app.api.guide_chat import router as guide_chat_router
from app.api.learning import router as learning_router
from app.api.live import router as live_router
from app.api.mastery_ladders import router as mastery_ladders_router
from app.api.quiz_attempts import router as quiz_attempts_router
from app.api.routes import router
from app.api.schemas import ErrorResponse
from app.api.study_material_organization import router as study_material_organization_router
from app.api.study_performance import router as study_performance_router
from app.api.study_tutor import router as study_tutor_router
from app.api.tutor_memory import router as tutor_memory_router
from app.api.upload_documents import router as upload_documents_router
from app.config import Settings, get_settings
from app.middleware.request_context import RequestContextMiddleware
from app.middleware.request_size import RequestSizeLimitMiddleware
from app.security.rate_limit import InMemoryRateLimiter
from app.services.ai_provider_settings import load_provider_config
from app.services.ai_usage_ledger import UsageLedgerRecorder
from app.services.concept_graph import (
    enqueue_missing_concept_extractions,
    process_queued_concept_extractions,
    recover_concept_extraction_jobs,
)
from app.services.database import create_database, recover_interrupted_jobs
from app.services.gemini_client import LIVE_API_VERSION, create_genai_client
from app.services.gemini_content import GeminiContentService
from app.services.guide_chat import GuideChatService
from app.services.lean_proofs import LeanProofService, LeanRuntimeUnavailable
from app.services.learning_calibration import (
    enqueue_learning_calibration_refresh,
    enqueue_question_bank_replenishment,
    process_learning_calibration_jobs,
    process_question_bank_replenishment_jobs,
    question_bank_replenishment_wait_seconds,
    recover_learning_calibration_jobs,
)
from app.services.live_sessions import GeminiLiveSessionService, LiveSessionTicketStore
from app.services.openai_compatible import OpenAICompatibleClient
from app.services.quiz_reasoning import QuizReasoningService
from app.services.source_index import (
    MAX_BACKFILL_PER_START,
    enqueue_backfill_jobs,
    process_queued_jobs,
    recover_source_index_jobs,
)
from app.services.storage import LocalPrivateStorage
from app.services.study_performance import StudyPerformanceScoringService
from app.services.study_tutor import StudyTutorService
from app.services.tutor_memory import TutorMemoryService

logger = logging.getLogger("screen_tutor.errors")
_PRIVATE_GUIDE_ROUTE = re.compile(
    r"^/api/study-materials/[0-9a-fA-F-]{36}/(?:guide-chat|guide-progress|"
    r"guide-sections/[0-9]{1,2}/mini-quiz)$"
)


def _private_guide_error_headers(request: Request) -> dict[str, str]:
    if _PRIVATE_GUIDE_ROUTE.fullmatch(request.url.path):
        return {"Cache-Control": "private, no-store", "Pragma": "no-cache"}
    return {}


async def _run_source_index_backfill(application: FastAPI) -> None:
    """Drain legacy source-index work in small batches after the server starts."""
    database = application.state.database
    try:
        while True:
            await enqueue_backfill_jobs(database, limit=MAX_BACKFILL_PER_START)
            processed = await process_queued_jobs(application, limit=MAX_BACKFILL_PER_START)
            queued = await database.fetchval(
                "SELECT count(*) FROM source_index_jobs WHERE status='queued'"
            )
            unindexed = await database.fetchval(
                "SELECT count(*) FROM course_notes n "
                "LEFT JOIN source_index_heads h ON h.note_id=n.id "
                "WHERE n.status='ready' AND (h.note_id IS NULL OR "
                "h.processing_revision<>n.processing_revision) AND NOT EXISTS ("
                "SELECT 1 FROM source_index_jobs j WHERE j.note_id=n.id "
                "AND j.processing_revision=n.processing_revision) AND NOT EXISTS ("
                "SELECT 1 FROM uploaded_assessments a WHERE a.note_id=n.id AND "
                "(a.has_student_responses=1 OR a.score_percent IS NOT NULL "
                "OR a.grading_result IS NOT NULL))"
            )
            if not queued and not unindexed:
                return
            if not processed:
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("source_index_backfill_worker_failed")


async def _run_concept_graph_worker(application: FastAPI) -> None:
    """Drain bounded class graph jobs after the server is available."""
    database = application.state.database
    try:
        while True:
            try:
                await enqueue_missing_concept_extractions(database, limit=20)
                processed = await process_queued_concept_extractions(application, limit=2)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("concept_graph_extraction_batch_failed")
                processed = 0
            await asyncio.sleep(0.05 if processed else 2)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("concept_graph_extraction_worker_failed")


async def _run_learning_calibration_worker(application: FastAPI) -> None:
    """Run bounded bank maintenance and aggregate calibration checks."""
    database = application.state.database
    try:
        while True:
            try:
                await enqueue_question_bank_replenishment(database)
                processed = await process_question_bank_replenishment_jobs(application, limit=1)
                await enqueue_learning_calibration_refresh(database)
                await process_learning_calibration_jobs(database, limit=1)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("learning_calibration_batch_failed")
                processed = 0
            delay = (
                2.0
                if processed
                else await question_bank_replenishment_wait_seconds(database)
            )
            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("learning_calibration_worker_failed")


def create_app(
    *,
    settings_override: Settings | None = None,
) -> FastAPI:
    """Build an application instance; overrides make integration composition explicit."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        settings = settings_override or get_settings()
        # Gemini remains required for Live voice and provider-specific file processing.
        settings.require_gemini_key()
        application.state.settings = settings
        application.state.live_session_rate_limiter = InMemoryRateLimiter(
            settings.live_session_rate_limit,
            settings.live_session_rate_window_seconds,
        )
        application.state.calculator_rate_limiter = InMemoryRateLimiter(
            settings.calculator_rate_limit,
            settings.calculator_rate_window_seconds,
        )
        application.state.lean_rate_limiter = InMemoryRateLimiter(
            settings.lean_rate_limit,
            settings.lean_rate_window_seconds,
        )
        application.state.auth_rate_limiter = InMemoryRateLimiter(
            settings.auth_rate_limit,
            settings.auth_rate_window_seconds,
        )
        application.state.upload_rate_limiter = InMemoryRateLimiter(
            settings.upload_rate_limit,
            settings.upload_rate_window_seconds,
        )
        application.state.source_search_rate_limiter = InMemoryRateLimiter(90, 60)
        application.state.concept_graph_rate_limiter = InMemoryRateLimiter(5, 300)
        application.state.invitation_rate_limiter = InMemoryRateLimiter(12, 60)
        application.state.study_tutor_rate_limiter = InMemoryRateLimiter(
            settings.study_tutor_rate_limit,
            settings.study_tutor_rate_window_seconds,
        )
        application.state.study_performance_rate_limiter = InMemoryRateLimiter(5, 60)
        application.state.quiz_reasoning_rate_limiter = InMemoryRateLimiter(20, 60)
        application.state.ai_provider_test_rate_limiter = InMemoryRateLimiter(5, 300)

        lean_proof_service = LeanProofService(settings)
        if settings.lean_tool_enabled:
            try:
                await lean_proof_service.ensure_ready()
            except LeanRuntimeUnavailable:
                raise RuntimeError(
                    "Lean proof checking is enabled but Lean/Mathlib is not installed or prepared. "
                    "Follow the backend README setup steps or set LEAN_TOOL_ENABLED=false."
                ) from None
            application.state.lean_proof_service = lean_proof_service
        else:
            application.state.lean_proof_service = None

        application.state.database = await create_database(settings)
        application.state.ai_provider_config = await load_provider_config(
            application.state.database, settings
        )
        application.state.ai_usage_ledger = UsageLedgerRecorder(
            application.state.database, asyncio.get_running_loop()
        )
        openai_client = OpenAICompatibleClient(
            application.state.ai_provider_config,
            usage_recorder=application.state.ai_usage_ledger,
        )
        application.state.openai_compatible_client = openai_client
        await recover_interrupted_jobs(application.state.database)
        await recover_learning_calibration_jobs(application.state.database)
        application.state.private_storage = LocalPrivateStorage(settings)
        await recover_source_index_jobs(application.state.database)
        await recover_concept_extraction_jobs(application.state.database)
        application.state.live_session_ticket_store = LiveSessionTicketStore()

        client = create_genai_client(settings, api_version=LIVE_API_VERSION)
        application.state.gemini_client = client
        content_client = create_genai_client(settings, no_timeout=True)
        quiz_client = openai_client
        application.state.gemini_content_client = content_client
        application.state.quiz_reasoning_client = quiz_client
        application.state.quiz_reasoning_service = QuizReasoningService(
            quiz_client,
            settings.gemini_text_model,
        )
        application.state.gemini_content_service = GeminiContentService(
            openai_client,
            settings,
            media_client=content_client,
        )
        application.state.study_tutor_service = StudyTutorService(
            openai_client,
            "admin-configured",
        )
        application.state.guide_chat_service = GuideChatService(
            openai_client,
            "admin-configured",
        )
        application.state.study_performance_scoring_service = StudyPerformanceScoringService(
            openai_client
        )
        application.state.tutor_memory_service = TutorMemoryService(
            application.state.database,
            openai_client,
            "admin-configured",
        )
        application.state.live_session_service = GeminiLiveSessionService(
            client,
            settings,
            calculator_limiter=application.state.calculator_rate_limiter,
            lean_limiter=application.state.lean_rate_limiter,
            lean_service=application.state.lean_proof_service,
            memory_service=application.state.tutor_memory_service,
        )

        source_index_task = asyncio.create_task(_run_source_index_backfill(application))
        concept_graph_task = asyncio.create_task(_run_concept_graph_worker(application))
        # Background replenishment makes provider calls and is enabled for the
        # production service. Local developers and test clients can exercise the
        # same bounded service functions explicitly without surprise API calls.
        learning_calibration_task = (
            asyncio.create_task(_run_learning_calibration_worker(application))
            if settings.app_env == "production"
            else None
        )
        try:
            yield
        finally:
            if learning_calibration_task is not None:
                learning_calibration_task.cancel()
            concept_graph_task.cancel()
            source_index_task.cancel()
            if learning_calibration_task is not None:
                try:
                    await learning_calibration_task
                except asyncio.CancelledError:
                    pass
            try:
                await concept_graph_task
            except asyncio.CancelledError:
                pass
            try:
                await source_index_task
            except asyncio.CancelledError:
                pass
            if client is not None:
                client.close()
            if content_client is not None:
                content_client.close()
            await application.state.database.close()

    application = FastAPI(
        title="Screen Tutor API",
        description=(
            "Authenticated class library, server-owned Live sessions and content processing, "
            "bounded symbolic calculator, and Lean proof checker. Live media is not stored."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    application.include_router(router)
    application.include_router(auth_router)
    application.include_router(admin_router)
    application.include_router(live_router)
    application.include_router(class_library_router)
    application.include_router(study_material_organization_router)
    application.include_router(class_memberships_router)
    application.include_router(class_question_bank_router)
    application.include_router(class_concept_graph_router)
    application.include_router(upload_documents_router)
    application.include_router(tutor_memory_router)
    application.include_router(study_tutor_router)
    application.include_router(guide_chat_router)
    application.include_router(study_performance_router)
    application.include_router(quiz_attempts_router)
    application.include_router(learning_router)
    application.include_router(mastery_ladders_router)

    @application.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        headers = _private_guide_error_headers(request)
        if exc.retry_after_seconds is not None:
            headers["Retry-After"] = str(exc.retry_after_seconds)
        body = ErrorResponse(code=exc.code, message=exc.message, request_id=request_id)
        return JSONResponse(
            status_code=exc.status_code,
            content=body.model_dump(mode="json"),
            headers=headers,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        invalid_class_id = any(
            error.get("loc") == ("path", "class_id")
            and error.get("type") in {"uuid_parsing", "uuid_type"}
            for error in exc.errors()
        )
        body = ErrorResponse(
            code="invalid_class_id" if invalid_class_id else "invalid_request",
            message=(
                "This class ID is not a backend UUID. Create or sync the class and its source "
                "uploads before using backend generation."
                if invalid_class_id
                else "The request is malformed or contains unsupported fields."
            ),
            request_id=request_id,
        )
        return JSONResponse(
            status_code=400,
            content=body.model_dump(mode="json"),
            headers=_private_guide_error_headers(request),
        )

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
        return JSONResponse(
            status_code=exc.status_code,
            content=body.model_dump(mode="json"),
            headers=_private_guide_error_headers(request),
        )

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
        return JSONResponse(
            status_code=500,
            content=body.model_dump(mode="json"),
            headers=_private_guide_error_headers(request),
        )

    # CORS is explicit and credential-free. Browser auth, if configured, uses
    # the Authorization header and never wildcard origins.
    cors_settings = settings_override or get_settings()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )
    application.add_middleware(
        RequestSizeLimitMiddleware,
        capability_max_bytes=cors_settings.request_body_max_bytes,
        default_max_bytes=cors_settings.default_request_body_max_bytes,
        lean_max_bytes=cors_settings.lean_request_max_bytes,
        library_json_max_bytes=cors_settings.library_json_body_max_bytes,
        upload_max_bytes=cors_settings.max_upload_bytes,
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
