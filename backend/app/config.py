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
from uuid import UUID

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
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"
    )
    allow_guest_access: bool = False
    signup_mode: Literal["approval", "open", "closed"] = "closed"

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.8-live"
    gemini_text_model: str = "gemini-3.8-flash"
    gemini_timeout_seconds: float = 10.0
    openai_compatible_base_url: str | None = None
    openai_compatible_api_key: SecretStr | None = None
    openai_compatible_model: str | None = None
    live_session_rate_limit: int = 5
    live_session_rate_window_seconds: int = 60
    live_review_timeout_seconds: float = 60.0
    calculator_rate_limit: int = 30
    calculator_rate_window_seconds: int = 60
    lean_tool_enabled: bool = True
    lean_rate_limit: int = 3
    lean_rate_window_seconds: int = 60
    lean_timeout_seconds: float = 30.0
    lean_request_max_bytes: int = 65_536
    lean_project_dir: str = "backend/lean"
    lean_lake_executable: str = "lake"
    upload_rate_limit: int = 10
    upload_rate_window_seconds: int = 3600
    study_tutor_rate_limit: int = 30
    study_tutor_rate_window_seconds: int = 60
    study_pdf_executable: str = "tectonic"
    auth_rate_limit: int = 10
    auth_rate_window_seconds: int = 300
    session_ttl_days: int = 30
    request_body_max_bytes: int = 1024
    library_json_body_max_bytes: int = 1_100_000
    frontend_dist: str = "frontend/dist"

    local_owner_id: UUID = UUID("00000000-0000-4000-8000-000000000001")
    sqlite_path: str = "backend/.local/screen_tutor.sqlite3"
    storage_dir: str = "backend/.local/private_uploads"
    file_url_signing_secret: SecretStr | None = None
    max_upload_mb: int = 100
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
            raise ValueError("Gemini timeouts must be greater than zero.")
        if self.live_session_rate_limit < 1 or self.live_session_rate_window_seconds < 1:
            raise ValueError("Live session rate limit and window must both be positive.")
        if not 1 <= self.live_review_timeout_seconds <= 180:
            raise ValueError("LIVE_REVIEW_TIMEOUT_SECONDS must be between 1 and 180.")
        if self.calculator_rate_limit < 1 or self.calculator_rate_window_seconds < 1:
            raise ValueError("Calculator rate limit and window must both be positive.")
        if self.lean_rate_limit < 1 or self.lean_rate_window_seconds < 1:
            raise ValueError("Lean rate limit and window must both be positive.")
        if not 1 <= self.lean_timeout_seconds <= 60:
            raise ValueError("LEAN_TIMEOUT_SECONDS must be between 1 and 60.")
        if self.upload_rate_limit < 1 or self.upload_rate_window_seconds < 1:
            raise ValueError("Upload rate limit and window must both be positive.")
        if self.study_tutor_rate_limit < 1 or self.study_tutor_rate_window_seconds < 1:
            raise ValueError("Study tutor rate limit and window must both be positive.")
        if not self.study_pdf_executable.strip():
            raise ValueError("STUDY_PDF_EXECUTABLE cannot be empty.")
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

        if not self.sqlite_path.strip():
            raise ValueError("SQLITE_PATH must be a file path or :memory:.")
        if (
            self.file_url_signing_secret is None
            or len(self.file_url_signing_secret.get_secret_value().strip()) < 32
        ):
            raise ValueError(
                "FILE_URL_SIGNING_SECRET must contain at least 32 characters "
                "for private file storage."
            )

        if not self.lean_lake_executable.strip():
            raise ValueError("LEAN_LAKE_EXECUTABLE must be a non-empty executable name or path.")

        if self.app_env == "production":
            origins = self.allowed_origins
            if any(not origin.startswith("https://") for origin in origins):
                raise ValueError("Production APP_ORIGINS must use HTTPS.")
        compatible_provider_values = (
            self.openai_compatible_base_url,
            self.openai_compatible_api_key,
            self.openai_compatible_model,
        )
        if any(value is not None for value in compatible_provider_values) and not all(
            value is not None for value in compatible_provider_values
        ):
            raise ValueError(
                "OPENAI_COMPATIBLE_BASE_URL, OPENAI_COMPATIBLE_API_KEY, and "
                "OPENAI_COMPATIBLE_MODEL must be configured together."
            )
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

    def require_gemini_key(self) -> str:
        """Return the configured key or fail startup without leaking its value."""
        if self.gemini_api_key is None:
            raise RuntimeError("GEMINI_API_KEY is required to start the backend services.")
        value = self.gemini_api_key.get_secret_value().strip()
        if not value:
            raise RuntimeError("GEMINI_API_KEY is required to start the backend services.")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process."""
    return Settings()
