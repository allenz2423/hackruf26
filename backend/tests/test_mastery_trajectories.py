"""Deterministic synthetic Gate 4 mastery trajectories (estimates, not calibration)."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.config import Settings
from app.services.database import create_database
from app.services.learning_scoring import (
    ALGORITHM_VERSION,
    CONFIRMATION_PROBABILITY,
    MASTERY_THRESHOLD,
    PRIOR_ALPHA,
    PRIOR_BETA,
    RECENCY_DECAY_PER_OBSERVATION,
    posterior,
    rebuild_projection,
    record_assessment_outcome,
    regularized_beta,
)


class MasteryTrajectoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        settings = Settings(
            _env_file=None,
            gemini_api_key="not-used",
            file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
            sqlite_path=":memory:",
            storage_dir=self.temp.name,
            lean_tool_enabled=False,
        )
        self.db = await create_database(settings)
        self.owner_id = str(uuid4())
        self.class_id = str(uuid4())
        self.material_id = str(uuid4())
        self.run_id = str(uuid4())
        self.concept_id = str(uuid4())
        self.base_time = datetime(2026, 10, 1, tzinfo=UTC)
        await self.db.execute(
            "INSERT INTO users(id,username,display_name,password_hash) "
            "VALUES (?1,'trajectory_student','Trajectory student','test')",
            self.owner_id,
        )
        await self.db.execute(
            "INSERT INTO course_classes(id,owner_id,name) VALUES (?1,?2,'Trajectory class')",
            self.class_id,
            self.owner_id,
        )
        await self.db.execute(
            "INSERT INTO study_materials(id,class_id,resource_scope,publication_state,kind,title,"
            "status,content) VALUES (?1,?2,'class_owned','published','quiz','Trajectory quiz',"
            "'ready','{}')",
            self.material_id,
            self.class_id,
        )
        await self.db.execute(
            "INSERT INTO quiz_runs(id,material_id,owner_id,operation_id,mode) "
            "VALUES (?1,?2,?3,'trajectory-run','standard')",
            self.run_id,
            self.material_id,
            self.owner_id,
        )

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.temp.cleanup()

    def timestamp(self, index: int) -> str:
        value = self.base_time + timedelta(seconds=index)
        return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    async def add_observations(
        self,
        outcomes: list[tuple[int, float, str]],
        *,
        family_prefix: str = "family",
        session_size: int | None = 1,
    ) -> None:
        session_ids: dict[int, str] = {}
        for index, (level, correctness, family_suffix) in enumerate(outcomes):
            attempt_id = str(uuid4())
            question_id = str(uuid4())
            family_id = f"{family_prefix}-{family_suffix}"
            session_index = 0 if session_size is None else index // session_size
            if session_index not in session_ids:
                run_id = self.run_id if session_index == 0 else str(uuid4())
                if session_index > 0:
                    await self.db.execute(
                        "INSERT INTO quiz_runs(id,material_id,owner_id,operation_id,mode) "
                        "VALUES (?1,?2,?3,?4,'standard')",
                        run_id,
                        self.material_id,
                        self.owner_id,
                        f"trajectory-run-{uuid4()}",
                    )
                session_ids[session_index] = run_id
            run_id = session_ids[session_index]
            await self.db.execute(
                "INSERT INTO quiz_question_attempts(id,run_id,question_id) VALUES (?1,?2,?3)",
                attempt_id,
                run_id,
                question_id,
            )
            await self.db.execute(
                "INSERT INTO mastery_evidence "
                "(id,owner_id,class_id,concept_id,concept_label,run_id,attempt_id,question_id,"
                "family_id,level,correctness,reasoning_result,grading_policy,quality_weight,"
                "algorithm_version,created_at) "
                "VALUES (?1,?2,?3,?4,'Cellular respiration',?5,?6,?7,?8,?9,?10,'none',"
                "'objective_only',1.0,?11,?12)",
                str(uuid4()),
                self.owner_id,
                self.class_id,
                self.concept_id,
                run_id,
                attempt_id,
                question_id,
                family_id,
                level,
                correctness,
                ALGORITHM_VERSION,
                self.timestamp(index),
            )

    async def project(self) -> dict[int, dict]:
        async with self.db.acquire() as connection:
            async with connection.transaction():
                await rebuild_projection(
                    connection,
                    owner_id=self.owner_id,
                    concept_id=self.concept_id,
                )
        rows = await self.db.fetch(
            "SELECT * FROM mastery_projections WHERE owner_id=?1 AND concept_id=?2 ORDER BY level",
            self.owner_id,
            self.concept_id,
        )
        return {int(row["level"]): row for row in rows}

    async def test_beginner_to_proficient_growth_uses_recent_independent_evidence(self) -> None:
        self.assertEqual(regularized_beta(0.3, 1.0, 1.0), 0.3)
        self.assertEqual((PRIOR_ALPHA, PRIOR_BETA), (1.0, 1.0))
        self.assertGreater(RECENCY_DECAY_PER_OBSERVATION, 0.0)
        self.assertLess(RECENCY_DECAY_PER_OBSERVATION, 1.0)
        outcomes = [(1, 0.0, f"early-{index}") for index in range(3)]
        outcomes.extend((1, 1.0, f"growth-{index}") for index in range(30))
        await self.add_observations(outcomes)
        rows = await self.project()
        level_one = rows[1]
        self.assertGreater(level_one["mean"], MASTERY_THRESHOLD)
        self.assertGreaterEqual(level_one["probability_threshold"], CONFIRMATION_PROBABILITY)
        self.assertEqual(level_one["historical_peak"], 1)
        self.assertGreater(level_one["upper_90"], level_one["lower_90"])

    async def test_growth_across_months_keeps_early_failures_in_dated_history(self) -> None:
        outcomes = [(1, 0.0, f"early-{index}") for index in range(3)]
        outcomes.extend((1, 1.0, f"later-{index}") for index in range(30))
        await self.add_observations(outcomes)
        rows = await self.db.fetch(
            "SELECT id FROM mastery_evidence WHERE owner_id=?1 AND concept_id=?2 "
            "ORDER BY created_at,id",
            self.owner_id,
            self.concept_id,
        )
        start = self.base_time - timedelta(days=120)
        for index, row in enumerate(rows):
            day = 0 if index < 3 else 90 + (index - 3) * 30 // 30
            await self.db.execute(
                "UPDATE mastery_evidence SET created_at=?2 WHERE id=?1",
                row["id"],
                (start + timedelta(days=day, seconds=index))
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
            )
        await self.project()
        snapshots = await self.db.fetch(
            "SELECT mean,current_level FROM mastery_snapshots WHERE owner_id=?1 "
            "AND concept_id=?2 AND level=1 ORDER BY as_of,id",
            self.owner_id,
            self.concept_id,
        )
        self.assertEqual(len(snapshots), len(outcomes))
        self.assertLess(snapshots[2]["mean"], 0.5)
        self.assertGreater(snapshots[-1]["mean"], 0.8)
        self.assertEqual(snapshots[-1]["current_level"], 1)

    async def test_lucky_guessing_does_not_confirm_from_a_few_correct_answers(self) -> None:
        await self.add_observations([(1, 1.0, f"guess-{index}") for index in range(4)])
        rows = await self.project()
        direct = posterior(rows[1]["alpha"], rows[1]["beta"])
        self.assertLess(rows[1]["probability_threshold"], CONFIRMATION_PROBABILITY)
        self.assertAlmostEqual(direct["probability_threshold"], rows[1]["probability_threshold"])
        self.assertIsNone(rows[1]["historical_peak"])

    async def test_easy_question_farming_does_not_transfer_to_unassessed_levels(self) -> None:
        await self.add_observations(
            [(1, 1.0, f"easy-{index}") for index in range(24)], session_size=None
        )
        rows = await self.project()
        self.assertLess(rows[1]["probability_threshold"], CONFIRMATION_PROBABILITY)
        self.assertLessEqual(rows[1]["alpha"], PRIOR_ALPHA + 2.0)
        for level in (2, 3, 4):
            self.assertEqual(rows[level]["evidence_count"], 0)
            self.assertEqual(rows[level]["alpha"], PRIOR_ALPHA)
            self.assertEqual(rows[level]["beta"], PRIOR_BETA)

    async def test_consistent_proficiency_confirms_the_assessed_level(self) -> None:
        await self.add_observations([(3, 1.0, f"consistent-{index}") for index in range(16)])
        rows = await self.project()
        self.assertGreaterEqual(rows[3]["probability_threshold"], CONFIRMATION_PROBABILITY)
        self.assertEqual(rows[3]["historical_peak"], 3)
        self.assertEqual(rows[2]["evidence_count"], 0)

    async def test_one_off_failure_does_not_erase_established_mastery(self) -> None:
        outcomes = [(2, 1.0, f"strong-{index}") for index in range(16)]
        outcomes.append((2, 0.0, "single-miss"))
        await self.add_observations(outcomes)
        rows = await self.project()
        self.assertGreaterEqual(rows[2]["historical_peak"], 2)
        self.assertEqual(
            rows[2]["current_confirmed"],
            1,
            "one recent failure must not demote established mastery",
        )

    async def test_three_failures_retain_confirmed_state_while_posterior_mean_is_over_half(
        self,
    ) -> None:
        outcomes = [(1, 1.0, f"strong-{index}") for index in range(100)]
        outcomes.extend((1, 0.0, f"miss-{index}") for index in range(3))
        await self.add_observations(outcomes)
        rows = await self.project()
        self.assertGreaterEqual(rows[1]["historical_peak"], 1)
        self.assertGreater(rows[1]["mean"], 0.5)
        self.assertEqual(rows[1]["current_confirmed"], 1)

    async def test_sustained_independent_regression_lowers_current_confidence(self) -> None:
        outcomes = [(3, 1.0, f"strong-{index}") for index in range(16)]
        outcomes.extend((3, 0.0, f"regression-{index}") for index in range(8))
        await self.add_observations(outcomes)
        rows = await self.project()
        self.assertGreaterEqual(rows[3]["historical_peak"], 3)
        self.assertEqual(rows[3]["current_confirmed"], 0)
        self.assertLess(rows[3]["probability_threshold"], CONFIRMATION_PROBABILITY)
        self.assertLess(rows[3]["mean"], MASTERY_THRESHOLD)

    async def test_repeated_variants_of_one_family_are_not_independent_evidence(self) -> None:
        await self.add_observations(
            [(2, 1.0, "same-family")] + [(2, 1.0, f"independent-{index}") for index in range(15)],
            family_prefix="variant",
        )
        before = (await self.project())[2]
        self.assertGreaterEqual(before["current_confirmed"], 1)
        await self.add_observations(
            [(2, 1.0, "same-family") for _ in range(24)],
            family_prefix="variant",
        )
        after = (await self.project())[2]
        self.assertEqual(after["evidence_count"], 16)
        self.assertLessEqual(after["alpha"], PRIOR_ALPHA + 16.0)
        self.assertAlmostEqual(
            after["probability_threshold"],
            before["probability_threshold"],
            places=10,
            msg="non-independent variants must not inflate or age valid evidence",
        )
        self.assertEqual(after["current_confirmed"], 1)

    async def test_question_family_cap_applies_across_demand_levels(self) -> None:
        await self.add_observations(
            [(1, 1.0, "shared-objective"), (4, 1.0, "shared-objective")],
            family_prefix="same",
        )
        rows = await self.project()
        self.assertEqual(rows[1]["evidence_count"], 1)
        self.assertEqual(rows[4]["evidence_count"], 0)

    async def test_grading_outage_creates_no_mastery_or_xp_evidence(self) -> None:
        attempt_id = str(uuid4())
        question_id = str(uuid4())
        await self.db.execute(
            "INSERT INTO quiz_question_attempts(id,run_id,question_id) VALUES (?1,?2,?3)",
            attempt_id,
            self.run_id,
            question_id,
        )
        private_snapshot = {
            "level": 2,
            "concept_ids": [self.concept_id],
            "concept_labels": {self.concept_id: "Cellular respiration"},
        }
        async with self.db.acquire() as connection:
            async with connection.transaction():
                await record_assessment_outcome(
                    connection,
                    run={
                        "id": self.run_id,
                        "owner_id": self.owner_id,
                        "material_id": self.material_id,
                        "mode": "standard",
                    },
                    attempt={"id": attempt_id},
                    question_row={
                        "id": question_id,
                        "family_id": "grading-outage-family",
                        "private_snapshot": json.dumps(private_snapshot),
                        "public_snapshot": "{}",
                        "class_id": self.class_id,
                        "evidence_eligible": 1,
                    },
                    status="pending",
                    correctness=None,
                    reasoning_status="failed",
                )
        self.assertEqual(await self.db.fetchval("SELECT count(*) FROM mastery_evidence"), 0)
        self.assertEqual(await self.db.fetchval("SELECT count(*) FROM learning_xp_events"), 0)
        self.assertEqual(await self.db.fetchval("SELECT count(*) FROM assessment_outcomes"), 1)
