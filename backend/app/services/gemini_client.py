"""Server-side Google Gen AI client construction."""

from __future__ import annotations

from google import genai
from google.genai import types

from app.config import Settings

API_VERSION = "v1alpha"
LIVE_API_VERSION = "v1beta"


def create_genai_client(
    settings: Settings, *, timeout_seconds: float | None = None, api_version: str = API_VERSION
) -> genai.Client:
    """Create a server-only SDK client with an explicit network timeout."""
    return genai.Client(
        api_key=settings.require_gemini_key(),
        http_options=types.HttpOptions(
            api_version=api_version,
            timeout=int((timeout_seconds or settings.gemini_timeout_seconds) * 1000),
        ),
    )
