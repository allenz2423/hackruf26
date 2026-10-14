"""Encrypted administrator-managed compatible-provider configuration."""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.ai_provider_settings import load_provider_config
from app.services.auth import create_user

ORIGIN = "http://localhost:5173"


class AIProviderSettingsAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = Settings(
            _env_file=None,
            gemini_api_key="test-gemini-key",
            file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
            sqlite_path=":memory:",
            storage_dir=self.temp.name,
            allow_guest_access=False,
            signup_mode="open",
            lean_tool_enabled=False,
            openai_compatible_base_url="https://env-provider.example/v1",
            openai_compatible_api_key="environment-provider-key",
            openai_compatible_model="env-model",
        )
        self.client = TestClient(create_app(settings_override=settings), headers={"origin": ORIGIN})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

        async def create_accounts() -> None:
            await create_user(
                self.client.app.state.database,
                username="Provider_Admin",
                password="tutor-pass-1",
                is_admin=True,
            )
            await create_user(
                self.client.app.state.database,
                username="Provider_Member",
                password="tutor-pass-1",
            )

        self.client.portal.call(create_accounts)

    def test_application_routes_text_services_to_compatible_client(self) -> None:
        app = self.client.app
        provider = app.state.openai_compatible_client
        self.assertIs(app.state.gemini_content_service._client, provider)
        self.assertIs(app.state.quiz_reasoning_service._client, provider)
        self.assertIs(app.state.study_tutor_service._client, provider)
        self.assertIs(app.state.tutor_memory_service.client, provider)
        self.assertIs(
            app.state.gemini_content_service._media_client,
            app.state.gemini_content_client,
        )

    def login(self, username: str) -> None:
        response = self.client.post(
            "/api/auth/login", json={"identifier": username, "password": "tutor-pass-1"}
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_provider_settings_are_admin_only_encrypted_and_applied_without_restart(self) -> None:
        self.assertEqual(self.client.get("/api/admin/ai-provider").status_code, 401)
        self.login("Provider_Member")
        self.assertEqual(self.client.get("/api/admin/ai-provider").status_code, 403)
        self.assertEqual(
            self.client.put(
                "/api/admin/ai-provider",
                json={"base_url": "https://provider.example/v1", "model": "m"},
            ).status_code,
            403,
        )
        self.client.post("/api/auth/logout")
        self.login("Provider_Admin")
        initial = self.client.get("/api/admin/ai-provider")
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertEqual(initial.json()["settings_source"], "environment")
        self.assertNotIn("environment-provider-key", initial.text)

        saved_key = "secret-provider-key-that-must-not-return"
        response = self.client.put(
            "/api/admin/ai-provider",
            json={
                "base_url": "https://api.example.test/v1",
                "model": "educational-model",
                "api_key": saved_key,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["provider_configured"])
        self.assertTrue(response.json()["api_key_configured"])
        self.assertNotIn(saved_key, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store, private")
        self.assertEqual(
            self.client.app.state.openai_compatible_client._current_config().api_key,
            saved_key,
        )

        row = self.client.portal.call(
            self.client.app.state.database.fetchrow,
            "SELECT base_url,model,encrypted_api_key FROM ai_provider_settings WHERE id=1",
        )
        self.assertNotIn(saved_key, row["encrypted_api_key"])
        self.assertNotEqual(row["encrypted_api_key"], saved_key)

        async def reload_config():
            return await load_provider_config(
                self.client.app.state.database, self.client.app.state.settings
            )

        reloaded = self.client.portal.call(reload_config)
        self.assertEqual(reloaded.get().api_key, saved_key)
        self.assertEqual(reloaded.get().model, "educational-model")

        changed = self.client.put(
            "/api/admin/ai-provider",
            json={"base_url": "https://other.example/v1", "model": "second-model"},
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(
            self.client.app.state.openai_compatible_client._current_config().api_key,
            saved_key,
        )

        cleared = self.client.put(
            "/api/admin/ai-provider",
            json={
                "base_url": "https://other.example/v1",
                "model": "second-model",
                "clear_api_key": True,
            },
        )
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertFalse(cleared.json()["api_key_configured"])

        reset = self.client.delete("/api/admin/ai-provider")
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertEqual(reset.json()["settings_source"], "environment")
        self.assertEqual(
            self.client.app.state.openai_compatible_client._current_config().api_key,
            "environment-provider-key",
        )
        self.assertIsNone(
            self.client.portal.call(
                self.client.app.state.database.fetchrow,
                "SELECT id FROM ai_provider_settings WHERE id=1",
            )
        )

    def test_provider_test_is_admin_only_and_does_not_return_provider_errors(self) -> None:
        self.login("Provider_Member")
        self.assertEqual(self.client.post("/api/admin/ai-provider/test", json={}).status_code, 403)
        self.client.post("/api/auth/logout")
        self.login("Provider_Admin")
        checker = AsyncMock()
        self.client.app.state.openai_compatible_client.test_connection = checker
        response = self.client.post("/api/admin/ai-provider/test", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["success"])
        checker.assert_awaited_once()

    def test_invalid_url_does_not_replace_valid_settings(self) -> None:
        self.login("Provider_Admin")
        invalid = self.client.put(
            "/api/admin/ai-provider",
            json={
                "base_url": "https://user:pass@provider.example/v1",
                "model": "model",
                "api_key": "provider-key",
            },
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertIsNone(
            self.client.portal.call(
                self.client.app.state.database.fetchrow,
                "SELECT id FROM ai_provider_settings WHERE id=1",
            )
        )
