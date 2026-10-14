"""Private, replayable scoring and Bayesian-inspired mastery projections."""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.services.database import SQLiteConnection, SQLiteDatabase, json_value

ALGORITHM_VERSION = "beta-evidence-v2"
PRIOR_ALPHA = 1.0
PRIOR_BETA = 1.0
MASTERY_THRESHOLD = 0.80
CONFIRMATION_PROBABILITY = 0.90
RECENCY_DECAY_PER_OBSERVATION = 0.96
REVIEW_AFTER_DAYS = 90
MAX_EFFECTIVE_WEIGHT_PER_FAMILY = 1.0
MAX_EFFECTIVE_WEIGHT_PER_RUN_CONCEPT_LEVEL = 2.0
SHORT_INTERVAL_WEIGHT_BANDS = ((30, 0.25), (300, 0.5), (1800, 0.75))
MAX_XP_PER_RUN_AND_TYPE = {
    "valid_response": 25,
    "correct_response": 25,
    "accepted_reasoning": 25,
    "assessment_complete": 10,
    "ladder_complete": 20,
    "verified_improvement": 15,
}

XP_AWARDS = {
    "valid_response": 5,
    "correct_response": 5,
    "accepted_reasoning": 5,
    "assessment_complete": 10,
    "ladder_complete": 20,
    "verified_improvement": 15,
}


def _source_revision_digest(private_snapshot: dict[str, Any]) -> str:
    values = private_snapshot.get("source_chunk_revision_ids", [])
    source_ids = sorted({str(value) for value in values if isinstance(value, str)})
    return hashlib.sha256("\n".join(source_ids).encode("utf-8")).hexdigest()


async def record_question_exposure(
    conn: SQLiteConnection,
    *,
    run_id: str,
    question_id: str,
    bank_item_id: str,
    private_snapshot: dict[str, Any],
) -> None:
    """Record one safe assessment delivery without storing learner identity or text."""
    run = await conn.fetchrow(
        "SELECT r.owner_id,m.class_id,m.origin_class_id FROM quiz_runs r "
        "JOIN study_materials m ON m.id=r.material_id WHERE r.id=?1",
        run_id,
    )
    item = await conn.fetchrow(
        "SELECT family_id,level,generation_version,exposure_count FROM class_question_bank_items "
        "WHERE id=?1",
        bank_item_id,
    )
    if run is None or item is None:
        return
    class_id = str(run["class_id"] or run["origin_class_id"] or "")
    concepts = private_snapshot.get("concept_ids", [])
    concept_id = str(concepts[0]) if isinstance(concepts, list) and concepts else ""
    level = private_snapshot.get("level")
    source_ids = private_snapshot.get("source_chunk_revision_ids", [])
    if not class_id or not concept_id or not isinstance(level, int) or not source_ids:
        return
    ability = await conn.fetchval(
        "SELECT mean FROM mastery_projections WHERE owner_id=?1 AND concept_id=?2 AND level=?3",
        run["owner_id"],
        concept_id,
        level,
    )
    await conn.execute(
        "INSERT OR IGNORE INTO question_calibration_events "
        "(id,class_id,bank_item_id,run_id,question_id,concept_id,family_id,level,event_type,"
        "ability_estimate,repeated_exposure,source_revision_digest,generation_version) "
        "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,'exposure',?9,?10,?11,?12)",
        str(uuid4()),
        class_id,
        bank_item_id,
        run_id,
        question_id,
        concept_id,
        item["family_id"],
        level,
        ability,
        int(int(item["exposure_count"]) > 0),
        _source_revision_digest(private_snapshot),
        item["generation_version"],
    )


async def _record_calibration_response(
    conn: SQLiteConnection,
    *,
    run: dict[str, Any],
    attempt: dict[str, Any],
    question_row: dict[str, Any],
    private_snapshot: dict[str, Any],
    correctness: float,
    reasoning_status: str,
) -> None:
    bank_item_id = question_row.get("bank_item_id")
    concepts = private_snapshot.get("concept_ids", [])
    level = private_snapshot.get("level")
    source_ids = private_snapshot.get("source_chunk_revision_ids", [])
    if (
        not bank_item_id
        or not isinstance(concepts, list)
        or not concepts
        or not isinstance(level, int)
        or not source_ids
    ):
        return
    item = await conn.fetchrow(
        "SELECT family_id,generation_version FROM class_question_bank_items WHERE id=?1",
        bank_item_id,
    )
    material = await conn.fetchrow(
        "SELECT class_id,origin_class_id FROM study_materials WHERE id=?1", run["material_id"]
    )
    if item is None or material is None:
        return
    class_id = str(material["class_id"] or material["origin_class_id"] or "")
    concept_id = str(concepts[0])
    if not class_id:
        return
    ability = await conn.fetchval(
        "SELECT mean FROM mastery_projections WHERE owner_id=?1 AND concept_id=?2 AND level=?3",
        run["owner_id"],
        concept_id,
        level,
    )
    await conn.execute(
        "INSERT OR IGNORE INTO question_calibration_events "
        "(id,class_id,bank_item_id,run_id,question_id,attempt_id,concept_id,family_id,level,"
        "event_type,ability_estimate,correctness,reasoning_result,repeated_exposure,"
        "source_revision_digest,generation_version) "
        "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,'response',?10,?11,?12,"
        "EXISTS(SELECT 1 FROM question_calibration_events WHERE bank_item_id=?3 "
        "AND event_type='exposure' AND repeated_exposure=1),?13,?14)",
        str(uuid4()),
        class_id,
        bank_item_id,
        run["id"],
        question_row["id"],
        attempt["id"],
        concept_id,
        item["family_id"],
        level,
        ability,
        correctness,
        reasoning_status,
        _source_revision_digest(private_snapshot),
        item["generation_version"],
    )


