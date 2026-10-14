"""Provider client deadline configuration tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.config import Settings
from app.services.gemini_client import create_genai_client


class GeminiClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            _env_file=None,
            gemini_api_key="test-api-key",
            file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
            sqlite_path=":memory:",
            lean_tool_enabled=False,
        )

    @patch("app.services.gemini_client.genai.Client")
    def test_content_client_can_disable_provider_request_timeout(self, client) -> None:
        create_genai_client(self.settings, no_timeout=True)
        http_options = client.call_args.kwargs["http_options"]
        self.assertIsNone(http_options.timeout)

    @patch("app.services.gemini_client.genai.Client")
    def test_default_client_keeps_the_configured_timeout(self, client) -> None:
        self.settings.gemini_timeout_seconds = 3
        create_genai_client(self.settings)
        http_options = client.call_args.kwargs["http_options"]
        self.assertEqual(http_options.timeout, 3000)


if __name__ == "__main__":
    unittest.main()
