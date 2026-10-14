"""Study tutor review must use the exact source revisions pinned by a quiz run."""

from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.class_library import _process_upload_job
from app.api.schemas import StudyTutorResponse
from app.config import Settings
from app.main import create_app

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


class CapturingStudyTutor:
    def __init__(self) -> None:
        self.context = None

    async def reply(self, context, _messages, *, request_id: str) -> StudyTutorResponse:
        self.context = context
        return StudyTutorResponse(reply="Compare the source with your answer.")


class StudyTutorProvenanceTests(unittest.TestCase):
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
        signup = self.client.post(
            "/api/auth/signup",
            json={"username": "Provenance_Student", "password": "source-test-pass"},
        )
        self.assertEqual(signup.status_code, 201, signup.text)
        self.user_id = signup.json()["user"]["id"]
        class_response = self.client.post("/api/classes", json={"name": "Source History"})
        self.assertEqual(class_response.status_code, 201, class_response.text)
        self.class_id = class_response.json()["id"]
        self.database = self.client.app.state.database
        self.client.app.state.gemini_content_service = FakeContentService()

    def call_db(self, method: str, *args):
        return self.client.portal.call(getattr(self.database, method), *args)

    def test_review_uses_run_pinned_source_revision_after_reprocessing(self) -> None:
        old_text = "Old source revision contains stable wording for the original quiz."
        new_text = "New replacement text must never enter the historical review context."
        note_id = str(uuid4())
        self.call_db(
            "execute",
            "INSERT INTO course_notes "
            "(id,class_id,created_by,publication_state,title,kind,source_type,"
            "extracted_text,status) "
            "VALUES (?1,?2,?3,'published','Review source','document','pasted_text',"
            "?4,'processing')",
            note_id,
            self.class_id,
            self.user_id,
            old_text,
        )
        with patch("app.api.class_library.create_default_document", new_callable=AsyncMock):
            self.client.portal.call(_process_upload_job, self.client.app, UUID(note_id))
        old_chunk_id = self.call_db(
            "fetchval",
            "SELECT cr.id FROM source_index_heads h JOIN source_chunk_revisions cr "
            "ON cr.revision_id=h.revision_id WHERE h.note_id=?1 ORDER BY cr.chunk_order LIMIT 1",
            note_id,
        )
        self.assertIsNotNone(old_chunk_id)

        material_id = str(uuid4())
        question = {
            "id": "source-question",
            "kind": "short_answer",
            "prompt": "What wording did the original source use?",
            "answer": "original wording",
            "accepted_answers": [],
            "explanation": "The source used the original wording.",
            "source_chunk_revision_ids": [old_chunk_id],
            "sources": [{"upload_id": note_id}],
        }
        self.call_db(
            "execute",
            "INSERT INTO study_materials "
            "(id,origin_class_id,created_by,owner_id,resource_scope,kind,title,status,content,"
            "assessment_validation_state,assessment_validation_version) "
            "VALUES (?1,?2,?3,?3,'personal','quiz','Historical quiz','ready',?4,'validated',"
            "'source-grounded-test-v1')",
            material_id,
            self.class_id,
            self.user_id,
            json.dumps({"questions": [question]}),
        )
        self.call_db(
            "execute",
            "INSERT INTO study_material_sources "
            "(material_id,source_class_id,source_note_id,source_title,position) "
            "VALUES (?1,?2,?3,'Review source',0)",
            material_id,
            self.class_id,
            note_id,
        )

        created_run = self.client.post(
            f"/api/study-materials/{material_id}/runs",
            json={"operation_id": str(uuid4()), "mode": "standard"},
        )
        self.assertEqual(created_run.status_code, 201, created_run.text)
        run = created_run.json()
        question_id = run["questions"][0]["id"]
        submitted = self.client.post(
            f"/api/study-materials/{material_id}/runs/{run['run_id']}"
            f"/questions/{question_id}/answer",
            json={
                "operation_id": str(uuid4()),
                "answer": "original wording",
                "expected_version": 0,
            },
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)

        self.call_db(
            "execute",
            "UPDATE course_notes SET extracted_text=?2,status='processing' WHERE id=?1",
            note_id,
            new_text,
        )
        processing_revision = self.call_db(
            "fetchval", "SELECT processing_revision FROM course_notes WHERE id=?1", note_id
        )
        with patch("app.api.class_library.create_default_document", new_callable=AsyncMock):
            self.client.portal.call(
                _process_upload_job,
                self.client.app,
                UUID(note_id),
                processing_revision,
            )
        current_source_text = self.call_db(
            "fetchval", "SELECT extracted_text FROM course_notes WHERE id=?1", note_id
        )
        self.assertEqual(current_source_text, new_text)

        tutor = CapturingStudyTutor()
        self.client.app.state.study_tutor_service = tutor
        review = self.client.post(
            "/api/study-tutor",
            json={
                "material_id": material_id,
                "question_id": question_id,
                "attempt_id": run["run_id"],
                "messages": [{"role": "user", "text": "Can you explain the source?"}],
            },
        )
        self.assertEqual(review.status_code, 200, review.text)
        excerpts = tutor.context["course_excerpts"]
        combined = "\n".join(item["text"] for item in excerpts)
        self.assertIn(old_text, combined)
        self.assertNotIn(new_text, combined)


if __name__ == "__main__":
    unittest.main()
