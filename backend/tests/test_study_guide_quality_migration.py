"""Schema coverage for study guide quality review versioning."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.services.database import create_database


class StudyGuideQualityMigrationTests(unittest.IsolatedAsyncioTestCase):
    def make_settings(self, path: str) -> Settings:
        return Settings(
            _env_file=None,
            sqlite_path=path,
            file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
        )

    async def seed_material(self, database, material_id: str) -> None:
        await database.execute(
            "INSERT INTO users (id,username,display_name,password_hash) "
            "VALUES ('owner','quality_migration_owner','Owner','hash')"
        )
        await database.execute(
            "INSERT INTO course_classes (id,owner_id,name) "
            "VALUES ('class','owner','Quality migration class')"
        )
        await database.execute(
            "INSERT INTO study_materials "
            "(id,class_id,publication_state,kind,title,status,content) "
            "VALUES (?1,'class','published','study_guide','Existing guide','ready','{}')",
            material_id,
        )

    async def test_fresh_schema_defaults_quality_review_version_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "fresh.sqlite")
            database = await create_database(self.make_settings(path))
            self.addAsyncCleanup(database.close)

            columns = await database.fetch("PRAGMA table_info(study_materials)")
            quality_column = next(
                column for column in columns if column["name"] == "quality_review_version"
            )
            self.assertEqual(quality_column["dflt_value"], "0")

            await self.seed_material(database, "fresh-material")
            self.assertEqual(
                await database.fetchval(
                    "SELECT quality_review_version FROM study_materials WHERE id=?1",
                    "fresh-material",
                ),
                0,
            )

    async def test_legacy_schema_upgrade_preserves_material_and_assigns_version_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "legacy.sqlite")
            legacy_database = await create_database(self.make_settings(path))
            await self.seed_material(legacy_database, "legacy-material")
            await legacy_database.execute(
                "ALTER TABLE study_materials DROP COLUMN quality_review_version"
            )
            await legacy_database.close()

            upgraded_database = await create_database(self.make_settings(path))
            self.addAsyncCleanup(upgraded_database.close)

            columns = await upgraded_database.fetch("PRAGMA table_info(study_materials)")
            self.assertIn("quality_review_version", {column["name"] for column in columns})
            material = await upgraded_database.fetchrow(
                "SELECT id,title,status,content,quality_review_version "
                "FROM study_materials WHERE id=?1",
                "legacy-material",
            )
            self.assertEqual(
                material,
                {
                    "id": "legacy-material",
                    "title": "Existing guide",
                    "status": "ready",
                    "content": "{}",
                    "quality_review_version": 0,
                },
            )


if __name__ == "__main__":
    unittest.main()
