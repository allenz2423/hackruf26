"""SQLite storage helpers for the backend-owned application data."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import aiosqlite

from app.config import Settings


def _parameters(values: Sequence[object]) -> tuple[object, ...]:
    """Convert Python values to SQLite's native binding types."""
    return tuple(str(value) if isinstance(value, UUID) else value for value in values)


def _row(value: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(value) if value is not None else None


class SQLiteConnection:
    """Small async connection facade shared by route and transaction code."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def fetch(self, query: str, *values: object) -> list[dict[str, Any]]:
        cursor = await self._connection.execute(query, _parameters(values))
        try:
            return [dict(row) async for row in cursor]
        finally:
            await cursor.close()

    async def fetchrow(self, query: str, *values: object) -> dict[str, Any] | None:
        cursor = await self._connection.execute(query, _parameters(values))
        try:
            return _row(await cursor.fetchone())
        finally:
            await cursor.close()

    async def fetchval(self, query: str, *values: object) -> Any:
        row = await self.fetchrow(query, *values)
        return next(iter(row.values())) if row else None

    async def execute(self, query: str, *values: object) -> str:
        cursor = await self._connection.execute(query, _parameters(values))
        try:
            command = query.lstrip().split(None, 1)[0].upper()
            return f"{command} {max(cursor.rowcount, 0)}"
        finally:
            await cursor.close()

    async def executemany(
        self, query: str, values: Iterable[Sequence[object]]
    ) -> None:
        normalized = (_parameters(row) for row in values)
        cursor = await self._connection.executemany(query, normalized)
        await cursor.close()

    @asynccontextmanager
    async def transaction(self):
        """Start a write-reserving transaction and roll it back on failure."""
        await self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self
        except BaseException:
            await self._connection.rollback()
            raise
        else:
            await self._connection.commit()


class SQLiteDatabase(SQLiteConnection):
    """Single-connection SQLite database serialized for async application use."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        super().__init__(connection)
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self):
        """Provide exclusive access; callers may hold it for a transaction."""
        async with self._lock:
            yield SQLiteConnection(self._connection)

    async def _run(self, method: str, query: str, *values: object) -> Any:
        async with self.acquire() as connection:
            return await getattr(connection, method)(query, *values)

    async def fetch(self, query: str, *values: object) -> list[dict[str, Any]]:
        return await self._run("fetch", query, *values)

    async def fetchrow(self, query: str, *values: object) -> dict[str, Any] | None:
        return await self._run("fetchrow", query, *values)

    async def fetchval(self, query: str, *values: object) -> Any:
        return await self._run("fetchval", query, *values)

    async def execute(self, query: str, *values: object) -> str:
        return await self._run("execute", query, *values)

    async def executemany(
        self, query: str, values: Iterable[Sequence[object]]
    ) -> None:
        async with self.acquire() as connection:
            await connection.executemany(query, values)

    async def close(self) -> None:
        await self._connection.close()


async def create_database(settings: Settings) -> SQLiteDatabase:
    """Open the configured SQLite file and initialize the current schema."""
    path = settings.sqlite_path_value
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = await aiosqlite.connect(path, isolation_level=None)
    connection.row_factory = aiosqlite.Row
    await connection.execute("PRAGMA foreign_keys = ON")
    await connection.execute("PRAGMA journal_mode = WAL")
    await connection.execute("PRAGMA busy_timeout = 5000")
    schema = Path(__file__).with_name("sqlite_schema.sql").read_text(encoding="utf-8")
    await connection.executescript(schema)
    await _add_missing_columns(connection)
    return SQLiteDatabase(connection)


async def recover_interrupted_jobs(database: SQLiteDatabase) -> None:
    """Make jobs left by a stopped single-process server available for retry."""
    async with database.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "UPDATE upload_documents SET status = 'failed', failure_code = 'generation_interrupted', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE status = 'generating'"
            )
            await connection.execute(
                "UPDATE uploaded_assessments SET processing_status = 'failed', "
                "failure_code = 'processing_interrupted', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE processing_status = 'processing'"
            )
            await connection.execute(
                "UPDATE course_notes SET status = 'failed', failure_code = 'processing_interrupted', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE status = 'processing'"
            )
            await connection.execute(
                "UPDATE study_materials SET status = 'failed', "
                "failure_code = 'generation_interrupted', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE status = 'generating'"
            )


def json_value(value: object, fallback: object = None) -> object:
    """Decode JSON text stored in SQLite while tolerating legacy values."""
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value


async def _add_missing_columns(connection: aiosqlite.Connection) -> None:
    """Upgrade tables created by earlier versions of the schema in place."""
    cursor = await connection.execute("PRAGMA table_info(users)")
    columns = {row[1] for row in await cursor.fetchall()}
    await cursor.close()
    if "is_demo" not in columns:
        await connection.execute("ALTER TABLE users ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")
    if "avatar" not in columns:
        await connection.execute("ALTER TABLE users ADD COLUMN avatar TEXT")
