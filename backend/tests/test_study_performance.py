"""Private aggregate-only AI study score endpoint and service tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.errors import ApiError
from app.api.schemas import StudyPerformanceScoreResponse
from app.api.study_performance import _weights
from app.config import Settings
from app.main import create_app
from app.security.rate_limit import InMemoryRateLimiter
from app.services.study_performance import StudyPerformanceScoringService

ORIGIN = "http://localhost:5173"


class FakeScoringService:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.usage_contexts: list[dict[str, str]] = []

    async def score(self, summary: dict, *, request_id: str):
        from app.services.ai_usage_ledger import usage_context_values

        self.calls.append({"summary": summary, "request_id": request_id})
        self.usage_contexts.append(usage_context_values())
        from app.services.study_performance import StudyPerformanceDraft

        quiz_scores = summary["quiz"]["recent_scores"]
        exam_scores = summary["exam"]["recent_scores"]
        cards = summary["flashcards"]
        rated = cards["rated_cards"]
        flashcard_score = (
            round((100 * cards["recalled"] + 50 * cards["unsure"]) / rated, 1)
            if rated else None
        )
        return StudyPerformanceDraft(
            quiz_score=round(sum(quiz_scores) / len(quiz_scores), 1) if quiz_scores else None,
            exam_score=round(sum(exam_scores) / len(exam_scores), 1) if exam_scores else None,
            flashcard_score=flashcard_score,
            explanation="Recent scores and practice ratings are summarized as a study aid.",
        )


class StudyPerformanceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        settings = Settings(
            _env_file=None,
            gemini_api_key="test-key-not-used",
            file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
            sqlite_path=":memory:",
            storage_dir=self.storage.name,
            signup_mode="open",
            lean_tool_enabled=False,
        )
        self.client = TestClient(create_app(settings_override=settings), headers={"origin": ORIGIN})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        signed_up = self.client.post(
            "/api/auth/signup", json={"username": "Score_Student", "password": "score-test-pass"}
        )
        self.assertEqual(signed_up.status_code, 201, signed_up.text)
        self.owner_id = signed_up.json()["user"]["id"]
        self.class_id = self.client.post("/api/classes", json={"name": "Biology"}).json()["id"]
        self.db = self.client.app.state.database
        self.scorer = FakeScoringService()
        self.client.app.state.study_performance_scoring_service = self.scorer
        self.quiz_id = self.add_material("quiz", "Membranes quiz", {"questions": []})
        self.exam_id = self.add_material("practice_exam", "Cell exam", {"questions": []})
        self.cards_id = self.add_material(
            "flashcards", "Osmosis cards", {"cards": [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]}
        )
        self.add_run(self.quiz_id, 80)
        self.add_run(self.quiz_id, 60)
        self.add_run(self.exam_id, 90)
        self.add_card_rating(self.cards_id, "c1", "recalled")
        self.add_card_rating(self.cards_id, "c2", "unsure")
        self.add_card_rating(self.cards_id, "c3", "missed")

    def call_db(self, method: str, *args):
        return self.client.portal.call(getattr(self.db, method), *args)

    def add_material(self, kind: str, title: str, content: dict) -> str:
        material_id = str(uuid4())
        self.call_db(
            "execute",
            "INSERT INTO study_materials "
            "(id,origin_class_id,owner_id,created_by,resource_scope,kind,title,status,content) "
            "VALUES (?1,?2,?3,?3,'personal',?4,?5,'ready',?6)",
            material_id,
            self.class_id,
            self.owner_id,
            kind,
            title,
            json.dumps(content),
        )
        return material_id

    def add_run(
        self,
        material_id: str,
        score: int,
        *,
        completed_at: str = "2026-10-18T12:00:00Z",
        release_policy: str = "after_completion",
    ) -> None:
        self.call_db(
            "execute",
            "INSERT INTO quiz_runs "
            "(id,material_id,owner_id,operation_id,status,score_percent,completed_at,"
            "feedback_release_policy) "
            "VALUES (?1,?2,?3,?4,'completed',?5,?6,?7)",
            str(uuid4()),
            material_id,
            self.owner_id,
            str(uuid4()),
            score,
            completed_at,
            release_policy,
        )

    def add_card_rating(self, material_id: str, card_id: str, rating: str) -> None:
        self.call_db(
            "execute",
            "INSERT INTO flashcard_review_state "
            "(owner_id,material_id,card_id,last_rating,next_due_at,reviewed_at,review_count) "
            "VALUES (?1,?2,?3,?4,'2099-01-01T00:00:00Z','2026-10-18T12:00:00Z',1)",
            self.owner_id,
            material_id,
            card_id,
            rating,
        )

    def test_scores_include_released_assessments_and_card_ratings_only(self) -> None:
        response = self.client.post(
            f"/api/study-materials/{self.quiz_id}/performance-score"
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("no-store", response.headers["cache-control"])
        score = StudyPerformanceScoreResponse.model_validate(response.json())
        self.assertEqual(score.quiz_score, 70)
        self.assertEqual(score.exam_score, 90)
        self.assertEqual(score.flashcard_score, 50)
        self.assertEqual(score.quiz_weight, 0.45)
        self.assertEqual(score.exam_weight, 0.45)
        self.assertEqual(score.flashcard_weight, 0.1)
        self.assertEqual(score.overall_score, 77)
        self.assertEqual(score.quiz_attempts, 2)
        self.assertEqual(score.exam_attempts, 1)
        self.assertEqual(score.flashcards_rated, 3)
        self.assertEqual(self.scorer.usage_contexts[0]["operation_id"], self.quiz_id)
        self.assertEqual(self.scorer.usage_contexts[0]["operation_kind"], "study_performance_score")
        sent = json.dumps(self.scorer.calls[0]["summary"])
        self.assertNotIn("Osmosis", sent)
        self.assertNotIn("Membranes", sent)
        self.assertNotIn("answers", sent)

    def test_same_evidence_is_cached_and_does_not_call_the_model_twice(self) -> None:
        route = f"/api/study-materials/{self.exam_id}/performance-score"
        first = self.client.post(route)
        second = self.client.post(route)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(len(self.scorer.calls), 1)

    def test_each_category_uses_its_own_twenty_most_recent_released_runs(self) -> None:
        self.call_db("execute", "DELETE FROM quiz_runs WHERE owner_id=?1", self.owner_id)
        for index in range(25):
            completed_at = f"2026-10-{index + 1:02d}T12:00:00Z"
            self.add_run(self.quiz_id, index, completed_at=completed_at)
            self.add_run(self.exam_id, 100 - index, completed_at=completed_at)
        self.add_run(self.quiz_id, 100, release_policy="never")
        self.add_run(self.exam_id, 100, release_policy="manual")

        response = self.client.post(f"/api/study-materials/{self.quiz_id}/performance-score")

        self.assertEqual(response.status_code, 200, response.text)
        summary = self.scorer.calls[0]["summary"]
        self.assertEqual(summary["quiz"]["attempts"], 20)
        self.assertEqual(summary["exam"]["attempts"], 20)
        self.assertEqual(
            summary["quiz"]["recent_scores"],
            list(map(float, range(24, 4, -1))),
        )
        self.assertEqual(
            summary["exam"]["recent_scores"],
            list(map(float, range(76, 96))),
        )

    def test_flashcard_only_signal_does_not_create_a_composite_score(self) -> None:
        self.call_db("execute", "DELETE FROM quiz_runs WHERE owner_id=?1", self.owner_id)
        response = self.client.post(
            f"/api/study-materials/{self.cards_id}/performance-score"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["overall_score"])
        self.assertEqual(response.json()["flashcard_weight"], 0)

    def test_score_history_reset_is_owner_and_class_scoped(self) -> None:
        route = f"/api/study-materials/{self.quiz_id}/performance-score"
        self.assertEqual(self.client.post(route).status_code, 200)
        reset = self.client.delete(route)
        self.assertEqual(reset.status_code, 204)
        self.assertIn("no-store", reset.headers["cache-control"])
        self.assertEqual(
            self.call_db(
                "fetchval",
                "SELECT count(*) FROM study_performance_scores WHERE owner_id=?1",
                self.owner_id,
            ),
            0,
        )

    def test_score_requests_are_rate_limited_per_owner(self) -> None:
        self.client.app.state.study_performance_rate_limiter = InMemoryRateLimiter(1, 60)
        route = f"/api/study-materials/{self.quiz_id}/performance-score"
        self.assertEqual(self.client.post(route).status_code, 200)
        self.add_run(self.quiz_id, 85, completed_at="2026-10-18T13:00:00Z")
        limited = self.client.post(route)
        self.assertEqual(limited.status_code, 429, limited.text)
        self.assertEqual(limited.json()["code"], "study_score_rate_limited")


class StudyPerformanceServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_composite_weights_keep_flashcard_practice_secondary(self) -> None:
        quiz_only = {
            "quiz": {"attempts": 2},
            "exam": {"attempts": 0},
            "flashcards": {"rated_cards": 4},
        }
        weights = _weights(
            quiz_only,
            {"quiz_score": 80, "exam_score": None, "flashcard_score": 50},
        )
        self.assertEqual(weights, (0.9, 0.0, 0.1, 77.0))

        without_flashcards = {
            "quiz": {"attempts": 2},
            "exam": {"attempts": 1},
            "flashcards": {"rated_cards": 0},
        }
        weights = _weights(
            without_flashcards,
            {"quiz_score": 80, "exam_score": 90, "flashcard_score": None},
        )
        self.assertEqual(weights, (0.5, 0.5, 0.0, 85.0))

    async def test_provider_contract_is_bounded_and_rejects_missing_category_scores(self) -> None:
        class FakeModels:
            async def generate_content(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(
                    text='{"quiz_score":80,"exam_score":null,"flashcard_score":null,'
                    '"explanation":"The recent quiz result was strong."}'
                )

        class FakeClient:
            def __init__(self) -> None:
                self.aio = SimpleNamespace(models=FakeModels())

        client = FakeClient()
        service = StudyPerformanceScoringService(client)
        summary = {
            "quiz": {"attempts": 1, "question_count": 10, "recent_scores": [80]},
            "exam": {"attempts": 0, "question_count": 0, "recent_scores": []},
            "flashcards": {"decks": 0, "rated_cards": 0, "recalled": 0, "unsure": 0, "missed": 0},
            "scoring_rule": {"quiz_weight": 0.45, "exam_weight": 0.45, "flashcard_weight": 0.1},
        }
        draft = await service.score(summary, request_id="test")
        self.assertEqual(draft.quiz_score, 80)
        config = client.aio.models.kwargs["config"]
        self.assertEqual(config.temperature, 0)
        self.assertEqual(config.max_output_tokens, 512)

        class InvalidModels:
            async def generate_content(self, **_kwargs):
                return SimpleNamespace(
                    text='{"quiz_score":80,"exam_score":72,"flashcard_score":null,'
                    '"explanation":"A score."}'
                )

        service = StudyPerformanceScoringService(
            SimpleNamespace(aio=SimpleNamespace(models=InvalidModels()))
        )
        with self.assertRaises(ApiError):
            await service.score(summary, request_id="invalid")

        class ImplausibleModels:
            async def generate_content(self, **_kwargs):
                return SimpleNamespace(
                    text='{"quiz_score":20,"exam_score":null,"flashcard_score":null,'
                    '"explanation":"A score."}'
                )

        service = StudyPerformanceScoringService(
            SimpleNamespace(aio=SimpleNamespace(models=ImplausibleModels()))
        )
        with self.assertRaises(ApiError):
            await service.score(summary, request_id="inconsistent")


if __name__ == "__main__":
    unittest.main()