def _beta_fraction(a: float, b: float, x: float) -> float:
    """Continued fraction used by the regularized incomplete beta function."""
    max_iterations = 240
    epsilon = 3.0e-14
    tiny = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    result = d
    for index in range(1, max_iterations + 1):
        m2 = 2 * index
        aa = index * (b - index) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        result *= d * c
        aa = -(a + index) * (qab + index) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) < epsilon:
            break
    return result


def regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_fraction(a, b, x) / a
    return 1.0 - front * _beta_fraction(b, a, 1.0 - x) / b


def beta_quantile(probability: float, alpha: float, beta: float) -> float:
    low, high = 0.0, 1.0
    for _ in range(72):
        middle = (low + high) / 2
        if regularized_beta(middle, alpha, beta) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def posterior(alpha: float, beta: float) -> dict[str, float]:
    mean = alpha / (alpha + beta)
    probability = 1.0 - regularized_beta(MASTERY_THRESHOLD, alpha, beta)
    return {
        "mean": mean,
        "lower_90": beta_quantile(0.05, alpha, beta),
        "upper_90": beta_quantile(0.95, alpha, beta),
        "probability_threshold": min(1.0, max(0.0, probability)),
    }


async def _award_xp(
    conn: SQLiteConnection,
    *,
    owner_id: str,
    event_key: str,
    event_type: str,
    run_id: str | None,
    attempt_id: str | None = None,
    release_material_id: str | None = None,
) -> bool:
    amount = XP_AWARDS[event_type]
    if run_id is not None:
        awarded = await conn.fetchval(
            "SELECT COALESCE(sum(amount),0) FROM learning_xp_events "
            "WHERE owner_id=?1 AND run_id=?2 AND event_type=?3",
            owner_id,
            run_id,
            event_type,
        )
        amount = min(
            amount,
            max(0, MAX_XP_PER_RUN_AND_TYPE[event_type] - int(awarded or 0)),
        )
        if amount <= 0:
            return False
    result = await conn.fetchrow(
        "INSERT INTO learning_xp_events "
        "(id,owner_id,event_key,event_type,amount,run_id,attempt_id,release_material_id,"
        "algorithm_version) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9) "
        "ON CONFLICT(owner_id,event_key) DO NOTHING RETURNING id",
        str(uuid4()),
        owner_id,
        event_key,
        event_type,
        amount,
        run_id,
        attempt_id,
        release_material_id,
        ALGORITHM_VERSION,
    )
    return result is not None


