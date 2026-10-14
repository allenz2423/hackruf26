"""Opt-in quiz run, reasoning gate, persistence, and retry integration tests."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.quiz_attempts import _record_metric
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
            "INSERT INTO study_materials (id, class_id, kind, title, status, content) "
            "VALUES (?1, ?2, 'quiz', 'Kernel quiz', 'ready', ?3)",
            self.material_id,
            self.class_id,
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

    def submit_reasoning(self, run_id, text, *, operation_id=None):
        attempt = self.current_attempt(run_id)
        return self.client.post(
            f"/api/study-materials/{self.material_id}/runs/{run_id}/questions/{self.question['id']}/reasoning",
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
        return next(
            attempt
            for attempt in response.json()["attempts"]
            if attempt["question_id"] == self.question["id"]
        )

    def submit_answer(self, run_id, answer, *, operation_id=None, **options):
        attempt = self.current_attempt(run_id)
        return self.client.post(
            f"/api/study-materials/{self.material_id}/runs/{run_id}"
            f"/questions/{self.question['id']}/answer",
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
        base += f"/questions/{self.question['id']}"

        locked = self.client.post(
            f"{base}/answer",
            json={"operation_id": str(uuid4()), "answer": "b", "expected_version": 0},
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
                "answer": "b",
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
                "answer": "b",
                "expected_version": 0,
            },
        )
        self.assertEqual(replayed_answer.status_code, 200, replayed_answer.text)
        self.assertEqual(replayed_answer.json()["final_answer"], "b")
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
            f"/api/study-materials/{self.material_id}/runs/{run['run_id']}/questions/{self.question['id']}/reasoning/retry",
            json={
                "operation_id": retry_operation_id,
                "expected_version": self.current_attempt(run["run_id"])["version"],
            },
        )
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()["state"], "reasoning_accepted")
        self.assertEqual(len(retry.json()["reasoning_turns"]), 1)
        retry_replay = self.client.post(
            f"/api/study-materials/{self.material_id}/runs/{run['run_id']}/questions/{self.question['id']}/reasoning/retry",
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
        base += f"/questions/{self.question['id']}"
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
            f"/questions/{self.question['id']}/feedback/retry"
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
        self.assertEqual(refreshed_retry["attempts"][0]["final_answer"], "b")
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
                "answers": {"kernel-difference": "a"},
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
            f"/questions/{self.question['id']}/reasoning"
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
            f"/questions/{self.question['id']}/answer"
        )
        missing_answer_version = self.client.post(
            answer_path,
            json={"operation_id": str(uuid4()), "answer": "a"},
        )
        self.assertEqual(missing_answer_version.status_code, 400)
        stale_answer = self.client.post(
            answer_path,
            json={
                "operation_id": str(uuid4()),
                "answer": "a",
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
            f"/questions/{self.question['id']}/answer"
        )
        sync_res = self.client.post(
            answer_path,
            json={
                "operation_id": str(uuid4()),
                "answer": "a",
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


if __name__ == "__main__":
    unittest.main()
