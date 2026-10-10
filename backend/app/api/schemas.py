"""Validated HTTP request and response contracts."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LiveTokenRequest(BaseModel):
    """An intentionally empty request; policy and model stay server-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CalculatorRequest(BaseModel):
    """Bounded numeric or symbolic expression from a Live calculator call."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    expression: str = Field(min_length=1, max_length=256, strict=True)


class CalculatorResponse(BaseModel):
    """Exact or symbolic calculator result returned as text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result: str = Field(min_length=1, max_length=512)


class LiveTokenResponse(BaseModel):
    """Short-lived bearer credential returned to the requesting browser."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str = Field(min_length=1)
    model: str = Field(min_length=1)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def require_timezone_aware_expiration(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone.")
        return value.astimezone(timezone.utc)


class HealthResponse(BaseModel):
    status: str = "ok"


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class TokenIssueResult(BaseModel):
    """Internal validated result produced by the Gemini token service."""

    access_token: str = Field(min_length=1)
    model: str = Field(min_length=1)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def require_timezone_aware_expiration(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("token expiration must include a timezone")
        return value.astimezone(timezone.utc)