async def record_assessment_outcome(
    conn: SQLiteConnection,
    *,
    run: dict[str, Any],
    attempt: dict[str, Any],
    question_row: dict[str, Any],
    status: str,
    correctness: float | None,
    reasoning_status: str,
) -> None:
    """Atomically persist one outcome and capped participation/grade XP."""
    private = json_value(question_row.get("private_snapshot"), {})
    family_id = str(question_row.get("family_id") or "unknown")
    attempt_id = str(attempt["id"])
    run_id = str(run["id"])
    owner_id = str(run["owner_id"])
    material_id = str(run["material_id"])
    level = private.get("level")
    level = level if isinstance(level, int) and 1 <= level <= 4 else None
    grading_policy = str(
        run.get("grading_policy")
        or ("reasoning_optional" if run.get("mode") == "reasoning" else "objective_only")
    )
    quality_weight = await _quality_weight_for_outcome(
        conn,
        owner_id=owner_id,
        private_snapshot=private,
        level=level,
        evidence_eligible=(
            status in {"correct", "incorrect"} and bool(question_row.get("evidence_eligible"))
        ),
    )
    await conn.execute(
        "INSERT OR IGNORE INTO assessment_outcomes "
        "(id,attempt_id,run_id,owner_id,material_id,question_id,family_id,status,correctness,"
        "reasoning_status,grading_policy,level,quality_weight) "
        "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13)",
        str(uuid4()),
        attempt_id,
        run_id,
        owner_id,
        material_id,
        str(question_row["id"]),
        family_id,
        status,
        correctness,
        reasoning_status,
        grading_policy,
        level,
        quality_weight,
    )
    if status in {"correct", "incorrect"} and question_row.get("bank_item_id"):
        await _record_calibration_response(
            conn,
            run=run,
            attempt=attempt,
            question_row=question_row,
            private_snapshot=private,
            correctness=float(correctness or 0),
            reasoning_status=reasoning_status,
        )
    # Objective accuracy and participation stay separate from reasoning quality.
    # A failed rubric blocks mastery evidence and reasoning XP, not these events.
    if status in {"correct", "incorrect"}:
        prefix = f"run:{run_id}:family:{family_id}"
        await _award_xp(
            conn,
            owner_id=owner_id,
            event_key=f"{prefix}:valid_response",
            event_type="valid_response",
            run_id=run_id,
            attempt_id=attempt_id,
        )
        if correctness == 1.0:
            await _award_xp(
                conn,
                owner_id=owner_id,
                event_key=f"{prefix}:correct_response",
                event_type="correct_response",
                run_id=run_id,
                attempt_id=attempt_id,
                release_material_id=material_id,
            )
        accepted_snapshot = await conn.fetchval(
            "SELECT accepted_snapshot_hash FROM quiz_question_attempts WHERE id=?1",
            attempt_id,
        )
        if accepted_snapshot or reasoning_status in {"sufficient", "rubric_passed"}:
            await _award_xp(
                conn,
                owner_id=owner_id,
                event_key=f"{prefix}:accepted_reasoning",
                event_type="accepted_reasoning",
                run_id=run_id,
                attempt_id=attempt_id,
            )
    persisted = await conn.fetchrow(
        "SELECT o.*,r.feedback_release_policy,r.status AS run_status "
        "FROM assessment_outcomes o JOIN quiz_runs r ON r.id=o.run_id "
        "WHERE o.attempt_id=?1",
        attempt_id,
    )
    if persisted and await feedback_released(conn, persisted):
        await _record_recommendation_followup(conn, persisted)
        await _materialize_outcome_evidence(conn, persisted)


async def _record_recommendation_followup(conn: SQLiteConnection, outcome: dict[str, Any]) -> None:
    """Record later, released performance after an engaged practice suggestion."""
    if outcome["status"] not in {"correct", "incorrect"}:
        return
    question = await conn.fetchrow(
        "SELECT q.private_snapshot,COALESCE(m.class_id,m.origin_class_id) AS class_id "
        "FROM quiz_run_questions q JOIN study_materials m ON m.id=q.material_id "
        "WHERE q.run_id=?1 AND q.id=?2",
        outcome["run_id"],
        outcome["question_id"],
    )
    if question is None or question["class_id"] is None:
        return
    snapshot = json_value(question["private_snapshot"], {})
    concept_ids = snapshot.get("concept_ids", [])
    if not isinstance(concept_ids, list):
        return
    for concept_id in dict.fromkeys(str(value) for value in concept_ids if value):
        await conn.execute(
            "INSERT OR IGNORE INTO learning_recommendation_events "
            "(id,recommendation_id,owner_id,event_type,related_attempt_id,event_date) "
            "SELECT lower(hex(randomblob(16))),r.id,r.owner_id,'followed_by_performance',?4,"
            "substr(?5,1,10) FROM learning_recommendations r WHERE r.owner_id=?1 "
            "AND r.class_id=?2 AND r.concept_id=?3 AND r.created_at<?5 "
            "AND EXISTS (SELECT 1 FROM learning_recommendation_events e "
            "WHERE e.recommendation_id=r.id AND e.event_type='engaged') "
            "AND NOT EXISTS (SELECT 1 FROM learning_recommendation_events e "
            "WHERE e.recommendation_id=r.id AND e.event_type='followed_by_performance')",
            outcome["owner_id"],
            question["class_id"],
            concept_id,
            outcome["attempt_id"],
            outcome["created_at"],
        )


