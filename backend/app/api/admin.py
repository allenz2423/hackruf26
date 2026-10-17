"""Administrator-only signup request review endpoints."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Request, Response

from app.api.dependencies import database, enforce_origin_policy, session_user
from app.api.errors import ApiError
from app.api.schemas import (
    AIProviderSettingsSummary,
    AIProviderSettingsUpdate,
    AIProviderTestResponse,
    AIUsageBalanceUpdate,
    AIUsageLedgerResponse,
    SignupRequestDecision,
    SignupRequestsResponse,
    SignupRequestSummary,
)
from app.services import auth as accounts
from app.services.ai_provider_settings import clear_provider_config, save_provider_config
from app.services.ai_usage_ledger import MICRO_USD, set_tracked_balance, usage_ledger_snapshot
from app.services.openai_compatible import OpenAICompatibleClient, OpenAICompatibleError

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger("screen_tutor.admin")


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


def _ai_provider_summary(request: Request) -> AIProviderSettingsSummary:
    manager = request.app.state.ai_provider_config
    config = manager.get()
    return AIProviderSettingsSummary(
        base_url=config.base_url if config else None,
        model=config.model if config else None,
        api_key_configured=bool(config and config.api_key),
        provider_configured=bool(
            config and config.base_url and config.model and config.api_key
        ),
        settings_source=manager.source(),
    )


@router.get("/ai-provider", response_model=AIProviderSettingsSummary)
async def get_ai_provider_settings(
    request: Request, response: Response
) -> AIProviderSettingsSummary:
    await _require_admin(request)
    _no_store(response)
    return _ai_provider_summary(request)


@router.put("/ai-provider", response_model=AIProviderSettingsSummary)
async def update_ai_provider_settings(
    payload: AIProviderSettingsUpdate, request: Request, response: Response
) -> AIProviderSettingsSummary:
    await _require_admin(request)
    if payload.clear_api_key and payload.api_key is not None:
        raise ApiError(
            422,
            "invalid_provider_settings",
            "Enter a replacement key or choose to clear the saved key, not both.",
        )
    manager = request.app.state.ai_provider_config
    current = manager.get()
    if payload.clear_api_key:
        api_key = None
    elif payload.api_key is not None:
        api_key = payload.api_key.get_secret_value()
    else:
        api_key = current.api_key if current else None
    try:
        await save_provider_config(
            database(request),
            request.app.state.settings,
            manager,
            base_url=payload.base_url,
            model=payload.model,
            api_key=api_key,
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_provider_settings", str(exc)) from None
    except RuntimeError:
        raise ApiError(
            503,
            "provider_settings_encryption_unavailable",
            "Provider settings cannot be saved until FILE_URL_SIGNING_SECRET "
            "is configured securely.",
        ) from None
    _no_store(response)
    return _ai_provider_summary(request)


@router.delete("/ai-provider", response_model=AIProviderSettingsSummary)
async def reset_ai_provider_settings(
    request: Request, response: Response
) -> AIProviderSettingsSummary:
    await _require_admin(request)
    try:
        await clear_provider_config(
            database(request),
            request.app.state.settings,
            request.app.state.ai_provider_config,
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_environment_provider_settings", str(exc)) from None
    _no_store(response)
    return _ai_provider_summary(request)


@router.post("/ai-provider/test", response_model=AIProviderTestResponse)
async def test_ai_provider(request: Request, response: Response) -> AIProviderTestResponse:
    admin = await _require_admin(request)
    retry_after = await request.app.state.ai_provider_test_rate_limiter.check(
        str(admin["id"])
    )
    if retry_after is not None:
        raise ApiError(
            429,
            "provider_test_rate_limited",
            "Wait before testing the provider again.",
            retry_after_seconds=retry_after,
        )
    client: OpenAICompatibleClient = request.app.state.openai_compatible_client
    try:
        await client.test_connection()
    except OpenAICompatibleError as exc:
        logger.warning(
            "ai_provider_test_failed provider_code=%s provider_status=%s",
            exc.code,
            exc.status,
        )
        raise ApiError(
            502,
            "provider_test_failed",
            "The provider test failed. Check the URL, API key, and model.",
        ) from None
    except Exception as exc:
        logger.warning("ai_provider_test_failed category=%s", type(exc).__name__)
        raise ApiError(
            502,
            "provider_test_failed",
            "The provider test failed. Check the URL, API key, and model.",
        ) from None
    _no_store(response)
    return AIProviderTestResponse(success=True, message="Provider connection succeeded.")


@router.get("/ai-usage", response_model=AIUsageLedgerResponse)
async def get_ai_usage_ledger(request: Request, response: Response) -> AIUsageLedgerResponse:
    """Show content-free request usage and reported provider charges to admins."""
    await _require_admin(request)
    snapshot = await usage_ledger_snapshot(database(request))
    _no_store(response)
    return AIUsageLedgerResponse(**snapshot)


@router.put("/ai-usage/balance", response_model=AIUsageLedgerResponse)
async def set_ai_usage_balance(
    payload: AIUsageBalanceUpdate, request: Request, response: Response
) -> AIUsageLedgerResponse:
    """Record a manual provider-balance snapshot for the spend ledger."""
    await _require_admin(request)
    balance_microusd = int(payload.balance_usd * MICRO_USD)
    await set_tracked_balance(
        database(request), balance_microusd, payload.note
    )
    snapshot = await usage_ledger_snapshot(database(request))
    _no_store(response)
    return AIUsageLedgerResponse(**snapshot)


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
