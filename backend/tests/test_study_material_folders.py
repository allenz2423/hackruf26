"""Private per-class study-material folder behavior and isolation."""

from __future__ import annotations

import tempfile
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.auth import SESSION_COOKIE

ORIGIN = "http://localhost:5173"


class StudyMaterialFolderApiTests(unittest.TestCase):
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
        self.add_user("folder_owner")
        self.add_user("folder_reader")
        response = self.call("post", "/api/classes", "folder_owner", json={"name": "BIO 170"})
        self.assertEqual(response.status_code, 201, response.text)
        self.class_id = response.json()["id"]
        self.database = self.client.app.state.database
        self.material_id = self.seed_shared_material()

    def folder_path(self) -> str:
        return f"/api/classes/{self.class_id}/study-material-organization/folders"

    def assignment_path(self, material_id: str | None = None) -> str:
        base = f"/api/classes/{self.class_id}/study-material-organization"
        if material_id is None:
            return f"{base}/assignments"
        return f"{base}/materials/{material_id}/folder"

    def add_user(self, username: str) -> None:
        response = self.client.post(
            "/api/auth/signup", json={"username": username, "password": "folder-test-pass"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.tokens[username] = response.cookies.get(SESSION_COOKIE)
        self.users[username] = response.json()["user"]

    def call(self, method: str, path: str, username: str, **kwargs):
        return getattr(self.client, method)(
            path, cookies={SESSION_COOKIE: self.tokens[username]}, **kwargs
        )

    def seed_shared_material(self) -> str:
        material_id = str(uuid4())
        self.client.portal.call(
            self.database.execute,
            "INSERT INTO study_materials "
            "(id,class_id,resource_scope,created_by,publication_state,kind,title,status,content) "
            "VALUES (?1,?2,'class_owned',?3,'published','study_guide','Cell biology','ready','{}')",
            material_id,
            self.class_id,
            self.users["folder_owner"]["id"],
        )
        return material_id

    def seed_personal_material(self) -> str:
        material_id = str(uuid4())
        self.client.portal.call(
            self.database.execute,
            "INSERT INTO study_materials "
            "(id,class_id,origin_class_id,resource_scope,owner_id,created_by,publication_state,"
            "kind,title,status,content) "
            "VALUES (?1,NULL,?2,'personal',?3,?3,NULL,'study_guide',"
            "'My chapter guide','ready','{}')",
            material_id,
            self.class_id,
            self.users["folder_owner"]["id"],
        )
        return material_id

    def add_reader_to_class(self) -> None:
        invitation = self.call(
            "post",
            f"/api/classes/{self.class_id}/invitations",
            "folder_owner",
            json={"capabilities": ["read"], "max_uses": 1, "expires_in_days": 7},
        )
        self.assertEqual(invitation.status_code, 201, invitation.text)
        joined = self.call(
            "post",
            "/api/class-invitations/redeem",
            "folder_reader",
            json={"code": invitation.json()["code"]},
        )
        self.assertEqual(joined.status_code, 200, joined.text)

    def test_crud_normalizes_names_and_deleting_folder_unfiles_materials(self) -> None:
        base = self.folder_path()
        created = self.call("post", base, "folder_owner", json={"name": "  Chapter   1  "})
        self.assertEqual(created.status_code, 200, created.text)
        folder = created.json()
        self.assertEqual(folder["name"], "Chapter 1")

        duplicate = self.call("post", base, "folder_owner", json={"name": "chapter 1"})
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

        assigned = self.call(
            "put",
            self.assignment_path(self.material_id),
            "folder_owner",
            json={"folder_id": folder["id"]},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertEqual(
            self.call("get", self.assignment_path(), "folder_owner").json()["assignments"],
            {self.material_id: folder["id"]},
        )

        renamed = self.call(
            "patch", f"{base}/{folder['id']}", "folder_owner", json={"name": "Unit 1"}
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["name"], "Unit 1")

        deleted = self.call("delete", f"{base}/{folder['id']}", "folder_owner")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(
            self.call("get", self.assignment_path(), "folder_owner").json()["assignments"],
            {},
        )

    def test_folders_and_assignments_are_private_and_class_scoped(self) -> None:
        base = self.folder_path()
        hidden = self.call("get", base, "folder_reader")
        self.assertEqual(hidden.status_code, 404, hidden.text)
        self.add_reader_to_class()
        owner_folder = self.call("post", base, "folder_owner", json={"name": "Owner only"}).json()
        reader_folders = self.call("get", base, "folder_reader")
        self.assertEqual(reader_folders.status_code, 200, reader_folders.text)
        self.assertEqual(reader_folders.json()["items"], [])

        forbidden_folder = self.call(
            "put",
            self.assignment_path(self.material_id),
            "folder_reader",
            json={"folder_id": owner_folder["id"]},
        )
        self.assertEqual(forbidden_folder.status_code, 404, forbidden_folder.text)

        owner_assigns = self.call(
            "put",
            self.assignment_path(self.material_id),
            "folder_owner",
            json={"folder_id": owner_folder["id"]},
        )
        self.assertEqual(owner_assigns.status_code, 200, owner_assigns.text)
        self.assertEqual(
            self.call("get", self.assignment_path(), "folder_reader").json()["assignments"],
            {},
        )

        reader_folder = self.call("post", base, "folder_reader", json={"name": "My review"}).json()
        reader_assigns = self.call(
            "put",
            self.assignment_path(self.material_id),
            "folder_reader",
            json={"folder_id": reader_folder["id"]},
        )
        self.assertEqual(reader_assigns.status_code, 200, reader_assigns.text)
        self.assertEqual(
            self.call("get", self.assignment_path(), "folder_owner").json()["assignments"],
            {self.material_id: owner_folder["id"]},
        )

    def test_personal_materials_can_be_filed_in_their_origin_class(self) -> None:
        folder = self.call(
            "post",
            self.folder_path(),
            "folder_owner",
            json={"name": "Private chapters"},
        ).json()
        personal_id = self.seed_personal_material()
        assigned = self.call(
            "put",
            self.assignment_path(personal_id),
            "folder_owner",
            json={"folder_id": folder["id"]},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)

        self.assertEqual(
            self.call("get", self.assignment_path(), "folder_owner").json()["assignments"],
            {personal_id: folder["id"]},
        )
