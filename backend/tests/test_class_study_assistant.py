"""Private class assistant authorization, grounding, and side-effect tests."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.class_study_assistant import (
    _looks_like_event_declaration,
    _topic_query,
)
from app.config import Settings
from app.main import create_app
from app.services.class_study_assistant import ClassAssistantDraft

ORIGIN = "http://localhost:5173"


class FakeContentService:
    def process_text(self, text: str, _kind: str) -> dict:
        return {
            "extracted_text": text,
            "summary": "summary",
            "key_topics": [],
            "transcript_segments": [],
            "duration_seconds": None,
            "syllabus_outline": None,
        }


class FakeClassAssistant:
    def __init__(self, drafts: list[ClassAssistantDraft] | None = None) -> None:
        self.drafts = list(drafts or [])
        self.calls: list[dict] = []

    async def reply(self, context, history, message, *, request_id: str):
        self.calls.append(
            {
                "context": context,
                "history": history,
                "message": message,
                "request_id": request_id,
            }
        )
        return (
            self.drafts.pop(0)
            if self.drafts
            else ClassAssistantDraft(reply="Let's review what you have done.")
        )


class ClassStudyAssistantTests(unittest.TestCase):
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
        self.owner = self.signup("Assistant_Owner")
        class_response = self.client.post("/api/classes", json={"name": "Biology"})
        self.assertEqual(class_response.status_code, 201, class_response.text)
        self.class_id = class_response.json()["id"]
        self.db = self.client.app.state.database
        self.fake = FakeClassAssistant()
        self.client.app.state.class_study_assistant_service = self.fake
        self.client.app.state.gemini_content_service = FakeContentService()

    def signup(self, username: str) -> dict:
        response = self.client.post(
            "/api/auth/signup", json={"username": username, "password": "assistant-test-pass"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["user"]

    def create_user_client(self, username: str) -> tuple[dict, TestClient]:
        client = TestClient(self.client.app, headers={"origin": ORIGIN})
        response = client.post(
            "/api/auth/signup", json={"username": username, "password": "assistant-test-pass"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["user"], client

    def call_db(self, method: str, *args):
        return self.client.portal.call(getattr(self.db, method), *args)

    def add_private_material(self, owner_id: str, kind: str, title: str) -> str:
        material_id = str(uuid4())
        self.call_db(
            "execute",
            "INSERT INTO study_materials "
            "(id,origin_class_id,owner_id,created_by,resource_scope,kind,title,status,content) "
            "VALUES (?1,?2,?3,?3,'personal',?4,?5,'ready','{}')",
            material_id,
            self.class_id,
            owner_id,
            kind,
            title,
        )
        return material_id

    def add_run(
        self, owner_id: str, material_id: str, score: int, policy: str = "after_completion"
    ) -> None:
        self.call_db(
            "execute",
            "INSERT INTO quiz_runs "
            "(id,material_id,owner_id,operation_id,status,score_percent,completed_at,"
            "feedback_release_policy) "
            "VALUES (?1,?2,?3,?4,'completed',?5,'2026-10-18T12:00:00Z',?6)",
            str(uuid4()),
            material_id,
            owner_id,
            str(uuid4()),
            score,
            policy,
        )

    def seed_source(self, text: str, title: str = "Biology notes") -> str:
        note_id = str(uuid4())
        self.call_db(
            "execute",
            "INSERT INTO course_notes "
            "(id,class_id,created_by,publication_state,title,kind,source_type,"
            "extracted_text,status) "
            "VALUES (?1,?2,?3,'published',?4,'document','pasted_text',?5,'processing')",
            note_id,
            self.class_id,
            self.owner["id"],
            title,
            text,
        )
        with patch("app.api.class_library.create_default_document", new_callable=AsyncMock):
            from app.api.class_library import _process_upload_job

            self.client.portal.call(_process_upload_job, self.client.app, UUID(note_id))
        return note_id

    def post_chat(self, message: str, history: list[dict] | None = None, *, client=None):
        return (client or self.client).post(
            f"/api/classes/{self.class_id}/study-assistant",
            json={"message": message, "history": history or []},
        )

    def test_performance_context_contains_only_requester_released_data(self) -> None:
        owner_quiz = self.add_private_material(self.owner["id"], "quiz", "Cell quiz")
        self.add_run(self.owner["id"], owner_quiz, 82)
        self.call_db(
            "execute",
            "INSERT INTO study_performance_scores "
            "(id,owner_id,class_id,triggering_material_id,input_digest,status,overall_score,"
            "evidence_level,explanation,prompt_version) "
            "VALUES (?1,?2,?3,?4,?5,'ready',78,'moderate','Recent practice is improving.',?6)",
            str(uuid4()),
            self.owner["id"],
            self.class_id,
            owner_quiz,
            "a" * 64,
            "test-v1",
        )
        other, _other_client = self.create_user_client("Assistant_Other")
        other_quiz = self.add_private_material(other["id"], "quiz", "Private other quiz")
        self.add_run(other["id"], other_quiz, 12)
        self.call_db(
            "execute",
            "INSERT INTO study_performance_scores "
            "(id,owner_id,class_id,triggering_material_id,input_digest,status,overall_score,"
            "evidence_level,explanation,prompt_version) "
            "VALUES (?1,?2,?3,?4,?5,'ready',5,'high','Private other estimate.',?6)",
            str(uuid4()),
            other["id"],
            self.class_id,
            other_quiz,
            "b" * 64,
            "test-v1",
        )
        hidden_quiz = self.add_private_material(self.owner["id"], "quiz", "Hidden quiz")
        self.add_run(self.owner["id"], hidden_quiz, 99, policy="never")

        response = self.post_chat("How am I doing?")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get("cache-control"), "private, no-store")
        context = self.fake.calls[0]["context"]["performance"]
        self.assertEqual(context["quiz_exam_summary"]["quiz"]["average_percent"], 82)
        self.assertEqual(context["previous_ai_study_estimates"][0]["overall_score"], 78)
        encoded = str(context)
        self.assertNotIn("Private other quiz", encoded)
        self.assertNotIn("Private other estimate", encoded)
        self.assertNotIn("Hidden quiz", encoded)
        self.assertNotIn("12.0", encoded)
        self.assertNotIn("99.0", encoded)

    def test_exam_date_is_saved_privately_and_duplicate_request_is_idempotent(self) -> None:
        upcoming = date.today() + timedelta(days=5)
        draft = ClassAssistantDraft(
            reply="Added your exam.",
            action="add_event",
            event_kind="exam",
            event_title="Midterm",
            event_date=upcoming,
        )
        self.fake.drafts = [draft, draft]

        declaration = f"Exam on {upcoming.strftime('%B')} {upcoming.day}"
        first = self.post_chat(declaration)
        second = self.post_chat(declaration)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["saved_event"]["id"], second.json()["saved_event"]["id"])
        listed = self.client.get(f"/api/classes/{self.class_id}/study-assistant/events")
        self.assertEqual(len(listed.json()["events"]), 1)
        member, member_client = self.create_user_client("Assistant_Reader")
        invitation = self.client.post(
            f"/api/classes/{self.class_id}/invitations",
            json={"capabilities": ["read"], "max_uses": 1, "expires_in_days": 7},
        )
        self.assertEqual(invitation.status_code, 201, invitation.text)
        joined = member_client.post(
            "/api/class-invitations/redeem", json={"code": invitation.json()["code"]}
        )
        self.assertEqual(joined.status_code, 200, joined.text)
        hidden = member_client.get(f"/api/classes/{self.class_id}/study-assistant/events")
        self.assertEqual(hidden.status_code, 200, hidden.text)
        self.assertEqual(hidden.json()["events"], [])
        deleted = member_client.delete(
            f"/api/classes/{self.class_id}/study-assistant/events/{first.json()['saved_event']['id']}"
        )
        self.assertEqual(deleted.status_code, 404)

    def test_non_member_cannot_use_the_assistant_or_read_its_reminders(self) -> None:
        _member, member_client = self.create_user_client("Assistant_Nonmember")

        chat = self.post_chat("How am I doing?", client=member_client)
        events = member_client.get(f"/api/classes/{self.class_id}/study-assistant/events")

        self.assertEqual(chat.status_code, 404)
        self.assertEqual(events.status_code, 404)
        self.assertEqual(self.fake.calls, [])

    def test_event_action_is_not_executed_for_a_question_about_a_date(self) -> None:
        self.fake.drafts = [
            ClassAssistantDraft(
                reply="I think it is October 19.",
                action="add_event",
                event_kind="exam",
                event_title="Exam",
                event_date=date.today() + timedelta(days=1),
            )
        ]

        response = self.post_chat("When is the exam on Oct 19?")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["saved_event"])
        self.assertEqual(
            self.call_db("fetchval", "SELECT count(*) FROM class_study_assistant_events"), 0
        )
        self.assertIn("haven't saved", response.json()["reply"])

    def test_material_action_is_limited_to_retrieved_authorized_sources(self) -> None:
        source_id = self.seed_source(
            "Cell membranes regulate transport. Osmosis is water movement across a "
            "selectively permeable membrane."
        )
        self.fake.drafts = [
            ClassAssistantDraft(
                reply="I found the osmosis notes and started your guide.",
                action="create_materials",
                material_topic="osmosis",
                material_kinds=["study_guide", "quiz"],
                source_upload_ids=[source_id],
                count=8,
            )
        ]

        response = self.post_chat("Create a study guide and quiz about osmosis")

        self.assertEqual(response.status_code, 200, response.text)
        request = response.json()["material_request"]
        self.assertIsNotNone(request, response.json())
        self.assertEqual(request["upload_ids"], [source_id])
        self.assertEqual([source["title"] for source in request["sources"]], ["Biology notes"])
        self.assertEqual(request["kinds"], ["study_guide", "quiz"])
        self.assertEqual(
            self.fake.calls[0]["context"]["source_excerpts"][0]["upload_id"], source_id
        )

    def test_material_action_with_forged_source_id_fails_closed(self) -> None:
        self.seed_source("Osmosis moves water across a selectively permeable membrane.")
        self.fake.drafts = [
            ClassAssistantDraft(
                reply="Starting a guide.",
                action="create_materials",
                material_topic="osmosis",
                material_kinds=["study_guide"],
                source_upload_ids=[str(uuid4())],
                count=8,
            )
        ]

        response = self.post_chat("Create a study guide about osmosis")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["material_request"])
        self.assertIn("didn't start generation", response.json()["reply"])

    def test_material_action_cannot_add_a_type_the_user_did_not_request(self) -> None:
        source_id = self.seed_source("Osmosis moves water across a selectively permeable membrane.")
        self.fake.drafts = [
            ClassAssistantDraft(
                reply="I also made a quiz for you.",
                action="create_materials",
                material_topic="osmosis",
                material_kinds=["quiz"],
                source_upload_ids=[source_id],
                count=8,
            )
        ]

        response = self.post_chat("Create a study guide about osmosis")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["material_request"])
        self.assertIn("only create the material types you asked for", response.json()["reply"])

    def test_chat_history_is_bounded_and_never_persisted(self) -> None:
        malformed = [{"role": "assistant", "text": "out of order"}]
        response = self.post_chat("Question", malformed)
        self.assertEqual(response.status_code, 400)
        response = self.post_chat(
            "What should I study next?",
            [
                {"role": "user", "text": "I struggle with membranes."},
                {"role": "assistant", "text": "Let's start with transport."},
            ],
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.fake.calls[-1]["history"][0].text, "I struggle with membranes.")
        tables = self.call_db(
            "fetch",
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%assistant%chat%'",
        )
        self.assertEqual(tables, [])

    def test_event_declaration_and_topic_query_helpers(self) -> None:
        self.assertTrue(_looks_like_event_declaration("Exam on Oct 19th"))
        self.assertFalse(_looks_like_event_declaration("When is the exam on Oct 19?"))
        self.assertEqual(
            _topic_query("Create a study guide about cellular respiration"), "cellular respiration"
        )

    def test_typed_actions_reject_side_effect_fields_for_other_action_types(self) -> None:
        with self.assertRaises(ValidationError):
            ClassAssistantDraft(reply="Okay.", action="none", event_title="Exam")
        with self.assertRaises(ValidationError):
            ClassAssistantDraft(
                reply="Starting.",
                action="create_materials",
                material_topic="osmosis",
                material_kinds=["study_guide"],
                source_upload_ids=[str(uuid4()), str(uuid4())],
                event_date=date.today() + timedelta(days=1),
            )


if __name__ == "__main__":
    unittest.main()