async def award_ladder_completion(conn: SQLiteConnection, *, owner_id: str, run_id: str) -> bool:
    """Award the one-time completion bonus only after every ladder concept passed."""
    run = await conn.fetchrow(
        "SELECT assessment_type,status FROM quiz_runs WHERE id=?1 AND owner_id=?2",
        run_id,
        owner_id,
    )
    if run is None or run["assessment_type"] != "mastery_ladder" or run["status"] != "completed":
        return False
    counts = await conn.fetchrow(
        "SELECT count(*) AS total,sum(CASE WHEN c.state='passed' THEN 1 ELSE 0 END) AS passed "
        "FROM mastery_ladder_concepts c JOIN mastery_ladder_sessions l ON l.id=c.ladder_id "
        "WHERE l.run_id=?1 AND l.owner_id=?2",
        run_id,
        owner_id,
    )
    if counts is None or int(counts["total"] or 0) == 0:
        return False
    if int(counts["passed"] or 0) != int(counts["total"]):
        return False
    return await _award_xp(
        conn,
        owner_id=owner_id,
        event_key=f"run:{run_id}:ladder_complete",
        event_type="ladder_complete",
        run_id=run_id,
    )


async def _quality_weight_for_outcome(
    conn: SQLiteConnection,
    *,
    owner_id: str,
    private_snapshot: dict[str, Any],
    level: int | None,
    evidence_eligible: bool,
) -> float:
    """Persist source validation and spacing discounts before replay."""
    if not evidence_eligible or level is None:
        return 0.0
    concept_ids = {str(value) for value in private_snapshot.get("concept_ids", [])}
    if not concept_ids:
        return 0.0
    # Only previously released, positive-weight observations that actually
    # reached the authoritative ledger may affect spacing. Pending/manual/never
    # outcomes and invalid items must not make later evidence look like a repeat.
    prior_rows = await conn.fetch(
        "SELECT e.created_at,e.level,q.private_snapshot FROM mastery_evidence e "
        "JOIN quiz_run_questions q ON q.run_id=e.run_id AND q.id=e.question_id "
        "WHERE e.owner_id=?1 AND e.quality_weight>0 "
        "ORDER BY e.created_at DESC,e.id DESC LIMIT 250",
        owner_id,
    )
    prior_time: datetime | None = None
    for row in prior_rows:
        if row["level"] != level:
            continue
        prior_snapshot = json_value(row["private_snapshot"], {})
        prior_concepts = prior_snapshot.get("concept_ids", [])
        if not concept_ids.intersection(str(value) for value in prior_concepts):
            continue
        try:
            prior_time = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        break
    if prior_time is None:
        return 1.0
    elapsed = max(0.0, (datetime.now(UTC) - prior_time).total_seconds())
    for seconds, weight in SHORT_INTERVAL_WEIGHT_BANDS:
        if elapsed < seconds:
            return weight
    return 1.0


async def _materialize_outcome_evidence(conn: SQLiteConnection, outcome: dict[str, Any]) -> None:
    if outcome["status"] not in {"correct", "incorrect"} or outcome["correctness"] is None:
        return
    if float(outcome["quality_weight"]) <= 0:
        return
    if (
        outcome["grading_policy"] == "reasoning_required"
        and outcome["reasoning_status"] != "rubric_passed"
    ):
        return
    question_row = await conn.fetchrow(
        "SELECT q.*,COALESCE(m.class_id,m.origin_class_id) AS class_id "
        "FROM quiz_run_questions q JOIN study_materials m ON m.id=q.material_id "
        "WHERE q.run_id=?1 AND q.id=?2",
        outcome["run_id"],
        outcome["question_id"],
    )
    if question_row is None or not int(question_row["evidence_eligible"]):
        return
    private = json_value(question_row["private_snapshot"], {})
    concepts = private.get("concept_ids")
    labels = private.get("concept_labels")
    level = private.get("level")
    if not isinstance(concepts, list) or not isinstance(labels, dict) or not isinstance(level, int):
        return
    owner_id = str(outcome["owner_id"])
    for concept_id in dict.fromkeys(str(item) for item in concepts):
        label = labels.get(concept_id)
        if not isinstance(label, str) or not label.strip():
            continue
        previous = await conn.fetchrow(
            "SELECT current_confirmed FROM mastery_projections WHERE owner_id=?1 "
            "AND concept_id=?2 AND level=?3",
            owner_id,
            concept_id,
            level,
        )
        inserted = await conn.fetchrow(
            "INSERT INTO mastery_evidence "
            "(id,owner_id,class_id,concept_id,concept_label,run_id,attempt_id,question_id,family_id,"
            "level,correctness,reasoning_result,grading_policy,quality_weight,algorithm_version) "
            "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15) "
            "ON CONFLICT(owner_id,attempt_id,concept_id) DO NOTHING RETURNING id",
            str(uuid4()),
            owner_id,
            question_row["class_id"],
            concept_id,
            label[:240],
            outcome["run_id"],
            outcome["attempt_id"],
            outcome["question_id"],
            outcome["family_id"],
            level,
            outcome["correctness"],
            outcome["reasoning_status"],
            outcome["grading_policy"],
            float(outcome["quality_weight"]),
            ALGORITHM_VERSION,
        )
        if inserted is None:
            continue
        await rebuild_projection(conn, owner_id=owner_id, concept_id=concept_id)
        current = await conn.fetchval(
            "SELECT current_confirmed FROM mastery_projections WHERE owner_id=?1 "
            "AND concept_id=?2 AND level=?3",
            owner_id,
            concept_id,
            level,
        )
        if not bool(previous and previous["current_confirmed"]) and bool(current):
            await _award_xp(
                conn,
                owner_id=owner_id,
                event_key=(f"concept:{concept_id}:evidence:{inserted['id']}:verified-improvement"),
                event_type="verified_improvement",
                run_id=str(outcome["run_id"]),
                attempt_id=str(outcome["attempt_id"]),
                release_material_id=str(outcome["material_id"]),
            )


