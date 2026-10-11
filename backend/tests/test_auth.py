"""Accounts, sessions, and the guest/logged-in boundary."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services import auth as accounts
from app.services.auth import hash_password, verify_password
from app.services.database import create_database

ORIGIN = "http://localhost:5173"


def make_client(storage_dir: str, **overrides: object) -> TestClient:
    settings = Settings(
        _env_file=None,
        gemini_api_key="test-key-not-used",
        file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
        sqlite_path=":memory:",
        storage_dir=storage_dir,
        lean_tool_enabled=False,
        **overrides,
    )
    return TestClient(create_app(settings_override=settings), headers={"origin": ORIGIN})


class PasswordTests(unittest.IsolatedAsyncioTestCase):
    async def test_hash_verifies_and_is_salted(self) -> None:
        first = await hash_password("correct horse")
        second = await hash_password("correct horse")
        self.assertNotEqual(first, second)
        self.assertNotIn("correct horse", first)
        self.assertTrue(await verify_password("correct horse", first))
        self.assertFalse(await verify_password("correct horsf", first))
        self.assertFalse(await verify_password("anything", "not-a-hash"))


class AccountBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.client = make_client(self.storage.name)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def other_client(self) -> TestClient:
        """A second browser with its own cookies against the same app."""
        return TestClient(self.client.app, headers={"origin": ORIGIN})

    def signup(self, client: TestClient, username: str = "Ada_L", **extra: object):
        body = {"username": username, "password": "tutor-pass-1", **extra}
        return client.post("/api/auth/signup", json=body)

    def test_guest_has_no_user(self) -> None:
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["user"])

    def test_signup_logs_in_with_an_http_only_cookie(self) -> None:
        response = self.signup(self.client, email="ada@example.com", display_name="Ada")
        self.assertEqual(response.status_code, 201)
        body = response.json()["user"]
        self.assertEqual(body["username"], "Ada_L")
        self.assertEqual(body["display_name"], "Ada")
        self.assertNotIn("password", str(response.json()).lower())
        self.assertIn("httponly", response.headers["set-cookie"].lower())
        self.assertEqual(self.client.get("/api/auth/me").json()["user"]["username"], "Ada_L")

    def test_username_is_unique_ignoring_case(self) -> None:
        self.assertEqual(self.signup(self.client, "Ada_L").status_code, 201)
        duplicate = self.signup(self.other_client(), "ada_l")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["code"], "username_taken")

    def test_weak_or_malformed_signups_are_rejected(self) -> None:
        for body in (
            {"username": "ab", "password": "tutor-pass-1"},
            {"username": "has space", "password": "tutor-pass-1"},
            {"username": "Valid_Name", "password": "short"},
            {"username": "Valid_Name", "password": "tutor-pass-1", "email": "nope"},
            {"username": "Valid_Name", "password": "tutor-pass-1", "role": "admin"},
        ):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/api/auth/signup", json=body).status_code, 400)

    def test_login_by_username_or_email_and_generic_failure(self) -> None:
        self.signup(self.client, "Ada_L", email="ada@example.com")
        for identifier in ("ada_l", "Ada@Example.com"):
            with self.subTest(identifier=identifier):
                browser = self.other_client()
                response = browser.post(
                    "/api/auth/login", json={"identifier": identifier, "password": "tutor-pass-1"}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(browser.get("/api/auth/me").json()["user"]["username"], "Ada_L")

        for body in (
            {"identifier": "Ada_L", "password": "wrong-password"},
            {"identifier": "nobody", "password": "tutor-pass-1"},
        ):
            failed = self.other_client().post("/api/auth/login", json=body)
            self.assertEqual(failed.status_code, 401)
            self.assertEqual(failed.json()["code"], "invalid_credentials")
            self.assertEqual(failed.json()["message"], "Incorrect username or password.")

    def test_login_attempts_are_rate_limited(self) -> None:
        limited = make_client(self.storage.name, auth_rate_limit=3)
        with limited:
            codes = [
                limited.post(
                    "/api/auth/login", json={"identifier": "nobody", "password": "x" * 8}
                ).status_code
                for _ in range(5)
            ]
        self.assertEqual(codes[:3], [401, 401, 401])
        self.assertEqual(codes[3:], [429, 429])

    def test_guest_cannot_use_the_class_library(self) -> None:
        guest = self.other_client()
        for method, path in (("get", "/api/classes"), ("post", "/api/classes")):
            with self.subTest(path=path, method=method):
                response = getattr(guest, method)(path, **({"json": {"name": "x"}} if method == "post" else {}))
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["code"], "authentication_required")

    def test_users_only_see_their_own_classes(self) -> None:
        ada, grace = self.other_client(), self.other_client()
        self.signup(ada, "Ada_L")
        self.signup(grace, "Grace_H")

        created = ada.post("/api/classes", json={"name": "Ada's Calculus"})
        self.assertEqual(created.status_code, 201)
        class_id = created.json()["id"]

        self.assertEqual([c["name"] for c in ada.get("/api/classes").json()["items"]], ["Ada's Calculus"])
        self.assertEqual(grace.get("/api/classes").json()["items"], [])
        self.assertEqual(grace.patch(f"/api/classes/{class_id}", json={"name": "Mine now"}).status_code, 404)

    def test_class_tutor_needs_an_account_but_quick_tutor_does_not(self) -> None:
        guest = self.other_client()
        quick = guest.post("/api/live-sessions", json={})
        self.assertEqual(quick.status_code, 200)

        member = self.other_client()
        self.signup(member, "Ada_L")
        class_id = member.post("/api/classes", json={"name": "Physics"}).json()["id"]

        self.assertEqual(
            guest.post("/api/live-sessions", json={"class_id": class_id}).status_code, 401
        )
        other = self.other_client()
        self.signup(other, "Grace_H")
        self.assertEqual(
            other.post("/api/live-sessions", json={"class_id": class_id}).status_code, 404
        )
        self.assertEqual(
            member.post("/api/live-sessions", json={"class_id": class_id}).status_code, 200
        )

    def test_logout_ends_the_session_even_if_the_cookie_is_replayed(self) -> None:
        self.signup(self.client, "Ada_L")
        stolen = self.client.cookies.get("jot_session")
        self.assertTrue(stolen)

        self.assertEqual(self.client.post("/api/auth/logout").status_code, 204)
        self.assertIsNone(self.client.get("/api/auth/me").json()["user"])
        self.assertEqual(self.client.get("/api/classes").status_code, 401)

        replay = self.other_client()
        replay.cookies.set("jot_session", stolen)
        self.assertIsNone(replay.get("/api/auth/me").json()["user"])
        self.assertEqual(replay.get("/api/classes").status_code, 401)

    def test_state_changing_auth_requests_need_an_allowed_origin(self) -> None:
        foreign = TestClient(self.client.app, headers={"origin": "https://evil.example"})
        response = foreign.post("/api/auth/login", json={"identifier": "a", "password": "b"})
        self.assertEqual(response.status_code, 403)



class ProfileAndPasswordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.client = make_client(self.storage.name)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def browser(self) -> TestClient:
        return TestClient(self.client.app, headers={"origin": ORIGIN})

    def signup(self, client: TestClient, username: str = "Ada_L", **extra: object):
        return client.post(
            "/api/auth/signup", json={"username": username, "password": "tutor-pass-1", **extra}
        )

    def test_guest_cannot_change_anything(self) -> None:
        guest = self.browser()
        self.assertEqual(guest.patch("/api/auth/me", json={"username": "Hacker"}).status_code, 401)
        self.assertEqual(
            guest.post(
                "/api/auth/password",
                json={"current_password": "tutor-pass-1", "new_password": "brand-new-pass"},
            ).status_code,
            401,
        )

    def test_change_username_and_email(self) -> None:
        self.signup(self.client)
        response = self.client.patch(
            "/api/auth/me", json={"username": "Ada_Lovelace", "email": "ada@example.com"}
        )
        self.assertEqual(response.status_code, 200)
        user = response.json()["user"]
        self.assertEqual(user["username"], "Ada_Lovelace")
        self.assertEqual(user["email"], "ada@example.com")
        # The display name was just the old username, so it follows the change.
        self.assertEqual(user["display_name"], "Ada_Lovelace")
        self.assertEqual(self.client.get("/api/auth/me").json()["user"]["username"], "Ada_Lovelace")

        old = self.browser().post(
            "/api/auth/login", json={"identifier": "Ada_L", "password": "tutor-pass-1"}
        )
        self.assertEqual(old.status_code, 401)
        new = self.browser().post(
            "/api/auth/login", json={"identifier": "ada@example.com", "password": "tutor-pass-1"}
        )
        self.assertEqual(new.status_code, 200)

    def test_custom_display_name_is_kept_and_email_can_be_removed(self) -> None:
        self.signup(self.client, email="ada@example.com", display_name="Ada the Great")
        response = self.client.patch("/api/auth/me", json={"username": "Ada_2", "email": ""})
        user = response.json()["user"]
        self.assertEqual(user["display_name"], "Ada the Great")
        self.assertIsNone(user["email"])

    def test_username_and_email_must_be_free_and_valid(self) -> None:
        self.signup(self.browser(), "Grace_H", email="grace@example.com")
        self.signup(self.client)

        taken = self.client.patch("/api/auth/me", json={"username": "grace_h"})
        self.assertEqual((taken.status_code, taken.json()["code"]), (409, "username_taken"))
        taken_email = self.client.patch("/api/auth/me", json={"email": "Grace@Example.com"})
        self.assertEqual((taken_email.status_code, taken_email.json()["code"]), (409, "email_taken"))

        for body in (
            {"username": "ab"},
            {"username": "bad name"},
            {"email": "nope"},
            {},
            {"username": None},
            {"role": "admin"},
        ):
            with self.subTest(body=body):
                self.assertEqual(self.client.patch("/api/auth/me", json=body).status_code, 400)
        self.assertEqual(self.client.get("/api/auth/me").json()["user"]["username"], "Ada_L")

    def test_changing_only_the_letter_case_of_your_own_username_is_allowed(self) -> None:
        self.signup(self.client)
        response = self.client.patch("/api/auth/me", json={"username": "ada_l"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["username"], "ada_l")

    def test_change_password_checks_the_old_one_and_logs_out_other_devices(self) -> None:
        phone, laptop = self.browser(), self.browser()
        self.signup(phone)
        laptop.post("/api/auth/login", json={"identifier": "Ada_L", "password": "tutor-pass-1"})
        self.assertEqual(laptop.get("/api/classes").status_code, 200)

        def change(current: str, new: str):
            return phone.post(
                "/api/auth/password", json={"current_password": current, "new_password": new}
            )

        wrong = change("not-my-password", "brand-new-pass")
        self.assertEqual((wrong.status_code, wrong.json()["code"]), (400, "incorrect_password"))
        same = change("tutor-pass-1", "tutor-pass-1")
        self.assertEqual((same.status_code, same.json()["code"]), (400, "password_unchanged"))
        self.assertEqual(change("tutor-pass-1", "short").status_code, 400)

        self.assertEqual(change("tutor-pass-1", "brand-new-pass").status_code, 204)

        # This device stays logged in; the other device is logged out.
        self.assertEqual(phone.get("/api/classes").status_code, 200)
        self.assertEqual(laptop.get("/api/classes").status_code, 401)

        old = self.browser().post(
            "/api/auth/login", json={"identifier": "Ada_L", "password": "tutor-pass-1"}
        )
        self.assertEqual(old.status_code, 401)
        new = self.browser().post(
            "/api/auth/login", json={"identifier": "Ada_L", "password": "brand-new-pass"}
        )
        self.assertEqual(new.status_code, 200)

    def test_wrong_current_password_attempts_are_rate_limited(self) -> None:
        limited = make_client(self.storage.name, auth_rate_limit=3)
        with limited:
            limited.post("/api/auth/signup", json={"username": "Ada_L", "password": "tutor-pass-1"})
            codes = [
                limited.post(
                    "/api/auth/password",
                    json={"current_password": "wrong-guess", "new_password": "brand-new-pass"},
                ).status_code
                for _ in range(4)
            ]
        self.assertEqual(codes, [400, 400, 400, 429])


class DemoAccountTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.settings = Settings(
            _env_file=None,
            gemini_api_key="test-key-not-used",
            file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
            sqlite_path=":memory:",
            storage_dir=self.storage.name,
        )
        self.database = await create_database(self.settings)
        self.addAsyncCleanup(self.database.close)

    async def test_only_flagged_accounts_are_demo_accounts(self) -> None:
        plain = await accounts.create_user(
            self.database, username="plain_user", password="tutor-pass-1"
        )
        demo = await accounts.create_user(
            self.database, username="demo_user", password="tutor-pass-1", is_demo=True
        )
        self.assertFalse(accounts.public_user(plain)["is_demo"])
        self.assertTrue(accounts.public_user(demo)["is_demo"])

        token, _ = await accounts.create_session(self.database, demo["id"], 1)
        session_user = await accounts.user_for_token(self.database, token)
        self.assertTrue(accounts.public_user(session_user)["is_demo"])

        logged_in = await accounts.authenticate(self.database, "demo_user", "tutor-pass-1")
        self.assertTrue(accounts.public_user(logged_in)["is_demo"])


class DemoColumnMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_from_before_the_demo_flag_is_upgraded_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = f"{folder}/old.sqlite3"
            old = sqlite3.connect(path)
            old.execute(
                "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE, "
                "email TEXT COLLATE NOCASE UNIQUE, display_name TEXT NOT NULL, password_hash TEXT NOT NULL, "
                "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))"
            )
            old.execute(
                "INSERT INTO users (id, username, display_name, password_hash) "
                "VALUES ('u1', 'Old_User', 'Old_User', 'x')"
            )
            old.commit()
            old.close()

            settings = Settings(
                _env_file=None,
                gemini_api_key="test-key-not-used",
                file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
                sqlite_path=path,
                storage_dir=folder,
            )
            database = await create_database(settings)
            try:
                row = await database.fetchrow("SELECT username, is_demo FROM users")
                self.assertEqual((row["username"], row["is_demo"]), ("Old_User", 0))
            finally:
                await database.close()


if __name__ == "__main__":
    unittest.main()
