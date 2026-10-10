"""PostgreSQL pool helpers for the Supabase-hosted application schema."""

from __future__ import annotations

import json

import asyncpg

from app.config import Settings


async def create_database_pool(settings: Settings) -> asyncpg.Pool | None:
    """Create a small async pool when a Supabase database URL is configured."""
    if settings.database_url is None:
        return None
    dsn = settings.database_url.get_secret_value().strip()
    return await asyncpg.create_pool(
        dsn=dsn,
        min_size=1,
        max_size=5,
        command_timeout=30,
        statement_cache_size=0,
    )


def json_value(value: object, fallback: object = None) -> object:
    """Normalize asyncpg JSON/JSONB values across codec settings."""
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value
