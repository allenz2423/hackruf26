"""Private learning projection and access-revocation regressions."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.class_library import _process_upload_job
from app.api.errors import ApiError
from app.config import Settings
from app.main import create_app
from app.services.auth import SESSION_COOKIE
from app.services.learning_scoring import (
    _quality_weight_for_outcome,
    rebuild_projection,
    record_assessment_outcome,
)

ORIGIN = "http://localhost:5173"


class FakeContentService:
    def process_text(self, text: str, _kind: str) -> dict:
        return {
            "extracted_text": text,
            "summary": "Course source",
            "key_topics": ["mitochondria"],
            "transcript_segments": [],
            "duration_seconds": None,
            "syllabus_outline": None,
        }


class LearningProgressApiTests(unittest.TestCase):
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
        self.client.app.state.gemini_content_service = FakeContentService()
        self.users: dict[str, dict] = {}
        self.cookies: dict[str, str] = {}
        self.owner = self.add_user("progress_owner")
        class_response = self.call(
            "post", "/api/classes", "progress_owner", json={"name": "Progress"}
        )
        self.assertEqual(class_response.status_code, 201, class_response.text)
        self.class_id = class_response.json()["id"]
        self.note_id, self.chunk_id = self.seed_source()
        concept = self.call(
            "post",
            f"/api/classes/{self.class_id}/concepts",
            "progress_owner",
            json={
                "canonical_name": "Cellular energy",
                "description": "How cells store and use energy.",
                "source_chunk_revision_ids": [self.chunk_id],
            },
        )
        self.assertEqual(concept.status_code, 201, concept.text)
        self.concept = concept.json()
        approved = self.call(
            "post",
            f"/api/classes/{self.class_id}/concepts/{self.concept['id']}/decision",
            "progress_owner",
            json={"decision": "approve", "expected_version": self.concept["version"]},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.concept = approved.json()
        self.reader = self.add_user("progress_reader")
        invitation = self.call(
            "post",
            f"/api/classes/{self.class_id}/invitations",
            "progress_owner",
            json={"capabilities": ["read"], "max_uses": 1, "expires_in_days": 7},
        )
        self.assertEqual(invitation.status_code, 201, invitation.text)
        joined = self.call(
            "post",
            "/api/class-invitations/redeem",
            "progress_reader",
            json={"code": invitation.json()["code"]},
        )
        self.assertEqual(joined.status_code, 200, joined.text)

    def add_user(self, username: str) -> dict:
        response = self.client.post(
            "/api/auth/signup", json={"username": username, "password": "progress-test-pass"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.users[username] = response.json()["user"]
        self.cookies[username] = response.cookies.get(SESSION_COOKIE)
        return self.users[username]

    def call(self, method: str, path: str, username: str, **kwargs):
        return getattr(self.client, method)(
            path, cookies={SESSION_COOKIE: self.cookies[username]}, **kwargs
        )

    def seed_source(self) -> tuple[str, str]:
        note_id = str(uuid4())
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO course_notes(id,class_id,created_by,publication_state,title,kind,"
            "source_type,"
            "extracted_text,status,processing_progress) "
            "VALUES (?1,?2,?3,'published','Course source','document','pasted_text',"
            "'Mitochondria support cellular respiration and ATP production.','processing',0)",
            note_id,
            self.class_id,
            self.owner["id"],
        )
        with patch("app.api.class_library.create_default_document", new_callable=AsyncMock):
            self.client.portal.call(_process_upload_job, self.client.app, note_id)
        chunk_id = self.client.portal.call(
            self.db.fetchval,
            "SELECT id FROM source_chunk_revisions WHERE revision_id="
            "(SELECT revision_id FROM source_index_heads WHERE note_id=?1) LIMIT 1",
            note_id,
        )
        self.assertTrue(chunk_id)
        return note_id, str(chunk_id)

    def seed_old_assessment_history(self, *, include_xp: bool = True) -> tuple[str, str, str]:
        material_id = str(uuid4())
        run_id = str(uuid4())
        owner_id = self.reader["id"]
        now = datetime.now(UTC)
        stale_time = (
            (now - timedelta(days=120)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        fresh_time = (
            (now - timedelta(days=30)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO study_materials(id,class_id,resource_scope,publication_state,kind,title,"
            "status,content,created_by,assessment_validation_state) "
            "VALUES (?1,?2,'class_owned','published','quiz','History','ready','{}',?3,'validated')",
            material_id,
            self.class_id,
            self.owner["id"],
        )
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO quiz_runs(id,material_id,owner_id,operation_id,status,score_percent,"
            "completed_at) "
            "VALUES (?1,?2,?3,?4,'completed',50,?5)",
            run_id,
            material_id,
            owner_id,
            f"progress-{uuid4()}",
            fresh_time,
        )
        stale_attempt_id = self._seed_one_assessment_record(
            material_id, run_id, owner_id, level=4, created_at=stale_time, correctness=1.0
        )
        fresh_attempt_id = self._seed_one_assessment_record(
            material_id, run_id, owner_id, level=1, created_at=fresh_time, correctness=0.0
        )
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO assessment_outcomes(id,attempt_id,run_id,owner_id,material_id,question_id,"
            "family_id,status,correctness,reasoning_status,grading_policy,level,quality_weight,"
            "created_at) "
            "VALUES (?1,?2,?3,?4,?5,(SELECT question_id FROM quiz_question_attempts WHERE id=?6),"
            "'old-family','correct',1,'none','objective_only',4,1,?7)",
            str(uuid4()),
            stale_attempt_id,
            run_id,
            owner_id,
            material_id,
            stale_attempt_id,
            stale_time,
        )
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO assessment_outcomes(id,attempt_id,run_id,owner_id,material_id,question_id,"
            "family_id,status,correctness,reasoning_status,grading_policy,level,quality_weight,"
            "created_at) "
            "VALUES (?1,?2,?3,?4,?5,(SELECT question_id FROM quiz_question_attempts WHERE id=?6),"
            "'fresh-family','incorrect',0,'none','objective_only',1,1,?7)",
            str(uuid4()),
            fresh_attempt_id,
            run_id,
            owner_id,
            material_id,
            fresh_attempt_id,
            fresh_time,
        )
        if include_xp:
            self.add_old_xp(owner_id, run_id, stale_time)
        self.client.portal.call(self._rebuild_projection, owner_id, self.concept["id"])
        return material_id, run_id, owner_id

    def add_old_xp(self, owner_id: str, run_id: str, created_at: str) -> None:
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO learning_xp_events(id,owner_id,event_key,event_type,amount,run_id,"
            "algorithm_version,created_at) VALUES (?1,?2,?3,'valid_response',5,?4,'test-v1',?5)",
            str(uuid4()),
            owner_id,
            f"old-xp-{uuid4()}",
            run_id,
            created_at,
        )

    def _seed_one_assessment_record(
        self,
        material_id: str,
        run_id: str,
        owner_id: str,
        *,
        level: int,
        created_at: str,
        correctness: float,
    ) -> str:
        question_id = f"question-{uuid4()}"
        attempt_id = str(uuid4())
        private = {
            "concept_ids": [self.concept["id"]],
            "concept_labels": {self.concept["id"]: "Cellular energy"},
            "level": level,
        }
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO quiz_run_questions(id,run_id,material_id,ordinal,family_id,"
            "public_snapshot,"
            "private_snapshot,option_mapping,snapshot_sha256,evidence_eligible) "
            "VALUES (?1,?2,?3,?4,?5,'{}',?6,'{}','test-hash',1)",
            question_id,
            run_id,
            material_id,
            level,
            f"family-{level}",
            json.dumps(private),
        )
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO quiz_question_attempts(id,run_id,question_id,state,answer_status,correct,"
            "answer_operation_id,submitted_through_turn,submitted_snapshot_hash,submitted_at,"
            "completed_at) "
            "VALUES (?1,?2,?3,'completed','answered',?4,?5,0,'empty-snapshot',?6,?6)",
            attempt_id,
            run_id,
            question_id,
            int(correctness),
            f"answer-{uuid4()}",
            created_at,
        )
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO mastery_evidence(id,owner_id,class_id,concept_id,concept_label,run_id,"
            "attempt_id,question_id,family_id,level,correctness,reasoning_result,grading_policy,"
            "quality_weight,algorithm_version,created_at) "
            "VALUES (?1,?2,?3,?4,'Cellular energy',?5,?6,?7,?8,?9,?10,'none',"
            "'objective_only',1,'test-v1',?11)",
            str(uuid4()),
            owner_id,
            self.class_id,
            self.concept["id"],
            run_id,
            attempt_id,
            question_id,
            f"family-{level}",
            level,
            correctness,
            created_at,
        )
        return attempt_id

    async def _rebuild_projection(self, owner_id: str, concept_id: str) -> None:
        async with self.db.acquire() as connection:
            async with connection.transaction():
                await rebuild_projection(connection, owner_id=owner_id, concept_id=concept_id)

    def test_old_history_keeps_lifetime_evidence_and_per_level_review_due(self) -> None:
        _material_id, run_id, owner_id = self.seed_old_assessment_history(include_xp=False)
        response = self.call("get", "/api/learning/progress?window_days=7", "progress_reader")
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["recent_accuracy"]["graded_count"], 0)
        self.assertEqual(result["total_xp"], 0)
        concept = next(item for item in result["concepts"] if item["id"] == self.concept["id"])
        self.assertEqual(concept["status"], "provisional")
        self.assertEqual(concept["estimate"]["evidence_count"], 1)
        self.assertTrue(concept["review_due"])
        self.add_old_xp(
            owner_id,
            run_id,
            (datetime.now(UTC) - timedelta(days=120))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        )
        lifetime = self.call("get", "/api/learning/progress?window_days=7", "progress_reader")
        self.assertEqual(lifetime.status_code, 200, lifetime.text)
        self.assertEqual(lifetime.json()["recent_accuracy"]["graded_count"], 0)
        self.assertEqual(lifetime.json()["total_xp"], 5)
        levels = self.client.portal.call(
            self.db.fetch,
            "SELECT level,review_due FROM mastery_projections WHERE owner_id=?1 AND concept_id=?2",
            self.reader["id"],
            self.concept["id"],
        )
        review_by_level = {row["level"]: bool(row["review_due"]) for row in levels}
        self.assertEqual(review_by_level, {1: False, 2: False, 3: False, 4: True})

    def test_class_leave_immediately_hides_private_progress(self) -> None:
        self.seed_old_assessment_history()
        visible = self.call("get", "/api/learning/progress", "progress_reader")
        self.assertEqual(visible.status_code, 200, visible.text)
        self.assertTrue(visible.json()["concepts"])
        left = self.call("post", f"/api/classes/{self.class_id}/leave", "progress_reader")
        self.assertEqual(left.status_code, 204, left.text)
        revoked = self.call("get", "/api/learning/progress", "progress_reader")
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(revoked.json()["total_xp"], 0)
        self.assertEqual(revoked.json()["concepts"], [])

    def test_feedback_release_rejects_manager_revoked_before_transaction(self) -> None:
        material_id, _run_id, _owner_id = self.seed_old_assessment_history(include_xp=False)
        self.client.portal.call(
            self.db.execute,
            "UPDATE study_materials SET feedback_release_policy='manual' WHERE id=?1",
            material_id,
        )
        material = {
            "id": material_id,
            "kind": "quiz",
            "resource_scope": "class_owned",
            "owner_id": None,
            "feedback_release_policy": "manual",
        }
        authorized = {"is_owner": True, "capabilities": ["manage_permissions"]}
        with patch(
            "app.api.class_question_bank.material_access",
            side_effect=[
                (material, authorized),
                ApiError(403, "insufficient_permission", "Manager permission was revoked."),
            ],
        ) as access_check:
            response = self.call(
                "post",
                f"/api/study-materials/{material_id}/feedback-release",
                "progress_owner",
                json={"operation_id": str(uuid4())},
            )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(access_check.call_count, 2)
        self.assertEqual(
            self.client.portal.call(
                self.db.fetchval,
                "SELECT count(*) FROM study_material_feedback_releases WHERE material_id=?1",
                material_id,
            ),
            0,
        )

    def test_short_interval_family_weight_is_persisted_and_replayable(self) -> None:
        material_id, run_id, owner_id = self.seed_old_assessment_history(include_xp=False)
        recent = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.client.portal.call(
            self.db.execute,
            "UPDATE assessment_outcomes SET created_at=?2 WHERE run_id=?1 AND level=4",
            run_id,
            recent,
        )
        self.client.portal.call(
            self.db.execute,
            "UPDATE mastery_evidence SET created_at=?2 WHERE run_id=?1 AND level=4",
            run_id,
            recent,
        )
        private = {
            "concept_ids": [self.concept["id"]],
            "concept_labels": {self.concept["id"]: "Cellular energy"},
            "level": 4,
        }
        quick_weight = self.client.portal.call(
            self._quality_weight,
            owner_id,
            private,
        )
        self.assertEqual(quick_weight, 0.25)
        old = (
            (datetime.now(UTC) - timedelta(hours=1))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        self.client.portal.call(
            self.db.execute,
            "UPDATE assessment_outcomes SET created_at=?2 WHERE run_id=?1 AND level=4",
            run_id,
            old,
        )
        self.client.portal.call(
            self.db.execute,
            "UPDATE mastery_evidence SET created_at=?2 WHERE run_id=?1 AND level=4",
            run_id,
            old,
        )
        spaced_weight = self.client.portal.call(self._quality_weight, owner_id, private)
        self.assertEqual(spaced_weight, 1.0)
        self.client.portal.call(
            self.db.execute,
            "UPDATE assessment_outcomes SET created_at=?2 WHERE run_id=?1 AND level=4",
            run_id,
            recent,
        )
        self.client.portal.call(
            self.db.execute,
            "UPDATE mastery_evidence SET created_at=?2 WHERE run_id=?1 AND level=4",
            run_id,
            recent,
        )

        question_id = str(uuid4())
        attempt_id = str(uuid4())
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO quiz_run_questions(id,run_id,material_id,ordinal,family_id,"
            "public_snapshot,"
            "private_snapshot,option_mapping,snapshot_sha256,evidence_eligible) "
            "VALUES (?1,?2,?3,5,'quick-family','{}',?4,'{}','new-hash',1)",
            question_id,
            run_id,
            material_id,
            json.dumps(private),
        )
        now_text = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO quiz_question_attempts(id,run_id,question_id,state,answer_status,correct,"
            "answer_operation_id,submitted_through_turn,submitted_snapshot_hash,submitted_at,"
            "completed_at) "
            "VALUES (?1,?2,?3,'completed','answered',1,?4,0,'empty-snapshot',?5,?5)",
            attempt_id,
            run_id,
            question_id,
            f"answer-{uuid4()}",
            now_text,
        )
        run = self.client.portal.call(
            self.db.fetchrow, "SELECT * FROM quiz_runs WHERE id=?1", run_id
        )
        attempt = self.client.portal.call(
            self.db.fetchrow, "SELECT * FROM quiz_question_attempts WHERE id=?1", attempt_id
        )
        question = {
            "id": question_id,
            "private_snapshot": private,
            "family_id": "quick-family",
            "evidence_eligible": True,
        }
        self.client.portal.call(self._record_outcome, run, attempt, question)
        persisted = self.client.portal.call(
            self.db.fetchrow,
            "SELECT quality_weight FROM assessment_outcomes WHERE attempt_id=?1",
            attempt_id,
        )
        self.assertEqual(persisted["quality_weight"], 0.25)
        evidence = self.client.portal.call(
            self.db.fetchrow,
            "SELECT quality_weight FROM mastery_evidence WHERE attempt_id=?1 AND concept_id=?2",
            attempt_id,
            self.concept["id"],
        )
        self.assertEqual(evidence["quality_weight"], 0.25)

    def seed_review_question(self, *, family_id: str) -> str:
        item_id = str(uuid4())
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO class_question_bank_items "
            "(id,class_id,family_id,family_signature,prompt,kind,level,language,choices_json,"
            "grading_json,state,validator_version,generation_model,generation_version,created_by) "
            "VALUES (?1,?2,?3,?4,?5,'multiple_choice',1,'en',?6,?7,'validated','test-v1',"
            "'test-model','test-generation-v1',?8)",
            item_id,
            self.class_id,
            family_id,
            f"signature-{family_id}",
            f"Which statement describes cellular energy? {family_id}",
            json.dumps(
                [
                    {"id": "a", "text": "Mitochondria support ATP production."},
                    {"id": "b", "text": "Ribosomes store all cellular energy."},
                ]
            ),
            json.dumps(
                {
                    "answer": "a",
                    "explanation": "Mitochondria support cellular respiration and ATP production.",
                }
            ),
            self.owner["id"],
        )
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO class_question_bank_concepts(item_id,class_id,concept_id,relation) "
            "VALUES (?1,?2,?3,'primary')",
            item_id,
            self.class_id,
            self.concept["id"],
        )
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO class_question_bank_sources("
            "item_id,class_id,chunk_revision_id,source_order) "
            "VALUES (?1,?2,?3,0)",
            item_id,
            self.class_id,
            self.chunk_id,
        )
        return item_id

    def seed_review_recommendation(self, evidence_cursor: str) -> str:
        recommendation_id = str(uuid4())
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO learning_recommendations "
            "(id,owner_id,class_id,concept_id,recommendation_type,chosen_goal,label,reason,"
            "supporting_evidence_ids,evidence_cursor) "
            "VALUES (?1,?2,?3,?4,'review','review','Review cellular energy',"
            "'Short source-linked review','[]',?5)",
            recommendation_id,
            self.reader["id"],
            self.class_id,
            self.concept["id"],
            evidence_cursor,
        )
        return recommendation_id

    def test_short_learning_review_hides_answer_persists_due_date_and_uses_new_family(self) -> None:
        first_item = self.seed_review_question(family_id="review-family-a")
        recommendation_id = self.seed_review_recommendation("initial-review")
        response = self.call(
            "post",
            f"/api/learning/recommendations/{recommendation_id}/review",
            "progress_reader",
            json={},
        )
        self.assertEqual(response.status_code, 200, response.text)
        session = response.json()
        public_json = json.dumps(session).casefold()
        self.assertEqual(session["status"], "in_progress")
        self.assertFalse(session["mastery_updated"])
        self.assertEqual(session["citations"], [])
        self.assertNotIn("correct_answer", public_json)
        self.assertNotIn("explanation", public_json)
        self.assertNotIn(first_item, public_json)
        correct_choice = self.client.portal.call(
            self.db.fetchval,
            "SELECT json_extract(private_snapshot,'$.correct_choice_id') "
            "FROM learning_review_sessions WHERE id=?1",
            session["id"],
        )
        before_evidence = self.client.portal.call(
            self.db.fetchval,
            "SELECT count(*) FROM mastery_evidence WHERE owner_id=?1 AND concept_id=?2",
            self.reader["id"],
            self.concept["id"],
        )
        answered = self.call(
            "post",
            f"/api/learning/reviews/{session['id']}/answer",
            "progress_reader",
            json={"choice_id": correct_choice},
        )
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertTrue(answered.json()["correct"])
        self.assertFalse(answered.json()["mastery_updated"])
        self.assertTrue(answered.json()["correct_answer"])
        self.assertTrue(answered.json()["citations"])
        self.assertGreater(
            datetime.fromisoformat(answered.json()["next_due_at"].replace("Z", "+00:00")),
            datetime.now(UTC),
        )
        after_evidence = self.client.portal.call(
            self.db.fetchval,
            "SELECT count(*) FROM mastery_evidence WHERE owner_id=?1 AND concept_id=?2",
            self.reader["id"],
            self.concept["id"],
        )
        self.assertEqual(after_evidence, before_evidence)

        second_item = self.seed_review_question(family_id="review-family-b")
        self.assertNotEqual(first_item, second_item)
        self.client.portal.call(
            self.db.execute,
            "UPDATE learning_review_state SET due_at='2000-01-01T00:00:00Z' "
            "WHERE owner_id=?1 AND class_id=?2 AND concept_id=?3",
            self.reader["id"],
            self.class_id,
            self.concept["id"],
        )
        second_recommendation = self.seed_review_recommendation(f"review:{session['id']}")
        second = self.call(
            "post",
            f"/api/learning/recommendations/{second_recommendation}/review",
            "progress_reader",
            json={},
        )
        self.assertEqual(second.status_code, 200, second.text)
        selected_family = self.client.portal.call(
            self.db.fetchval,
            "SELECT family_id FROM learning_review_sessions WHERE id=?1",
            second.json()["id"],
        )
        self.assertNotEqual(selected_family, "review-family-a")
        self.assertEqual(selected_family, "review-family-b")

    def test_revoked_source_commits_review_abandonment_and_is_owner_scoped(self) -> None:
        self.seed_review_question(family_id="revoked-review-family")
        recommendation_id = self.seed_review_recommendation("revoked-review")
        started = self.call(
            "post",
            f"/api/learning/recommendations/{recommendation_id}/review",
            "progress_reader",
            json={},
        )
        self.assertEqual(started.status_code, 200, started.text)
        session_id = started.json()["id"]
        stranger = self.call(
            "post",
            f"/api/learning/reviews/{session_id}/answer",
            "progress_owner",
            json={"choice_id": started.json()["choices"][0]["id"]},
        )
        self.assertEqual(stranger.status_code, 404, stranger.text)
        self.client.portal.call(
            self.db.execute,
            "UPDATE course_notes SET status='failed' WHERE id=?1",
            self.note_id,
        )
        revoked = self.call(
            "post",
            f"/api/learning/reviews/{session_id}/answer",
            "progress_reader",
            json={"choice_id": started.json()["choices"][0]["id"]},
        )
        self.assertEqual(revoked.status_code, 404, revoked.text)
        status = self.client.portal.call(
            self.db.fetchval,
            "SELECT status FROM learning_review_sessions WHERE id=?1",
            session_id,
        )
        self.assertEqual(status, "abandoned")

    def test_flashcard_review_state_is_private_idempotent_and_clearable(self) -> None:
        material_id = str(uuid4())
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO study_materials("
            "id,resource_scope,owner_id,created_by,kind,title,status,content) "
            "VALUES (?1,'personal',?2,?2,'flashcards','Private cards','ready',?3)",
            material_id,
            self.reader["id"],
            json.dumps(
                {
                    "format": "flashcards",
                    "cards": [
                        {"id": "card-1", "front": "ATP?", "back": "Cellular energy currency."}
                    ],
                }
            ),
        )
        empty = self.call(
            "get", f"/api/study-materials/{material_id}/flashcard-review", "progress_reader"
        )
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["items"], [])
        self.assertFalse(empty.json()["mastery_updated"])
        operation_id = str(uuid4())
        saved = self.call(
            "put",
            f"/api/study-materials/{material_id}/flashcard-review",
            "progress_reader",
            json={"operation_id": operation_id, "card_id": "card-1", "rating": "missed"},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["rating"], "missed")
        self.assertFalse(saved.json()["mastery_updated"])
        replay = self.call(
            "put",
            f"/api/study-materials/{material_id}/flashcard-review",
            "progress_reader",
            json={"operation_id": operation_id, "card_id": "card-1", "rating": "recalled"},
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["rating"], "missed")
        forbidden = self.call(
            "get", f"/api/study-materials/{material_id}/flashcard-review", "progress_owner"
        )
        self.assertEqual(forbidden.status_code, 404, forbidden.text)
        cleared = self.call(
            "delete", f"/api/study-materials/{material_id}/flashcard-review", "progress_reader"
        )
        self.assertEqual(cleared.status_code, 204, cleared.text)
        self.assertEqual(
            self.client.portal.call(
                self.db.fetchval,
                "SELECT count(*) FROM flashcard_review_state WHERE owner_id=?1 AND material_id=?2",
                self.reader["id"],
                material_id,
            ),
            0,
        )

    async def _quality_weight(self, owner_id: str, private: dict) -> float:
        async with self.db.acquire() as connection:
            return await _quality_weight_for_outcome(
                connection,
                owner_id=owner_id,
                private_snapshot=private,
                level=4,
                evidence_eligible=True,
            )

    async def _record_outcome(self, run: dict, attempt: dict, question: dict) -> None:
        async with self.db.acquire() as connection:
            async with connection.transaction():
                await record_assessment_outcome(
                    connection,
                    run=run,
                    attempt=attempt,
                    question_row=question,
                    status="correct",
                    correctness=1.0,
                    reasoning_status="none",
                )
