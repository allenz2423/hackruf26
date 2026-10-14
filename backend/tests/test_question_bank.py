"""Question-bank source validation, privacy, generation, and duplicate-family tests."""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.class_library import _process_upload_job, _reprocess_upload_job
from app.api.errors import ApiError
from app.api.schemas import (
    QuestionBankAIItem,
    QuestionBankAIReviewItem,
    QuestionBankGenerationRequest,
)
from app.config import Settings
from app.main import create_app
from app.services.auth import SESSION_COOKIE
from app.services.learning_calibration import (
    enqueue_learning_calibration_refresh,
    enqueue_question_bank_replenishment,
    process_learning_calibration_jobs,
    process_question_bank_replenishment_jobs,
    recover_learning_calibration_jobs,
    refresh_calibration_flags,
)
from app.services.question_bank import (
    COGNITIVE_LEVELS,
    _validate_ai_items,
    build_coverage_plan,
)

ORIGIN = "http://localhost:5173"


class FakeQuestionBankService:
    def __init__(self) -> None:
        self.generation_contexts: list[dict] = []
        self.review_contexts: list[dict] = []
        self.block_review: tuple[threading.Event, threading.Event] | None = None
        self.invalid_source = False

    def process_text(self, text: str, _kind: str) -> dict:
        return {
            "extracted_text": text,
            "summary": "summary",
            "key_topics": [],
            "transcript_segments": [],
            "duration_seconds": None,
            "syllabus_outline": None,
        }

    def generate_question_bank(self, context: dict):
        from app.api.schemas import QuestionBankAIGeneration

        self.generation_contexts.append(context)
        questions = []
        independent_targets = [
            "cellular respiration ATP output in mitochondria",
            "electron transport chain location in inner membrane",
            "proton gradient across the inner mitochondrial membrane",
            "matrix enzymes and citric acid cycle reactions",
            "mitochondrial DNA replication before cell division",
            "organelle role in powering active transport",
            "cristae surface area for oxidative phosphorylation",
            "oxygen use and mitochondrial ATP synthesis",
        ]
        for index, slot in enumerate(context["assignments"]):
            source_id = slot["source_chunk_revision_ids"][0]
            if self.invalid_source:
                source_id = str(uuid4())
            level = slot["level"]
            questions.append(
                QuestionBankAIItem(
                    assignment_id=slot["assignment_id"],
                    prompt=(
                        f"Which statement describes mitochondria at demand level {level} "
                        f"for {slot['assignment_id']}?"
                    ),
                    kind=slot["kind"],
                    choices=[
                        {"id": "a", "text": "They produce ATP through cellular respiration."},
                        {"id": "b", "text": "They store the cell's genetic code."},
                    ],
                    answer="a",
                    accepted_answers=[],
                    explanation="Mitochondria support cellular respiration and ATP production.",
                    rubric="Select the supported function.",
                    level=level,
                    language=context["requested_language"],
                    assessment_target=independent_targets[index % len(independent_targets)],
                    concept_ids=[slot["concept_id"]],
                    source_chunk_revision_ids=[source_id],
                )
            )
        return QuestionBankAIGeneration(questions=questions)

    def validate_question_bank(self, context: dict):
        from app.api.schemas import QuestionBankAIReview

        self.review_contexts.append(context)
        if self.block_review is not None:
            started, release = self.block_review
            started.set()
            if not release.wait(5):
                raise RuntimeError("provider barrier timed out")
        return QuestionBankAIReview(
            reviews=[
                QuestionBankAIReviewItem(
                    index=index,
                    source_supported=True,
                    concept_aligned=True,
                    answer_correct=True,
                    distractors_valid=True,
                    unambiguous=True,
                    level_aligned=True,
                    language_valid=True,
                    leakage_free=True,
                    duplicate_family=False,
                    reason="Each candidate has distinct supported demand.",
                )
                for index, _ in enumerate(context["candidates"])
            ]
        )


class QuestionBankTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = Settings(
            _env_file=None,
            gemini_api_key="test-key-not-used",
            file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
            sqlite_path=":memory:",
            storage_dir=self.temp.name,
            signup_mode="open",
            lean_tool_enabled=False,
        )
        self.client = TestClient(create_app(settings_override=settings), headers={"origin": ORIGIN})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.db = self.client.app.state.database
        self.fake = FakeQuestionBankService()
        self.client.app.state.gemini_content_service = self.fake
        self.tokens: dict[str, str] = {}
        self.users: dict[str, dict] = {}
        self.owner = self.add_user("bank_owner")
        created = self.call("post", "/api/classes", "bank_owner", json={"name": "Bank Class"})
        self.assertEqual(created.status_code, 201, created.text)
        self.class_id = created.json()["id"]
        self.note_id, self.chunk_id = self.seed_source()
        self.concept_id = self.create_validated_concept()

    def add_user(self, username: str) -> dict:
        response = self.client.post(
            "/api/auth/signup", json={"username": username, "password": "bank-test-pass"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.tokens[username] = response.cookies.get(SESSION_COOKIE)
        self.users[username] = response.json()["user"]
        return self.users[username]

    def call(self, method: str, path: str, username: str, **kwargs):
        return getattr(self.client, method)(
            path, cookies={SESSION_COOKIE: self.tokens[username]}, **kwargs
        )

    def seed_source(self) -> tuple[str, str]:
        note_id = str(uuid4())
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO course_notes "
            "(id,class_id,created_by,publication_state,title,kind,source_type,"
            "extracted_text,status,processing_progress) VALUES "
            "(?1,?2,?3,'published','Cell Biology','document','pasted_text',"
            "'Mitochondria support cellular respiration and produce ATP for the cell.',"
            "'processing',0)",
            note_id,
            self.class_id,
            self.owner["id"],
        )
        with patch("app.api.class_library.create_default_document", new_callable=AsyncMock):
            self.client.portal.call(_process_upload_job, self.client.app, UUID(note_id))
        chunk_id = self.client.portal.call(
            self.db.fetchval,
            "SELECT id FROM source_chunk_revisions WHERE revision_id="
            "(SELECT revision_id FROM source_index_heads WHERE note_id=?1)",
            note_id,
        )
        self.assertIsNotNone(chunk_id)
        return note_id, str(chunk_id)

    def create_validated_concept(self) -> str:
        created = self.call(
            "post",
            f"/api/classes/{self.class_id}/concepts",
            "bank_owner",
            json={
                "canonical_name": "Mitochondria",
                "description": "Organelles involved in cellular respiration.",
                "source_chunk_revision_ids": [self.chunk_id],
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        approved = self.call(
            "post",
            f"/api/classes/{self.class_id}/concepts/{created.json()['id']}/decision",
            "bank_owner",
            json={"decision": "approve", "expected_version": created.json()["version"]},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        return approved.json()["id"]

    def add_readonly_member(self, username: str) -> None:
        self.add_user(username)
        invitation = self.call(
            "post",
            f"/api/classes/{self.class_id}/invitations",
            "bank_owner",
            json={"capabilities": ["read"], "max_uses": 1, "expires_in_days": 7},
        )
        self.assertEqual(invitation.status_code, 201, invitation.text)
        joined = self.call(
            "post",
            "/api/class-invitations/redeem",
            username,
            json={"code": invitation.json()["code"]},
        )
        self.assertEqual(joined.status_code, 200, joined.text)

    def add_create_member(self, username: str) -> None:
        self.add_user(username)
        invitation = self.call(
            "post",
            f"/api/classes/{self.class_id}/invitations",
            "bank_owner",
            json={"capabilities": ["read", "create"], "max_uses": 1, "expires_in_days": 7},
        )
        self.assertEqual(invitation.status_code, 201, invitation.text)
        joined = self.call(
            "post",
            "/api/class-invitations/redeem",
            username,
            json={"code": invitation.json()["code"]},
        )
        self.assertEqual(joined.status_code, 200, joined.text)

    def test_coverage_plan_defines_all_four_cognitive_demands(self) -> None:
        payload = QuestionBankGenerationRequest(operation_id=uuid4(), count=4)
        plan = build_coverage_plan(
            [{"id": "concept", "canonical_name": "Concept", "description": "Description"}],
            payload,
        )
        self.assertEqual([slot["level"] for slot in plan], [1, 2, 3, 4])
        self.assertEqual(
            [slot["level_description"] for slot in plan], list(COGNITIVE_LEVELS.values())
        )

    def test_bounded_replenishment_adds_bank_items_without_creating_a_student_material(
        self,
    ) -> None:
        before = self.client.portal.call(
            self.db.fetchval,
            "SELECT count(*) FROM study_materials WHERE class_id=?1",
            self.class_id,
        )
        queued = self.client.portal.call(enqueue_question_bank_replenishment, self.db, 4, 10)
        self.assertEqual(queued, 4)
        processed = self.client.portal.call(
            process_question_bank_replenishment_jobs, self.client.app, 1
        )
        jobs = self.client.portal.call(
            self.db.fetch,
            "SELECT status,error_code,attempts FROM question_bank_replenishment_jobs "
            "WHERE class_id=?1",
            self.class_id,
        )
        self.assertEqual(processed, 1, jobs)
        after = self.client.portal.call(
            self.db.fetchval,
            "SELECT count(*) FROM study_materials WHERE class_id=?1",
            self.class_id,
        )
        self.assertEqual(after, before)
        bank_counts = self.client.portal.call(
            self.db.fetch,
            "SELECT i.level,count(*) AS count FROM class_question_bank_items i "
            "JOIN class_question_bank_concepts c ON c.item_id=i.id AND c.class_id=i.class_id "
            "WHERE i.class_id=?1 AND c.concept_id=?2 AND c.relation='primary' "
            "GROUP BY i.level ORDER BY i.level",
            self.class_id,
            self.concept_id,
        )
        self.assertEqual([row["count"] for row in bank_counts], [2, 2, 2, 2])
        self.assertEqual(len(self.fake.generation_contexts), 1)
        self.assertEqual(len(self.fake.generation_contexts[0]["assignments"]), 8)

    def test_replenishment_supersedes_stale_source_digest_without_calling_provider(self) -> None:
        self.client.portal.call(enqueue_question_bank_replenishment, self.db, 1, 10)
        self.client.portal.call(
            self.db.execute,
            "UPDATE question_bank_replenishment_jobs SET source_digest='stale' WHERE class_id=?1",
            self.class_id,
        )
        processed = self.client.portal.call(
            process_question_bank_replenishment_jobs, self.client.app, 1
        )
        self.assertEqual(processed, 0)
        state = self.client.portal.call(
            self.db.fetchval,
            "SELECT status FROM question_bank_replenishment_jobs WHERE class_id=?1 "
            "ORDER BY created_at LIMIT 1",
            self.class_id,
        )
        self.assertEqual(state, "superseded")
        self.assertEqual(self.fake.generation_contexts, [])

    def test_running_replenishment_jobs_recover_with_bounded_attempts(self) -> None:
        self.client.portal.call(enqueue_question_bank_replenishment, self.db, 2, 10)
        job_ids = self.client.portal.call(
            self.db.fetch,
            "SELECT id FROM question_bank_replenishment_jobs WHERE class_id=?1 "
            "ORDER BY created_at,id",
            self.class_id,
        )
        self.client.portal.call(
            self.db.execute,
            "UPDATE question_bank_replenishment_jobs SET status='running',attempts=1 WHERE id=?1",
            job_ids[0]["id"],
        )
        self.client.portal.call(
            self.db.execute,
            "UPDATE question_bank_replenishment_jobs SET status='running',"
            "attempts=max_attempts WHERE id=?1",
            job_ids[1]["id"],
        )
        self.client.portal.call(recover_learning_calibration_jobs, self.db)
        states = self.client.portal.call(
            self.db.fetch,
            "SELECT status,error_code FROM question_bank_replenishment_jobs "
            "WHERE class_id=?1 ORDER BY created_at",
            self.class_id,
        )
        self.assertIn({"status": "queued", "error_code": "worker_interrupted"}, states)
        self.assertIn({"status": "failed", "error_code": "retry_limit_reached"}, states)

    def test_calibration_jobs_are_idempotent_retryable_and_recoverable(self) -> None:
        first_window = datetime(2026, 10, 14, 12, 7, tzinfo=UTC)
        self.assertTrue(
            self.client.portal.call(enqueue_learning_calibration_refresh, self.db, first_window)
        )
        self.assertFalse(
            self.client.portal.call(
                enqueue_learning_calibration_refresh,
                self.db,
                datetime(2026, 10, 14, 12, 13, tzinfo=UTC),
            )
        )
        self.assertEqual(self.client.portal.call(process_learning_calibration_jobs, self.db), 1)
        completed = self.client.portal.call(
            self.db.fetchrow,
            "SELECT status,attempts,error_code FROM learning_calibration_jobs "
            "WHERE schedule_key='2026-10-14T12:00:00Z'",
        )
        self.assertEqual(completed, {"status": "complete", "attempts": 1, "error_code": None})

        self.assertTrue(
            self.client.portal.call(
                enqueue_learning_calibration_refresh,
                self.db,
                datetime(2026, 10, 14, 12, 15, tzinfo=UTC),
            )
        )
        second_id = self.client.portal.call(
            self.db.fetchval,
            "SELECT id FROM learning_calibration_jobs WHERE schedule_key='2026-10-14T12:15:00Z'",
        )
        self.client.portal.call(
            self.db.execute,
            "UPDATE learning_calibration_jobs SET max_attempts=1 WHERE id=?1",
            second_id,
        )
        with patch(
            "app.services.learning_calibration.refresh_calibration_flags",
            new_callable=AsyncMock,
            side_effect=RuntimeError("simulated database failure"),
        ):
            self.assertEqual(self.client.portal.call(process_learning_calibration_jobs, self.db), 0)
        failed = self.client.portal.call(
            self.db.fetchrow,
            "SELECT status,attempts,error_code FROM learning_calibration_jobs WHERE id=?1",
            second_id,
        )
        self.assertEqual(
            failed, {"status": "failed", "attempts": 1, "error_code": "refresh_failed"}
        )

        self.assertTrue(
            self.client.portal.call(
                enqueue_learning_calibration_refresh,
                self.db,
                datetime(2026, 10, 14, 12, 30, tzinfo=UTC),
            )
        )
        third_id = self.client.portal.call(
            self.db.fetchval,
            "SELECT id FROM learning_calibration_jobs WHERE schedule_key='2026-10-14T12:30:00Z'",
        )
        self.client.portal.call(
            self.db.execute,
            "UPDATE learning_calibration_jobs SET status='running',attempts=1 WHERE id=?1",
            third_id,
        )
        self.client.portal.call(recover_learning_calibration_jobs, self.db)
        recovered = self.client.portal.call(
            self.db.fetchrow,
            "SELECT status,error_code FROM learning_calibration_jobs WHERE id=?1",
            third_id,
        )
        self.assertEqual(recovered, {"status": "queued", "error_code": "worker_interrupted"})

    def test_calibration_status_is_admin_only_and_contains_no_student_identity(self) -> None:
        denied = self.call("get", "/api/admin/learning-calibration", "bank_owner")
        self.assertEqual(denied.status_code, 403, denied.text)
        self.client.portal.call(
            self.db.execute,
            "UPDATE users SET is_admin=1 WHERE id=?1",
            self.owner["id"],
        )
        allowed = self.call("get", "/api/admin/learning-calibration", "bank_owner")
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertIn("calibration_jobs", allowed.json())
        self.assertIn("latest_calibration_job", allowed.json())
        self.assertEqual(allowed.headers["cache-control"], "private, no-store")
        self.assertNotIn(str(self.owner["id"]), json.dumps(allowed.json()))

    def test_item_calibration_flag_requires_thirty_responses_and_stores_only_aggregate_data(
        self,
    ) -> None:
        generated = self.call(
            "post",
            f"/api/classes/{self.class_id}/question-bank/generate",
            "bank_owner",
            json={"operation_id": str(uuid4()), "concept_ids": [self.concept_id], "count": 4},
        )
        self.assertEqual(generated.status_code, 201, generated.text)
        item = self.client.portal.call(
            self.db.fetchrow,
            "SELECT id,family_id,level,generation_version FROM class_question_bank_items "
            "WHERE class_id=?1 AND level=1 ORDER BY created_at,id LIMIT 1",
            self.class_id,
        )

        async def seed_responses(count: int) -> None:
            async with self.db.acquire() as conn:
                async with conn.transaction():
                    for _ in range(count):
                        run_id, question_id, attempt_id = str(uuid4()), str(uuid4()), str(uuid4())
                        await conn.execute(
                            "INSERT INTO quiz_runs "
                            "(id,material_id,owner_id,operation_id,mode,snapshot_version) "
                            "VALUES (?1,?2,?3,?4,'standard',1)",
                            run_id,
                            generated.json()["material_id"],
                            self.owner["id"],
                            str(uuid4()),
                        )
                        await conn.execute(
                            "INSERT INTO quiz_run_questions "
                            "(id,run_id,material_id,bank_item_id,ordinal,family_id,public_snapshot,"
                            "private_snapshot,option_mapping,snapshot_sha256) "
                            "VALUES (?1,?2,?3,?4,0,?5,?6,?7,'{}',?8)",
                            question_id,
                            run_id,
                            generated.json()["material_id"],
                            item["id"],
                            item["family_id"],
                            json.dumps({"id": question_id, "prompt": "Question", "choices": []}),
                            json.dumps({"level": 1}),
                            hashlib.sha256(question_id.encode()).hexdigest(),
                        )
                        await conn.execute(
                            "INSERT INTO quiz_question_attempts(id,run_id,question_id) "
                            "VALUES (?1,?2,?3)",
                            attempt_id,
                            run_id,
                            question_id,
                        )
                        await conn.execute(
                            "INSERT INTO question_calibration_events "
                            "(id,class_id,bank_item_id,run_id,question_id,attempt_id,concept_id,"
                            "family_id,level,event_type,ability_estimate,correctness,reasoning_result,"
                            "source_revision_digest,generation_version) "
                            "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,1,'response',0.5,1,'none',?9,?10)",
                            str(uuid4()),
                            self.class_id,
                            item["id"],
                            run_id,
                            question_id,
                            attempt_id,
                            self.concept_id,
                            item["family_id"],
                            hashlib.sha256(self.chunk_id.encode()).hexdigest(),
                            item["generation_version"],
                        )

        self.client.portal.call(seed_responses, 29)
        self.client.portal.call(refresh_calibration_flags, self.db)
        flag_count = self.client.portal.call(
            self.db.fetchval,
            "SELECT count(*) FROM question_calibration_flags WHERE bank_item_id=?1",
            item["id"],
        )
        self.assertEqual(flag_count, 0)
        self.client.portal.call(seed_responses, 1)
        summary = self.client.portal.call(refresh_calibration_flags, self.db)
        self.assertEqual(summary["question_flags"], 1)
        flag = self.client.portal.call(
            self.db.fetchrow,
            "SELECT response_count,adjusted_residual FROM question_calibration_flags "
            "WHERE bank_item_id=?1",
            item["id"],
        )
        self.assertEqual(flag, {"response_count": 30, "adjusted_residual": 0.5})
        columns = self.client.portal.call(
            self.db.fetch,
            "PRAGMA table_info(question_calibration_flags)",
        )
        self.assertNotIn("owner_id", {column["name"] for column in columns})

    def test_deterministic_paraphrase_detection_rejects_same_concept_level(self) -> None:
        source = "chunk-a"
        plan = [
            {
                "assignment_id": "slot-0",
                "concept_id": "concept-a",
                "level": 2,
                "kind": "multiple_choice",
            },
            {
                "assignment_id": "slot-1",
                "concept_id": "concept-a",
                "level": 2,
                "kind": "multiple_choice",
            },
        ]
        generated = [
            self._candidate("slot-0", "Explain how cellular respiration produces ATP", source),
            self._candidate(
                "slot-1", "Explain how ATP is produced through cellular respiration", source
            ),
        ]
        reviews = [self._review(0), self._review(1)]
        with self.assertRaises(ApiError) as raised:
            _validate_ai_items(
                generated,
                reviews,
                plan=plan,
                source_context={source: {"concept_id": "concept-a"}},
                language="en",
            )
        self.assertEqual(raised.exception.code, "duplicate_question_family")

    def test_model_review_rejects_prior_validated_paraphrased_family(self) -> None:
        source = "chunk-a"
        item = self._candidate(
            "slot-0", "Describe the job of mitochondria in ATP production", source
        )
        plan = [
            {
                "assignment_id": "slot-0",
                "concept_id": "concept-a",
                "level": 2,
                "kind": "multiple_choice",
            }
        ]
        review = self._review(0, duplicate=True)
        with self.assertRaises(ApiError) as raised:
            _validate_ai_items(
                [item],
                [review],
                plan=plan,
                source_context={source: {"concept_id": "concept-a"}},
                language="en",
                prior_items=[
                    {
                        "concept_id": "concept-a",
                        "level": 2,
                        "prompt": "How do mitochondria make ATP?",
                        "assessment_target": "Explain ATP production by mitochondria",
                    }
                ],
            )
        self.assertEqual(raised.exception.code, "question_validation_failed")

    def test_deterministic_prior_family_match_rejects_paraphrase(self) -> None:
        source = "chunk-a"
        item = self._candidate("slot-0", "Explain how cellular respiration produces ATP", source)
        plan = [
            {
                "assignment_id": "slot-0",
                "concept_id": "concept-a",
                "level": 2,
                "kind": "multiple_choice",
            }
        ]
        with self.assertRaises(ApiError) as raised:
            _validate_ai_items(
                [item],
                [self._review(0)],
                plan=plan,
                source_context={source: {"concept_id": "concept-a"}},
                language="en",
                prior_items=[
                    {
                        "concept_id": "concept-a",
                        "level": 2,
                        "prompt": "Explain cellular respiration produced ATP",
                        "assessment_target": "Explain cellular respiration producing ATP",
                    }
                ],
            )
        self.assertEqual(raised.exception.code, "duplicate_question_family")

    def test_candidate_cannot_invent_source_chunk_revision(self) -> None:
        candidate = self._candidate("slot-0", "Explain ATP production in respiration", "forged")
        plan = [
            {
                "assignment_id": "slot-0",
                "concept_id": "concept-a",
                "level": 2,
                "kind": "multiple_choice",
            }
        ]
        with self.assertRaises(ApiError) as raised:
            _validate_ai_items(
                [candidate],
                [self._review(0)],
                plan=plan,
                source_context={"authorized": {"concept_id": "concept-a"}},
                language="en",
            )
        self.assertEqual(raised.exception.code, "question_validation_failed")

    def test_read_only_member_cannot_generate_shared_question_bank_items(self) -> None:
        self.add_readonly_member("bank_reader")
        response = self.call(
            "post",
            f"/api/classes/{self.class_id}/question-bank/generate",
            "bank_reader",
            json={"operation_id": str(uuid4()), "concept_ids": [self.concept_id], "count": 4},
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.fake.generation_contexts, [])

    def test_generation_is_grounded_and_safe_response_is_private_metadata(self) -> None:
        operation_id = str(uuid4())
        response = self.call(
            "post",
            f"/api/classes/{self.class_id}/question-bank/generate",
            "bank_owner",
            json={"operation_id": operation_id, "concept_ids": [self.concept_id], "count": 4},
        )
        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertEqual(result["publication_state"], "draft")
        self.assertEqual(result["created_bank_item_count"], 4)
        self.assertNotIn("answer", result)
        self.assertNotIn("questions", result)
        self.assertEqual(len(self.fake.generation_contexts[0]["source_chunks"]), 1)
        self.assertIn("level_description", self.fake.generation_contexts[0]["assignments"][0])
        self.assertTrue(self.fake.review_contexts[0]["prior_validated_items"] == [])
        material = self.call("get", f"/api/study-materials/{result['material_id']}", "bank_owner")
        self.assertEqual(material.status_code, 200, material.text)
        public = material.json().get("content", {})
        self.assertNotIn("answer", str(public).casefold())
        self.assertNotIn("choices", public.get("questions", [{}])[0])

        run_id = str(uuid4())
        run_path = f"/api/study-materials/{result['material_id']}/runs"
        started = self.call(
            "post", run_path, "bank_owner", json={"operation_id": run_id, "mode": "standard"}
        )
        self.assertEqual(started.status_code, 201, started.text)
        self.assertEqual(len(started.json()["questions"]), 4)
        self.assertNotIn("answer", str(started.json()).casefold())
        self.assertEqual(started.json()["questions"][0]["concepts"], [{"label": "Mitochondria"}])
        self.client.portal.call(
            self.db.execute,
            "UPDATE course_notes SET source_index_status='processing' WHERE id=?1",
            self.note_id,
        )
        # A retry of the same start operation recovers the already pinned run;
        # source reprocessing blocks only a genuinely new run.
        replay = self.call(
            "post", run_path, "bank_owner", json={"operation_id": run_id, "mode": "standard"}
        )
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.json()["run_id"], started.json()["run_id"])
        blocked = self.call(
            "post",
            run_path,
            "bank_owner",
            json={"operation_id": str(uuid4()), "mode": "standard"},
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)

    def test_active_run_grades_pinned_question_after_source_edit_and_revocation_denies_access(
        self,
    ) -> None:
        self.add_readonly_member("pinned_reader")
        generated = self.call(
            "post",
            f"/api/classes/{self.class_id}/question-bank/generate",
            "bank_owner",
            json={"operation_id": str(uuid4()), "concept_ids": [self.concept_id], "count": 4},
        )
        self.assertEqual(generated.status_code, 201, generated.text)
        material_id = generated.json()["material_id"]
        publish = self.call(
            "patch",
            f"/api/resources/study_materials/{material_id}/publication",
            "bank_owner",
            json={"state": "published"},
        )
        self.assertEqual(publish.status_code, 200, publish.text)
        operation_id = str(uuid4())
        run_path = f"/api/study-materials/{material_id}/runs"
        started = self.call(
            "post",
            run_path,
            "pinned_reader",
            json={"operation_id": operation_id, "mode": "standard"},
        )
        self.assertEqual(started.status_code, 201, started.text)
        pinned_prompts = [item["prompt"] for item in started.json()["questions"]]
        answers = {
            question["id"]: next(
                choice["id"]
                for choice in question["choices"]
                if choice["text"] == "They produce ATP through cellular respiration."
            )
            for question in started.json()["questions"]
        }

        self.client.portal.call(
            self.db.execute,
            "UPDATE course_notes SET extracted_text=extracted_text || ' edited',"
            "status='processing' "
            "WHERE id=?1",
            self.note_id,
        )
        processing_revision = self.client.portal.call(
            self.db.fetchval,
            "SELECT processing_revision FROM course_notes WHERE id=?1",
            self.note_id,
        )
        with patch("app.api.class_library.create_default_document", new_callable=AsyncMock):
            self.client.portal.call(
                _reprocess_upload_job,
                self.client.app,
                UUID(self.note_id),
                True,
                processing_revision,
            )
        refreshed = self.call("get", f"{run_path}/{started.json()['run_id']}", "pinned_reader")
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertEqual([item["prompt"] for item in refreshed.json()["questions"]], pinned_prompts)
        submitted = self.call(
            "post",
            f"/api/study-materials/{material_id}/attempts",
            "pinned_reader",
            json={
                "run_id": started.json()["run_id"],
                "operation_id": str(uuid4()),
                "answers": answers,
            },
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertEqual(submitted.json()["score_percent"], 100)

        removed = self.call(
            "delete",
            f"/api/classes/{self.class_id}/members/{self.users['pinned_reader']['id']}",
            "bank_owner",
        )
        self.assertEqual(removed.status_code, 204, removed.text)
        denied = self.call("get", f"{run_path}/{started.json()['run_id']}", "pinned_reader")
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_unchanged_source_reprocessing_keeps_bank_material_current_for_new_runs(self) -> None:
        generated = self.call(
            "post",
            f"/api/classes/{self.class_id}/question-bank/generate",
            "bank_owner",
            json={"operation_id": str(uuid4()), "concept_ids": [self.concept_id], "count": 4},
        )
        self.assertEqual(generated.status_code, 201, generated.text)
        material_id = generated.json()["material_id"]
        self.client.portal.call(
            self.db.execute,
            "UPDATE course_notes SET status='processing' WHERE id=?1",
            self.note_id,
        )
        processing_revision = self.client.portal.call(
            self.db.fetchval,
            "SELECT processing_revision FROM course_notes WHERE id=?1",
            self.note_id,
        )
        with patch("app.api.class_library.create_default_document", new_callable=AsyncMock):
            self.client.portal.call(
                _reprocess_upload_job,
                self.client.app,
                UUID(self.note_id),
                True,
                processing_revision,
            )

        run = self.call(
            "post",
            f"/api/study-materials/{material_id}/runs",
            "bank_owner",
            json={"operation_id": str(uuid4()), "mode": "standard"},
        )
        self.assertEqual(run.status_code, 201, run.text)

    def test_personal_quiz_uses_strict_source_validation_without_shared_bank_rows(self) -> None:
        response = self.call(
            "post",
            f"/api/classes/{self.class_id}/study-materials",
            "bank_owner",
            json={
                "kind": "quiz",
                "scope": {"kind": "uploads", "upload_ids": [self.note_id]},
                "options": {"count": 4, "difficulty": "mixed"},
                "destination": "personal",
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        material_id = response.json()["id"]
        material = self.client.portal.call(
            self.db.fetchrow,
            "SELECT status,assessment_validation_state,assessment_validation_version,content "
            "FROM study_materials WHERE id=?1",
            material_id,
        )
        self.assertEqual(material["status"], "ready")
        self.assertEqual(material["assessment_validation_state"], "validated")
        questions = json.loads(material["content"])["questions"]
        self.assertEqual(len(questions), 4)
        self.assertTrue(all(question["source_chunk_revision_ids"] for question in questions))
        self.assertEqual(
            self.client.portal.call(
                self.db.fetchval,
                "SELECT count(*) FROM class_question_bank_items WHERE class_id=?1",
                self.class_id,
            ),
            0,
        )
        run = self.call(
            "post",
            f"/api/study-materials/{material_id}/runs",
            "bank_owner",
            json={"operation_id": str(uuid4()), "mode": "standard"},
        )
        self.assertEqual(run.status_code, 201, run.text)
        eligibility = self.client.portal.call(
            self.db.fetchval,
            "SELECT evidence_eligible FROM quiz_run_questions WHERE run_id=?1",
            run.json()["run_id"],
        )
        self.assertEqual(eligibility, 1)

    def test_personal_quiz_rejects_unsupported_provider_source_output(self) -> None:
        self.fake.invalid_source = True
        response = self.call(
            "post",
            f"/api/classes/{self.class_id}/study-materials",
            "bank_owner",
            json={
                "kind": "quiz",
                "scope": {"kind": "uploads", "upload_ids": [self.note_id]},
                "options": {"count": 4},
                "destination": "personal",
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        state = self.client.portal.call(
            self.db.fetchrow,
            "SELECT status,assessment_validation_state FROM study_materials WHERE id=?1",
            response.json()["id"],
        )
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["assessment_validation_state"], "legacy_unvalidated")

    def test_personal_quiz_generation_replays_by_owner_class_and_operation(self) -> None:
        operation_id = str(uuid4())
        body = {
            "operation_id": operation_id,
            "kind": "quiz",
            "scope": {"kind": "uploads", "upload_ids": [self.note_id]},
            "options": {"count": 4},
            "destination": "personal",
        }
        path = f"/api/classes/{self.class_id}/study-materials"
        first = self.call("post", path, "bank_owner", json=body)
        self.assertEqual(first.status_code, 202, first.text)
        replay = self.call("post", path, "bank_owner", json=body)
        self.assertEqual(replay.status_code, 202, replay.text)
        self.assertEqual(replay.json()["id"], first.json()["id"])
        self.assertEqual(
            self.client.portal.call(
                self.db.fetchval,
                "SELECT count(*) FROM study_materials WHERE owner_id=?1 "
                "AND origin_class_id=?2 AND resource_scope='personal' AND kind='quiz'",
                self.owner["id"],
                self.class_id,
            ),
            1,
        )
        changed = {**body, "options": {"count": 5}}
        conflict = self.call("post", path, "bank_owner", json=changed)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["code"], "idempotency_conflict")

    def test_private_quiz_provider_timeout_keeps_material_unvalidated(self) -> None:
        started = threading.Event()
        release = threading.Event()
        self.fake.block_review = (started, release)
        try:
            with patch(
                "app.api.class_library.PRIVATE_ASSESSMENT_GENERATION_DEADLINE_SECONDS",
                0.03,
            ):
                response = self.call(
                    "post",
                    f"/api/classes/{self.class_id}/study-materials",
                    "bank_owner",
                    json={
                        "operation_id": str(uuid4()),
                        "kind": "quiz",
                        "scope": {"kind": "uploads", "upload_ids": [self.note_id]},
                        "options": {"count": 4},
                        "destination": "personal",
                    },
                )
            self.assertEqual(response.status_code, 202, response.text)
            self.assertTrue(started.wait(1), "the controlled provider did not start")
            state = self.client.portal.call(
                self.db.fetchrow,
                "SELECT status,assessment_validation_state FROM study_materials WHERE id=?1",
                response.json()["id"],
            )
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["assessment_validation_state"], "legacy_unvalidated")
        finally:
            release.set()

    def test_revoked_create_permission_during_provider_call_blocks_commit(self) -> None:
        self.add_create_member("race_creator")
        started = threading.Event()
        release = threading.Event()
        self.fake.block_review = (started, release)
        body = {
            "operation_id": str(uuid4()),
            "concept_ids": [self.concept_id],
            "count": 4,
        }
        path = f"/api/classes/{self.class_id}/question-bank/generate"
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.call, "post", path, "race_creator", json=body)
            self.assertTrue(started.wait(5), "fake provider did not reach its review barrier")
            revoked = self.call(
                "delete",
                f"/api/classes/{self.class_id}/members/{self.users['race_creator']['id']}",
                "bank_owner",
            )
            self.assertEqual(revoked.status_code, 204, revoked.text)
            release.set()
            result = pending.result(timeout=10)
        self.fake.block_review = None
        self.assertEqual(result.status_code, 404, result.text)
        self.assertEqual(
            self.client.portal.call(
                self.db.fetchval,
                "SELECT count(*) FROM class_question_bank_items WHERE class_id=?1",
                self.class_id,
            ),
            0,
        )

    def test_source_revision_change_during_provider_call_blocks_commit(self) -> None:
        started = threading.Event()
        release = threading.Event()
        self.fake.block_review = (started, release)
        body = {
            "operation_id": str(uuid4()),
            "concept_ids": [self.concept_id],
            "count": 4,
        }
        path = f"/api/classes/{self.class_id}/question-bank/generate"
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.call, "post", path, "bank_owner", json=body)
            self.assertTrue(started.wait(5), "fake provider did not reach its review barrier")
            self.client.portal.call(
                self.db.execute,
                "UPDATE course_notes SET processing_revision=processing_revision+1,"
                "source_index_status='processing' WHERE id=?1",
                self.note_id,
            )
            release.set()
            result = pending.result(timeout=10)
        self.fake.block_review = None
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(
            self.client.portal.call(
                self.db.fetchval,
                "SELECT count(*) FROM class_question_bank_items WHERE class_id=?1",
                self.class_id,
            ),
            0,
        )

    def test_client_operation_id_is_scoped_to_requester(self) -> None:
        self.add_create_member("bank_creator")
        operation_id = str(uuid4())
        body = {
            "operation_id": operation_id,
            "concept_ids": [self.concept_id],
            "count": 4,
        }
        owner_result = self.call(
            "post",
            f"/api/classes/{self.class_id}/question-bank/generate",
            "bank_owner",
            json=body,
        )
        self.assertEqual(owner_result.status_code, 201, owner_result.text)
        member_result = self.call(
            "post",
            f"/api/classes/{self.class_id}/question-bank/generate",
            "bank_creator",
            json=body,
        )
        self.assertEqual(member_result.status_code, 201, member_result.text)
        ids = self.client.portal.call(
            self.db.fetch,
            "SELECT id FROM class_question_generation_operations WHERE operation_id=?1 "
            "ORDER BY requester_id",
            operation_id,
        )
        self.assertEqual(len(ids), 2)
        self.assertEqual(len({row["id"] for row in ids}), 2)

    @staticmethod
    def _candidate(assignment: str, target: str, source: str) -> QuestionBankAIItem:
        return QuestionBankAIItem(
            assignment_id=assignment,
            prompt="Which statement about the concept is supported?",
            kind="multiple_choice",
            choices=[
                {"id": "a", "text": "Supported statement"},
                {"id": "b", "text": "Other statement"},
            ],
            answer="a",
            accepted_answers=[],
            explanation="The source supports the selected statement.",
            rubric="Choose the supported statement.",
            level=2,
            language="en",
            assessment_target=target,
            concept_ids=["concept-a"],
            source_chunk_revision_ids=[source],
        )

    @staticmethod
    def _review(index: int, *, duplicate: bool = False) -> QuestionBankAIReviewItem:
        return QuestionBankAIReviewItem(
            index=index,
            source_supported=True,
            concept_aligned=True,
            answer_correct=True,
            distractors_valid=True,
            unambiguous=True,
            level_aligned=True,
            language_valid=True,
            leakage_free=True,
            duplicate_family=duplicate,
            reason="Valid candidate.",
        )


if __name__ == "__main__":
    unittest.main()
