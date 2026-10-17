"""Gate 2 authorization, graph integrity, and extraction job tests."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.class_library import _process_upload_job
from app.api.schemas import ConceptGraphAIOutput
from app.config import Settings
from app.main import create_app
from app.services.auth import SESSION_COOKIE
from app.services.concept_graph import (
    decide_edge,
    normalize_concept_name,
    recover_concept_extraction_jobs,
)
from app.services.database import create_database
from app.services.gemini_content import ContentGenerationFailure

ORIGIN = "http://localhost:5173"


class FakeGraphService:
    def __init__(self) -> None:
        self.model_name = "fake-concept-model-v1"
        self.output = ConceptGraphAIOutput(concepts=[], edges=[])
        self.fail = False
        self.failure: Exception | None = None
        self.contexts: list[dict] = []

    def process_text(self, text: str, _kind: str) -> dict:
        return {
            "extracted_text": text,
            "summary": "summary",
            "key_topics": [],
            "transcript_segments": [],
            "duration_seconds": None,
            "syllabus_outline": None,
        }

    def extract_concept_graph(self, context: dict) -> ConceptGraphAIOutput:
        self.contexts.append(context)
        if self.failure is not None:
            raise self.failure
        if self.fail:
            raise RuntimeError("provider unavailable")
        return self.output


class ConceptGraphApiTests(unittest.TestCase):
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
        self.fake = FakeGraphService()
        self.client.app.state.gemini_content_service = self.fake
        self.tokens: dict[str, str] = {}
        self.users: dict[str, dict] = {}
        self.owner = self.add_user("graph_owner")
        created = self.call("post", "/api/classes", "graph_owner", json={"name": "Graph Class"})
        self.assertEqual(created.status_code, 201, created.text)
        self.class_id = created.json()["id"]
        self.note_id = self.seed_source()
        self.chunk_id = self.client.portal.call(
            self.db.fetchval,
            "SELECT id FROM source_chunk_revisions WHERE revision_id="
            "(SELECT revision_id FROM source_index_heads WHERE note_id=?1)",
            self.note_id,
        )

    def add_user(self, username: str) -> dict:
        response = self.client.post(
            "/api/auth/signup", json={"username": username, "password": "graph-test-pass"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.tokens[username] = response.cookies.get(SESSION_COOKIE)
        self.users[username] = response.json()["user"]
        return self.users[username]

    def call(self, method: str, path: str, username: str, **kwargs):
        return getattr(self.client, method)(
            path, cookies={SESSION_COOKIE: self.tokens[username]}, **kwargs
        )

    def add_reader(self, username: str) -> dict:
        self.add_user(username)
        invitation = self.call(
            "post",
            f"/api/classes/{self.class_id}/invitations",
            "graph_owner",
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
        return self.users[username]

    def seed_source(self, *, publication_state: str = "published") -> str:
        note_id = str(uuid4())
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO "
            "course_notes(id,class_id,created_by,publication_state,title,kind,source_"
            "type,"
            "extracted_text,status,processing_progress) VALUES (?1,?2,?3,?4,'Graph "
            "source','document',"
            "'pasted_text','# Biology\\nMitochondria produce ATP and support cellular "
            "respiration.',"
            "'processing',0)",
            note_id,
            self.class_id,
            self.owner["id"],
            publication_state,
        )
        with patch("app.api.class_library.create_default_document", new_callable=AsyncMock):
            self.client.portal.call(_process_upload_job, self.client.app, UUID(note_id))
        note = self.client.portal.call(
            self.db.fetchrow,
            "SELECT status,source_index_status FROM course_notes WHERE id=?1",
            note_id,
        )
        self.assertEqual(note, {"status": "ready", "source_index_status": "ready"})
        return note_id

    def create_concept(
        self, name: str, *, class_id: str | None = None, source: str | None = None
    ) -> dict:
        body = {"canonical_name": name, "description": f"Description of {name}."}
        if source is not None:
            body["source_chunk_revision_ids"] = [source]
        response = self.call(
            "post", f"/api/classes/{class_id or self.class_id}/concepts", "graph_owner", json=body
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def approve_concept(self, concept: dict) -> dict:
        response = self.call(
            "post",
            f"/api/classes/{self.class_id}/concepts/{concept['id']}/decision",
            "graph_owner",
            json={"decision": "approve", "expected_version": concept["version"]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def create_edge(self, source: dict, target: dict, *, necessity: str = "essential"):
        return self.call(
            "post",
            f"/api/classes/{self.class_id}/concept-edges",
            "graph_owner",
            json={
                "from_concept_id": source["id"],
                "to_concept_id": target["id"],
                "type": "prerequisite_for",
                "necessity": necessity,
                "provenance": "explicit",
                "explanation": "The source is required first.",
                "source_chunk_revision_ids": [self.chunk_id],
            },
        )

    def test_access_normalization_and_duplicate_alias_canonical_protection(self) -> None:
        self.add_reader("graph_reader")
        concept = self.create_concept("  Bézier   curve ")
        self.assertEqual(concept["state"], "proposed")
        self.assertIsInstance(concept["version"], int)
        self.assertEqual(
            normalize_concept_name("BÉZIER CURVE"), normalize_concept_name("Bézier curve")
        )
        duplicate = self.call(
            "post",
            f"/api/classes/{self.class_id}/concepts",
            "graph_owner",
            json={"canonical_name": "BÉZIER CURVE", "description": "A duplicate."},
        )
        self.assertEqual(duplicate.status_code, 409)
        reader_create = self.call(
            "post",
            f"/api/classes/{self.class_id}/concepts",
            "graph_reader",
            json={"canonical_name": "Unauthorized", "description": "No."},
        )
        self.assertEqual(reader_create.status_code, 403)
        outsider = self.add_user("graph_outsider")
        outsider_graph = self.call(
            "get", f"/api/classes/{self.class_id}/concept-graph", "graph_outsider"
        )
        self.assertEqual(outsider_graph.status_code, 404)
        reader_review = self.call(
            "get", f"/api/classes/{self.class_id}/concept-graph/review", "graph_reader"
        )
        self.assertEqual(reader_review.status_code, 403)
        self.assertIsNotNone(outsider)

    def test_source_citations_are_current_authorized_and_ids_are_filtered(self) -> None:
        self.add_reader("graph_reader")
        concept = self.create_concept("Cellular respiration", source=self.chunk_id)
        self.assertNotIn("source_chunk_revision_ids", concept)
        concept = self.approve_concept(concept)
        graph = self.call(
            "get", f"/api/classes/{self.class_id}/concept-graph", "graph_reader"
        ).json()
        self.assertEqual(len(graph["concepts"]), 1)
        self.assertEqual(graph["concepts"][0]["sources"][0]["chunk_revision_id"], self.chunk_id)
        self.assertEqual(graph["concepts"][0]["source_chunk_revision_ids"], [self.chunk_id])

        self.client.portal.call(
            self.db.execute,
            "UPDATE course_notes SET publication_state='draft' WHERE id=?1",
            self.note_id,
        )
        hidden = self.call(
            "get", f"/api/classes/{self.class_id}/concept-graph", "graph_reader"
        ).json()
        self.assertEqual(hidden["concepts"], [])
        manager_graph = self.call(
            "get", f"/api/classes/{self.class_id}/concept-graph", "graph_owner"
        ).json()
        self.assertTrue(manager_graph["concepts"])
        self.assertEqual(
            manager_graph["concepts"][0]["sources"][0]["chunk_revision_id"], self.chunk_id
        )

        self.client.portal.call(
            self.db.execute,
            "UPDATE course_notes SET publication_state='published' WHERE id=?1",
            self.note_id,
        )
        alias_id = str(uuid4())
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO class_concept_aliases "
            "(id,class_id,concept_id,name,normalized_name,language,status,created_by) "
            "VALUES (?1,?2,?3,'metabolic energy cycle','metabolic energy "
            "cycle','en','proposed',?4)",
            alias_id,
            self.class_id,
            concept["id"],
            self.owner["id"],
        )
        # Exact old revisions remain citable, but no longer support a current graph fact.
        current_revision = self.client.portal.call(
            self.db.fetchrow,
            "SELECT r.revision_number,r.processing_revision FROM source_revisions r "
            "JOIN source_index_heads h ON h.revision_id=r.id WHERE h.note_id=?1",
            self.note_id,
        )
        self.client.portal.call(
            self.db.execute,
            "UPDATE course_notes SET processing_revision=processing_revision+1 WHERE id=?1",
            self.note_id,
        )
        stale_revision_id = str(uuid4())
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO source_revisions "
            "(id,note_id,revision_number,processing_revision,extracted_sha256,extraction_method) "
            "VALUES (?1,?2,?3,?4,'replacement-hash','test')",
            stale_revision_id,
            self.note_id,
            current_revision["revision_number"] + 1,
            current_revision["processing_revision"] + 1,
        )
        self.client.portal.call(
            self.db.execute,
            "UPDATE source_index_heads SET revision_id=?1 WHERE note_id=?2",
            stale_revision_id,
            self.note_id,
        )
        stale_graph = self.call(
            "get", f"/api/classes/{self.class_id}/concept-graph", "graph_reader"
        ).json()
        self.assertEqual(stale_graph["concepts"], [])
        self.assertNotIn(self.chunk_id, str(stale_graph))
        alias_approval = self.call(
            "post",
            f"/api/classes/{self.class_id}/concepts/{concept['id']}/aliases/{alias_id}/decision",
            "graph_owner",
            json={"decision": "approve", "expected_version": concept["version"]},
        )
        self.assertEqual(alias_approval.status_code, 409)
        self.assertEqual(alias_approval.json()["code"], "stale_source_reference")
        self.assertEqual(concept["state"], "validated")

    def test_hidden_parent_node_identifier_is_removed_from_student_graph(self) -> None:
        self.add_reader("graph_reader")
        second_note_id = self.seed_source()
        second_chunk_id = self.client.portal.call(
            self.db.fetchval,
            "SELECT id FROM source_chunk_revisions WHERE revision_id="
            "(SELECT revision_id FROM source_index_heads WHERE note_id=?1)",
            second_note_id,
        )
        parent = self.approve_concept(
            self.create_concept("Unreleased parent", source=self.chunk_id)
        )
        child = self.create_concept("Visible child", source=second_chunk_id)
        updated = self.call(
            "patch",
            f"/api/classes/{self.class_id}/concepts/{child['id']}",
            "graph_owner",
            json={"expected_version": child["version"], "parent_concept_id": parent["id"]},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.approve_concept(updated.json())
        self.client.portal.call(
            self.db.execute,
            "UPDATE course_notes SET publication_state='draft' WHERE id=?1",
            self.note_id,
        )
        student_graph = self.call(
            "get", f"/api/classes/{self.class_id}/concept-graph", "graph_reader"
        ).json()
        self.assertEqual(len(student_graph["concepts"]), 1)
        self.assertEqual(student_graph["concepts"][0]["canonical_name"], "Visible child")
        self.assertIsNone(student_graph["concepts"][0]["parent_concept_id"])
        self.assertNotIn(parent["id"], str(student_graph))

    def test_reprocessing_hides_old_head_and_rejects_old_source_refs(self) -> None:
        self.add_reader("graph_reader")
        concept = self.approve_concept(
            self.create_concept("Source-backed concept", source=self.chunk_id)
        )
        alias_id = str(uuid4())
        self.client.portal.call(
            self.db.execute,
            "INSERT INTO class_concept_aliases "
            "(id,class_id,concept_id,name,normalized_name,language,status,created_by) "
            "VALUES (?1,?2,?3,'source-backed alias','source-backed alias','en','proposed',?4)",
            alias_id,
            self.class_id,
            concept["id"],
            self.owner["id"],
        )
        # Editing text advances processing_revision before the replacement head
        # exists, so the previous indexed revision must stop supporting facts.
        self.client.portal.call(
            self.db.execute,
            "UPDATE course_notes SET extracted_text=extracted_text || ' revised' WHERE id=?1",
            self.note_id,
        )
        note_state = self.client.portal.call(
            self.db.fetchrow,
            "SELECT source_index_status,processing_revision FROM course_notes WHERE id=?1",
            self.note_id,
        )
        self.assertEqual(note_state["source_index_status"], "ready")
        self.assertGreater(note_state["processing_revision"], 1)
        student_graph = self.call(
            "get", f"/api/classes/{self.class_id}/concept-graph", "graph_reader"
        ).json()
        self.assertEqual(student_graph["concepts"], [])

        create_with_old_ref = self.call(
            "post",
            f"/api/classes/{self.class_id}/concepts",
            "graph_owner",
            json={
                "canonical_name": "Stale support",
                "description": "This reference belongs to the previous extraction.",
                "source_chunk_revision_ids": [self.chunk_id],
            },
        )
        self.assertEqual(create_with_old_ref.status_code, 409)
        self.assertEqual(create_with_old_ref.json()["code"], "stale_source_reference")

        approve_old_alias = self.call(
            "post",
            f"/api/classes/{self.class_id}/concepts/{concept['id']}/aliases/{alias_id}/decision",
            "graph_owner",
            json={"decision": "approve", "expected_version": concept["version"]},
        )
        self.assertEqual(approve_old_alias.status_code, 409)
        self.assertEqual(approve_old_alias.json()["code"], "stale_source_reference")

    def test_edge_boundaries_duplicate_necessity_contradiction_and_cycles(self) -> None:
        left = self.approve_concept(self.create_concept("Prerequisite"))
        right = self.approve_concept(self.create_concept("Outcome"))
        first = self.create_edge(left, right)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json()["review_flags"], [])
        self.assertNotIn("source_chunk_revision_ids", first.json())
        duplicate = self.create_edge(left, right, necessity="helpful")
        self.assertEqual(duplicate.status_code, 409)
        reverse = self.create_edge(right, left)
        self.assertEqual(reverse.status_code, 201, reverse.text)
        self.assertIn("opposite_prerequisite", reverse.json()["review_flags"])
        approve_first = self.call(
            "post",
            f"/api/classes/{self.class_id}/concept-edges/{first.json()['id']}/decision",
            "graph_owner",
            json={"decision": "approve", "expected_version": 1},
        )
        self.assertEqual(approve_first.status_code, 200, approve_first.text)
        approve_reverse = self.call(
            "post",
            f"/api/classes/{self.class_id}/concept-edges/{reverse.json()['id']}/decision",
            "graph_owner",
            json={"decision": "approve", "expected_version": 1},
        )
        self.assertEqual(approve_reverse.status_code, 409)
        self.assertEqual(approve_reverse.json()["code"], "prerequisite_cycle")

        other_class = self.call(
            "post", "/api/classes", "graph_owner", json={"name": "Other graph"}
        ).json()
        foreign = self.create_concept("Foreign concept", class_id=other_class["id"])
        cross = self.create_edge(left, foreign)
        self.assertEqual(cross.status_code, 400)
        self.assertEqual(cross.json()["code"], "cross_class_concept")
        self_edge = self.create_edge(left, left)
        self.assertEqual(self_edge.status_code, 400)
        self.assertEqual(self_edge.json()["code"], "self_edge")

    def test_concurrent_opposite_approvals_cannot_create_cycle(self) -> None:
        left = self.approve_concept(self.create_concept("Concurrent A"))
        right = self.approve_concept(self.create_concept("Concurrent B"))
        edge_a = self.create_edge(left, right).json()
        edge_b = self.create_edge(right, left).json()

        async def approve_both():
            async def approve(edge):
                try:
                    return await decide_edge(
                        self.db,
                        class_id=self.class_id,
                        user_id=str(self.owner["id"]),
                        edge_id=edge["id"],
                        decision="approve",
                        expected_version=1,
                    )
                except Exception as exc:
                    return exc

            return await asyncio.gather(approve(edge_a), approve(edge_b))

        outcomes = self.client.portal.call(approve_both)
        self.assertEqual(sum(isinstance(item, dict) for item in outcomes), 1)
        errors = [item for item in outcomes if not isinstance(item, dict)]
        self.assertEqual(len(errors), 1)
        self.assertEqual(getattr(errors[0], "code", None), "prerequisite_cycle")

    def test_merge_preserves_alias_history_and_deprecation_hides_node(self) -> None:
        source = self.create_concept("Old name")
        alias_result = self.call(
            "post",
            f"/api/classes/{self.class_id}/concepts/{source['id']}/aliases",
            "graph_owner",
            json={"name": "Common name", "language": "en-US"},
        )
        self.assertEqual(alias_result.status_code, 201, alias_result.text)
        target = self.create_concept("Preferred name")
        merged = self.call(
            "post",
            f"/api/classes/{self.class_id}/concepts/{source['id']}/merge",
            "graph_owner",
            json={
                "target_concept_id": target["id"],
                "expected_version": alias_result.json()["version"],
            },
        )
        self.assertEqual(merged.status_code, 200, merged.text)
        stored_alias = self.client.portal.call(
            self.db.fetchrow,
            "SELECT concept_id,status FROM class_concept_aliases WHERE "
            "normalized_name='common name'",
        )
        self.assertEqual(stored_alias, {"concept_id": target["id"], "status": "proposed"})
        history = self.client.portal.call(
            self.db.fetchrow,
            "SELECT source_name,target_concept_id FROM class_concept_merge_history "
            "WHERE source_concept_id=?1",
            source["id"],
        )
        self.assertEqual(history["source_name"], "Old name")
        self.assertEqual(history["target_concept_id"], target["id"])
        merge_history = self.call(
            "get", f"/api/classes/{self.class_id}/concept-graph/merges", "graph_owner"
        )
        self.assertEqual(merge_history.status_code, 200, merge_history.text)
        self.assertEqual(merge_history.headers.get("cache-control"), "private, no-store")
        self.assertEqual(
            merge_history.json()["items"][0],
            {
                "source_concept_id": source["id"],
                "target_concept_id": target["id"],
                "source_name": "Old name",
                "source_description": "Description of Old name.",
                "source_version": alias_result.json()["version"],
                "merged_by": self.owner["id"],
                "created_at": merge_history.json()["items"][0]["created_at"],
                "target_name": "Preferred name",
            },
        )
        self.add_reader("merge_reader")
        unauthorized_history = self.call(
            "get", f"/api/classes/{self.class_id}/concept-graph/merges", "merge_reader"
        )
        self.assertEqual(unauthorized_history.status_code, 403)
        validated = self.approve_concept(merged.json())
        deprecated = self.call(
            "post",
            f"/api/classes/{self.class_id}/concepts/{target['id']}/decision",
            "graph_owner",
            json={"decision": "deprecate", "expected_version": validated["version"]},
        )
        self.assertEqual(deprecated.status_code, 200, deprecated.text)
        self.assertEqual(deprecated.json()["state"], "deprecated")
        self.assertEqual(
            self.call("get", f"/api/classes/{self.class_id}/concept-graph", "graph_owner").json()[
                "concepts"
            ],
            [],
        )

    def test_extraction_status_provider_failure_retry_and_recovery(self) -> None:
        current_id = self.chunk_id
        self.fake.fail = True
        response = self.call(
            "post",
            f"/api/classes/{self.class_id}/concept-graph/extractions",
            "graph_owner",
            json={"upload_id": self.note_id},
        )
        self.assertEqual(response.status_code, 202, response.text)
        operation_id = response.json()["operation_id"]
        self.client.portal.call(
            self.db.execute,
            "UPDATE class_concept_extraction_jobs SET status='failed',attempts=max_attempts,"
            "error_code='provider_or_output_failure' WHERE id=?1",
            operation_id,
        )
        retry = self.call(
            "post",
            f"/api/classes/{self.class_id}/concept-graph/extractions",
            "graph_owner",
            json={"upload_id": self.note_id},
        )
        self.assertEqual(retry.status_code, 202, retry.text)
        self.assertEqual(retry.json()["status"], "queued")
        status = self.call(
            "get",
            f"/api/classes/{self.class_id}/concept-graph/extractions/{operation_id}",
            "graph_owner",
        )
        self.assertEqual(status.status_code, 200, status.text)
        self.assertIn(status.json()["status"], {"queued", "failed"})

        self.client.portal.call(
            self.db.execute,
            "UPDATE class_concept_extraction_jobs SET status='running',attempts=1 WHERE id=?1",
            operation_id,
        )
        self.client.portal.call(recover_concept_extraction_jobs, self.db)
        recovered = self.client.portal.call(
            self.db.fetchrow,
            "SELECT status,error_code FROM class_concept_extraction_jobs WHERE id=?1",
            operation_id,
        )
        self.assertEqual(recovered["status"], "queued")
        self.assertEqual(recovered["error_code"], "extraction_interrupted")
        self.assertTrue(current_id)

    def test_extraction_failure_logs_safe_structured_diagnostics(self) -> None:
        self.fake.failure = ContentGenerationFailure(
            "private provider response must not be logged",
            code="invalid_model_response",
            diagnostic_code="schema_validation_failed",
            validation_issues=[{"type": "missing", "hint": "private detail"}],
        )
        with self.assertLogs("screen_tutor.concept_graph", level="WARNING") as captured:
            response = self.call(
                "post",
                f"/api/classes/{self.class_id}/concept-graph/extractions",
                "graph_owner",
                json={"upload_id": self.note_id},
            )

        self.assertEqual(response.status_code, 202, response.text)
        message = "\n".join(captured.output)
        self.assertIn("category=ContentGenerationFailure", message)
        self.assertIn("error_code=invalid_model_response", message)
        self.assertIn("diagnostic_code=schema_validation_failed", message)
        self.assertIn("validation_issue_count=1", message)
        self.assertNotIn("private provider response", message)
        self.assertNotIn("private detail", message)

    def test_controlled_extraction_creates_reviewable_same_class_proposals(self) -> None:
        self.add_reader("graph_reader")
        self.fake.output = ConceptGraphAIOutput.model_validate(
            {
                "concepts": [
                    {
                        "key": "cell",
                        "canonical_name": "Cellular respiration",
                        "description": "A process that releases energy from nutrients.",
                        "aliases": [{"name": "Aerobic respiration", "language": "en"}],
                        "parent_key": None,
                        "existing_concept_id": None,
                        "provenance": "explicit",
                        "source_chunk_revision_ids": [self.chunk_id],
                    },
                    {
                        "key": "atp",
                        "canonical_name": "ATP production",
                        "description": "The process produces ATP for cellular work.",
                        "aliases": [],
                        "parent_key": None,
                        "existing_concept_id": None,
                        "provenance": "inferred",
                        "source_chunk_revision_ids": [self.chunk_id],
                    },
                ],
                "edges": [
                    {
                        "from_key": "cell",
                        "to_key": "atp",
                        "type": "prerequisite_for",
                        "necessity": "helpful",
                        "provenance": "inferred",
                        "explanation": "The source connects this process to ATP production.",
                        "source_chunk_revision_ids": [self.chunk_id],
                    },
                ],
            }
        )
        queued = self.call(
            "post",
            f"/api/classes/{self.class_id}/concept-graph/extractions",
            "graph_owner",
            json={"upload_id": self.note_id},
        )
        self.assertEqual(queued.status_code, 202, queued.text)
        operation_id = queued.json()["operation_id"]
        completed = self.call(
            "get",
            f"/api/classes/{self.class_id}/concept-graph/extractions/{operation_id}",
            "graph_owner",
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(completed.json()["status"], "complete")
        self.assertEqual(
            completed.json()["prompt_version"], "concept-graph-extraction-v1"
        )
        self.assertEqual(completed.json()["model_name"], self.fake.model_name)

        context = self.fake.contexts[-1]
        supplied_chunk_ids = {chunk["chunk_revision_id"] for chunk in context["source_chunks"]}
        self.assertEqual(supplied_chunk_ids, {self.chunk_id})
        self.assertNotIn("answer_key", str(context))
        proposals = self.call(
            "get", f"/api/classes/{self.class_id}/concept-graph/review", "graph_owner"
        ).json()
        proposed = {item["canonical_name"]: item for item in proposals["concepts"]}
        self.assertEqual(set(proposed), {"Cellular respiration", "ATP production"})
        self.assertTrue(all(item["state"] == "proposed" for item in proposed.values()))
        alias = next(
            item for item in proposals["alias_proposals"] if item["name"] == "Aerobic respiration"
        )
        self.assertEqual(alias["state"], "proposed")
        self.assertEqual(alias["sources"][0]["chunk_revision_id"], self.chunk_id)

        refs = self.client.portal.call(
            self.db.fetch,
            "SELECT r.class_id,r.chunk_revision_id FROM class_concept_source_refs r "
            "WHERE r.concept_id IN (?1,?2) ORDER BY r.concept_id",
            proposed["Cellular respiration"]["id"],
            proposed["ATP production"]["id"],
        )
        self.assertEqual(len(refs), 2)
        self.assertTrue(all(item["class_id"] == self.class_id for item in refs))
        self.assertTrue(all(item["chunk_revision_id"] == self.chunk_id for item in refs))

        cell = self.approve_concept(proposed["Cellular respiration"])
        atp = self.approve_concept(proposed["ATP production"])
        edge = next(item for item in proposals["edges"] if item["type"] == "prerequisite_for")
        approved_edge = self.call(
            "post",
            f"/api/classes/{self.class_id}/concept-edges/{edge['id']}/decision",
            "graph_owner",
            json={"decision": "approve", "expected_version": edge["version"]},
        )
        self.assertEqual(approved_edge.status_code, 200, approved_edge.text)
        graph = self.call("get", f"/api/classes/{self.class_id}/concept-graph", "graph_reader")
        self.assertEqual(graph.status_code, 200, graph.text)
        self.assertEqual(len(graph.json()["concepts"]), 2)
        self.assertEqual(len(graph.json()["edges"]), 1)
        self.assertEqual(cell["state"], "validated")
        self.assertEqual(atp["state"], "validated")

    def test_generated_description_chunks_are_not_accepted_as_support(self) -> None:
        self.client.portal.call(
            self.db.execute,
            "UPDATE source_chunk_revisions SET provenance='generated_description' WHERE id=?1",
            self.chunk_id,
        )
        rejected = self.call(
            "post",
            f"/api/classes/{self.class_id}/concepts",
            "graph_owner",
            json={
                "canonical_name": "Unsupported description",
                "description": "Generated image text.",
                "source_chunk_revision_ids": [self.chunk_id],
            },
        )
        self.assertEqual(rejected.status_code, 404)
        self.assertEqual(rejected.json()["code"], "source_reference_not_found")


class ConceptGraphMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_populated_database_adds_job_and_alias_columns_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                _env_file=None,
                gemini_api_key="test-key-not-used",
                file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
                sqlite_path=str(Path(directory) / "migration.sqlite3"),
                storage_dir=directory,
                lean_tool_enabled=False,
            )
            db = await create_database(settings)
            await db.execute(
                "INSERT INTO users(id,username,display_name,password_hash) VALUES "
                "('u','u','User','x')"
            )
            await db.execute("INSERT INTO course_classes(id,owner_id,name) VALUES ('c','u','C')")
            await db.execute(
                "INSERT INTO "
                "class_concepts(id,class_id,canonical_name,normalized_name,description"
                ",provenance) "
                "VALUES ('n','c','Node','node','Description','manual')"
            )
            await db.close()
            raw = sqlite3.connect(settings.sqlite_path_value)
            raw.execute("DROP INDEX IF EXISTS class_concept_aliases_name_idx")
            raw.execute("DROP INDEX IF EXISTS class_concept_extraction_jobs_status_idx")
            raw.execute("ALTER TABLE class_concept_aliases DROP COLUMN status")
            raw.execute("ALTER TABLE class_concept_extraction_jobs DROP COLUMN available_at")
            raw.execute("ALTER TABLE class_concept_extraction_jobs DROP COLUMN prompt_version")
            raw.execute("ALTER TABLE class_concept_extraction_jobs DROP COLUMN model_name")
            raw.commit()
            raw.close()
            migrated = await create_database(settings)
            concept = await migrated.fetchrow(
                "SELECT canonical_name FROM class_concepts WHERE id='n'"
            )
            alias_columns = await migrated.fetch("PRAGMA table_info(class_concept_aliases)")
            job_columns = await migrated.fetch("PRAGMA table_info(class_concept_extraction_jobs)")
            self.assertEqual(concept["canonical_name"], "Node")
            self.assertIn("status", {row["name"] for row in alias_columns})
            self.assertIn("available_at", {row["name"] for row in job_columns})
            self.assertIn("prompt_version", {row["name"] for row in job_columns})
            self.assertIn("model_name", {row["name"] for row in job_columns})
            await migrated.close()