async def materialize_released_evidence_for_run(conn: SQLiteConnection, *, run_id: str) -> None:
    outcomes = await conn.fetch(
        "SELECT o.*,r.feedback_release_policy,r.status AS run_status "
        "FROM assessment_outcomes o JOIN quiz_runs r ON r.id=o.run_id "
        "WHERE o.run_id=?1 ORDER BY o.created_at,o.id",
        run_id,
    )
    for outcome in outcomes:
        if await feedback_released(conn, outcome):
            await _record_recommendation_followup(conn, outcome)
            await _materialize_outcome_evidence(conn, outcome)


async def award_run_completion(conn: SQLiteConnection, *, run: dict[str, Any]) -> None:
    await _award_xp(
        conn,
        owner_id=str(run["owner_id"]),
        event_key=f"run:{run['id']}:assessment_complete",
        event_type="assessment_complete",
        run_id=str(run["id"]),
    )


async def rebuild_projection(conn: SQLiteConnection, *, owner_id: str, concept_id: str) -> None:
    """Rebuild one private concept projection and its dated evidence snapshots."""
    evidence = await conn.fetch(
        "SELECT * FROM mastery_evidence WHERE owner_id=?1 AND concept_id=?2 ORDER BY created_at,id",
        owner_id,
        concept_id,
    )
    await conn.execute(
        "DELETE FROM mastery_projections WHERE owner_id=?1 AND concept_id=?2",
        owner_id,
        concept_id,
    )
    await conn.execute(
        "DELETE FROM mastery_snapshots WHERE owner_id=?1 AND concept_id=?2",
        owner_id,
        concept_id,
    )
    if not evidence:
        return
    label = str(evidence[-1]["concept_label"])
    class_id = evidence[-1]["class_id"]
    family_weight: dict[str, float] = {}
    session_weight: dict[tuple[str, int], float] = {}
    alpha_beta = {level: [PRIOR_ALPHA, PRIOR_BETA] for level in range(1, 5)}
    level_counts = {level: 0 for level in range(1, 5)}
    ever_confirmed = {level: False for level in range(1, 5)}
    currently_confirmed = {level: False for level in range(1, 5)}
    recent_independent_correctness = {level: [] for level in range(1, 5)}
    last_assessed_at_by_level: dict[int, str | None] = {level: None for level in range(1, 5)}
    historical_peak: int | None = None
    for item in evidence:
        level = int(item["level"])
        family = str(item["family_id"])
        used = family_weight.get(family, 0.0)
        base_weight = float(item["quality_weight"])
        run_level_key = (str(item["run_id"]), level)
        session_used = session_weight.get(run_level_key, 0.0)
        independent_weight = min(
            base_weight,
            max(0.0, MAX_EFFECTIVE_WEIGHT_PER_FAMILY - used),
            max(0.0, MAX_EFFECTIVE_WEIGHT_PER_RUN_CONCEPT_LEVEL - session_used),
        )
        family_weight[family] = used + independent_weight
        session_weight[run_level_key] = session_used + independent_weight
        # Retries of an already-consumed family/run cap are not independent
        # observations and therefore must not age the evidence ledger.
        if independent_weight <= 0:
            continue

        # Replay chronologically within this demand level. Evidence at another
        # level is a separate stratum and does not age this level's projection.
        # Each dated snapshot therefore depends only on its own level's prefix.
        alpha_beta[level][0] = (
            PRIOR_ALPHA + (alpha_beta[level][0] - PRIOR_ALPHA) * RECENCY_DECAY_PER_OBSERVATION
        )
        alpha_beta[level][1] = (
            PRIOR_BETA + (alpha_beta[level][1] - PRIOR_BETA) * RECENCY_DECAY_PER_OBSERVATION
        )
        weight = independent_weight
        correctness = float(item["correctness"])
        alpha_beta[level][0] += weight * correctness
        alpha_beta[level][1] += weight * (1.0 - correctness)
        level_counts[level] += 1
        last_assessed_at_by_level[level] = str(item["created_at"])
        recent_independent_correctness[level].append(correctness)
        recent_independent_correctness[level] = recent_independent_correctness[level][-4:]
        for candidate in range(1, 5):
            stats = posterior(*alpha_beta[candidate])
            if stats["probability_threshold"] >= CONFIRMATION_PROBABILITY:
                ever_confirmed[candidate] = True
                historical_peak = max(historical_peak or candidate, candidate)
            if ever_confirmed[candidate]:
                recent = recent_independent_correctness[candidate]
                too_many_failures = len(recent) >= 4 and sum(value < 0.5 for value in recent) >= 3
                # Require both a sustained independent regression pattern and
                # less than even posterior probability of meeting proficiency.
                # A single miss cannot demote a confirmed level.
                if too_many_failures and stats["probability_threshold"] < 0.5:
                    currently_confirmed[candidate] = False
                elif stats["probability_threshold"] >= CONFIRMATION_PROBABILITY:
                    currently_confirmed[candidate] = True
        snapshot_stats = posterior(*alpha_beta[level])
        snapshot_current_level = max(
            (candidate for candidate, confirmed in currently_confirmed.items() if confirmed),
            default=None,
        )
        await conn.execute(
            "INSERT OR IGNORE INTO mastery_snapshots "
            "(id,owner_id,concept_id,concept_label,class_id,level,mean,lower_90,upper_90,"
            "evidence_count,current_level,trigger_evidence_id,algorithm_version,as_of) "
            "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14)",
            str(uuid4()),
            owner_id,
            concept_id,
            label,
            class_id,
            level,
            snapshot_stats["mean"],
            snapshot_stats["lower_90"],
            snapshot_stats["upper_90"],
            level_counts[level],
            snapshot_current_level,
            item["id"],
            ALGORITHM_VERSION,
            item["created_at"],
        )
    for level in range(1, 5):
        last_assessed_at = last_assessed_at_by_level[level]
        if last_assessed_at is None:
            review_due = False
        else:
            try:
                parsed_last = datetime.fromisoformat(last_assessed_at.replace("Z", "+00:00"))
            except ValueError:
                parsed_last = datetime.now(UTC)
            review_due = parsed_last < datetime.now(UTC) - timedelta(days=REVIEW_AFTER_DAYS)
        stats = posterior(*alpha_beta[level])
        await conn.execute(
            "INSERT INTO mastery_projections "
            "(owner_id,concept_id,concept_label,class_id,level,alpha,beta,mean,lower_90,upper_90,"
            "probability_threshold,evidence_count,historical_peak,current_confirmed,last_assessed_at,"
            "review_due,algorithm_version) "
            "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17)",
            owner_id,
            concept_id,
            label,
            class_id,
            level,
            alpha_beta[level][0],
            alpha_beta[level][1],
            stats["mean"],
            stats["lower_90"],
            stats["upper_90"],
            stats["probability_threshold"],
            level_counts[level],
            historical_peak,
            int(currently_confirmed[level]),
            last_assessed_at,
            int(review_due),
            ALGORITHM_VERSION,
        )


