"""Provision constrained ephemeral credentials with Google's Gen AI SDK."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any

from google import genai
from google.genai import types

from app.api.schemas import TokenIssueResult
from app.config import Settings
from app.services.live_config import live_connect_config

API_VERSION = "v1alpha"


def _as_utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


class GeminiTokenService:
    """Create tokens with fixed server-owned Live constraints."""

    def __init__(self, client: genai.Client, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._constraints = types.LiveConnectConstraints(
            model=settings.gemini_model,
            config=live_connect_config(),
        )

    async def create_token(self) -> TokenIssueResult:
        """Provision one token without blocking FastAPI's event loop."""
        now = datetime.now(timezone.utc)
        expiration = now + timedelta(minutes=self._settings.token_expiration_minutes)
        new_session_expiration = now + timedelta(
            seconds=self._settings.new_session_expiration_seconds
        )

        config = types.CreateAuthTokenConfig(
            uses=1,
            expire_time=expiration,
            new_session_expire_time=new_session_expiration,
            # Omitting lock_additional_fields locks the full LiveConnectConfig.
            # This keeps model, instructions, modalities, and the one tool fixed.
            live_connect_constraints=self._constraints,
        )
        provision = partial(self._client.auth_tokens.create, config=config)
        token = await asyncio.to_thread(provision)

        access_token = getattr(token, "name", None)
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("Gemini returned an incomplete auth token resource.")

        expires_at = _as_utc_datetime(getattr(token, "expire_time", None)) or expiration
        return TokenIssueResult(
            access_token=access_token,
            model=self._settings.gemini_model,
            expires_at=expires_at,
        )


def create_genai_client(settings: Settings) -> genai.Client:
    """Create a server-only SDK client with an explicit network timeout."""
    return genai.Client(
        api_key=settings.require_gemini_key(),
        http_options=types.HttpOptions(
            api_version=API_VERSION,
            timeout=int(settings.gemini_timeout_seconds * 1000),
        ),
    )
