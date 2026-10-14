from __future__ import annotations

import unittest

from app.config import Settings


class UploadLimitSettingsTests(unittest.TestCase):
    def test_default_upload_limit_is_100_mib(self) -> None:
        settings = Settings(
            _env_file=None,
            file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
        )

        self.assertEqual(settings.max_upload_mb, 100)
        self.assertEqual(settings.max_upload_bytes, 100 * 1024 * 1024)

    def test_upload_limit_remains_configurable(self) -> None:
        settings = Settings(
            _env_file=None,
            file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
            max_upload_mb=75,
        )

        self.assertEqual(settings.max_upload_bytes, 75 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