async def rebuild_all_projections(database: SQLiteDatabase, owner_id: str | None = None) -> int:
    """Deterministic maintenance/test entry point for replaying the append-only ledger."""
    if owner_id is None:
        concepts = await database.fetch(
            "SELECT DISTINCT owner_id,concept_id FROM mastery_evidence ORDER BY owner_id,concept_id"
        )
    else:
        concepts = await database.fetch(
            "SELECT DISTINCT owner_id,concept_id FROM mastery_evidence WHERE owner_id=?1 "
            "ORDER BY concept_id",
            owner_id,
        )
    async with database.acquire() as conn:
        async with conn.transaction():
            if owner_id is None:
                await conn.execute("DELETE FROM mastery_projections")
                await conn.execute("DELETE FROM mastery_snapshots")
            else:
                await conn.execute("DELETE FROM mastery_projections WHERE owner_id=?1", owner_id)
                await conn.execute("DELETE FROM mastery_snapshots WHERE owner_id=?1", owner_id)
            for row in concepts:
                await rebuild_projection(
                    conn,
                    owner_id=str(row["owner_id"]),
                    concept_id=str(row["concept_id"]),
                )
    return len(concepts)


async def feedback_released(db: SQLiteConnection | SQLiteDatabase, run: dict[str, Any]) -> bool:
    policy = str(run.get("feedback_release_policy") or "after_completion")
    if policy == "never":
        return False
    if policy == "after_completion":
        return run.get("run_status", run.get("status")) == "completed"
    return bool(
        await db.fetchval(
            "SELECT 1 FROM study_material_feedback_releases WHERE material_id=?1",
            run["material_id"],
        )
    )


