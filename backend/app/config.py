"""Validated backend settings.

The root `.env` file is loaded for local development. Secrets are kept as
SecretStr values and are deliberately not exposed through application state,
API responses, or logs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _normalize_origin(value: str) -> str:
    """Validate and normalize an origin value without accepting URL paths."""
    candidate = value.strip().rstrip("/")
    parsed = urlsplit(candidate)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("APP_ORIGINS must contain origins, not full URLs or paths.")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


class Settings(BaseSettings):
    """Environment-backed service configuration."""

    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "production"] = "development"
    app_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:8000,http://127.0.0.1:8000"
    )
    demo_gate_token: SecretStr | None = None

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.8-live"
    gemini_timeout_seconds: float = 10.0
    token_expiration_minutes: int = 30
    new_session_expiration_seconds: int = 60

    token_rate_limit: int = 5
    token_rate_window_seconds: int = 60
    request_body_max_bytes: int = 1024
    frontend_dist: str = "frontend/dist"

    @field_validator("gemini_model")
    @classmethod
    def validate_model_identifier(cls, value: str) -> str:
        model = value.strip()
        if not model or any(character.isspace() for character in model):
            raise ValueError("GEMINI_MODEL must be a non-empty model identifier.")
        return model

    @field_validator("app_origins")
    @classmethod
    def validate_origin_list(cls, value: str) -> str:
        origins = [_normalize_origin(item) for item in value.split(",") if item.strip()]
        if not origins:
            raise ValueError("APP_ORIGINS must contain at least one allowed origin.")
        if len(origins) != len(set(origins)):
            raise ValueError("APP_ORIGINS cannot contain duplicate origins.")
        return ",".join(origins)

    @model_validator(mode="after")
    def validate_limits_and_deployment(self) -> Settings:
        if self.gemini_timeout_seconds <= 0:
            raise ValueError("GEMINI_TIMEOUT_SECONDS must be greater than zero.")
        if not 1 <= self.token_expiration_minutes <= 60:
            raise ValueError("TOKEN_EXPIRATION_MINUTES must be between 1 and 60.")
        if not 10 <= self.new_session_expiration_seconds <= 120:
            raise ValueError("NEW_SESSION_EXPIRATION_SECONDS must be between 10 and 120.")
        if self.token_rate_limit < 1 or self.token_rate_window_seconds < 1:
            raise ValueError("Token rate limit and window must both be positive.")
        if not 1 <= self.request_body_max_bytes <= 16_384:
            raise ValueError("REQUEST_BODY_MAX_BYTES must be between 1 and 16384.")

        if self.app_env == "production":
            origins = self.allowed_origins
            if any(not origin.startswith("https://") for origin in origins):
                raise ValueError("Production APP_ORIGINS must use HTTPS.")
            gate = self.demo_gate_token.get_secret_value().strip() if self.demo_gate_token else ""
            if len(gate) < 32:
                raise ValueError(
                    "Production requires a high-entropy DEMO_GATE_TOKEN until app sign-in is integrated."
                )
        return self

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        return tuple(self.app_origins.split(","))

    @property
    def frontend_dist_path(self) -> Path:
        path = Path(self.frontend_dist)
        return path if path.is_absolute() else REPOSITORY_ROOT / path

    def require_gemini_key(self) -> str:
        """Return the configured key or fail startup without leaking its value."""
        if self.gemini_api_key is None:
            raise RuntimeError("GEMINI_API_KEY is required to start the token service.")
        value = self.gemini_api_key.get_secret_value().strip()
        if not value:
            raise RuntimeError("GEMINI_API_KEY is required to start the token service.")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process."""
    return Settings()
