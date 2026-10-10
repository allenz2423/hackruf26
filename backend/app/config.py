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
    gemini_text_model: str = "gemini-3.8-flash"
    gemini_timeout_seconds: float = 10.0
    gemini_content_timeout_seconds: float = 600.0
    token_expiration_minutes: int = 30
    new_session_expiration_seconds: int = 60

    token_rate_limit: int = 5
    token_rate_window_seconds: int = 60
    calculator_rate_limit: int = 30
    calculator_rate_window_seconds: int = 60
    lean_tool_enabled: bool = True
    lean_rate_limit: int = 3
    lean_rate_window_seconds: int = 60
    lean_timeout_seconds: float = 12.0
    lean_request_max_bytes: int = 65_536
    lean_project_dir: str = "backend/lean"
    lean_lake_executable: str = "lake"
    upload_rate_limit: int = 10
    upload_rate_window_seconds: int = 3600
    generation_rate_limit: int = 10
    generation_rate_window_seconds: int = 3600
    request_body_max_bytes: int = 1024
    library_json_body_max_bytes: int = 1_100_000
    frontend_dist: str = "frontend/dist"

    supabase_url: str | None = None
    supabase_jwt_secret: SecretStr | None = None
    sqlite_path: str = "backend/.local/screen_tutor.sqlite3"
    storage_dir: str = "backend/.local/private_uploads"
    file_url_signing_secret: SecretStr | None = None
    max_upload_mb: int = 250
    max_uploads_per_class: int = 100
    max_lecture_minutes: int = 120
    max_class_name_length: int = 100

    @field_validator("gemini_model")
    @classmethod
    def validate_model_identifier(cls, value: str) -> str:
        model = value.strip()
        if not model or any(character.isspace() for character in model):
            raise ValueError("GEMINI_MODEL must be a non-empty model identifier.")
        return model

    @field_validator("gemini_text_model")
    @classmethod
    def validate_text_model_identifier(cls, value: str) -> str:
        model = value.strip()
        if not model or any(character.isspace() for character in model):
            raise ValueError("GEMINI_TEXT_MODEL must be a non-empty model identifier.")
        return model

    @field_validator("supabase_url")
    @classmethod
    def validate_supabase_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        candidate = value.strip().rstrip("/")
        parsed = urlsplit(candidate)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("SUPABASE_URL must be an HTTP(S) project origin without a path.")
        return candidate

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
        if self.gemini_timeout_seconds <= 0 or self.gemini_content_timeout_seconds <= 0:
            raise ValueError("Gemini timeouts must be greater than zero.")
        if not 1 <= self.token_expiration_minutes <= 60:
            raise ValueError("TOKEN_EXPIRATION_MINUTES must be between 1 and 60.")
        if not 10 <= self.new_session_expiration_seconds <= 120:
            raise ValueError("NEW_SESSION_EXPIRATION_SECONDS must be between 10 and 120.")
        if self.token_rate_limit < 1 or self.token_rate_window_seconds < 1:
            raise ValueError("Token rate limit and window must both be positive.")
        if self.calculator_rate_limit < 1 or self.calculator_rate_window_seconds < 1:
            raise ValueError("Calculator rate limit and window must both be positive.")
        if self.lean_rate_limit < 1 or self.lean_rate_window_seconds < 1:
            raise ValueError("Lean rate limit and window must both be positive.")
        if not 1 <= self.lean_timeout_seconds <= 60:
            raise ValueError("LEAN_TIMEOUT_SECONDS must be between 1 and 60.")
        if self.upload_rate_limit < 1 or self.upload_rate_window_seconds < 1:
            raise ValueError("Upload rate limit and window must both be positive.")
        if self.generation_rate_limit < 1 or self.generation_rate_window_seconds < 1:
            raise ValueError("Generation rate limit and window must both be positive.")
        if not 1 <= self.request_body_max_bytes <= 16_384:
            raise ValueError("REQUEST_BODY_MAX_BYTES must be between 1 and 16384.")
        if not 1_024 <= self.lean_request_max_bytes <= 131_072:
            raise ValueError("LEAN_REQUEST_MAX_BYTES must be between 1024 and 131072.")
        if not 1_024 <= self.library_json_body_max_bytes <= 16_777_216:
            raise ValueError("LIBRARY_JSON_BODY_MAX_BYTES must be between 1024 and 16777216.")
        if not 1 <= self.max_upload_mb <= 2_048:
            raise ValueError("MAX_UPLOAD_MB must be between 1 and 2048.")
        if not 1 <= self.max_uploads_per_class <= 10_000:
            raise ValueError("MAX_UPLOADS_PER_CLASS must be between 1 and 10000.")
        if not 1 <= self.max_lecture_minutes <= 600:
            raise ValueError("MAX_LECTURE_MINUTES must be between 1 and 600.")
        if not 1 <= self.max_class_name_length <= 200:
            raise ValueError("MAX_CLASS_NAME_LENGTH must be between 1 and 200.")

        if self.demo_gate_token is not None and len(
            self.demo_gate_token.get_secret_value().strip()
        ) < 32:
            raise ValueError("DEMO_GATE_TOKEN must contain at least 32 characters when configured.")

        if not self.sqlite_path.strip():
            raise ValueError("SQLITE_PATH must be a file path or :memory:.")
        if self.file_url_signing_secret is None or len(
            self.file_url_signing_secret.get_secret_value().strip()
        ) < 32:
            raise ValueError(
                "FILE_URL_SIGNING_SECRET must contain at least 32 characters for private file storage."
            )

        if not self.lean_lake_executable.strip():
            raise ValueError("LEAN_LAKE_EXECUTABLE must be a non-empty executable name or path.")

        if self.app_env == "production":
            origins = self.allowed_origins
            if any(not origin.startswith("https://") for origin in origins):
                raise ValueError("Production APP_ORIGINS must use HTTPS.")
            gate = self.demo_gate_token.get_secret_value().strip() if self.demo_gate_token else ""
            if self.supabase_url is None and len(gate) < 32:
                raise ValueError(
                    "Production requires SUPABASE_URL or a high-entropy DEMO_GATE_TOKEN."
                )
            if self.supabase_url is not None and not self.supabase_url.startswith("https://"):
                raise ValueError("Production SUPABASE_URL must use HTTPS.")
        return self

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        return tuple(self.app_origins.split(","))

    @property
    def frontend_dist_path(self) -> Path:
        path = Path(self.frontend_dist)
        return path if path.is_absolute() else REPOSITORY_ROOT / path

    @property
    def storage_dir_path(self) -> Path:
        path = Path(self.storage_dir)
        return path if path.is_absolute() else REPOSITORY_ROOT / path

    @property
    def sqlite_path_value(self) -> str:
        path = self.sqlite_path.strip()
        if path == ":memory:":
            return path
        resolved = Path(path)
        return str(resolved if resolved.is_absolute() else REPOSITORY_ROOT / resolved)

    @property
    def lean_project_dir_path(self) -> Path:
        path = Path(self.lean_project_dir)
        return path if path.is_absolute() else REPOSITORY_ROOT / path

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def supabase_issuer(self) -> str | None:
        return f"{self.supabase_url}/auth/v1" if self.supabase_url else None

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