async def attempt_learning_outcome(
    db: SQLiteConnection | SQLiteDatabase,
    *,
    attempt_id: str,
    owner_id: str,
) -> dict[str, Any] | None:
    outcome = await db.fetchrow(
        "SELECT o.*,r.feedback_release_policy,r.status AS run_status,r.material_id "
        "FROM assessment_outcomes o JOIN quiz_runs r ON r.id=o.run_id "
        "WHERE o.attempt_id=?1 AND o.owner_id=?2",
        attempt_id,
        owner_id,
    )
    if outcome is None:
        return None
    released = await feedback_released(db, outcome)
    xp_events = await db.fetch(
        "SELECT event_type,amount,release_material_id FROM learning_xp_events "
        "WHERE owner_id=?1 AND attempt_id=?2",
        owner_id,
        attempt_id,
    )
    xp_awarded = 0
    for event in xp_events:
        if event["event_type"] == "correct_response" and not released:
            continue
        xp_awarded += int(event["amount"])
    status = str(outcome["status"])
    correctness = outcome["correctness"]
    if status in {"correct", "incorrect"} and not released:
        status = "pending"
        correctness = None
    return {
        "status": status,
        "correctness": correctness,
        "reasoning_status": outcome["reasoning_status"],
        "xp_awarded": xp_awarded,
    }


