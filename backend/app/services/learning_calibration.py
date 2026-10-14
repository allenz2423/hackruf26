"""Bounded bank replenishment and aggregate-only learning calibration jobs."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.api.errors import ApiError
from app.api.schemas import QuestionBankGenerationRequest
from app.services.class_access import class_access
from app.services.database import SQLiteDatabase
from app.services.question_bank import (
    create_generated_assessment,
    current_concept_source_digest,
)

logger = logging.getLogger("screen_tutor.learning_calibration")

BANK_TARGET_PER_SLOT = 2
BANK_DAILY_JOB_BUDGET = 10
MAX_REPLENISHMENT_JOBS_PER_SCAN = 20
REPLENISHMENT_DEADLINE_SECONDS = 150
CALIBRATION_REFRESH_MINUTES = 15
CALIBRATION_JOB_MAX_ATTEMPTS = 3
QUESTION_FLAG_MIN_RESPONSES = 30
QUESTION_FLAG_RESIDUAL_THRESHOLD = 0.25
GRAPH_FLAG_MIN_LEARNERS = 10
GRAPH_FLAG_RATE_DIFFERENCE = 0.35


async def recover_learning_calibration_jobs(database: SQLiteDatabase) -> None:
    """Make interrupted jobs retryable, or terminal when their retry budget is spent."""
    async with database.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE question_bank_replenishment_jobs SET status='failed',"
                "error_code='retry_limit_reached',completed_at="
                "strftime('%Y-%m-%dT%H:%M:%fZ','now'),updated_at="
                "strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE status='running' AND attempts>=max_attempts"
            )
            await conn.execute(
                "UPDATE question_bank_replenishment_jobs SET status='queued',"
                "error_code='worker_interrupted',available_at="
                "strftime('%Y-%m-%dT%H:%M:%fZ','now'),updated_at="
                "strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE status='running' AND attempts<max_attempts"
            )
            await conn.execute(
                "UPDATE learning_calibration_jobs SET status='failed',"
                "error_code='retry_limit_reached',completed_at="
                "strftime('%Y-%m-%dT%H:%M:%fZ','now'),updated_at="
                "strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE status='running' AND attempts>=max_attempts"
            )
            await conn.execute(
                "UPDATE learning_calibration_jobs SET status='queued',"
                "error_code='worker_interrupted',available_at="
                "strftime('%Y-%m-%dT%H:%M:%fZ','now'),updated_at="
                "strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE status='running' AND attempts<max_attempts"
            )


async def enqueue_learning_calibration_refresh(
    database: SQLiteDatabase, now: datetime | None = None
) -> bool:
    """Queue one idempotent aggregate refresh per 15-minute UTC window."""
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    instant = instant.astimezone(UTC)
    bucket_minute = instant.minute - (instant.minute % CALIBRATION_REFRESH_MINUTES)
    schedule_key = instant.replace(minute=bucket_minute, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    async with database.acquire() as conn:
        async with conn.transaction():
            inserted = await conn.fetchrow(
                "INSERT OR IGNORE INTO learning_calibration_jobs "
                "(id,schedule_key,status,max_attempts) VALUES (?1,?2,'queued',?3) "
                "RETURNING id",
                str(uuid4()),
                schedule_key,
                CALIBRATION_JOB_MAX_ATTEMPTS,
            )
    return inserted is not None


async def _finish_calibration_job(
    database: SQLiteDatabase,
    *,
    job_id: str,
    status: str,
    error_code: str | None = None,
    delay_seconds: int = 0,
) -> None:
    await database.execute(
        "UPDATE learning_calibration_jobs SET status=?2,error_code=?3,"
        "available_at=strftime('%Y-%m-%dT%H:%M:%fZ','now','+'||?4||' seconds'),"
        "completed_at=CASE WHEN ?2 IN ('complete','failed') THEN "
        "strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE NULL END,"
        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
        "WHERE id=?1 AND status='running'",
        job_id,
        status,
        error_code,
        delay_seconds,
    )


async def process_learning_calibration_jobs(database: SQLiteDatabase, *, limit: int = 1) -> int:
    """Claim and run aggregate refreshes with bounded retry and restart recovery."""
    completed = 0
    for _ in range(max(0, limit)):
        async with database.acquire() as conn:
            async with conn.transaction():
                job = await conn.fetchrow(
                    "UPDATE learning_calibration_jobs SET status='running',"
                    "attempts=attempts+1,started_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=(SELECT id FROM "
                    "learning_calibration_jobs WHERE status='queued' AND attempts<max_attempts "
                    "AND available_at<=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                    "ORDER BY created_at,id LIMIT 1) RETURNING *"
                )
        if job is None:
            break
        job_id = str(job["id"])
        try:
            await refresh_calibration_flags(database)
        except asyncio.CancelledError:
            await _retry_calibration_job(database, job, error_code="worker_interrupted")
            raise
        except Exception:
            logger.warning("learning_calibration_refresh_failed job_id=%s", job_id)
            await _retry_calibration_job(database, job, error_code="refresh_failed")
        else:
            await _finish_calibration_job(database, job_id=job_id, status="complete")
            completed += 1
    return completed


async def _retry_calibration_job(
    database: SQLiteDatabase, job: dict[str, Any], *, error_code: str
) -> None:
    attempt = int(job["attempts"])
    if attempt >= int(job["max_attempts"]):
        await _finish_calibration_job(
            database, job_id=str(job["id"]), status="failed", error_code=error_code
        )
        return
    await _finish_calibration_job(
        database,
        job_id=str(job["id"]),
        status="queued",
        error_code=error_code,
        delay_seconds=min(1_800, 30 * (2 ** max(0, attempt - 1))),
    )


async def enqueue_question_bank_replenishment(
    database: SQLiteDatabase,
    limit: int = MAX_REPLENISHMENT_JOBS_PER_SCAN,
    daily_budget: int = BANK_DAILY_JOB_BUDGET,
) -> int:
    """Queue a small number of missing, source-supported class bank variants."""
    candidates = await database.fetch(
        "SELECT c.id AS concept_id,c.class_id,cl.owner_id FROM class_concepts c "
        "JOIN course_classes cl ON cl.id=c.class_id WHERE c.status='validated' AND EXISTS ("
        "SELECT 1 FROM class_concept_source_refs ref "
        "JOIN source_chunk_revisions scr ON scr.id=ref.chunk_revision_id "
        "JOIN source_index_heads h ON h.revision_id=scr.revision_id "
        "JOIN source_revisions sr ON sr.id=scr.revision_id "
        "JOIN course_notes n ON n.id=sr.note_id AND n.class_id=ref.class_id "
        "WHERE ref.class_id=c.class_id AND ref.concept_id=c.id AND scr.provenance='source' "
        "AND n.status='ready' AND n.publication_state='published' AND "
        "(n.release_at IS NULL OR n.release_at<=strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
        "AND h.processing_revision=n.processing_revision) "
        "ORDER BY c.class_id,c.id LIMIT 250"
    )
    queued = 0
    for candidate in candidates:
        if queued >= max(0, limit):
            break
        class_id = str(candidate["class_id"])
        concept_id = str(candidate["concept_id"])
        owner_id = str(candidate["owner_id"])
        try:
            # Owners have manager authority, but recheck it before scheduling work.
            await class_access(database, class_id, owner_id, required="create")
            source_digest = await current_concept_source_digest(
                database,
                class_id=class_id,
                user_id=owner_id,
                concept_ids=[concept_id],
            )
        except ApiError:
            continue
        for level in range(1, 5):
            current_count = int(
                await database.fetchval(
                    "SELECT count(*) FROM class_question_bank_items i "
                    "JOIN class_question_bank_concepts c "
                    "ON c.item_id=i.id AND c.class_id=i.class_id "
                    "WHERE i.class_id=?1 AND c.concept_id=?2 AND c.relation='primary' "
                    "AND i.level=?3 AND i.language='en' AND i.state='validated'",
                    class_id,
                    concept_id,
                    level,
                )
                or 0
            )
            if current_count >= BANK_TARGET_PER_SLOT:
                continue
            async with database.acquire() as conn:
                async with conn.transaction():
                    today_count = int(
                        await conn.fetchval(
                            "SELECT count(*) FROM question_bank_replenishment_jobs "
                            "WHERE created_at>=date('now')",
                        )
                        or 0
                    )
                    if today_count >= daily_budget:
                        return queued
                    existing = await conn.fetchval(
                        "SELECT 1 FROM question_bank_replenishment_jobs WHERE class_id=?1 "
                        "AND concept_id=?2 AND level=?3 AND language='en' AND source_digest=?4 "
                        "AND status IN ('queued','running','complete') LIMIT 1",
                        class_id,
                        concept_id,
                        level,
                        source_digest,
                    )
                    if existing:
                        continue
                    await conn.execute(
                        "INSERT OR IGNORE INTO question_bank_replenishment_jobs "
                        "(id,class_id,concept_id,level,language,source_digest,status) "
                        "VALUES (?1,?2,?3,?4,'en',?5,'queued')",
                        str(uuid4()),
                        class_id,
                        concept_id,
                        level,
                        source_digest,
                    )
                    queued += 1
                    if queued >= limit:
                        return queued
    return queued


async def _finish_job(
    database: SQLiteDatabase,
    *,
    job_id: str,
    status: str,
    error_code: str | None = None,
    delay_seconds: int = 0,
) -> None:
    await database.execute(
        "UPDATE question_bank_replenishment_jobs SET status=?2,error_code=?3,"
        "available_at=strftime('%Y-%m-%dT%H:%M:%fZ','now','+'||?4||' seconds'),"
        "completed_at=CASE WHEN ?2 IN ('complete','failed','superseded') THEN "
        "strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE NULL END,"
        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?1 AND status='running'",
        job_id,
        status,
        error_code,
        delay_seconds,
    )


async def process_question_bank_replenishment_jobs(application: Any, limit: int = 1) -> int:
    """Claim and process bounded jobs without holding SQLite across model calls."""
    database: SQLiteDatabase | None = getattr(application.state, "database", None)
    if database is None:
        return 0
    settings = application.state.settings
    service = getattr(application.state, "gemini_content_service", None)
    if service is None:
        return 0
    completed_count = 0
    for _ in range(max(0, limit)):
        async with database.acquire() as conn:
            async with conn.transaction():
                claimed = await conn.fetchrow(
                    "UPDATE question_bank_replenishment_jobs SET status='running',"
                    "attempts=attempts+1,started_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=(SELECT id FROM "
                    "question_bank_replenishment_jobs WHERE status='queued' "
                    "AND attempts<max_attempts "
                    "AND available_at<=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                    "ORDER BY created_at,id LIMIT 1) RETURNING *"
                )
        if claimed is None:
            break
        job_id = str(claimed["id"])
        class_row = await database.fetchrow(
            "SELECT owner_id FROM course_classes WHERE id=?1", claimed["class_id"]
        )
        if class_row is None:
            await _finish_job(
                database, job_id=job_id, status="superseded", error_code="class_deleted"
            )
            continue
        user_id = str(class_row["owner_id"])
        try:
            await class_access(database, str(claimed["class_id"]), user_id, required="create")
            current_digest = await current_concept_source_digest(
                database,
                class_id=str(claimed["class_id"]),
                user_id=user_id,
                concept_ids=[str(claimed["concept_id"])],
            )
            if current_digest != claimed["source_digest"]:
                await _finish_job(
                    database,
                    job_id=job_id,
                    status="superseded",
                    error_code="source_revision_superseded",
                )
                continue
            existing_count = int(
                await database.fetchval(
                    "SELECT count(*) FROM class_question_bank_items i "
                    "JOIN class_question_bank_concepts c "
                    "ON c.item_id=i.id AND c.class_id=i.class_id "
                    "WHERE i.class_id=?1 AND c.concept_id=?2 AND c.relation='primary' "
                    "AND i.level=?3 AND i.language=?4 AND i.state='validated'",
                    claimed["class_id"],
                    claimed["concept_id"],
                    claimed["level"],
                    claimed["language"],
                )
                or 0
            )
            if existing_count >= BANK_TARGET_PER_SLOT:
                await _finish_job(database, job_id=job_id, status="complete")
                continue
            # Two slots per cognitive level let the existing selector reuse
            # current families and generate only the missing independent variant.
            request = QuestionBankGenerationRequest(
                operation_id=UUID(job_id),
                kind="quiz",
                concept_ids=[UUID(str(claimed["concept_id"]))],
                count=8,
                level=None,
                language=str(claimed["language"]),
                question_types=["multiple_choice"],
            )
            await asyncio.wait_for(
                create_generated_assessment(
                    database,
                    class_id=str(claimed["class_id"]),
                    user_id=user_id,
                    payload=request,
                    service=service,
                    model_name=settings.gemini_text_model,
                    persist_assessment=False,
                    expected_source_digest=str(claimed["source_digest"]),
                ),
                timeout=REPLENISHMENT_DEADLINE_SECONDS,
            )
            current_digest = await current_concept_source_digest(
                database,
                class_id=str(claimed["class_id"]),
                user_id=user_id,
                concept_ids=[str(claimed["concept_id"])],
            )
            await _finish_job(
                database,
                job_id=job_id,
                status="complete" if current_digest == claimed["source_digest"] else "superseded",
                error_code=None
                if current_digest == claimed["source_digest"]
                else "source_revision_superseded",
            )
            completed_count += 1
        except ApiError as error:
            if error.code in {
                "not_found",
                "class_access_denied",
                "source_chunks_unavailable",
                "replenishment_source_superseded",
                "concept_validation_stale",
            }:
                await _finish_job(
                    database,
                    job_id=job_id,
                    status="superseded",
                    error_code="source_or_access_changed",
                )
            else:
                logger.warning(
                    "question_bank_replenishment_retryable job_id=%s code=%s",
                    job_id,
                    error.code[:80],
                )
                await _retry_or_fail(database, claimed, error_code=error.code[:80])
        except asyncio.CancelledError:
            await _retry_or_fail(database, claimed, error_code="worker_interrupted")
            raise
        except Exception:
            logger.exception("question_bank_replenishment_failed job_id=%s", job_id)
            await _retry_or_fail(database, claimed, error_code="generation_failed")
    return completed_count


async def _retry_or_fail(database: SQLiteDatabase, job: dict[str, Any], *, error_code: str) -> None:
    attempt = int(job["attempts"])
    if attempt >= int(job["max_attempts"]):
        await _finish_job(database, job_id=str(job["id"]), status="failed", error_code=error_code)
    else:
        await _finish_job(
            database,
            job_id=str(job["id"]),
            status="queued",
            error_code=error_code,
            delay_seconds=min(1_800, 30 * (2 ** max(0, attempt - 1))),
        )


async def refresh_calibration_flags(database: SQLiteDatabase) -> dict[str, int]:
    """Refresh conservative item and prerequisite flags using aggregate evidence only."""
    question_rows = await database.fetch(
        "SELECT e.class_id,e.bank_item_id,count(*) AS response_count,"
        "avg(e.correctness-COALESCE(e.ability_estimate,0.5)) AS residual "
        "FROM question_calibration_events e JOIN quiz_runs r ON r.id=e.run_id "
        "WHERE e.event_type='response' AND e.correctness IS NOT NULL "
        "GROUP BY e.class_id,e.bank_item_id HAVING count(*)>=?1",
        QUESTION_FLAG_MIN_RESPONSES,
    )
    question_flags = 0
    for row in question_rows:
        residual = max(-1.0, min(1.0, float(row["residual"] or 0.0)))
        if abs(residual) < QUESTION_FLAG_RESIDUAL_THRESHOLD:
            continue
        await database.execute(
            "INSERT INTO question_calibration_flags "
            "(id,class_id,bank_item_id,flag_code,response_count,adjusted_residual) "
            "VALUES (?1,?2,?3,'ability_adjusted_mismatch',?4,?5) "
            "ON CONFLICT(bank_item_id,flag_code) DO UPDATE SET "
            "response_count=excluded.response_count,adjusted_residual=excluded.adjusted_residual,"
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE question_calibration_flags.state='open'",
            str(uuid4()),
            row["class_id"],
            row["bank_item_id"],
            int(row["response_count"]),
            residual,
        )
        question_flags += 1

    edges = await database.fetch(
        "SELECT id,class_id,from_concept_id,to_concept_id FROM class_concept_edges "
        "WHERE status='validated' AND type='prerequisite_for' ORDER BY class_id,id"
    )
    graph_flags = 0
    for edge in edges:
        evidence = await database.fetch(
            "SELECT owner_id,concept_id,avg(correctness) AS rate FROM mastery_evidence "
            "WHERE class_id=?1 AND concept_id IN (?2,?3) GROUP BY owner_id,concept_id",
            edge["class_id"],
            edge["from_concept_id"],
            edge["to_concept_id"],
        )
        by_owner: dict[str, dict[str, float]] = {}
        for item in evidence:
            owner = str(item["owner_id"])
            concept = str(item["concept_id"])
            by_owner.setdefault(owner, {})[concept] = float(item["rate"])
        prerequisite_id = str(edge["from_concept_id"])
        downstream_id = str(edge["to_concept_id"])
        paired = [
            pair for pair in by_owner.values() if prerequisite_id in pair and downstream_id in pair
        ]
        if len(paired) < GRAPH_FLAG_MIN_LEARNERS:
            continue
        prerequisite_rate = sum(pair[prerequisite_id] for pair in paired) / len(paired)
        downstream_rate = sum(pair[downstream_id] for pair in paired) / len(paired)
        if abs(prerequisite_rate - downstream_rate) < GRAPH_FLAG_RATE_DIFFERENCE:
            continue
        await database.execute(
            "INSERT INTO calibration_graph_flags "
            "(id,class_id,edge_id,flag_code,observed_learners,prerequisite_rate,downstream_rate) "
            "VALUES (?1,?2,?3,'asymmetric_performance',?4,?5,?6) "
            "ON CONFLICT(edge_id,flag_code) DO UPDATE SET "
            "observed_learners=excluded.observed_learners,"
            "prerequisite_rate=excluded.prerequisite_rate,downstream_rate=excluded.downstream_rate,"
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE calibration_graph_flags.state='open'",
            str(uuid4()),
            edge["class_id"],
            edge["id"],
            len(paired),
            prerequisite_rate,
            downstream_rate,
        )
        graph_flags += 1
    return {"question_flags": question_flags, "graph_flags": graph_flags}
