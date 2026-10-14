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
    # These indexes depend on additive columns and are installed after the
    # populated-database migration has supplied them.
    await connection.execute("DROP INDEX IF EXISTS class_concept_aliases_name_idx")
    await connection.execute(
        "CREATE UNIQUE INDEX class_concept_aliases_name_idx ON class_concept_aliases "
        "(class_id,normalized_name) WHERE status IN ('proposed','validated')"
    )
    await connection.execute("DROP INDEX IF EXISTS class_concept_extraction_jobs_status_idx")
    await connection.execute(
        "CREATE INDEX class_concept_extraction_jobs_status_idx ON class_concept_extraction_jobs "
        "(status,available_at,created_at,id)"
    )
    await connection.execute(
        "UPDATE source_index_heads SET processing_revision=(SELECT processing_revision "
        "FROM source_revisions WHERE id=source_index_heads.revision_id) "
        "WHERE processing_revision=0"
    )
    await _migrate_shared_class_schema(connection)
    await _migrate_class_capability_schema(connection)
    triggers = Path(__file__).with_name("source_triggers.sql").read_text(encoding="utf-8")
    await connection.executescript(triggers)
    concept_triggers = Path(__file__).with_name("concept_graph_triggers.sql").read_text(
        encoding="utf-8"
    )
    await connection.executescript(concept_triggers)
    return SQLiteDatabase(connection)


