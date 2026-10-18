"""Guide chat privacy, evidence authorization, and mini-quiz receipt tests."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.class_library import _process_upload_job, _public_material_content
from app.api.guide_chat import _record_guide_check, _reference_from_chunk
from app.config import Settings
from app.main import create_app
from app.services.study_files import render_guide_latex

ORIGIN = "http://localhost:5173"
SOURCE_TEXT = (
    "Osmosis is the net movement of water across a selectively permeable membrane "
    "from higher water potential toward lower water potential."
)


class FakeContentService:
    def process_text(self, text: str, _kind: str) -> dict:
        return {
            "extracted_text": text,
            "summary": "summary",
            "key_topics": ["osmosis"],
            "transcript_segments": [],
            "duration_seconds": None,
            "syllabus_outline": None,
        }


class CapturingGuideChat:
    def __init__(self) -> None:
        self.context = None
        self.history = None
        self.message = None
        self.intent = None
        self.usage_context = None

    async def reply(self, context, history, message, *, intent: str, request_id: str):
        del request_id
        from app.services.ai_usage_ledger import usage_context_values

        self.usage_context = usage_context_values()
        self.context = context
        self.history = history
        self.message = message
        self.intent = intent
        from app.services.guide_chat import _GuideChatDraft

        return _GuideChatDraft(
            reply="Water moves down its water-potential gradient.",
            practice_question=(
                "Predict the net water movement when the outside solution has lower water potential."
                if intent == "practice" else None
            ),
        )


class GuideChatApiTests(unittest.TestCase):
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
            "/api/auth/signup",
            json={"username": "Guide_Student", "password": "guide-test-pass"},
        )
        self.assertEqual(signed_up.status_code, 201, signed_up.text)
        self.user_id = signed_up.json()["user"]["id"]
        self.class_id = self.client.post(
            "/api/classes", json={"name": "Biology Source"}
        ).json()["id"]
        self.database = self.client.app.state.database
        self.client.app.state.gemini_content_service = FakeContentService()
        self.guide_chat = CapturingGuideChat()
        self.client.app.state.guide_chat_service = self.guide_chat

        self.note_id = str(uuid4())
        self.call_db(
            "execute",
            "INSERT INTO course_notes "
            "(id,class_id,created_by,publication_state,title,kind,source_type,extracted_text,status) "
            "VALUES (?1,?2,?3,'published','Membrane lecture','document','pasted_text',?4,'processing')",
            self.note_id,
            self.class_id,
            self.user_id,
            SOURCE_TEXT,
        )
        self.client.portal.call(_process_upload_job, self.client.app, UUID(self.note_id))

        self.material_id = str(uuid4())
        self.quiz = {
            "question": "Which direction describes net water movement in osmosis?",
            "choices": ["Toward lower water potential", "Toward higher water potential", "No movement", "Only with ATP"],
            "correct_index": 0,
            "explanation": "Water moves down its water-potential gradient.",
        }
        content = {
            "format": "study_guide",
            "schema_version": 2,
            "sections": [{
                "id": "osmosis",
                "heading": "Osmosis",
                "body": SOURCE_TEXT,
                "key_terms": ["water potential"],
                "sources": [{"upload_id": self.note_id}],
                "blocks": [{
                    "id": "osmosis-explainer",
                    "type": "paragraph",
                    "text": SOURCE_TEXT,
                    "sources": [{"upload_id": self.note_id}],
                }],
                "mini_quiz": self.quiz,
            }],
        }
        self.guide_json = json.dumps(content)
        self.call_db(
            "execute",
            "INSERT INTO study_materials "
            "(id,origin_class_id,created_by,owner_id,resource_scope,kind,title,status,content) "
            "VALUES (?1,?2,?3,?3,'personal','study_guide','Membrane guide','ready',?4)",
            self.material_id,
            self.class_id,
            self.user_id,
            self.guide_json,
        )
        self.call_db(
            "execute",
            "INSERT INTO study_material_sources "
            "(material_id,source_class_id,source_note_id,source_title,position) "
            "VALUES (?1,?2,?3,'Membrane lecture',0)",
            self.material_id,
            self.class_id,
            self.note_id,
        )

    def call_db(self, method: str, *args):
        return self.client.portal.call(getattr(self.database, method), *args)

    def post_check(self, operation_id: str, choice_index: int):
        return self.client.post(
            f"/api/study-materials/{self.material_id}/guide-sections/0/mini-quiz",
            json={"operation_id": operation_id, "choice_index": choice_index},
        )

    def test_chat_loads_saved_guide_and_current_authorized_evidence_transiently(self) -> None:
        response = self.client.post(
            f"/api/study-materials/{self.material_id}/guide-chat",
            json={
                "section_index": 0,
                "scope": "section",
                "intent": "practice",
                "message": "Help me check my understanding.",
                "history": [
                    {"role": "user", "text": "Earlier private attempt text."},
                    {"role": "assistant", "text": "Earlier tutor reply."},
                ],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.guide_chat.usage_context["operation_id"], self.material_id)
        self.assertEqual(self.guide_chat.usage_context["operation_kind"], "study_guide_chat")
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertIn("Osmosis", self.guide_chat.context["guide_reference_text"])
        source_context = json.dumps(self.guide_chat.context["course_source_excerpts"])
        self.assertIn("selectively permeable membrane", source_context)
        self.assertNotIn("correct_index", json.dumps(self.guide_chat.context))
        self.assertNotIn("water moves down its water-potential gradient.", self.guide_chat.context["guide_reference_text"])
        self.assertEqual(self.guide_chat.message, "Help me check my understanding.")
        self.assertEqual(self.guide_chat.intent, "practice")
        self.assertEqual(response.json()["practice_question"]["references"][0]["upload_id"], self.note_id)
        self.assertIsNone(response.json()["references"][0].get("page"))
        self.assertEqual(
            self.call_db("fetchval", "SELECT count(*) FROM guide_section_review_state"), 0
        )
        self.assertEqual(
            self.call_db("fetchval", "SELECT count(*) FROM guide_section_review_receipts"), 0
        )

    def test_retrieved_reference_uses_only_a_real_source_chunk_locator(self) -> None:
        paged = _reference_from_chunk({
            "material_id": self.note_id,
            "title": "Lecture",
            "chunk_locator": {"page": 3},
        })
        self.assertIsNotNone(paged)
        self.assertEqual(paged.page, 3)

        unpaged = _reference_from_chunk({
            "material_id": self.note_id,
            "title": "Pasted class notes",
            "chunk_locator": {},
        })
        self.assertIsNotNone(unpaged)
        self.assertIsNone(unpaged.page)

    def test_guide_detail_and_latex_do_not_expose_mini_quiz_key(self) -> None:
        public = _public_material_content("study_guide", self.guide_json)
        quiz = public["sections"][0]["mini_quiz"]
        self.assertEqual(quiz["question"], self.quiz["question"])
        self.assertEqual(quiz["choices"], self.quiz["choices"])
        self.assertNotIn("correct_index", quiz)
        self.assertNotIn("explanation", quiz)

        detail = self.client.get(f"/api/study-materials/{self.material_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        detail_quiz = detail.json()["content"]["sections"][0]["mini_quiz"]
        self.assertNotIn("correct_index", detail_quiz)
        self.assertNotIn("explanation", detail_quiz)

        latex = render_guide_latex(
            title="Membrane guide",
            class_name="Biology Source",
            material_id=UUID(self.material_id),
            content=json.loads(self.guide_json),
            sources=[{"id": self.note_id, "title": "Membrane lecture"}],
        )
        self.assertIn("Check your understanding", latex)
        self.assertIn(self.quiz["question"], latex)
        self.assertNotIn("Mini-quiz answer key", latex)
        self.assertNotIn("\textbf{Answer:}", latex)
        self.assertNotIn("Water moves down its water-potential gradient.", latex)

    def test_mini_quiz_retry_replays_receipt_and_progress_clear_removes_receipts(self) -> None:
        operation_id = str(uuid4())
        first = self.post_check(operation_id, 0)
        replay = self.post_check(operation_id, 1)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(first.json()["correct"])
        self.assertEqual(first.json(), replay.json())
        self.assertEqual(first.json()["attempt_count"], 1)
        self.assertFalse(first.json()["mastery_updated"])
        self.assertIn("no-store", first.headers["cache-control"])

        second = self.post_check(str(uuid4()), 1)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertFalse(second.json()["correct"])
        self.assertEqual(second.json()["attempt_count"], 2)

        progress = self.client.get(f"/api/study-materials/{self.material_id}/guide-progress")
        self.assertEqual(progress.status_code, 200, progress.text)
        self.assertEqual(progress.json()["sections"][0]["attempt_count"], 2)
        self.assertFalse(progress.json()["sections"][0]["last_correct"])

        columns = {
            row["name"]
            for row in self.call_db("fetch", "PRAGMA table_info(guide_section_review_receipts)")
        }
        self.assertNotIn("choice_index", columns)
        self.assertNotIn("answer", columns)
        cleared = self.client.delete(f"/api/study-materials/{self.material_id}/guide-progress")
        self.assertEqual(cleared.status_code, 204)
        self.assertEqual(
            self.call_db("fetchval", "SELECT count(*) FROM guide_section_review_receipts"), 0
        )
        self.assertEqual(
            self.call_db("fetchval", "SELECT count(*) FROM guide_section_review_state"), 0
        )

    def test_concurrent_same_operation_id_creates_one_attempt(self) -> None:
        operation_id = uuid4()
        section_id = "0:osmosis"
        fingerprint = "a" * 64
        args = {
            "owner_id": UUID(self.user_id),
            "material_id": UUID(self.material_id),
            "section_id": section_id,
            "operation_id": operation_id,
            "content_fingerprint": fingerprint,
            "correct": True,
            "attempted_at": "2026-10-18T12:00:00Z",
            "next_due_at": "2026-10-19T12:00:00Z",
        }

        async def submit_twice():
            return await asyncio.gather(
                _record_guide_check(self.database, **args),
                _record_guide_check(self.database, **args),
            )

        results = self.client.portal.call(submit_twice)
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]["attempt_count"], 1)
        self.assertEqual(
            self.call_db("fetchval", "SELECT count(*) FROM guide_section_review_receipts"), 1
        )
        state = self.call_db(
            "fetchrow",
            "SELECT attempt_count,last_correct FROM guide_section_review_state "
            "WHERE owner_id=?1 AND material_id=?2 AND section_id=?3",
            self.user_id,
            self.material_id,
            section_id,
        )
        self.assertEqual(state, {"attempt_count": 1, "last_correct": 1})

    def test_progress_and_check_are_owner_scoped(self) -> None:
        other = TestClient(self.client.app, headers={"origin": ORIGIN})
        with other:
            signup = other.post(
                "/api/auth/signup",
                json={"username": "Other_Student", "password": "other-test-pass"},
            )
            self.assertEqual(signup.status_code, 201, signup.text)
            self.assertEqual(
                other.get(f"/api/study-materials/{self.material_id}/guide-progress").status_code,
                404,
            )
            self.assertEqual(
                other.post(
                    f"/api/study-materials/{self.material_id}/guide-sections/0/mini-quiz",
                    json={"operation_id": str(uuid4()), "choice_index": 0},
                ).status_code,
                404,
            )


if __name__ == "__main__":
    unittest.main()