async def learning_progress(
    db: SQLiteConnection | SQLiteDatabase,
    *,
    owner_id: str,
    visible_class_ids: list[str],
    visible_concepts: list[dict[str, Any]],
    window_days: int = 30,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    cutoff = (
        (now - timedelta(days=window_days))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    class_ids = sorted(set(visible_class_ids))
    class_slots = ",".join(f"?{index + 3}" for index in range(len(class_ids)))
    outcomes = []
    xp_rows = []
    projection_rows = []
    if class_ids:
        outcomes = await db.fetch(
            "SELECT o.*,r.feedback_release_policy,r.status AS run_status "
            "FROM assessment_outcomes o JOIN quiz_runs r ON r.id=o.run_id "
            "JOIN study_materials m ON m.id=o.material_id "
            f"WHERE o.owner_id=?1 AND o.created_at>=?2 AND "
            f"COALESCE(m.class_id,m.origin_class_id) IN ({class_slots}) "
            "ORDER BY o.created_at,o.id",
            owner_id,
            cutoff,
            *class_ids,
        )
    counts = {
        "window_days": window_days,
        "graded_count": 0,
        "correct_count": 0,
        "incorrect_count": 0,
        "unanswered_count": 0,
        "skipped_count": 0,
        "pending_count": 0,
        "rate": None,
    }
    for row in outcomes:
        status = str(row["status"])
        is_released = await feedback_released(db, row)
        if status == "skipped":
            counts["skipped_count"] += 1
        elif status == "unanswered":
            counts["unanswered_count"] += 1
        elif status in {"correct", "incorrect"} and is_released:
            counts["graded_count"] += 1
            counts["correct_count" if status == "correct" else "incorrect_count"] += 1
        else:
            counts["pending_count"] += 1
    if counts["graded_count"]:
        counts["rate"] = counts["correct_count"] / counts["graded_count"]
    if class_ids:
        xp_slots = ",".join(f"?{index + 2}" for index in range(len(class_ids)))
        xp_rows = await db.fetch(
            "SELECT x.*,r.feedback_release_policy,r.status AS run_status,"
            "COALESCE(r.material_id,x.release_material_id) AS material_id,"
            "COALESCE(m.class_id,m.origin_class_id) AS class_id "
            "FROM learning_xp_events x LEFT JOIN quiz_runs r ON r.id=x.run_id "
            "LEFT JOIN study_materials m ON m.id=COALESCE(r.material_id,x.release_material_id) "
            f"WHERE x.owner_id=?1 AND COALESCE(m.class_id,m.origin_class_id) IN ({xp_slots})",
            owner_id,
            *class_ids,
        )
    total_xp = 0
    for event in xp_rows:
        if event["event_type"] == "correct_response":
            if event["run_id"] is None or not await feedback_released(db, event):
                continue
        total_xp += int(event["amount"])
    if class_ids:
        projection_slots = ",".join(f"?{index + 2}" for index in range(len(class_ids)))
        projection_rows = await db.fetch(
            "SELECT * FROM mastery_projections WHERE owner_id=?1 "
            f"AND class_id IN ({projection_slots}) "
            "ORDER BY concept_label COLLATE NOCASE,concept_id,level",
            owner_id,
            *class_ids,
        )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in projection_rows:
        grouped.setdefault(str(row["concept_id"]), []).append(row)
    concepts: list[dict[str, Any]] = []
    visible_concept_ids = {
        str(concept.get("id")) for concept in visible_concepts if concept.get("id")
    }
    for concept_id, catalog_entry in sorted(
        ((str(item["id"]), item) for item in visible_concepts if item.get("id")),
        key=lambda pair: (str(pair[1].get("canonical_name", "")).casefold(), pair[0]),
    ):
        rows = grouped.get(concept_id, [])
        assessed = [row for row in rows if int(row["evidence_count"]) > 0]
        if not assessed:
            concepts.append(
                {
                    "id": concept_id,
                    "label": str(catalog_entry.get("canonical_name") or "Concept")[:240],
                    "status": "unassessed",
                    "latest_demonstrated_level": None,
                    "current_level": None,
                    "estimate": {
                        "mean": None,
                        "lower_90": None,
                        "upper_90": None,
                        "probability_threshold": None,
                        "threshold": MASTERY_THRESHOLD,
                        "evidence_count": 0,
                    },
                    "evidence_sufficiency": {
                        "valid_observations": 0,
                        "independent_families": 0,
                        "effective_weight": 0.0,
                    },
                    "historical_peak": None,
                    "recent_growth": None,
                    "last_assessed_at": None,
                    "review_due": False,
                }
            )
            continue
        selected = max(assessed, key=lambda row: int(row["level"]))
        confirmed = [int(row["level"]) for row in rows if int(row["current_confirmed"])]
        concept_class_id = str(catalog_entry.get("class_id") or "")
        latest = await db.fetchrow(
            "SELECT level,correctness FROM mastery_evidence WHERE owner_id=?1 "
            "AND concept_id=?2 AND class_id=?3 AND correctness>=0.5 "
            "ORDER BY created_at DESC,id DESC LIMIT 1",
            owner_id,
            concept_id,
            concept_class_id,
        )
        evidence_rows = await db.fetch(
            "SELECT run_id,family_id,level,quality_weight FROM mastery_evidence "
            "WHERE owner_id=?1 AND concept_id=?2 AND class_id=?3 ORDER BY created_at,id",
            owner_id,
            concept_id,
            concept_class_id,
        )
        family_weight: dict[str, float] = {}
        session_weight: dict[tuple[str, int], float] = {}
        independent_observations = 0
        independent_families: set[str] = set()
        total_effective_weight = 0.0
        for evidence in evidence_rows:
            family = str(evidence["family_id"])
            level = int(evidence["level"])
            run_id = str(evidence["run_id"])
            effective = min(
                float(evidence["quality_weight"]),
                max(
                    0.0,
                    MAX_EFFECTIVE_WEIGHT_PER_FAMILY - family_weight.get(family, 0.0),
                ),
                max(
                    0.0,
                    MAX_EFFECTIVE_WEIGHT_PER_RUN_CONCEPT_LEVEL
                    - session_weight.get((run_id, level), 0.0),
                ),
            )
            family_weight[family] = family_weight.get(family, 0.0) + effective
            session_key = (run_id, level)
            session_weight[session_key] = session_weight.get(session_key, 0.0) + effective
            if effective > 0 and level == int(selected["level"]):
                independent_observations += 1
                independent_families.add(family)
                total_effective_weight += effective
        prior = await db.fetchrow(
            "SELECT mean FROM mastery_snapshots WHERE owner_id=?1 AND concept_id=?2 "
            "AND class_id=?5 AND level=?3 "
            "AND as_of<=?4 ORDER BY as_of DESC,id DESC LIMIT 1",
            owner_id,
            concept_id,
            selected["level"],
            (now - timedelta(days=30)).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            concept_class_id,
        )
        recent_growth = (
            float(selected["mean"]) - float(prior["mean"]) if prior is not None else None
        )
        concepts.append(
            {
                "id": concept_id,
                "label": str(catalog_entry.get("canonical_name") or selected["concept_label"]),
                "status": ("confirmed" if confirmed else "provisional"),
                "latest_demonstrated_level": (int(latest["level"]) if latest is not None else None),
                "current_level": max(confirmed) if confirmed else None,
                "estimate": {
                    "mean": float(selected["mean"]),
                    "lower_90": float(selected["lower_90"]),
                    "upper_90": float(selected["upper_90"]),
                    "probability_threshold": float(selected["probability_threshold"]),
                    "threshold": MASTERY_THRESHOLD,
                    "evidence_count": int(selected["evidence_count"]),
                },
                "evidence_sufficiency": {
                    "valid_observations": independent_observations,
                    "independent_families": len(independent_families),
                    "effective_weight": round(total_effective_weight, 3),
                },
                "historical_peak": max(
                    (int(row["historical_peak"]) for row in rows if row["historical_peak"]),
                    default=None,
                ),
                "recent_growth": recent_growth,
                "last_assessed_at": selected["last_assessed_at"],
                "review_due": bool(selected["review_due"]),
            }
        )
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "as_of": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "total_xp": total_xp,
        "recent_accuracy": counts,
        "concepts": concepts if visible_concept_ids else [],
    }