async def recover_interrupted_jobs(database: SQLiteDatabase) -> None:
    """Make jobs left by a stopped single-process server available for retry."""
    async with database.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "UPDATE upload_documents SET status = 'failed', "
                "failure_code = 'generation_interrupted', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE status = 'generating'"
            )
            await connection.execute(
                "UPDATE uploaded_assessments SET processing_status = 'failed', "
                "failure_code = 'processing_interrupted', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE processing_status = 'processing'"
            )
            await connection.execute(
                "UPDATE course_notes SET status = 'failed', "
                "failure_code = 'processing_interrupted', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE status = 'processing'"
            )
            await connection.execute(
                "UPDATE study_materials SET status = 'failed', "
                "failure_code = 'generation_interrupted', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE status = 'generating'"
            )
            await connection.execute(
                "UPDATE quiz_reasoning_evaluations SET status = 'failed', "
                "completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE status = 'pending'"
            )
            await connection.execute(
                "UPDATE quiz_question_attempts SET state = 'reasoning_failed', "
                "active_evaluation_operation_id = NULL, version = version + 1, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE state = 'evaluating_reasoning'"
            )
            await connection.execute(
                "UPDATE quiz_question_attempts SET feedback_status = 'failed', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE feedback_status = 'pending'"
            )
            await connection.execute(
                "UPDATE quiz_feedback_operations SET status = 'failed', "
                "completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE status = 'pending'"
            )
            await connection.execute(
                "UPDATE mastery_ladder_rubric_jobs SET status='failed', "
                "error_code='rubric_interrupted', "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
                "completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE status='pending'"
            )
            await connection.execute(
                "UPDATE mastery_ladder_operations SET status='failed', "
                "error_code='operation_interrupted', "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE status='pending'"
            )
            await connection.execute(
                "UPDATE class_question_generation_operations SET status='failed', "
                "error_code='generation_interrupted',"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE status='running'"
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
    for table, additions in {
        "course_notes": {
            "processing_revision": "INTEGER NOT NULL DEFAULT 0",
            "source_index_status": (
                "TEXT NOT NULL DEFAULT 'unindexed' CHECK "
                "(source_index_status IN ('unindexed','processing','ready','failed'))"
            ),
            "resource_scope": (
                "TEXT NOT NULL DEFAULT 'class_owned' CHECK "
                "(resource_scope IN ('class_owned', 'student_contribution'))"
            ),
            "created_by": "TEXT REFERENCES users(id) ON DELETE SET NULL",
            "publication_state": (
                "TEXT NOT NULL DEFAULT 'published' CHECK "
                "(publication_state IN ('draft', 'scheduled', 'published'))"
            ),
            "release_at": "TEXT",
            "published_at": "TEXT",
        },
        "study_materials": {
            "sources_outdated": "INTEGER NOT NULL DEFAULT 0",
            "sources_missing": "INTEGER NOT NULL DEFAULT 0",
            "context_truncated": "INTEGER NOT NULL DEFAULT 0",
            "generation_instructions": (
                "TEXT CHECK (generation_instructions IS NULL OR "
                "length(generation_instructions) <= 2000)"
            ),
            "origin_class_id": "TEXT",
            "resource_scope": (
                "TEXT NOT NULL DEFAULT 'class_owned' CHECK "
                "(resource_scope IN ('class_owned', 'personal', 'student_contribution'))"
            ),
            "owner_id": "TEXT REFERENCES users(id) ON DELETE CASCADE",
            "created_by": "TEXT REFERENCES users(id) ON DELETE SET NULL",
            "publication_state": "TEXT DEFAULT 'published'",
            "release_at": "TEXT",
            "published_at": "TEXT",
            "feedback_release_policy": (
                "TEXT NOT NULL DEFAULT 'after_completion' CHECK "
                "(feedback_release_policy IN ('after_completion','manual','never'))"
            ),
            "assessment_validation_state": (
                "TEXT NOT NULL DEFAULT 'legacy_unvalidated' CHECK "
                "(assessment_validation_state IN ('legacy_unvalidated','validated'))"
            ),
            "assessment_validation_version": "TEXT",
            "assessment_engine_version": (
                "INTEGER NOT NULL DEFAULT 0 CHECK (assessment_engine_version IN (0,1))"
            ),
        },
        "source_index_heads": {
            "processing_revision": (
                "INTEGER NOT NULL DEFAULT 0 CHECK (processing_revision >= 0)"
            ),
        },
        "quiz_runs": {
            "snapshot_version": "INTEGER NOT NULL DEFAULT 0",
            "assessment_type": (
                "TEXT NOT NULL DEFAULT 'quiz' CHECK (assessment_type IN ('quiz','mastery_ladder'))"
            ),
            "grading_policy": (
                "TEXT NOT NULL DEFAULT 'objective_only' CHECK "
                "(grading_policy IN ('objective_only','reasoning_optional','reasoning_required'))"
            ),
            "feedback_release_policy": (
                "TEXT NOT NULL DEFAULT 'after_completion' CHECK "
                "(feedback_release_policy IN ('after_completion','manual','never'))"
            ),
            "feedback_released_at": "TEXT",
        },
        "quiz_run_questions": {
            "evidence_eligible": "INTEGER NOT NULL DEFAULT 0 CHECK (evidence_eligible IN (0,1))",
        },
        "quiz_question_attempts": {
            "answer_status": (
                "TEXT NOT NULL DEFAULT 'unanswered' "
                "CHECK (answer_status IN ('answered','unanswered'))"
            ),
        },
        "study_material_attempts": {
            "owner_id": "TEXT REFERENCES users(id) ON DELETE CASCADE",
        },
        "uploaded_assessments": {
            "owner_id": "TEXT REFERENCES users(id) ON DELETE CASCADE",
            "answer_key_release_state": (
                "TEXT NOT NULL DEFAULT 'draft' CHECK "
                "(answer_key_release_state IN ('draft', 'scheduled', 'published'))"
            ),
            "answer_key_release_at": "TEXT",
        },
        "quiz_reasoning_metrics": {
            "latency_median_ms": "REAL NOT NULL DEFAULT 0",
            "latency_p95_ms": "REAL NOT NULL DEFAULT 0",
        },
        "class_concept_aliases": {
            "status": (
                "TEXT NOT NULL DEFAULT 'validated' CHECK "
                "(status IN ('proposed','validated','rejected'))"
            ),
        },
        "class_concept_extraction_jobs": {
            "available_at": "TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
            "prompt_version": (
                "TEXT NOT NULL DEFAULT 'concept-graph-extraction-v1'"
            ),
            "model_name": "TEXT",
        },
        "mastery_snapshots": {
            "current_level": (
                "INTEGER CHECK (current_level IS NULL OR current_level BETWEEN 1 AND 4)"
            ),
        },
        "assessment_outcomes": {
            "quality_weight": (
                "REAL NOT NULL DEFAULT 1.0 CHECK (quality_weight >= 0 AND quality_weight <= 1)"
            ),
        },
    }.items():
        cursor = await connection.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        for name, definition in additions.items():
            if name not in columns:
                await connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    cursor = await connection.execute("PRAGMA table_info(users)")
    columns = {row[1] for row in await cursor.fetchall()}
    await cursor.close()
    if "is_demo" not in columns:
        await connection.execute("ALTER TABLE users ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")
    if "is_admin" not in columns:
        await connection.execute(
            "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"
        )
    if "avatar" not in columns:
        await connection.execute("ALTER TABLE users ADD COLUMN avatar TEXT")

    cursor = await connection.execute("PRAGMA table_info(course_classes)")
    columns = {row[1] for row in await cursor.fetchall()}
    await cursor.close()
    if "color" not in columns:
        await connection.execute("ALTER TABLE course_classes ADD COLUMN color TEXT")
    if "icon" not in columns:
        await connection.execute("ALTER TABLE course_classes ADD COLUMN icon TEXT")


async def _migrate_shared_class_schema(connection: aiosqlite.Connection) -> None:
    """Apply the restart-safe ownership/membership migration to existing SQLite files."""
    await connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT "
        "(strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))"
    )
    applied_versions = {
        int(row[0]) for row in await connection.execute_fetchall(
            "SELECT version FROM schema_migrations WHERE version IN (1, 2)"
        )
    }
    if 2 in applied_versions:
        cursor = await connection.execute("PRAGMA table_info(study_material_sources)")
        source_names = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        if "class_id" not in source_names:
            await connection.execute("ALTER TABLE study_material_sources ADD COLUMN class_id TEXT")
        if "note_id" not in source_names:
            await connection.execute("ALTER TABLE study_material_sources ADD COLUMN note_id TEXT")
        await connection.execute(
            "UPDATE study_material_sources SET class_id=COALESCE(class_id,source_class_id), "
            "note_id=COALESCE(note_id,source_note_id)"
        )
        await connection.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (2)")
        return

    if 1 in applied_versions:
        # Version 1 from older builds only migrated source-column aliases. Continue
        # the shared-class migration instead of treating that marker as completion.
        cursor = await connection.execute("PRAGMA table_info(study_material_sources)")
        source_names = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        if "class_id" not in source_names:
            await connection.execute("ALTER TABLE study_material_sources ADD COLUMN class_id TEXT")
        if "note_id" not in source_names:
            await connection.execute("ALTER TABLE study_material_sources ADD COLUMN note_id TEXT")
        await connection.execute(
            "UPDATE study_material_sources SET class_id=COALESCE(class_id,source_class_id), "
            "note_id=COALESCE(note_id,source_note_id)"
        )

    columns = await connection.execute_fetchall("PRAGMA table_info(study_materials)")
    class_id = next((row for row in columns if row[1] == "class_id"), None)
    source_columns = await connection.execute_fetchall("PRAGMA table_info(study_material_sources)")
    old_source_shape = any(row[1] == "class_id" for row in source_columns)
    needs_material_rebuild = bool(class_id and class_id[3])

    if needs_material_rebuild or old_source_shape:
        # Keep every dependent table intact while replacing the parent and provenance tables.
        # Foreign keys are checked immediately after the atomic rebuild.
        await connection.execute("PRAGMA foreign_keys = OFF")
        await connection.execute("BEGIN IMMEDIATE")
        try:
            await connection.execute("DROP TRIGGER IF EXISTS course_notes_delete_derived_materials")
            await connection.execute("DROP TRIGGER IF EXISTS course_notes_mark_materials_changed")
            await connection.execute("DROP TRIGGER IF EXISTS course_notes_mark_materials_missing")
            await connection.execute(
                "CREATE TABLE study_materials_new ("
                "id TEXT PRIMARY KEY, class_id TEXT REFERENCES course_classes(id) ON DELETE CASCADE, "
                "origin_class_id TEXT, resource_scope TEXT NOT NULL DEFAULT 'class_owned' "
                "CHECK (resource_scope IN ('class_owned','personal','student_contribution')), "
                "owner_id TEXT REFERENCES users(id) ON DELETE CASCADE, "
                "created_by TEXT REFERENCES users(id) ON DELETE SET NULL, "
                "publication_state TEXT, release_at TEXT, published_at TEXT, "
                "feedback_release_policy TEXT NOT NULL DEFAULT 'after_completion' "
                "CHECK (feedback_release_policy IN ('after_completion','manual','never')), "
                "assessment_validation_state TEXT NOT NULL DEFAULT 'legacy_unvalidated' "
                "CHECK (assessment_validation_state IN ('legacy_unvalidated','validated')), "
                "assessment_validation_version TEXT, "
                "assessment_engine_version INTEGER NOT NULL DEFAULT 0 "
                "CHECK (assessment_engine_version IN (0,1)), "
                "kind TEXT NOT NULL CHECK (kind IN ('quiz','practice_exam','study_guide','flashcards')), "
                "title TEXT NOT NULL CHECK (trim(title) <> ''), "
                "status TEXT NOT NULL DEFAULT 'generating' CHECK (status IN ('generating','ready','failed')), "
                "scope TEXT NOT NULL DEFAULT '{\"kind\":\"all\"}' CHECK (json_valid(scope) AND json_type(scope)='object'), "
                "options TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(options) AND json_type(options)='object'), "
                "generation_instructions TEXT CHECK (generation_instructions IS NULL OR length(generation_instructions)<=2000), "
                "content TEXT CHECK (content IS NULL OR (json_valid(content) AND json_type(content)='object')), "
                "failure_code TEXT, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), "
                "updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), generated_at TEXT, "
                "sources_outdated INTEGER NOT NULL DEFAULT 0 CHECK (sources_outdated IN (0,1)), "
                "sources_missing INTEGER NOT NULL DEFAULT 0 CHECK (sources_missing IN (0,1)), "
                "context_truncated INTEGER NOT NULL DEFAULT 0 CHECK (context_truncated IN (0,1)), "
                "UNIQUE (id,class_id), "
                "CHECK ((resource_scope='personal' AND class_id IS NULL AND owner_id IS NOT NULL "
                "AND publication_state IS NULL AND release_at IS NULL) OR "
                "(resource_scope IN ('class_owned','student_contribution') AND class_id IS NOT NULL "
                "AND owner_id IS NULL AND publication_state IN ('draft','scheduled','published'))), "
                "CHECK ((publication_state='scheduled' AND release_at IS NOT NULL) OR "
                "(resource_scope <> 'personal' AND publication_state <> 'scheduled' AND release_at IS NULL) "
                "OR resource_scope='personal'), "
                "CHECK (status <> 'ready' OR content IS NOT NULL), "
                "CHECK (status = 'failed' OR failure_code IS NULL))"
            )
            await connection.execute(
                "INSERT INTO study_materials_new (id,class_id,origin_class_id,resource_scope,owner_id,"
                "created_by,publication_state,release_at,published_at,feedback_release_policy,"
                "assessment_validation_state,assessment_validation_version,assessment_engine_version,"
                "kind,title,status,scope,options,"
                "generation_instructions,content,failure_code,created_at,updated_at,generated_at,"
                "sources_outdated,sources_missing,context_truncated) "
                "SELECT id,class_id,COALESCE(origin_class_id,class_id),COALESCE(resource_scope,'class_owned'),"
                "owner_id,created_by,COALESCE(publication_state,'published'),release_at,"
                "COALESCE(published_at,created_at),COALESCE(feedback_release_policy,'after_completion'),"
                "COALESCE(assessment_validation_state,'legacy_unvalidated'),"
                "assessment_validation_version,"
                "COALESCE(assessment_engine_version,0),"
                "kind,title,status,scope,options,generation_instructions,"
                "content,failure_code,created_at,updated_at,generated_at,sources_outdated,sources_missing,"
                "context_truncated FROM study_materials"
            )
            await connection.execute(
                "CREATE TABLE study_material_sources_new ("
                "material_id TEXT NOT NULL REFERENCES study_materials(id) ON DELETE CASCADE, "
                "source_class_id TEXT, source_note_id TEXT REFERENCES course_notes(id) ON DELETE SET NULL, "
                "source_title TEXT NOT NULL DEFAULT '', source_kind TEXT NOT NULL DEFAULT 'notes', "
                "position INTEGER NOT NULL DEFAULT 0 CHECK (position>=0), class_id TEXT, note_id TEXT, "
                "PRIMARY KEY (material_id,position))"
            )
            if old_source_shape:
                await connection.execute(
                    "INSERT INTO study_material_sources_new "
                    "(material_id,source_class_id,source_note_id,source_title,source_kind,position,class_id,note_id) "
                    "SELECT s.material_id,s.class_id,s.note_id,COALESCE(n.title,''),COALESCE(n.kind,'notes'),"
                    "s.position,s.class_id,s.note_id "
                    "FROM study_material_sources s LEFT JOIN course_notes n ON n.id=s.note_id"
                )
            await connection.execute("DROP TABLE study_material_sources")
            await connection.execute("DROP TABLE study_materials")
            await connection.execute("ALTER TABLE study_materials_new RENAME TO study_materials")
            await connection.execute(
                "ALTER TABLE study_material_sources_new RENAME TO study_material_sources"
            )
            await connection.execute("CREATE INDEX study_materials_class_created_idx ON study_materials(class_id,created_at DESC)")
            await connection.execute("CREATE INDEX study_materials_class_status_idx ON study_materials(class_id,status)")
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise
        finally:
            await connection.execute("PRAGMA foreign_keys = ON")
        violations = await connection.execute_fetchall("PRAGMA foreign_key_check")
        if violations:
            raise RuntimeError("Shared-class schema migration left invalid foreign keys.")

    await connection.execute("BEGIN IMMEDIATE")
    try:
        await connection.execute(
            "UPDATE study_materials SET origin_class_id = class_id "
            "WHERE origin_class_id IS NULL AND class_id IS NOT NULL"
        )
        await connection.execute(
            "INSERT OR IGNORE INTO class_memberships (class_id,user_id,status,added_by) "
            "SELECT c.id,c.owner_id,'active',c.owner_id FROM course_classes c "
            "JOIN users u ON u.id=c.owner_id"
        )
        for capability in (
            "read", "create", "edit", "delete", "delete_others", "invite", "manage_members",
            "manage_permissions",
        ):
            await connection.execute(
                "INSERT OR IGNORE INTO class_membership_capabilities "
                "(class_id,user_id,capability,granted_by) "
                "SELECT m.class_id,m.user_id,?1,m.user_id FROM class_memberships m "
                "JOIN course_classes c ON c.id=m.class_id AND c.owner_id=m.user_id "
                "WHERE m.status='active'",
                (capability,),
            )
        await connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (1)"
        )
        await connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (2)"
        )
        await connection.commit()
    except BaseException:
        await connection.rollback()
        raise


