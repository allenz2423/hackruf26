"""Stable, sanitized API errors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ApiError(Exception):
    status_code: int
    code: str
    message: str
    retry_after_seconds: int | None = None

    def __str__(self) -> str:
        return self.code
