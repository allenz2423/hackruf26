"""Accounts, sessions, and the guest/logged-in boundary."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.scripts import create_admin
from app.services import auth as accounts
from app.services.auth import hash_password, verify_password
from app.services.database import create_database

ORIGIN = "http://localhost:5173"


def make_client(storage_dir: str, **overrides: object) -> TestClient:
    # Preserve the pre-policy fixtures unless a test opts into the secure defaults.
    options: dict[str, object] = {"allow_guest_access": True, "signup_mode": "open"}
    options.update(overrides)
    settings = Settings(
        _env_file=None,
        gemini_api_key="test-key-not-used",
        file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
        sqlite_path=":memory:",
        storage_dir=storage_dir,
        lean_tool_enabled=False,
        **options,
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
            {"username": "Valid_Name", "password": "tutor-pass-1", "is_admin": True},
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

    def test_no_class_library_route_answers_a_guest(self) -> None:
        """Every /api route outside the open ones must turn guests away, including new ones."""
        open_prefixes = ("/api/auth/", "/api/live", "/api/tools/")
        guest = self.other_client()
        some_id = "11111111-1111-4111-8111-111111111111"

        # Original-file downloads use short-lived signed links minted by a logged-in endpoint,
        # so they need no session. A guest still cannot use a missing or forged link.
        signed_link = "/api/uploads/{upload_id}/file"
        forged = guest.get(
            signed_link.format(upload_id=some_id), params={"expires": 1, "signature": "forged"}
        )
        self.assertEqual(forged.status_code, 403)
        self.assertEqual(guest.get(signed_link.format(upload_id=some_id)).status_code, 400)

        checked = []
        for path, operations in self.client.app.openapi()["paths"].items():
            if not path.startswith("/api/") or path.startswith(open_prefixes) or path == signed_link:
                continue
            for method, spec in operations.items():
                # Fill in each parameter with a value the API accepts, so a request is only
                # stopped by the login check and not by input validation.
                url, query = path, {}
                for parameter in spec.get("parameters", []):
                    schema = parameter.get("schema", {})
                    if "enum" in schema:
                        value = schema["enum"][0]
                    elif schema.get("type") == "integer":
                        value = 1
                    else:
                        value = some_id
                    if parameter["in"] == "path":
                        url = url.replace("{" + parameter["name"] + "}", str(value))
                    elif parameter.get("required"):
                        query[parameter["name"]] = value

                has_body = method.upper() in {"POST", "PUT", "PATCH"}
                response = guest.request(
                    method.upper(), url, params=query, json={} if has_body else None
                )
                # A malformed body may be rejected as invalid before the login check runs; what
                # must never happen is a guest getting data or a lookup result.
                allowed = {401, 400, 413, 422} if has_body else {401}
                self.assertIn(
                    response.status_code, allowed, f"{method.upper()} {path} answered a guest"
                )
                checked.append(f"{method.upper()} {path}")

        self.assertGreater(len(checked), 10, checked)

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

    def test_class_color_and_icon_follow_the_account(self) -> None:
        self.signup(self.client)
        created = self.client.post("/api/classes", json={"name": "History", "color": "berry"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual((created.json()["color"], created.json()["icon"]), ("berry", None))
        class_id = created.json()["id"]

        # Changing only the look keeps the name; changing only the name keeps the look.
        styled = self.client.patch(f"/api/classes/{class_id}", json={"icon": "landmark"})
        self.assertEqual(styled.status_code, 200)
        self.assertEqual((styled.json()["name"], styled.json()["color"], styled.json()["icon"]), ("History", "berry", "landmark"))
        renamed = self.client.patch(f"/api/classes/{class_id}", json={"name": "World History"})
        self.assertEqual((renamed.json()["color"], renamed.json()["icon"]), ("berry", "landmark"))

        elsewhere = self.other_client()
        elsewhere.post("/api/auth/login", json={"identifier": "Ada_L", "password": "tutor-pass-1"})
        [listed] = elsewhere.get("/api/classes").json()["items"]
        self.assertEqual((listed["name"], listed["color"], listed["icon"]), ("World History", "berry", "landmark"))

        # null resets to the default look.
        reset = self.client.patch(f"/api/classes/{class_id}", json={"icon": None})
        self.assertIsNone(reset.json()["icon"])

    def test_class_look_only_accepts_the_fixed_choices(self) -> None:
        self.signup(self.client)
        class_id = self.client.post("/api/classes", json={"name": "Physics"}).json()["id"]
        for bad in (
            {"color": "#ff00ff"},
            {"color": "red; background: url(x)"},
            {"icon": "<svg onload=alert(1)>"},
            {"icon": "rocket"},
            {"name": None},
            {"name": "   "},
            {},
        ):
            with self.subTest(patch=bad):
                self.assertEqual(self.client.patch(f"/api/classes/{class_id}", json=bad).status_code, 400)
        self.assertEqual(self.client.post("/api/classes", json={"name": "Art", "color": "hotpink"}).status_code, 400)
        [physics] = self.client.get("/api/classes").json()["items"]
        self.assertEqual((physics["name"], physics["color"], physics["icon"]), ("Physics", None, None))

    def test_the_class_look_choices_match_the_ones_the_browser_offers(self) -> None:
        schema = self.client.app.openapi()["components"]["schemas"]["ClassPatch"]["properties"]
        enums = {field: next(option["enum"] for option in schema[field]["anyOf"] if "enum" in option) for field in ("color", "icon")}
        self.assertEqual(len(enums["color"]), 9)
        self.assertEqual(len(enums["icon"]), 16)

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

    def test_class_tutor_can_limit_context_to_chosen_uploads(self) -> None:
        self.signup(self.client, "Ada_L")
        class_id = self.client.post("/api/classes", json={"name": "Physics"}).json()["id"]
        app = self.client.app
        note_ids = {}
        for title in ("Kinematics notes", "Old midterm"):
            note_ids[title] = str(uuid4())
            self.client.portal.call(
                app.state.database.execute,
                "INSERT INTO course_notes (id, class_id, title, source_type, extracted_text, status, processing_progress) "
                "VALUES (?1, ?2, ?3, 'pasted_text', ?4, 'ready', 100)",
                note_ids[title], class_id, title, f"Text of {title}",
            )

        def context_for(body: dict) -> str:
            response = self.client.post("/api/live-sessions", json={"class_id": class_id, **body})
            self.assertEqual(response.status_code, 200)
            ticket = self.client.portal.call(app.state.live_session_ticket_store.consume, response.json()["ticket"])
            return ticket.class_context

        everything = context_for({})
        self.assertIn("Kinematics notes", everything)
        self.assertIn("Old midterm", everything)
        chosen = context_for({"upload_ids": [note_ids["Kinematics notes"]]})
        self.assertIn("Kinematics notes", chosen)
        self.assertNotIn("Old midterm", chosen)
        none = context_for({"upload_ids": []})
        self.assertIn("Physics", none)
        self.assertNotIn("Source:", none)

        # Another student's upload id never pulls in their notes, and ids need a class.
        other = self.other_client()
        self.signup(other, "Grace_H")
        other_class = other.post("/api/classes", json={"name": "Grace's"}).json()["id"]
        foreign = str(uuid4())
        self.client.portal.call(
            app.state.database.execute,
            "INSERT INTO course_notes (id, class_id, title, source_type, extracted_text, status, processing_progress) "
            "VALUES (?1, ?2, 'Secret', 'pasted_text', 'private', 'ready', 100)",
            foreign, other_class,
        )
        self.assertNotIn("Secret", context_for({"upload_ids": [foreign]}))
        self.assertEqual(self.client.post("/api/live-sessions", json={"upload_ids": [foreign]}).status_code, 400)
        self.assertEqual(
            self.client.post("/api/live-sessions", json={"class_id": class_id, "upload_ids": ["not-a-uuid"]}).status_code,
            400,
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

    def test_avatar_is_saved_per_account_and_can_be_cleared(self) -> None:
        self.signup(self.client)
        self.assertIsNone(self.client.get("/api/auth/me").json()["user"]["avatar"])

        mine = {"color": "berry", "face": "wink", "accessory": "crown"}
        response = self.client.patch("/api/auth/me", json={"avatar": mine})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["avatar"], mine)
        # Changing only the avatar leaves the rest of the account alone.
        self.assertEqual(response.json()["user"]["username"], "Ada_L")

        # It follows the account to another browser, and not to anyone else.
        elsewhere = self.browser()
        elsewhere.post("/api/auth/login", json={"identifier": "Ada_L", "password": "tutor-pass-1"})
        self.assertEqual(elsewhere.get("/api/auth/me").json()["user"]["avatar"], mine)
        other = self.browser()
        self.signup(other, "Grace_H")
        self.assertIsNone(other.get("/api/auth/me").json()["user"]["avatar"])

        # Changing details later keeps the avatar; sending null removes it.
        kept = self.client.patch("/api/auth/me", json={"email": "ada@example.com"})
        self.assertEqual(kept.json()["user"]["avatar"], mine)
        cleared = self.client.patch("/api/auth/me", json={"avatar": None})
        self.assertIsNone(cleared.json()["user"]["avatar"])

    def test_avatar_only_accepts_the_fixed_choices(self) -> None:
        self.signup(self.client)
        good = {"color": "grape", "face": "grin", "accessory": "none"}
        for bad in (
            {**good, "color": "hotpink"},
            {**good, "face": "<script>alert(1)</script>"},
            {**good, "accessory": "jetpack"},
            {**good, "extra": "field"},
            {"color": "grape", "face": "grin"},
            {**good, "color": 7},
            "slate",
        ):
            with self.subTest(avatar=bad):
                response = self.client.patch("/api/auth/me", json={"avatar": bad})
                self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.client.get("/api/auth/me").json()["user"]["avatar"])

    def test_the_avatar_choices_match_the_ones_the_browser_offers(self) -> None:
        schemas = self.client.app.openapi()["components"]["schemas"]["Avatar"]["properties"]
        self.assertEqual(len(schemas["color"]["enum"]), 9)
        self.assertEqual(len(schemas["face"]["enum"]), 5)
        self.assertEqual(
            schemas["accessory"]["enum"],
            ["none", "glasses", "cap", "headphones", "party", "crown"],
        )

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


class SignupApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.client = make_client(
            self.storage.name,
            allow_guest_access=False,
            signup_mode="approval",
        )
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def _create_account(self, username: str, *, is_admin: bool = False) -> dict:
        async def create_account() -> dict:
            return await accounts.create_user(
                self.client.app.state.database,
                username=username,
                password="tutor-pass-1",
                is_admin=is_admin,
            )

        return self.client.portal.call(create_account)

    def _login_admin(self) -> None:
        response = self.client.post(
            "/api/auth/login",
            json={"identifier": "Site_Admin", "password": "tutor-pass-1"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_access_defaults_are_closed_and_guest_tools_require_opt_in(self) -> None:
        default_settings = Settings(
            _env_file=None,
            file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
        )
        self.assertFalse(default_settings.allow_guest_access)
        self.assertEqual(default_settings.signup_mode, "closed")

        me = self.client.get("/api/auth/me").json()
        self.assertIsNone(me["user"])
        self.assertFalse(me["guest_access_enabled"])
        self.assertEqual(me["signup_mode"], "approval")
        for response in (
            self.client.post("/api/live-sessions", json={}),
            self.client.post("/api/tools/calculate", json={"expression": "1 + 1"}),
            self.client.post(
                "/api/tools/verify-lean",
                json={"proposition": "True", "proof": "by trivial"},
            ),
            self.client.post(
                "/api/study-tutor",
                json={
                    "question": {"prompt": "Question", "choices": []},
                    "messages": [{"role": "user", "text": "Help me understand"}],
                },
            ),
        ):
            self.assertEqual(response.status_code, 401, response.text)
            self.assertEqual(response.json()["code"], "authentication_required")

        default_client = make_client(
            self.storage.name,
            allow_guest_access=default_settings.allow_guest_access,
            signup_mode=default_settings.signup_mode,
        )
        with default_client:
            default_me = default_client.get("/api/auth/me").json()
            self.assertFalse(default_me["guest_access_enabled"])
            self.assertEqual(default_me["signup_mode"], "closed")
            closed_signup = default_client.post(
                "/api/auth/signup",
                json={"username": "Default_Closed", "password": "tutor-pass-1"},
            )
            self.assertEqual(closed_signup.status_code, 403)
            self.assertEqual(closed_signup.json()["code"], "signup_closed")

    def test_signup_waits_for_admin_and_approved_user_can_log_in(self) -> None:
        pending = self.client.post(
            "/api/auth/signup",
            json={
                "username": "New_Student",
                "password": "tutor-pass-1",
                "email": "student@example.com",
            },
        )
        self.assertEqual(pending.status_code, 202, pending.text)
        self.assertEqual(pending.json()["status"], "pending")
        self.assertNotIn("set-cookie", pending.headers)
        self.assertNotIn("password", str(pending.json()).lower())
        self.assertEqual(
            self.client.post(
                "/api/auth/login",
                json={"identifier": "New_Student", "password": "tutor-pass-1"},
            ).status_code,
            401,
        )

        self._create_account("Site_Admin", is_admin=True)
        self._login_admin()
        listed = self.client.get("/api/admin/signup-requests")
        self.assertEqual(listed.status_code, 200, listed.text)
        [request] = listed.json()["requests"]
        self.assertEqual(request["username"], "New_Student")
        self.assertNotIn("password_hash", request)
        decision = self.client.post(f"/api/admin/signup-requests/{request['id']}/approve", json={})
        self.assertEqual(decision.status_code, 200, decision.text)
        self.assertEqual(decision.json()["status"], "approved")

        browser = TestClient(self.client.app, headers={"origin": ORIGIN})
        login = browser.post(
            "/api/auth/login",
            json={"identifier": "New_Student", "password": "tutor-pass-1"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.json()["user"]["username"], "New_Student")
        row = self.client.portal.call(
            self.client.app.state.database.fetchrow,
            "SELECT status, password_hash FROM signup_requests WHERE id = ?1",
            request["id"],
        )
        self.assertEqual((row["status"], row["password_hash"]), ("approved", None))

    def test_rejection_clears_password_hash_and_non_admin_cannot_review(self) -> None:
        response = self.client.post(
            "/api/auth/signup",
            json={"username": "Rejected_User", "password": "tutor-pass-1"},
        )
        request_id = self.client.portal.call(
            self.client.app.state.database.fetchval,
            "SELECT id FROM signup_requests WHERE username = ?1",
            "Rejected_User",
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.client.get("/api/admin/signup-requests").status_code, 401)

        self._create_account("Ordinary_User")
        self.client.post(
            "/api/auth/login",
            json={"identifier": "Ordinary_User", "password": "tutor-pass-1"},
        )
        self.assertEqual(self.client.get("/api/admin/signup-requests").status_code, 403)
        self.assertEqual(
            self.client.post(
                f"/api/admin/signup-requests/{request_id}/reject", json={}
            ).status_code,
            403,
        )

        self.client.post("/api/auth/logout")
        self._create_account("Site_Admin", is_admin=True)
        self._login_admin()
        rejected = self.client.post(f"/api/admin/signup-requests/{request_id}/reject", json={})
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(rejected.json()["status"], "rejected")
        row = self.client.portal.call(
            self.client.app.state.database.fetchrow,
            "SELECT status, password_hash FROM signup_requests WHERE id = ?1",
            request_id,
        )
        self.assertEqual((row["status"], row["password_hash"]), ("rejected", None))
        repeated = self.client.post(
            f"/api/admin/signup-requests/{request_id}/reject", json={}
        )
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["status"], "rejected")
        opposite = self.client.post(
            f"/api/admin/signup-requests/{request_id}/approve", json={}
        )
        self.assertEqual(opposite.status_code, 409)

    def test_closed_signup_and_explicit_open_signup(self) -> None:
        closed = make_client(self.storage.name, allow_guest_access=False, signup_mode="closed")
        with closed:
            response = closed.post(
                "/api/auth/signup",
                json={"username": "Closed_User", "password": "tutor-pass-1"},
            )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["code"], "signup_closed")

        open_mode = make_client(
            self.storage.name,
            allow_guest_access=False,
            signup_mode="open",
        )
        with open_mode:
            response = open_mode.post(
                "/api/auth/signup",
                json={"username": "Open_User", "password": "tutor-pass-1"},
            )
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["status"], "active")
            self.assertTrue(response.headers.get("set-cookie"))
            self.assertFalse(open_mode.get("/api/auth/me").json()["guest_access_enabled"])


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


class AdminBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_creates_only_the_first_admin_from_confirmed_hidden_password(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(
                _env_file=None,
                file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
                sqlite_path=f"{folder}/accounts.sqlite3",
            )
            with (
                patch("app.scripts.create_admin.get_settings", return_value=settings),
                patch(
                    "app.scripts.create_admin.getpass.getpass",
                    side_effect=["tutor-pass-1", "tutor-pass-1"],
                ),
            ):
                self.assertEqual(await create_admin.run(["First_Admin"]), 0)

            database = await create_database(settings)
            try:
                admin = await accounts.authenticate(database, "First_Admin", "tutor-pass-1")
                self.assertTrue(admin["is_admin"])
                self.assertEqual(
                    await database.fetchval("SELECT COUNT(*) FROM users WHERE is_admin = 1"),
                    1,
                )
            finally:
                await database.close()

            with (
                patch("app.scripts.create_admin.get_settings", return_value=settings),
                patch(
                    "app.scripts.create_admin.getpass.getpass",
                    side_effect=["another-pass", "another-pass"],
                ),
            ):
                self.assertEqual(await create_admin.run(["Second_Admin"]), 2)

            database = await create_database(settings)
            try:
                self.assertEqual(
                    await database.fetchval("SELECT COUNT(*) FROM users WHERE is_admin = 1"),
                    1,
                )
            finally:
                await database.close()


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
                row = await database.fetchrow(
                    "SELECT username, is_demo, is_admin, avatar FROM users"
                )
                self.assertEqual(
                    (row["username"], row["is_demo"], row["is_admin"], row["avatar"]),
                    ("Old_User", 0, 0, None),
                )
            finally:
                await database.close()


if __name__ == "__main__":
    unittest.main()