async def _migrate_class_capability_schema(connection: aiosqlite.Connection) -> None:
    """Add the explicit delete_others grant without losing existing capability rows."""
    applied = await connection.execute_fetchall(
        "SELECT 1 FROM schema_migrations WHERE version=3"
    )
    if applied:
        return

    await connection.execute("PRAGMA foreign_keys = OFF")
    await connection.execute("BEGIN IMMEDIATE")
    try:
        await connection.execute(
            "CREATE TABLE class_membership_capabilities_v3 ("
            "class_id TEXT NOT NULL,user_id TEXT NOT NULL,"
            "capability TEXT NOT NULL CHECK (capability IN ("
            "'read','create','edit','delete','delete_others','invite','manage_members',"
            "'manage_permissions')),"
            "granted_by TEXT REFERENCES users(id) ON DELETE SET NULL,"
            "granted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),"
            "PRIMARY KEY (class_id,user_id,capability),"
            "FOREIGN KEY (class_id,user_id) REFERENCES class_memberships(class_id,user_id) "
            "ON DELETE CASCADE)"
        )
        await connection.execute(
            "INSERT INTO class_membership_capabilities_v3 "
            "SELECT class_id,user_id,capability,granted_by,granted_at "
            "FROM class_membership_capabilities"
        )
        await connection.execute("DROP TABLE class_membership_capabilities")
        await connection.execute(
            "ALTER TABLE class_membership_capabilities_v3 RENAME TO class_membership_capabilities"
        )
        await connection.execute(
            "CREATE TABLE class_invitation_capabilities_v3 ("
            "invitation_id TEXT NOT NULL REFERENCES class_invitations(id) ON DELETE CASCADE,"
            "capability TEXT NOT NULL CHECK (capability IN ("
            "'read','create','edit','delete','delete_others','invite','manage_members',"
            "'manage_permissions')),PRIMARY KEY (invitation_id,capability))"
        )
        await connection.execute(
            "INSERT INTO class_invitation_capabilities_v3 "
            "SELECT invitation_id,capability FROM class_invitation_capabilities"
        )
        await connection.execute("DROP TABLE class_invitation_capabilities")
        await connection.execute(
            "ALTER TABLE class_invitation_capabilities_v3 RENAME TO class_invitation_capabilities"
        )
        await connection.execute(
            "INSERT OR IGNORE INTO class_membership_capabilities "
            "(class_id,user_id,capability,granted_by) "
            "SELECT m.class_id,m.user_id,'delete_others',m.user_id FROM class_memberships m "
            "JOIN course_classes c ON c.id=m.class_id AND c.owner_id=m.user_id "
            "WHERE m.status='active'"
        )
        await connection.execute("INSERT INTO schema_migrations(version) VALUES (3)")
        await connection.commit()
    except BaseException:
        await connection.rollback()
        raise
    finally:
        await connection.execute("PRAGMA foreign_keys = ON")

    violations = await connection.execute_fetchall("PRAGMA foreign_key_check")
    if violations:
        raise RuntimeError("Class capability migration left invalid foreign keys.")
