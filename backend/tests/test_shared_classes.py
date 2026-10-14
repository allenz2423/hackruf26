"""Shared-class API authorization, privacy, release, and migration tests."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.auth import SESSION_COOKIE
from app.services.database import create_database
from app.services.live_sessions import LiveSessionTicketStore

ORIGIN = "http://localhost:5173"


class FakeGenerator:
    def __init__(self) -> None:
        self.sources: list[list[dict]] = []

    def generate_material(self, **kwargs):
        self.sources.append(kwargs["sources"])
        upload_id = str(kwargs["sources"][0]["id"])
        return "Shared quiz", {
            "format": "quiz",
            "questions": [{
                "id": "q1",
                "kind": "multiple_choice",
                "prompt": "Which answer is correct?",
                "choices": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
                "answer": "a",
                "explanation": "Because the source explains it.",
                "sources": [{"upload_id": upload_id}],
            }],
        }


class SharedClassApiTests(unittest.TestCase):
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
        self.tokens: dict[str, str] = {}
        self.users: dict[str, dict] = {}
        self.owner = self.add_user("class_owner")
        response = self.call("post", "/api/classes", "class_owner", json={"name": "BIO 170"})
        self.assertEqual(response.status_code, 201, response.text)
        self.class_id = response.json()["id"]
        self.database = self.client.app.state.database
        self.generator = FakeGenerator()
        self.client.app.state.gemini_content_service = self.generator

    def add_user(self, username: str) -> dict:
        response = self.client.post(
            "/api/auth/signup", json={"username": username, "password": "shared-class-pass"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.tokens[username] = response.cookies.get(SESSION_COOKIE)
        self.users[username] = response.json()["user"]
        return self.users[username]

    def call(self, method: str, path: str, username: str, **kwargs):
        return getattr(self.client, method)(
            path, cookies={SESSION_COOKIE: self.tokens[username]}, **kwargs
        )

    def invite(self, username: str, capabilities: list[str], *, max_uses: int = 1) -> dict:
        response = self.call(
            "post",
            f"/api/classes/{self.class_id}/invitations",
            "class_owner",
            json={"capabilities": capabilities, "max_uses": max_uses, "expires_in_days": 7},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def join(self, username: str, invitation: dict):
        return self.call(
            "post", "/api/class-invitations/redeem", username, json={"code": invitation["code"]}
        )

    def seed_note(
        self,
        *,
        title: str,
        text: str,
        created_by: str | None = None,
        publication_state: str = "published",
        storage_key: str | None = None,
    ) -> str:
        note_id = str(uuid4())
        source_type = "file" if storage_key else "pasted_text"
        file_size = len(text.encode("utf-8")) if storage_key else None
        self.client.portal.call(
            self.database.execute,
            "INSERT INTO course_notes (id,class_id,resource_scope,created_by,publication_state,title,kind,"
            "source_type,storage_key,original_filename,mime_type,file_size_bytes,extracted_text,summary,status,processing_progress) "
            "VALUES (?1,?2,'class_owned',?3,?4,?5,'document',?6,?7,?8,?9,?10,?11,?12,'ready',100)",
            note_id,
            self.class_id,
            self.users[created_by]["id"] if created_by else None,
            publication_state,
            title,
            source_type,
            storage_key,
            f"{title}.pdf" if storage_key else None,
            "application/pdf" if storage_key else None,
            file_size,
            text,
            text[:200],
        )
        return note_id

    def seed_assessment(
        self, note_id: str, *, owner: str, has_responses: bool = False
    ) -> str:
        assessment_id = str(uuid4())
        question = {
            "id": "q1",
            "prompt": "Which answer is correct?",
            "choices": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
            "points": 1,
            "student_response": "b" if has_responses else None,
            "response_status": "answered" if has_responses else "blank",
            "answer_key": "a",
            "answer_key_status": "present",
        }
        values = (
            assessment_id,
            self.class_id,
            note_id,
            self.users[owner]["id"],
            int(has_responses),
            json.dumps([question]),
            "answer_key" if has_responses else "not_graded",
            80.0 if has_responses else None,
            json.dumps({"review_reason": "private student result"}) if has_responses else None,
            0.9 if has_responses else None,
            "2026-10-14T12:00:00Z" if has_responses else None,
        )
        self.client.portal.call(
            self.database.execute,
            "INSERT INTO uploaded_assessments (id,class_id,note_id,owner_id,processing_status,"
            "has_student_responses,questions,grading_mode,score_percent,grading_result,grade_confidence,"
            "processed_at,graded_at) VALUES (?1,?2,?3,?4,'ready',?5,?6,?7,?8,?9,?10,?11,?11)",
            *values,
        )
        return assessment_id

    def generate(self, username: str, *, source_id: str, destination: str = "personal") -> dict:
        response = self.call(
            "post",
            f"/api/classes/{self.class_id}/study-materials",
            username,
            json={
                "kind": "quiz",
                "scope": {"kind": "uploads", "upload_ids": [source_id]},
                "options": {"count": 4},
                "destination": destination,
            },
        )
        return response

    def test_owner_membership_invitation_lifecycle_and_race_limit(self) -> None:
        owner_classes = self.call("get", "/api/classes", "class_owner").json()["items"]
        owner_summary = next(item for item in owner_classes if item["id"] == self.class_id)
        self.assertTrue(owner_summary["is_owner"])
        self.assertIn("manage_permissions", owner_summary["capabilities"])
        owner_member = next(
            item for item in self.call(
                "get", f"/api/classes/{self.class_id}/members", "class_owner"
            ).json()["items"]
            if item["is_owner"]
        )
        self.assertIn("delete_others", owner_member["capabilities"])

        member_a = self.add_user("reader_a")
        member_b = self.add_user("reader_b")
        member_c = self.add_user("reader_c")
        self.add_user("reader_d")
        self.add_user("reader_e")
        self.add_user("invite_manager")
        invitation = self.invite("reader_a", ["read"], max_uses=1)
        joined = self.join("reader_a", invitation)
        self.assertEqual(joined.status_code, 200, joined.text)
        self.assertEqual(joined.json()["status"], "joined")
        self.assertEqual(
            self.call(
                "post", f"/api/classes/{self.class_id}/invitations", "reader_a",
                json={"capabilities": ["read"], "max_uses": 1, "expires_in_days": 7},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.call(
                "delete", f"/api/classes/{self.class_id}/invitations/{invitation['id']}", "reader_a"
            ).status_code,
            403,
        )
        duplicate = self.join("reader_a", invitation)
        self.assertEqual(duplicate.json()["status"], "already_member")
        uses = self.client.portal.call(
            self.database.fetchval,
            "SELECT successful_uses FROM class_invitations WHERE id=?1",
            invitation["id"],
        )
        self.assertEqual(uses, 1)

        race_invitation = self.invite("reader_a", ["read"], max_uses=1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda name: self.join(name, race_invitation), ["reader_b", "reader_c"]
            ))
        self.assertEqual(sorted(item.status_code for item in results), [200, 410])
        self.assertEqual(
            self.client.portal.call(
                self.database.fetchval,
                "SELECT successful_uses FROM class_invitations WHERE id=?1",
                race_invitation["id"],
            ),
            1,
        )
        self.assertEqual(member_a["username"], "reader_a")
        self.assertEqual(member_b["username"], "reader_b")
        self.assertEqual(member_c["username"], "reader_c")

        invite_manager_grant = self.invite("reader_a", ["read", "invite"])
        self.assertEqual(self.join("invite_manager", invite_manager_grant).status_code, 200)
        owner_create_invitation = self.invite("reader_a", ["read", "create"])
        self.assertEqual(
            self.call(
                "delete",
                f"/api/classes/{self.class_id}/invitations/{owner_create_invitation['id']}",
                "invite_manager",
            ).status_code,
            403,
        )

        listed = self.call("get", f"/api/classes/{self.class_id}/members", "reader_a")
        self.assertEqual(listed.status_code, 200, listed.text)
        own = next(row for row in listed.json()["items"] if row["user_id"] == member_a["id"])
        self.assertFalse(own["is_owner"])
        self.assertEqual(own["capabilities"], ["read"])
        self.assertEqual(self.join("reader_e", invitation).status_code, 410)
        expired = self.invite("reader_a", ["read"])
        self.client.portal.call(
            self.database.execute,
            "UPDATE class_invitations SET expires_at='2000-01-01T00:00:00Z' WHERE id=?1",
            expired["id"],
        )
        self.assertEqual(self.join("reader_d", expired).status_code, 410)

    def test_read_only_personal_generation_private_attempts_and_membership_revocation(self) -> None:
        self.add_user("read_only")
        creator = self.add_user("creator")
        self.add_user("another_reader")
        self.assertEqual(self.join("read_only", self.invite("read_only", ["read"])).status_code, 200)
        self.assertEqual(
            self.join("creator", self.invite("creator", ["read", "create", "edit", "delete"])).status_code,
            200,
        )
        self.assertEqual(self.join("another_reader", self.invite("another_reader", ["read"])).status_code, 200)

        source_id = self.seed_note(title="Published notes", text="Course fact: mitochondria produce ATP.")
        draft_id = self.seed_note(
            title="Unreleased exam key", text="SECRET ANSWER KEY", publication_state="draft"
        )
        scheduled_id = self.seed_note(
            title="Future lecture", text="FUTURE RELEASE TEXT", publication_state="draft"
        )
        self.client.portal.call(
            self.database.execute,
            "UPDATE course_notes SET publication_state='scheduled',release_at='2099-10-14T12:00:00Z' WHERE id=?1",
            scheduled_id,
        )
        personal = self.generate("read_only", source_id=source_id)
        self.assertEqual(personal.status_code, 202, personal.text)
        personal_id = personal.json()["id"]
        personal_detail = self.call("get", f"/api/study-materials/{personal_id}", "read_only")
        self.assertEqual(personal_detail.status_code, 200, personal_detail.text)
        self.assertEqual(personal_detail.json()["resource_scope"], "personal")
        self.assertIsNone(personal_detail.json()["class_id"])
        self.assertIn("Course fact", self.generator.sources[-1][0]["extracted_text"])
        live_response = self.call(
            "post", "/api/live-sessions", "read_only", json={"class_id": self.class_id}
        )
        self.assertEqual(live_response.status_code, 200, live_response.text)
        live_ticket = self.client.portal.call(
            self.client.app.state.live_session_ticket_store.consume,
            live_response.json()["ticket"],
        )
        self.assertIn("Course fact", live_ticket.class_context)
        self.assertNotIn("SECRET ANSWER KEY", live_ticket.class_context)
        revocable_live = self.call(
            "post", "/api/live-sessions", "read_only", json={"class_id": self.class_id}
        )
        self.assertEqual(revocable_live.status_code, 200, revocable_live.text)
        self.assertEqual(
            self.call("get", f"/api/study-materials/{personal_id}", "class_owner").status_code,
            404,
        )
        blocked_draft = self.generate("read_only", source_id=draft_id)
        self.assertEqual(blocked_draft.status_code, 404)
        self.assertEqual(
            self.call("get", f"/api/uploads/{draft_id}", "read_only").status_code,
            404,
        )
        self.assertEqual(self.generate("read_only", source_id=scheduled_id).status_code, 404)
        self.assertEqual(self.call("get", f"/api/uploads/{scheduled_id}", "read_only").status_code, 404)
        self.client.portal.call(
            self.database.execute,
            "UPDATE course_notes SET release_at='2000-01-01T00:00:00Z' WHERE id=?1",
            scheduled_id,
        )
        self.assertEqual(self.call("get", f"/api/uploads/{scheduled_id}", "read_only").status_code, 200)
        forbidden_contribution = self.generate(
            "read_only", source_id=source_id, destination="class_contribution"
        )
        self.assertEqual(forbidden_contribution.status_code, 403)

        shared = self.generate("creator", source_id=source_id, destination="class_contribution")
        self.assertEqual(shared.status_code, 202, shared.text)
        shared_id = shared.json()["id"]
        self.assertEqual(shared.json()["publication_state"], "draft")
        self.assertEqual(
            self.call("get", f"/api/study-materials/{shared_id}", "another_reader").status_code,
            404,
        )
        published = self.call(
            "patch",
            f"/api/resources/study_materials/{shared_id}/publication",
            "class_owner",
            json={"state": "published"},
        )
        self.assertEqual(published.status_code, 200, published.text)

        for username, answer in (("creator", "a"), ("another_reader", "b")):
            run = self.call(
                "post",
                f"/api/study-materials/{shared_id}/runs",
                username,
                json={"operation_id": str(uuid4()), "mode": "standard"},
            )
            self.assertEqual(run.status_code, 201, run.text)
            run_id = run.json()["run_id"]
            submitted = self.call(
                "post",
                f"/api/study-materials/{shared_id}/attempts",
                username,
                json={
                    "run_id": run_id,
                    "operation_id": str(uuid4()),
                    "answers": {"q1": answer},
                },
            )
            self.assertEqual(submitted.status_code, 200, submitted.text)
            if username == "creator":
                creator_run_id = run_id
            else:
                viewer_run_id = run_id

        self.assertEqual(
            self.call("get", f"/api/study-materials/{shared_id}/runs/{creator_run_id}", "another_reader").status_code,
            404,
        )
        self.assertEqual(
            self.call("get", f"/api/study-materials/{shared_id}/runs/{viewer_run_id}", "creator").status_code,
            404,
        )
        legacy_submit = self.call(
            "post",
            f"/api/study-materials/{shared_id}/attempts",
            "another_reader",
            json={"answers": {"q1": "a"}},
        )
        self.assertEqual(legacy_submit.status_code, 409, legacy_submit.text)
        preserve_history = self.call("delete", f"/api/study-materials/{shared_id}", "creator")
        self.assertEqual(preserve_history.status_code, 409, preserve_history.text)
        self.assertEqual(
            self.call("get", f"/api/study-materials/{shared_id}/runs/{creator_run_id}", "creator").status_code,
            200,
        )
        self.client.portal.call(
            self.database.execute,
            "INSERT INTO study_material_attempts (id,material_id,owner_id,answers,score_percent,completed_at) "
            "VALUES (?1,?2,NULL,'{}',99,'9999-12-31T00:00:00Z')",
            str(uuid4()), shared_id,
        )
        creator_detail = self.call("get", f"/api/study-materials/{shared_id}", "creator").json()
        viewer_detail = self.call("get", f"/api/study-materials/{shared_id}", "another_reader").json()
        self.assertEqual(creator_detail["latest_attempt"]["score_percent"], 100)
        self.assertEqual(viewer_detail["latest_attempt"]["score_percent"], 0)

        remove = self.call(
            "delete",
            f"/api/classes/{self.class_id}/members/{creator['id']}",
            "class_owner",
        )
        self.assertEqual(remove.status_code, 204, remove.text)
        self.assertEqual(self.call("get", f"/api/study-materials/{shared_id}", "creator").status_code, 404)
        self.assertEqual(self.call("get", f"/api/study-materials/{personal_id}", "read_only").status_code, 200)
        leave = self.call("post", f"/api/classes/{self.class_id}/leave", "read_only")
        self.assertEqual(leave.status_code, 204, leave.text)
        self.assertIsNone(
            self.client.portal.call(
                self.client.app.state.live_session_ticket_store.consume,
                revocable_live.json()["ticket"],
            )
        )
        self.assertEqual(self.call("get", f"/api/uploads/{source_id}", "read_only").status_code, 404)
        self.assertEqual(self.call("get", f"/api/study-materials/{personal_id}", "read_only").status_code, 200)

    def test_own_resource_permissions_do_not_apply_to_other_authors_and_management_is_separate(self) -> None:
        self.add_user("resource_creator")
        manager = self.add_user("permission_manager")
        self.assertEqual(
            self.join("resource_creator", self.invite("resource_creator", ["read", "create", "edit", "delete"])).status_code,
            200,
        )
        self.assertEqual(
            self.join("permission_manager", self.invite("permission_manager", ["read", "manage_permissions"])).status_code,
            200,
        )
        owner_note = self.seed_note(title="Owner notes", text="Owner content", created_by="class_owner")
        self.client.portal.call(
            self.database.execute,
            "UPDATE course_notes SET resource_scope='student_contribution' WHERE id=?1",
            owner_note,
        )
        edit_other = self.call(
            "patch", f"/api/uploads/{owner_note}", "resource_creator", json={"title": "Hijacked"}
        )
        self.assertEqual(edit_other.status_code, 403, edit_other.text)
        delete_other = self.call("delete", f"/api/uploads/{owner_note}", "resource_creator")
        self.assertEqual(delete_other.status_code, 403, delete_other.text)
        manager_delete = self.call("delete", f"/api/uploads/{owner_note}", "permission_manager")
        self.assertEqual(manager_delete.status_code, 403, manager_delete.text)
        manager_self_escalation = self.call(
            "patch",
            f"/api/classes/{self.class_id}/members/{manager['id']}/capabilities",
            "permission_manager",
            json={"capabilities": ["read", "manage_permissions", "delete"]},
        )
        self.assertEqual(manager_self_escalation.status_code, 403)
        self.assertEqual(self.call("delete", f"/api/classes/{self.class_id}", "resource_creator").status_code, 404)
        own_note = self.seed_note(
            title="Creator draft", text="Own content", created_by="resource_creator", publication_state="draft"
        )
        own_edit = self.call(
            "patch", f"/api/uploads/{own_note}", "resource_creator", json={"title": "My revised notes"}
        )
        self.assertEqual(own_edit.status_code, 200, own_edit.text)
        own_delete = self.call("delete", f"/api/uploads/{own_note}", "resource_creator")
        self.assertEqual(own_delete.status_code, 204, own_delete.text)
        self.add_user("create_only")
        self.assertEqual(
            self.join("create_only", self.invite("create_only", ["read", "create"])).status_code,
            200,
        )
        create_only_note = self.seed_note(
            title="Create-only draft", text="Contribution", created_by="create_only",
            publication_state="draft",
        )
        self.client.portal.call(
            self.database.execute,
            "UPDATE course_notes SET resource_scope='student_contribution' WHERE id=?1",
            create_only_note,
        )
        self.assertEqual(
            self.call(
                "patch", f"/api/uploads/{create_only_note}", "create_only",
                json={"title": "Edited without permission"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.call("delete", f"/api/uploads/{create_only_note}", "create_only").status_code,
            403,
        )

    def test_delete_others_is_separate_and_limited_to_published_member_contributions(self) -> None:
        self.add_user("delete_author")
        self.add_user("delete_moderator")
        self.add_user("delete_only")
        self.assertEqual(
            self.join(
                "delete_author",
                self.invite("delete_author", ["read", "create", "edit", "delete"]),
            ).status_code,
            200,
        )
        moderator_invitation = self.invite(
            "delete_moderator", ["read", "delete_others"]
        )
        self.assertIn("delete_others", moderator_invitation["capabilities"])
        joined = self.join("delete_moderator", moderator_invitation)
        self.assertEqual(joined.status_code, 200, joined.text)
        self.assertIn("delete_others", joined.json()["capabilities"])
        self.assertEqual(
            self.join("delete_only", self.invite("delete_only", ["read", "delete"])).status_code,
            200,
        )

        published_other = self.seed_note(
            title="Published member notes", text="Shared notes", created_by="delete_author"
        )
        self.client.portal.call(
            self.database.execute,
            "UPDATE course_notes SET resource_scope='student_contribution' WHERE id=?1",
            published_other,
        )
        self.assertEqual(
            self.call("delete", f"/api/uploads/{published_other}", "delete_only").status_code,
            403,
        )
        self.assertEqual(
            self.call("delete", f"/api/uploads/{published_other}", "delete_moderator").status_code,
            204,
        )

        own_without_delete = self.seed_note(
            title="Moderator's own notes", text="Still protected", created_by="delete_moderator"
        )
        self.client.portal.call(
            self.database.execute,
            "UPDATE course_notes SET resource_scope='student_contribution' WHERE id=?1",
            own_without_delete,
        )
        self.assertEqual(
            self.call("delete", f"/api/uploads/{own_without_delete}", "delete_moderator").status_code,
            403,
        )

        draft_other = self.seed_note(
            title="Unreleased member draft", text="Not visible to the moderator",
            created_by="delete_author", publication_state="draft",
        )
        self.client.portal.call(
            self.database.execute,
            "UPDATE course_notes SET resource_scope='student_contribution' WHERE id=?1",
            draft_other,
        )
        self.assertEqual(
            self.call("delete", f"/api/uploads/{draft_other}", "delete_moderator").status_code,
            404,
        )

        class_owned = self.seed_note(
            title="Class-owned notes", text="Owner-managed", created_by="class_owner"
        )
        self.assertEqual(
            self.call("delete", f"/api/uploads/{class_owned}", "delete_moderator").status_code,
            403,
        )

        unattributed = self.seed_note(
            title="Legacy contribution", text="Unknown author", created_by=None
        )
        self.client.portal.call(
            self.database.execute,
            "UPDATE course_notes SET resource_scope='student_contribution' WHERE id=?1",
            unattributed,
        )
        self.assertEqual(
            self.call("delete", f"/api/uploads/{unattributed}", "delete_moderator").status_code,
            403,
        )

        private_assessment_file = "private-other-contribution.pdf"
        self.client.app.state.private_storage.path_for(private_assessment_file).write_bytes(
            b"%PDF-1.4 private responses"
        )
        private_assessment_note = self.seed_note(
            title="Private responses", text="Student responses", created_by="delete_author",
            storage_key=private_assessment_file,
        )
        self.client.portal.call(
            self.database.execute,
            "UPDATE course_notes SET resource_scope='student_contribution' WHERE id=?1",
            private_assessment_note,
        )
        self.seed_assessment(private_assessment_note, owner="delete_author", has_responses=True)
        self.assertEqual(
            self.call(
                "delete", f"/api/uploads/{private_assessment_note}", "delete_moderator"
            ).status_code,
            404,
        )

        self.add_user("permission_limited_manager")
        self.assertEqual(
            self.join(
                "permission_limited_manager",
                self.invite("permission_limited_manager", ["read", "manage_permissions"]),
            ).status_code,
            200,
        )
        grant_escalation = self.call(
            "patch",
            f"/api/classes/{self.class_id}/members/{self.users['delete_only']['id']}/capabilities",
            "permission_limited_manager",
            json={"capabilities": ["read", "delete_others"]},
        )
        self.assertEqual(grant_escalation.status_code, 403)

    def test_delete_others_moderates_published_generated_contributions_without_bypassing_history(self) -> None:
        self.add_user("material_author")
        self.add_user("material_moderator")
        self.assertEqual(
            self.join("material_author", self.invite("material_author", ["read", "create"])).status_code,
            200,
        )
        self.assertEqual(
            self.join(
                "material_moderator",
                self.invite("material_moderator", ["read", "delete_others"]),
            ).status_code,
            200,
        )
        source_id = self.seed_note(title="Source", text="The source text.", created_by="class_owner")
        generated = self.generate(
            "material_author", source_id=source_id, destination="class_contribution"
        )
        self.assertEqual(generated.status_code, 202, generated.text)
        material_id = generated.json()["id"]
        published = self.call(
            "patch",
            f"/api/resources/study_materials/{material_id}/publication",
            "material_author",
            json={"state": "published"},
        )
        self.assertEqual(published.status_code, 200, published.text)
        deleted = self.call(
            "delete", f"/api/study-materials/{material_id}", "material_moderator"
        )
        self.assertEqual(deleted.status_code, 204, deleted.text)

        protected = self.generate(
            "material_author", source_id=source_id, destination="class_contribution"
        )
        self.assertEqual(protected.status_code, 202, protected.text)
        protected_id = protected.json()["id"]
        self.assertEqual(
            self.call(
                "patch",
                f"/api/resources/study_materials/{protected_id}/publication",
                "material_author",
                json={"state": "published"},
            ).status_code,
            200,
        )
        self.client.portal.call(
            self.database.execute,
            "INSERT INTO quiz_runs (id,material_id,owner_id,operation_id,mode) "
            "VALUES ('moderated_run',?1,?2,'run-once','standard')",
            protected_id,
            self.users["material_moderator"]["id"],
        )
        protected_delete = self.call(
            "delete", f"/api/study-materials/{protected_id}", "material_moderator"
        )
        self.assertEqual(protected_delete.status_code, 409, protected_delete.text)

    def test_release_and_answer_key_are_enforced_on_detail_file_and_generation(self) -> None:
        self.add_user("assessment_viewer")
        self.add_user("assessment_uploader")
        self.assertEqual(self.join("assessment_viewer", self.invite("assessment_viewer", ["read"])).status_code, 200)
        self.assertEqual(
            self.join("assessment_uploader", self.invite("assessment_uploader", ["read", "create"])).status_code,
            200,
        )
        self.add_user("assessment_manager")
        self.assertEqual(
            self.join(
                "assessment_manager",
                self.invite("assessment_manager", ["read", "manage_permissions"]),
            ).status_code,
            200,
        )
        key = "answer-key-fixture.pdf"
        self.client.app.state.private_storage.path_for(key).write_bytes(b"%PDF-1.4 SECRET KEY")
        key_note = self.seed_note(
            title="Midterm", text="SECRET CORRECT ANSWER", created_by="class_owner", storage_key=key
        )
        assessment_id = self.seed_assessment(key_note, owner="class_owner")
        hidden_detail = self.call("get", f"/api/assessments/{assessment_id}", "assessment_viewer")
        self.assertEqual(hidden_detail.status_code, 200, hidden_detail.text)
        self.assertIsNone(hidden_detail.json()["questions"][0]["answer_key"])
        hidden_upload = self.call("get", f"/api/uploads/{key_note}", "assessment_viewer")
        self.assertEqual(hidden_upload.status_code, 200, hidden_upload.text)
        self.assertIsNone(hidden_upload.json()["extracted_text"])
        self.assertIsNone(hidden_upload.json()["file_url"])
        self.assertEqual(
            self.generate("assessment_viewer", source_id=key_note).status_code, 404
        )
        signed_url, _ = self.client.app.state.private_storage.signed_url("http://testserver", key_note)
        self.assertEqual(self.client.get(signed_url, cookies={SESSION_COOKIE: self.tokens["assessment_viewer"]}).status_code, 404)

        release = self.call(
            "patch",
            f"/api/assessments/{assessment_id}/answer-key-release",
            "class_owner",
            json={"state": "scheduled", "release_at": "2099-10-14T12:00:00Z"},
        )
        self.assertEqual(release.status_code, 200, release.text)
        still_hidden = self.call("get", f"/api/assessments/{assessment_id}", "assessment_viewer")
        self.assertIsNone(still_hidden.json()["questions"][0]["answer_key"])
        release = self.call(
            "patch", f"/api/assessments/{assessment_id}/answer-key-release", "class_owner",
            json={"state": "published"},
        )
        self.assertEqual(release.status_code, 200, release.text)
        released = self.call("get", f"/api/assessments/{assessment_id}", "assessment_viewer")
        self.assertEqual(released.json()["questions"][0]["answer_key"], "a")
        self.assertEqual(self.call("get", f"/api/uploads/{key_note}", "assessment_viewer").json()["extracted_text"], "SECRET CORRECT ANSWER")

        contribution_key = "contribution-exam.pdf"
        self.client.app.state.private_storage.path_for(contribution_key).write_bytes(b"%PDF-1.4 CONTRIBUTION KEY")
        contribution_note = self.seed_note(
            title="Student contribution exam", text="CONTRIBUTION ANSWER KEY",
            created_by="assessment_uploader", publication_state="draft", storage_key=contribution_key,
        )
        self.client.portal.call(
            self.database.execute,
            "UPDATE course_notes SET resource_scope='student_contribution' WHERE id=?1",
            contribution_note,
        )
        contribution_assessment = self.seed_assessment(contribution_note, owner="assessment_uploader")
        manager_detail = self.call(
            "get", f"/api/assessments/{contribution_assessment}", "assessment_manager"
        )
        self.assertEqual(manager_detail.status_code, 200, manager_detail.text)
        self.assertIsNone(manager_detail.json()["questions"][0]["answer_key"])
        self.assertIsNone(manager_detail.json()["file_url"])
        manager_upload = self.call("get", f"/api/uploads/{contribution_note}", "assessment_manager")
        self.assertIsNone(manager_upload.json()["extracted_text"])
        self.assertIsNone(manager_upload.json()["file_url"])
        contribution_url, _ = self.client.app.state.private_storage.signed_url(
            "http://testserver", contribution_note
        )
        self.assertEqual(
            self.client.get(
                contribution_url,
                cookies={SESSION_COOKIE: self.tokens["assessment_manager"]},
            ).status_code,
            404,
        )
        generated_before = len(self.generator.sources)
        self.assertEqual(
            self.generate("assessment_manager", source_id=contribution_note).status_code, 404
        )
        self.assertEqual(len(self.generator.sources), generated_before)

        private_key = "private-student-response.pdf"
        self.client.app.state.private_storage.path_for(private_key).write_bytes(b"%PDF-1.4 PRIVATE RESPONSE")
        response_note = self.seed_note(
            title="Submitted test", text="PRIVATE STUDENT ANSWERS", created_by="assessment_uploader",
            storage_key=private_key,
        )
        response_assessment = self.seed_assessment(
            response_note, owner="assessment_uploader", has_responses=True
        )
        self.assertEqual(
            self.call("get", f"/api/assessments/{response_assessment}", "class_owner").status_code,
            404,
        )
        self.assertEqual(self.call("get", f"/api/uploads/{response_note}", "class_owner").status_code, 404)
        private_url, _ = self.client.app.state.private_storage.signed_url("http://testserver", response_note)
        self.assertEqual(self.client.get(private_url, cookies={SESSION_COOKIE: self.tokens["class_owner"]}).status_code, 404)
        self.assertEqual(
            self.call("get", f"/api/uploads/{response_note}", "assessment_uploader").status_code,
            200,
        )


class SharedClassMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_populated_migration_backfills_owner_but_not_legacy_attempt_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "populated.sqlite")
            settings = Settings(
                _env_file=None,
                sqlite_path=path,
                file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
            )
            database = await create_database(settings)
            await database.execute(
                "INSERT INTO users (id,username,display_name,password_hash) "
                "VALUES ('owner','legacy_owner','Legacy Owner','hash')"
            )
            await database.execute(
                "INSERT INTO course_classes (id,owner_id,name) VALUES ('class','owner','Legacy class')"
            )
            await database.execute(
                "INSERT INTO course_notes (id,class_id,title,kind,source_type,extracted_text,status,processing_progress) "
                "VALUES ('note','class','Old notes','notes','pasted_text','Legacy source text','ready',100)"
            )
            await database.execute(
                "INSERT INTO study_materials (id,class_id,origin_class_id,resource_scope,created_by,"
                "publication_state,kind,title,status,content) VALUES ('material','class','class',"
                "'class_owned',NULL,'published','quiz','Old quiz','ready','{}')"
            )
            await database.execute(
                "INSERT INTO study_material_attempts (id,material_id,owner_id,answers,score_percent) "
                "VALUES ('legacy_attempt','material',NULL,'{}',75)"
            )
            await database.execute("DELETE FROM schema_migrations")
            # Version 1 on older deployments marked only source-column compatibility.
            # It must not suppress the new owner-membership migration.
            await database.execute("INSERT INTO schema_migrations(version) VALUES (1)")
            await database.execute("DELETE FROM class_membership_capabilities")
            await database.execute("DELETE FROM class_memberships")
            await database.close()

            migrated = await create_database(settings)
            self.addAsyncCleanup(migrated.close)
            self.assertEqual(
                await migrated.fetchval(
                    "SELECT count(*) FROM class_memberships WHERE class_id='class' "
                    "AND user_id='owner' AND status='active'"
                ),
                1,
            )
            self.assertEqual(
                await migrated.fetchval(
                    "SELECT count(*) FROM class_membership_capabilities WHERE class_id='class' "
                    "AND user_id='owner' AND capability='manage_permissions'"
                ),
                1,
            )
            self.assertEqual(
                await migrated.fetchval("SELECT id FROM study_materials WHERE id='material'"),
                "material",
            )
            self.assertIsNone(
                await migrated.fetchval(
                    "SELECT owner_id FROM study_material_attempts WHERE id='legacy_attempt'"
                )
            )
            self.assertEqual(await migrated.fetch("PRAGMA foreign_key_check"), [])

    async def test_capability_migration_preserves_grants_and_adds_delete_others(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "capabilities.sqlite")
            settings = Settings(
                _env_file=None,
                sqlite_path=path,
                file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
            )
            database = await create_database(settings)
            await database.execute(
                "INSERT INTO users (id,username,display_name,password_hash) "
                "VALUES ('owner','capability_owner','Owner','hash')"
            )
            await database.execute(
                "INSERT INTO course_classes (id,owner_id,name) VALUES ('class','owner','Class')"
            )
            await database.execute(
                "INSERT INTO class_memberships (class_id,user_id,status,added_by) "
                "VALUES ('class','owner','active','owner')"
            )
            await database.executemany(
                "INSERT INTO class_membership_capabilities "
                "(class_id,user_id,capability,granted_by) VALUES ('class','owner',?1,'owner')",
                [("read",), ("delete",)],
            )
            await database.execute(
                "INSERT INTO class_invitations (id,class_id,code_digest,created_by,expires_at,max_uses) "
                "VALUES ('invitation','class','digest','owner','9999-01-01T00:00:00Z',2)"
            )
            await database.execute(
                "INSERT INTO class_invitation_capabilities (invitation_id,capability) "
                "VALUES ('invitation','delete')"
            )
            await database.execute(
                "DELETE FROM class_membership_capabilities WHERE capability='delete_others'"
            )
            await database.execute("DELETE FROM schema_migrations WHERE version=3")
            await database.close()

            legacy = sqlite3.connect(path)
            legacy.execute("PRAGMA foreign_keys=OFF")
            legacy.executescript(
                "BEGIN IMMEDIATE;"
                "CREATE TABLE class_membership_capabilities_legacy ("
                "class_id TEXT NOT NULL,user_id TEXT NOT NULL,"
                "capability TEXT NOT NULL CHECK (capability IN ("
                "'read','create','edit','delete','invite','manage_members','manage_permissions')),"
                "granted_by TEXT REFERENCES users(id) ON DELETE SET NULL,"
                "granted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),"
                "PRIMARY KEY (class_id,user_id,capability),"
                "FOREIGN KEY (class_id,user_id) REFERENCES class_memberships(class_id,user_id) "
                "ON DELETE CASCADE);"
                "INSERT INTO class_membership_capabilities_legacy "
                "SELECT class_id,user_id,capability,granted_by,granted_at "
                "FROM class_membership_capabilities;"
                "DROP TABLE class_membership_capabilities;"
                "ALTER TABLE class_membership_capabilities_legacy "
                "RENAME TO class_membership_capabilities;"
                "CREATE TABLE class_invitation_capabilities_legacy ("
                "invitation_id TEXT NOT NULL REFERENCES class_invitations(id) ON DELETE CASCADE,"
                "capability TEXT NOT NULL CHECK (capability IN ("
                "'read','create','edit','delete','invite','manage_members','manage_permissions')),"
                "PRIMARY KEY (invitation_id,capability));"
                "INSERT INTO class_invitation_capabilities_legacy "
                "SELECT invitation_id,capability FROM class_invitation_capabilities;"
                "DROP TABLE class_invitation_capabilities;"
                "ALTER TABLE class_invitation_capabilities_legacy "
                "RENAME TO class_invitation_capabilities;"
                "COMMIT;"
            )
            legacy.close()

            migrated = await create_database(settings)
            self.addAsyncCleanup(migrated.close)
            owner_caps = {
                row["capability"] for row in await migrated.fetch(
                    "SELECT capability FROM class_membership_capabilities "
                    "WHERE class_id='class' AND user_id='owner'"
                )
            }
            self.assertIn("delete", owner_caps)
            self.assertIn("delete_others", owner_caps)
            self.assertEqual(
                await migrated.fetchval(
                    "SELECT capability FROM class_invitation_capabilities "
                    "WHERE invitation_id='invitation'"
                ),
                "delete",
            )
            await migrated.execute(
                "INSERT INTO class_invitation_capabilities (invitation_id,capability) "
                "VALUES ('invitation','delete_others')"
            )
            self.assertEqual(await migrated.fetch("PRAGMA foreign_key_check"), [])


class SharedLiveRevocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_membership_removal_revokes_tickets_and_active_class_sessions(self) -> None:
        store = LiveSessionTicketStore()
        pending, _ = await store.issue("private class context", class_id="class", user_id="student")
        active, _ = await store.issue("private class context", class_id="class", user_id="student")
        consumed = await store.consume(active)
        websocket = AsyncMock()
        self.assertTrue(await store.register_active(consumed, websocket))

        await store.revoke_member("class", "student")

        self.assertIsNone(await store.consume(pending))
        websocket.close.assert_awaited_once_with(code=1008, reason="Class access was revoked")
        self.assertFalse(await store.register_active(consumed, AsyncMock()))

        fresh, _ = await store.issue("new class context", class_id="class", user_id="student")
        fresh_ticket = await store.consume(fresh)
        self.assertTrue(await store.register_active(fresh_ticket, AsyncMock()))

    async def test_class_deletion_invalidates_consumed_tickets_before_registration(self) -> None:
        store = LiveSessionTicketStore()
        token, _ = await store.issue("private class context", class_id="class", user_id="student")
        consumed = await store.consume(token)
        await store.revoke_class("class")
        self.assertFalse(await store.register_active(consumed, AsyncMock()))
