"""Opt-in quiz run, reasoning gate, persistence, and retry integration tests."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.errors import ApiError
from app.api.quiz_attempts import _is_correct, _record_metric
from app.config import Settings
from app.main import create_app
from app.services.quiz_reasoning import ReasoningDecision, ReasoningFeedback

ORIGIN = "http://localhost:5173"


class FakeReasoningService:
    def __init__(self, decisions=None, *, failure=False, feedback_failure=False):
        self.decisions = list(decisions or [])
        self.failure = failure
        self.feedback_failure = feedback_failure
        self.contexts = []
        self.feedback_contexts = []

    def evaluate(self, context):
        self.contexts.append(context)
        if self.failure:
            raise RuntimeError("provider unavailable")
        if not self.decisions:
            return ReasoningDecision(status="sufficient", reason="Relevant attempt", follow_up=None)
        return self.decisions.pop(0)

    def feedback(self, context):
        self.feedback_contexts.append(context)
        if self.feedback_failure:
            raise RuntimeError("feedback provider unavailable")
        return ReasoningFeedback(
            answer_feedback="The selected option does not describe the kernel.",
            reasoning_feedback=(
                "You tried to distinguish the roles. Reconsider which component manages hardware."
            ),
        )


class BlockingReasoningService(FakeReasoningService):
    def __init__(self, started, release):
        super().__init__()
        self.started = started
        self.release = release

    def evaluate(self, context):
        self.contexts.append(context)
        self.started.set()
        if not self.release.wait(5):
            raise RuntimeError("test evaluation release timed out")
        return ReasoningDecision(status="sufficient", reason="Relevant attempt", follow_up=None)


class CapturingStudyTutor:
    def __init__(self):
        self.context = None

    async def reply(self, context, messages, *, request_id):
        from app.api.schemas import StudyTutorResponse

        self.context = context
        return StudyTutorResponse(reply="Try comparing the two components' responsibilities.")


class ReasoningQuizApiTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        settings = Settings(
            _env_file=None,
            gemini_api_key="test-key-not-used",
            file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
            sqlite_path=":memory:",
            storage_dir=self.storage.name,
            lean_tool_enabled=False,
            signup_mode="open",
        )
        self.client = TestClient(create_app(settings_override=settings), headers={"origin": ORIGIN})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.user = self.client.post(
            "/api/auth/signup", json={"username": "Reasoning_Student", "password": "tutor-pass-1"}
        ).json()["user"]
        self.class_id = self.client.post("/api/classes", json={"name": "Operating Systems"}).json()[
            "id"
        ]
        self.material_id = str(uuid4())
        self.question = {
            "id": "kernel-difference",
            "kind": "multiple_choice",
            "prompt": "Which statement best describes the kernel?",
            "choices": [
                {
                    "id": "a",
                    "text": "The part of an operating system that manages hardware resources",
                },
                {"id": "b", "text": "The user-facing desktop and application suite"},
            ],
            "answer": "a",
            "explanation": "The kernel manages core system resources and hardware access.",
        }
        database = self.client.app.state.database
        self.client.portal.call(
            database.execute,
            "INSERT INTO study_materials "
            "(id,origin_class_id,resource_scope,owner_id,kind,title,status,content,"
            "assessment_validation_state,assessment_validation_version) "
            "VALUES (?1,?2,'personal',?3,'quiz','Kernel quiz','ready',?4,"
            "'validated','test-validator-v1')",
            self.material_id,
            self.class_id,
            self.user["id"],
            json.dumps({"questions": [self.question]}),
        )
        self.service = FakeReasoningService()
        self.client.app.state.quiz_reasoning_service = self.service

    def create_run(self, mode="reasoning"):
        response = self.client.post(
            f"/api/study-materials/{self.material_id}/runs",
            json={"operation_id": str(uuid4()), "mode": mode},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def run_question_id(self, run_id):
        run = self.client.get(f"/api/study-materials/{self.material_id}/runs/{run_id}").json()
        return run["questions"][0]["id"]

    def run_option_id(self, run_id, source_option_id):
        source = next(item for item in self.question["choices"] if item["id"] == source_option_id)
        run = self.client.get(f"/api/study-materials/{self.material_id}/runs/{run_id}").json()
        return next(
            item["id"] for item in run["questions"][0]["choices"] if item["text"] == source["text"]
        )

    def submit_reasoning(self, run_id, text, *, operation_id=None):
        attempt = self.current_attempt(run_id)
        return self.client.post(
            f"/api/study-materials/{self.material_id}/runs/{run_id}/questions/{attempt['question_id']}/reasoning",
            json={
                "operation_id": operation_id or str(uuid4()),
                "text": text,
                "source": "text",
                "expected_version": attempt["version"],
            },
        )

    def current_attempt(self, run_id):
        response = self.client.get(f"/api/study-materials/{self.material_id}/runs/{run_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["attempts"][0]

    def test_new_runs_fail_closed_for_unvalidated_legacy_personal_and_shared_assessments(self):
        database = self.client.app.state.database
        self.client.portal.call(
            database.execute,
            "UPDATE study_materials SET assessment_validation_state='legacy_unvalidated' "
            "WHERE id=?1",
            self.material_id,
        )
        legacy_shared_id = str(uuid4())
        self.client.portal.call(
            database.execute,
            "INSERT INTO study_materials "
            "(id,class_id,resource_scope,owner_id,created_by,publication_state,kind,title,status,content,"
            "assessment_validation_state) VALUES (?1,?2,'student_contribution',NULL,?3,'draft',"
            "'quiz','Legacy shared','ready',?4,'legacy_unvalidated')",
            legacy_shared_id,
            self.class_id,
            self.user["id"],
            json.dumps({"questions": [self.question]}),
        )
        shared = self.client.post(
            f"/api/study-materials/{legacy_shared_id}/runs",
            json={"operation_id": str(uuid4()), "mode": "standard"},
        )
        self.assertEqual(shared.status_code, 409, shared.text)
        self.assertEqual(shared.json()["code"], "assessment_unvalidated")

        personal_id = str(uuid4())
        self.client.portal.call(
            database.execute,
            "INSERT INTO study_materials "
            "(id,origin_class_id,resource_scope,owner_id,kind,title,status,content,"
            "assessment_validation_state) VALUES (?1,?2,'personal',?3,'quiz','Legacy personal',"
            "'ready',?4,'legacy_unvalidated')",
            personal_id,
            self.class_id,
            self.user["id"],
            json.dumps({"questions": [self.question]}),
        )
        personal = self.client.post(
            f"/api/study-materials/{personal_id}/runs",
            json={"operation_id": str(uuid4()), "mode": "reasoning"},
        )
        self.assertEqual(personal.status_code, 409, personal.text)
        self.assertEqual(personal.json()["code"], "assessment_unvalidated")

    def submit_answer(self, run_id, answer, *, operation_id=None, **options):
        attempt = self.current_attempt(run_id)
        if answer in {"a", "b"}:
            answer = self.run_option_id(run_id, answer)
        return self.client.post(
            f"/api/study-materials/{self.material_id}/runs/{run_id}"
            f"/questions/{attempt['question_id']}/answer",
            json={
                "operation_id": operation_id or str(uuid4()),
                "answer": answer,
                "expected_version": attempt["version"],
                **options,
            },
        )

    def test_reasoning_acceptance_is_independent_from_answer_correctness_and_snapshots_freeze(self):
        self.service.decisions = [
            ReasoningDecision(status="sufficient", reason="Substantive distinction", follow_up=None)
        ]
        run = self.create_run()
        base = f"/api/study-materials/{self.material_id}/runs/{run['run_id']}"
        base += f"/questions/{self.run_question_id(run['run_id'])}"

        locked = self.client.post(
            f"{base}/answer",
            json={
                "operation_id": str(uuid4()),
                "answer": self.run_option_id(run["run_id"], "b"),
                "expected_version": 0,
            },
        )
        self.assertEqual(locked.status_code, 409)
        self.assertEqual(locked.json()["code"], "reasoning_required")

        accepted = self.submit_reasoning(
            run["run_id"],
            "The operating system manages hardware, while the kernel is what I click on.",
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["state"], "reasoning_accepted")
        accepted_through = accepted.json()["accepted_through_turn"]
        accepted_hash = self.client.portal.call(
            self.client.app.state.database.fetchval,
            "SELECT accepted_snapshot_hash FROM quiz_question_attempts WHERE id = ?1",
            accepted.json()["attempt_id"],
        )

        revised = self.submit_reasoning(
            run["run_id"], "Actually, the kernel manages hardware resources."
        )
        self.assertEqual(revised.status_code, 200, revised.text)
        self.assertEqual(revised.json()["state"], "reasoning_accepted")
        self.assertEqual(revised.json()["accepted_through_turn"], accepted_through)
        self.assertEqual(len(revised.json()["reasoning_turns"]), 2)

        answer_operation_id = str(uuid4())
        submitted = self.client.post(
            f"{base}/answer",
            json={
                "operation_id": answer_operation_id,
                "answer": self.run_option_id(run["run_id"], "b"),
                "expected_version": revised.json()["version"],
            },
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        result = submitted.json()
        self.assertEqual(result["state"], "completed")
        self.assertFalse(result["correct"])
        self.assertEqual(result["submitted_through_turn"], 2)
        self.assertEqual(result["feedback_status"], "pending")
        self.assertEqual(len(result["reasoning_turns"]), 2)
        replayed_answer = self.client.post(
            f"{base}/answer",
            json={
                "operation_id": answer_operation_id,
                "answer": self.run_option_id(run["run_id"], "b"),
                "expected_version": 0,
            },
        )
        self.assertEqual(replayed_answer.status_code, 200, replayed_answer.text)
        self.assertEqual(
            replayed_answer.json()["final_answer"],
            "The user-facing desktop and application suite",
        )
        refreshed = self.client.get(
            f"/api/study-materials/{self.material_id}/runs/{run['run_id']}"
        ).json()
        self.assertEqual(refreshed["attempts"][0]["feedback_status"], "available")
        self.assertEqual(
            self.client.portal.call(
                self.client.app.state.database.fetchval,
                "SELECT accepted_snapshot_hash FROM quiz_question_attempts WHERE id = ?1",
                result["attempt_id"],
            ),
            accepted_hash,
        )
        self.assertEqual(len(self.service.feedback_contexts[0]["reasoning_turns"]), 2)
        self.assertNotIn("answer", self.service.contexts[0]["question"])
        self.assertEqual(run["mode"], "reasoning")

        duplicate = self.client.post(
            f"{base}/answer",
            json={"operation_id": str(uuid4()), "answer": "a", "expected_version": 0},
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_cumulative_follow_up_accepts_meaningful_wrong_turn_without_escalation(self):
        self.service.decisions = [
            ReasoningDecision(
                status="insufficient",
                reason="Roles are not explained",
                follow_up="What do you think each one does?",
            ),
            ReasoningDecision(status="sufficient", reason="Relevant follow-up", follow_up=None),
        ]
        run = self.create_run()
        first = self.submit_reasoning(
            run["run_id"], "The kernel is software and the operating system is a concept."
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["state"], "reasoning_insufficient")
        self.assertEqual(
            first.json()["evaluation"]["follow_up"], "What do you think each one does?"
        )

        second = self.submit_reasoning(run["run_id"], "I think the kernel handles hardware.")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["state"], "reasoning_accepted")
        self.assertEqual(len(self.service.contexts[1]["reasoning_turns"]), 2)
        self.assertEqual(
            self.service.contexts[1]["previous_follow_up_prompts"],
            ["What do you think each one does?"],
        )

    def test_provider_failure_persists_reasoning_and_retry_does_not_duplicate_the_turn(self):
        self.service.failure = True
        run = self.create_run()
        operation_id = str(uuid4())
        failed = self.submit_reasoning(
            run["run_id"], "I think the kernel handles hardware.", operation_id=operation_id
        )
        self.assertEqual(failed.status_code, 503)
        current = self.client.get(f"/api/study-materials/{self.material_id}/runs/{run['run_id']}")
        attempt = current.json()["attempts"][0]
        self.assertEqual(attempt["state"], "reasoning_failed")
        self.assertEqual(len(attempt["reasoning_turns"]), 1)

        self.service.failure = False
        retry_operation_id = str(uuid4())
        retry = self.client.post(
            f"/api/study-materials/{self.material_id}/runs/{run['run_id']}/questions/{self.run_question_id(run['run_id'])}/reasoning/retry",
            json={
                "operation_id": retry_operation_id,
                "expected_version": self.current_attempt(run["run_id"])["version"],
            },
        )
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()["state"], "reasoning_accepted")
        self.assertEqual(len(retry.json()["reasoning_turns"]), 1)
        retry_replay = self.client.post(
            f"/api/study-materials/{self.material_id}/runs/{run['run_id']}/questions/{self.run_question_id(run['run_id'])}/reasoning/retry",
            json={"operation_id": retry_operation_id, "expected_version": 1},
        )
        self.assertEqual(retry_replay.status_code, 200, retry_replay.text)
        self.assertEqual(retry_replay.json()["state"], "reasoning_accepted")

    def test_skip_wins_against_a_late_evaluator_result(self):
        started, release = threading.Event(), threading.Event()
        service = BlockingReasoningService(started, release)
        self.client.app.state.quiz_reasoning_service = service
        run = self.create_run()
        base = f"/api/study-materials/{self.material_id}/runs/{run['run_id']}"
        base += f"/questions/{self.run_question_id(run['run_id'])}"
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(
                self.submit_reasoning, run["run_id"], "A meaningful but not yet graded thought."
            )
            self.assertTrue(started.wait(3), "The evaluator did not start")
            conflict = self.submit_reasoning(
                run["run_id"], "A second thought submitted during evaluation."
            )
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.json()["code"], "evaluation_in_progress")
            skip_operation_id = str(uuid4())
            skipped = self.client.post(
                f"{base}/skip",
                json={
                    "operation_id": skip_operation_id,
                    "expected_version": self.current_attempt(run["run_id"])["version"],
                },
            )
            self.assertEqual(skipped.status_code, 200, skipped.text)
            self.assertEqual(skipped.json()["state"], "skipped")
            release.set()
            late = pending.result(timeout=5)
        self.assertEqual(late.status_code, 200, late.text)
        self.assertEqual(late.json()["state"], "skipped")
        self.assertEqual(len(late.json()["reasoning_turns"]), 1)
        self.assertNotIn("correct", late.json())
        replay = self.client.post(
            f"{base}/skip",
            json={"operation_id": skip_operation_id, "expected_version": 1},
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["state"], "skipped")

    def test_feedback_failure_keeps_grade_and_retry_does_not_resubmit_or_regrade(self):
        self.service.decisions = [
            ReasoningDecision(status="sufficient", reason="Relevant attempt", follow_up=None)
        ]
        run = self.create_run()
        accepted = self.submit_reasoning(run["run_id"], "The kernel manages hardware resources.")
        self.assertEqual(accepted.json()["state"], "reasoning_accepted")

        self.service.feedback_failure = True
        operation_id = str(uuid4())
        submitted = self.submit_answer(run["run_id"], "b", operation_id=operation_id)
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertEqual(submitted.json()["state"], "completed")
        self.assertFalse(submitted.json()["correct"])
        self.assertEqual(submitted.json()["feedback_status"], "pending")
        refreshed = self.client.get(
            f"/api/study-materials/{self.material_id}/runs/{run['run_id']}"
        ).json()
        self.assertEqual(refreshed["attempts"][0]["feedback_status"], "failed")

        self.service.feedback_failure = False
        feedback_path = (
            f"/api/study-materials/{self.material_id}/runs/{run['run_id']}"
            f"/questions/{self.run_question_id(run['run_id'])}/feedback/retry"
        )
        retry_id = str(uuid4())
        retried = self.client.post(
            feedback_path,
            json={
                "operation_id": retry_id,
                "expected_version": self.current_attempt(run["run_id"])["version"],
            },
        )
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()["feedback_status"], "pending")
        refreshed_retry = self.client.get(
            f"/api/study-materials/{self.material_id}/runs/{run['run_id']}"
        ).json()
        self.assertEqual(refreshed_retry["attempts"][0]["feedback_status"], "available")
        self.assertEqual(
            refreshed_retry["attempts"][0]["final_answer"],
            "The user-facing desktop and application suite",
        )
        self.assertFalse(refreshed_retry["attempts"][0]["correct"])
        self.assertEqual(
            self.client.portal.call(
                self.client.app.state.database.fetchval,
                "SELECT count(*) FROM study_material_attempts WHERE id = ?1",
                run["run_id"],
            ),
            1,
        )
        replay = self.client.post(
            feedback_path,
            json={"operation_id": retry_id, "expected_version": 0},
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(len(self.service.feedback_contexts), 2)

    def test_standard_mode_keeps_legacy_grader_and_runs_are_independent(self):
        standard = self.create_run("standard")
        retake = self.create_run("standard")
        self.assertNotEqual(standard["run_id"], retake["run_id"])
        self.assertEqual(standard["mode"], "standard")
        submitted = self.client.post(
            f"/api/study-materials/{self.material_id}/attempts",
            json={
                "run_id": standard["run_id"],
                "operation_id": str(uuid4()),
                "answers": {
                    standard["questions"][0]["id"]: self.run_option_id(standard["run_id"], "a")
                },
            },
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertTrue(submitted.json()["question_results"][0]["correct"])
        run_state = self.client.get(
            f"/api/study-materials/{self.material_id}/runs/{standard['run_id']}"
        ).json()
        self.assertEqual(run_state["mode"], "standard")
        self.assertEqual(run_state["status"], "completed")
        history = self.client.get(f"/api/study-materials/{self.material_id}/runs").json()["items"]
        self.assertEqual({item["mode"] for item in history}, {"standard"})

    def test_run_question_and_grading_are_immutable_and_material_detail_is_allowlisted(self):
        run = self.create_run("standard")
        question = run["questions"][0]
        self.assertNotEqual(question["id"], self.question["id"])
        self.assertNotIn("answer", question)
        self.assertNotEqual(question["choices"][0]["id"], self.question["choices"][0]["id"])
        detail = self.client.get(f"/api/study-materials/{self.material_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        material_question = detail.json()["content"]["questions"][0]
        self.assertEqual(set(material_question), {"prompt", "kind"})
        self.assertNotIn("answer", detail.text)
        self.assertNotIn('"choices"', detail.text)

        changed = {
            **self.question,
            "prompt": "A later mutable edit that this run must not use",
            "answer": "b",
            "explanation": "Changed after this run began.",
        }
        self.client.portal.call(
            self.client.app.state.database.execute,
            "UPDATE study_materials SET content=?2 WHERE id=?1",
            self.material_id,
            json.dumps({"questions": [changed]}),
        )
        refreshed = self.client.get(
            f"/api/study-materials/{self.material_id}/runs/{run['run_id']}"
        ).json()
        self.assertEqual(refreshed["questions"][0]["prompt"], self.question["prompt"])
        correct_choice = next(
            item["id"]
            for item in question["choices"]
            if item["text"] == self.question["choices"][0]["text"]
        )
        submitted = self.client.post(
            f"/api/study-materials/{self.material_id}/attempts",
            json={
                "run_id": run["run_id"],
                "operation_id": str(uuid4()),
                "answers": {question["id"]: correct_choice},
            },
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertTrue(submitted.json()["question_results"][0]["correct"])

    def test_manual_release_gates_scores_and_tutor_answer_context(self):
        self.client.portal.call(
            self.client.app.state.database.execute,
            "UPDATE study_materials SET feedback_release_policy='manual' WHERE id=?1",
            self.material_id,
        )
        run = self.create_run("standard")
        q = run["questions"][0]
        learning_concept_id = str(uuid4())
        database = self.client.app.state.database
        snapshot = self.client.portal.call(
            database.fetchrow,
            "SELECT id,private_snapshot FROM quiz_run_questions WHERE run_id=?1 AND id=?2",
            run["run_id"],
            q["id"],
        )
        private_snapshot = json.loads(snapshot["private_snapshot"])
        private_snapshot.update(
            {
                "level": 1,
                "concept_ids": [learning_concept_id],
                "concept_labels": {learning_concept_id: "Operating systems"},
                "source_chunk_revision_ids": [str(uuid4())],
            }
        )
        self.client.portal.call(
            database.execute,
            "UPDATE quiz_run_questions SET private_snapshot=?2,evidence_eligible=1 WHERE id=?1",
            snapshot["id"],
            json.dumps(private_snapshot),
        )
        answer_id = next(
            item["id"]
            for item in q["choices"]
            if item["text"] == self.question["choices"][0]["text"]
        )
        submitted = self.client.post(
            f"/api/study-materials/{self.material_id}/attempts",
            json={
                "run_id": run["run_id"],
                "operation_id": str(uuid4()),
                "answers": {q["id"]: answer_id},
            },
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertFalse(submitted.json()["feedback_available"])
        self.assertIsNone(submitted.json()["question_results"][0]["correct"])
        state = self.client.get(
            f"/api/study-materials/{self.material_id}/runs/{run['run_id']}"
        ).json()
        self.assertFalse(state["feedback_policy"]["feedback_available"])
        self.assertNotIn("correct", state["attempts"][0])
        self.assertEqual(
            self.client.portal.call(
                database.fetchval,
                "SELECT count(*) FROM mastery_evidence WHERE owner_id=?1",
                self.user["id"],
            ),
            0,
        )
        tutor = CapturingStudyTutor()
        self.client.app.state.study_tutor_service = tutor
        hint = self.client.post(
            "/api/study-tutor",
            json={
                "material_id": self.material_id,
                "question_id": q["id"],
                "attempt_id": run["run_id"],
                "messages": [{"role": "user", "text": "Can I have a hint?"}],
            },
        )
        self.assertEqual(hint.status_code, 200, hint.text)
        self.assertNotIn("answer", tutor.context["question"])
        self.assertEqual(tutor.context["correct_answer"], "")
        self.assertEqual(tutor.context["course_excerpts"], "")

        release_body = {"operation_id": str(uuid4())}
        release_path = f"/api/study-materials/{self.material_id}/feedback-release"
        release = self.client.post(release_path, json=release_body)
        self.assertEqual(release.status_code, 200, release.text)
        self.assertEqual(
            self.client.portal.call(
                database.fetchval,
                "SELECT count(*) FROM mastery_evidence WHERE owner_id=?1",
                self.user["id"],
            ),
            1,
        )
        replayed_release = self.client.post(release_path, json=release_body)
        self.assertEqual(replayed_release.status_code, 200, replayed_release.text)
        self.assertTrue(replayed_release.json()["already_released"])
        self.assertEqual(
            self.client.portal.call(
                database.fetchval,
                "SELECT count(*) FROM mastery_evidence WHERE owner_id=?1",
                self.user["id"],
            ),
            1,
        )
        released = self.client.get(
            f"/api/study-materials/{self.material_id}/runs/{run['run_id']}"
        ).json()
        self.assertTrue(released["feedback_policy"]["feedback_available"])
        self.assertTrue(released["attempts"][0]["correct"])
        released_hint = self.client.post(
            "/api/study-tutor",
            json={
                "material_id": self.material_id,
                "question_id": q["id"],
                "attempt_id": run["run_id"],
                "messages": [{"role": "user", "text": "What is the answer?"}],
            },
        )
        self.assertEqual(released_hint.status_code, 200, released_hint.text)
        self.assertEqual(tutor.context["correct_answer"], self.question["choices"][0]["text"])
        self.assertNotIn(tutor.context["correct_answer"], {choice["id"] for choice in q["choices"]})

    def test_feedback_release_rechecks_manager_inside_write_transaction(self):
        database = self.client.app.state.database
        self.client.portal.call(
            database.execute,
            "UPDATE study_materials SET class_id=?2,origin_class_id=NULL,"
            "resource_scope='class_owned',owner_id=NULL,created_by=?3,"
            "publication_state='published',feedback_release_policy='manual' "
            "WHERE id=?1",
            self.material_id,
            self.class_id,
            self.user["id"],
        )
        material = {
            "id": self.material_id,
            "kind": "quiz",
            "resource_scope": "class_owned",
            "owner_id": None,
            "feedback_release_policy": "manual",
        }
        authorized = {"is_owner": True, "capabilities": ["manage_permissions"]}
        release_path = f"/api/study-materials/{self.material_id}/feedback-release"
        with patch(
            "app.api.class_question_bank.material_access",
            side_effect=[
                (material, authorized),
                ApiError(403, "insufficient_permission", "Manager permission was revoked."),
            ],
        ) as access_check:
            response = self.client.post(release_path, json={"operation_id": str(uuid4())})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(access_check.call_count, 2)
        self.assertEqual(
            self.client.portal.call(
                database.fetchval,
                "SELECT count(*) FROM study_material_feedback_releases WHERE material_id=?1",
                self.material_id,
            ),
            0,
        )

    def test_run_and_attempt_operations_are_owner_scoped_and_content_bound(self):
        run = self.create_run()
        operation_id = str(uuid4())
        first = self.submit_reasoning(
            run["run_id"], "I think the kernel manages hardware.", operation_id=operation_id
        )
        self.assertEqual(first.status_code, 200, first.text)
        duplicate = self.submit_reasoning(
            run["run_id"], "I think the kernel manages hardware.", operation_id=operation_id
        )
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        conflict = self.submit_reasoning(
            run["run_id"], "A different turn.", operation_id=operation_id
        )
        self.assertEqual(conflict.status_code, 409)

        other = TestClient(self.client.app, headers={"origin": ORIGIN})
        other.post(
            "/api/auth/signup", json={"username": "Other_Student", "password": "tutor-pass-1"}
        )
        hidden = other.get(f"/api/study-materials/{self.material_id}/runs/{run['run_id']}")
        self.assertEqual(hidden.status_code, 404)

    def test_version_optimistic_concurrency_rejects_stale_mutation(self):
        self.service.decisions = [
            ReasoningDecision(status="sufficient", reason="Meaningful attempt", follow_up=None)
        ]
        run = self.create_run()
        path = (
            f"/api/study-materials/{self.material_id}/runs/{run['run_id']}"
            f"/questions/{self.run_question_id(run['run_id'])}/reasoning"
        )
        missing_version = self.client.post(
            path,
            json={"operation_id": str(uuid4()), "text": "The kernel handles hardware."},
        )
        self.assertEqual(missing_version.status_code, 400)

        operation_id = str(uuid4())
        stale = self.client.post(
            path,
            json={
                "operation_id": operation_id,
                "text": "The kernel handles hardware.",
                "source": "text",
                "expected_version": 99,
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "version_conflict")

        valid = self.client.post(
            path,
            json={
                "operation_id": operation_id,
                "text": "The kernel handles hardware.",
                "source": "text",
                "expected_version": 0,
            },
        )
        self.assertEqual(valid.status_code, 200, valid.text)
        self.assertEqual(valid.json()["state"], "reasoning_accepted")
        self.assertGreater(valid.json()["version"], 0)

        replay = self.client.post(
            path,
            json={
                "operation_id": operation_id,
                "text": "The kernel handles hardware.",
                "source": "text",
                "expected_version": 0,
            },
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["state"], "reasoning_accepted")

        answer_path = (
            f"/api/study-materials/{self.material_id}/runs/{run['run_id']}"
            f"/questions/{self.run_question_id(run['run_id'])}/answer"
        )
        missing_answer_version = self.client.post(
            answer_path,
            json={"operation_id": str(uuid4()), "answer": self.run_option_id(run["run_id"], "a")},
        )
        self.assertEqual(missing_answer_version.status_code, 400)
        stale_answer = self.client.post(
            answer_path,
            json={
                "operation_id": str(uuid4()),
                "answer": self.run_option_id(run["run_id"], "a"),
                "expected_version": 0,
            },
        )
        self.assertEqual(stale_answer.status_code, 409)
        self.assertEqual(stale_answer.json()["code"], "version_conflict")

    def test_exact_median_and_p95_latency_tracking_and_metrics_endpoint(self):
        self.client.portal.call(
            self._record_latency_samples,
            [0, 20, 30, 40],
        )

        forbidden = self.client.get("/api/quiz-reasoning/metrics")
        self.assertEqual(forbidden.status_code, 403)

        # Aggregate diagnostic metrics are intentionally administrator-only.
        self.client.portal.call(
            self.client.app.state.database.execute,
            "UPDATE users SET is_admin=1 WHERE id=?1",
            self.user["id"],
        )
        metrics = self.client.get("/api/quiz-reasoning/metrics")
        self.assertEqual(metrics.status_code, 200)
        items = metrics.json()["items"]
        self.assertGreaterEqual(len(items), 1)
        today_metrics = items[0]
        self.assertIn("latency_median_ms", today_metrics)
        self.assertIn("latency_p95_ms", today_metrics)
        self.assertEqual(today_metrics["latency_median_ms"], 25)
        self.assertEqual(today_metrics["latency_p95_ms"], 40)

    async def _record_latency_samples(self, samples):
        database = self.client.app.state.database
        async with database.acquire() as connection:
            async with connection.transaction():
                await connection.execute("DELETE FROM quiz_reasoning_latency_samples")
                await connection.execute("DELETE FROM quiz_reasoning_metrics")
                for latency in samples:
                    await _record_metric(connection, latency_ms=latency)

    def test_wait_for_feedback_flag_returns_available_synchronously(self):
        self.service.decisions = [
            ReasoningDecision(status="sufficient", reason="Good attempt", follow_up=None)
        ]
        run = self.create_run()
        self.submit_reasoning(run["run_id"], "The kernel controls hardware directly.")
        answer_path = (
            f"/api/study-materials/{self.material_id}/runs/{run['run_id']}"
            f"/questions/{self.run_question_id(run['run_id'])}/answer"
        )
        sync_res = self.client.post(
            answer_path,
            json={
                "operation_id": str(uuid4()),
                "answer": self.run_option_id(run["run_id"], "a"),
                "expected_version": self.current_attempt(run["run_id"])["version"],
                "wait_for_feedback": True,
            },
        )
        self.assertEqual(sync_res.status_code, 200)
        self.assertEqual(sync_res.json()["state"], "completed")
        self.assertEqual(sync_res.json()["feedback_status"], "available")
        self.assertTrue(sync_res.json()["correct"])
        self.assertIsNotNone(sync_res.json()["reasoning_feedback"])

    def test_fixture_set_is_manually_reviewable_and_not_a_runtime_rule_list(self):
        fixture_path = Path(__file__).parent / "fixtures" / "reasoning-sufficiency.json"
        fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(fixtures), 8)
        self.assertEqual({item["expected"] for item in fixtures}, {"sufficient", "insufficient"})
        self.assertTrue(all(item.get("justification") for item in fixtures))


class ShortAnswerMatchingTests(unittest.TestCase):
    def test_accepted_paraphrases_notation_and_units_are_explicit(self):
        cases = [
            (
                {
                    "kind": "short_answer",
                    "answer": "ATP",
                    "accepted_answers": ["adenosine triphosphate"],
                },
                "Adenosine triphosphate",
                True,
            ),
            ({"kind": "short_answer", "answer": "Na+", "accepted_answers": ["Na⁺"]}, "na⁺", True),
            (
                {
                    "kind": "short_answer",
                    "answer": "4.5 mg",
                    "accepted_answers": ["4.5 milligrams"],
                },
                "4.5 milligrams",
                True,
            ),
            (
                {
                    "kind": "short_answer",
                    "answer": "4.5 mg",
                    "accepted_answers": ["4.5 milligrams"],
                },
                "4.5",
                False,
            ),
            (
                {
                    "kind": "short_answer",
                    "answer": "mitochondrion",
                    "accepted_answers": ["mitochondria"],
                },
                "mitochodria",
                False,
            ),
        ]
        for question, response, expected in cases:
            with self.subTest(response=response):
                self.assertEqual(_is_correct(question, response), expected)

    def test_case_and_whitespace_variants_are_normalized(self):
        question = {
            "kind": "short_answer",
            "answer": "cellular respiration",
            "accepted_answers": ["aerobic respiration"],
        }
        self.assertTrue(_is_correct(question, "  CELLULAR   RESPIRATION  "))


if __name__ == "__main__":
    unittest.main()
