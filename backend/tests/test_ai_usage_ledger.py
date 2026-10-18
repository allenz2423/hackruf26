"""Admin cost rollups retain per-study-material totals and unknown attempts."""

from __future__ import annotations

import tempfile
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ORIGIN = "http://localhost:5173"


class MaterialCostLedgerTests(unittest.TestCase):
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
        response = self.client.post(
            "/api/auth/signup",
            json={"username": "CostAdmin", "password": "cost-test-pass"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.user_id = response.json()["user"]["id"]
        self.database = self.client.app.state.database
        self.call_db("execute", "UPDATE users SET is_admin=1 WHERE id=?1", self.user_id)

    def call_db(self, method: str, *args):
        return self.client.portal.call(getattr(self.database, method), *args)

    def add_material(self, kind: str, title: str, status: str = "ready") -> str:
        material_id = str(uuid4())
        content = '{"items":[]}' if status == "ready" else None
        self.call_db(
            "execute",
            "INSERT INTO study_materials "
            "(id,owner_id,resource_scope,kind,title,status,content) "
            "VALUES (?1,?2,'personal',?3,?4,?5,?6)",
            material_id,
            self.user_id,
            kind,
            title,
            status,
            content,
        )
        return material_id

    def add_attempt(
        self,
        material_id: str,
        *,
        outcome: str,
        cost_microusd: int | None,
        entry_type: str = "provider_call",
        operation_kind: str = "study_guide",
        operation_label: str = "Study Guide · Biology",
    ) -> None:
        self.call_db(
            "execute",
            "INSERT INTO ai_usage_ledger_entries "
            "(id,entry_type,operation_id,operation_kind,operation_label,provider_host,model,"
            "attempt,outcome,reported_cost_microusd) "
            "VALUES (?1,?2,?3,?4,?5,'openrouter.ai','test-model',1,?6,?7)",
            str(uuid4()),
            entry_type,
            material_id,
            operation_kind,
            operation_label,
            outcome,
            cost_microusd,
        )

    def test_admin_response_rolls_up_all_material_attempts_and_marks_unknown_cost(self) -> None:
        guide_id = self.add_material("study_guide", "Cell Membranes", "failed")
        quiz_id = self.add_material("quiz", "Membrane Quiz")
        old_id = self.add_material("flashcards", "Untracked legacy cards")
        self.add_attempt(guide_id, outcome="success", cost_microusd=24_000)
        self.add_attempt(guide_id, outcome="timeout", cost_microusd=None)
        self.add_attempt(quiz_id, outcome="success", cost_microusd=0)

        response = self.client.get("/api/admin/ai-usage")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("no-store", response.headers["cache-control"])
        materials = {item["material_id"]: item for item in response.json()["materials"]}
        self.assertEqual(materials[guide_id]["title"], "Cell Membranes")
        self.assertEqual(materials[guide_id]["kind"], "study_guide")
        self.assertEqual(materials[guide_id]["status"], "failed")
        self.assertEqual(materials[guide_id]["provider_calls"], 2)
        self.assertEqual(materials[guide_id]["known_cost_microusd"], 24_000)
        self.assertEqual(materials[guide_id]["unpriced_calls"], 1)
        self.assertEqual(materials[guide_id]["unsuccessful_calls"], 1)
        self.assertEqual(materials[guide_id]["cost_status"], "partial")
        self.assertEqual(materials[quiz_id]["known_cost_microusd"], 0)
        self.assertEqual(materials[quiz_id]["cost_status"], "complete")
        self.assertEqual(materials[old_id]["provider_calls"], 0)
        self.assertEqual(materials[old_id]["cost_status"], "not_recorded")
        self.assertNotIn("PRIVATE_PROMPT_TEXT", response.text)

    def test_companion_note_variants_and_guide_chat_roll_into_their_study_item(self) -> None:
        class_id = str(uuid4())
        note_id = str(uuid4())
        self.call_db(
            "execute",
            "INSERT INTO course_classes (id,owner_id,name) VALUES (?1,?2,'Biology')",
            class_id,
            self.user_id,
        )
        self.call_db(
            "execute",
            "INSERT INTO course_notes "
            "(id,class_id,created_by,title,kind,source_type,extracted_text,status,processing_progress) "
            "VALUES (?1,?2,?3,'Cell membranes','document','pasted_text','Osmosis', 'ready',100)",
            note_id,
            class_id,
            self.user_id,
        )
        source_updated_at = self.call_db(
            "fetchval", "SELECT updated_at FROM course_notes WHERE id=?1", note_id
        )
        for level, status in (("balanced", "ready"), ("detailed", "failed")):
            self.call_db(
                "execute",
                "INSERT INTO upload_documents "
                "(note_id,detail_level,job_id,source_updated_at,status,content,pdf,latex,original_text) "
                "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,'Osmosis')",
                note_id,
                level,
                str(uuid4()),
                source_updated_at,
                status,
                '{"sections":[]}' if status == "ready" else None,
                b"%PDF-test" if status == "ready" else None,
                b"\\documentclass{article}" if status == "ready" else None,
            )
        self.add_attempt(
            f"upload-document:{note_id}:balanced",
            outcome="success",
            cost_microusd=7_500,
            operation_kind="companion_notes",
            operation_label="Companion notes · balanced",
        )
        self.add_attempt(
            f"upload-document:{note_id}:detailed",
            outcome="timeout",
            cost_microusd=None,
            operation_kind="companion_notes",
            operation_label="Companion notes · detailed",
        )
        guide_id = self.add_material("study_guide", "Cell transport")
        self.add_attempt(
            guide_id,
            outcome="success",
            cost_microusd=3_200,
            operation_kind="study_guide_chat",
            operation_label="Study guide chat",
        )

        response = self.client.get("/api/admin/ai-usage")

        self.assertEqual(response.status_code, 200, response.text)
        costs = {
            (item["material_id"], item.get("detail_level")): item
            for item in response.json()["materials"]
        }
        balanced = costs[(note_id, "balanced")]
        self.assertEqual(balanced["kind"], "companion_notes")
        self.assertEqual(balanced["status"], "ready")
        self.assertEqual(balanced["provider_calls"], 1)
        self.assertEqual(balanced["known_cost_microusd"], 7_500)
        self.assertEqual(balanced["cost_status"], "complete")
        detailed = costs[(note_id, "detailed")]
        self.assertEqual(detailed["status"], "failed")
        self.assertEqual(detailed["provider_calls"], 1)
        self.assertEqual(detailed["unpriced_calls"], 1)
        self.assertEqual(detailed["cost_status"], "partial")
        self.assertEqual(costs[(guide_id, None)]["known_cost_microusd"], 3_200)
        activity = {item["operation_label"]: item for item in response.json()["entries"]}
        self.assertEqual(activity["Companion notes · detailed"]["material_status"], "failed")
        self.assertEqual(activity["Study guide chat"]["material_status"], "ready")

    def test_balance_adjustments_are_not_counted_as_generation_attempts(self) -> None:
        guide_id = self.add_material("study_guide", "Osmosis")
        self.add_attempt(guide_id, outcome="success", cost_microusd=1_500)
        self.add_attempt(
            guide_id, outcome="timeout", cost_microusd=None
        )
        self.call_db(
            "execute",
            "INSERT INTO ai_usage_ledger_entries "
            "(id,entry_type,operation_id,operation_kind,operation_label,reported_cost_microusd,"
            "balance_delta_microusd,adjustment_note) "
            "VALUES (?1,'balance_adjustment',?2,'balance','Balance adjustment',NULL,100_000,'test snapshot')",
            str(uuid4()),
            guide_id,
        )

        snapshot = self.client.get("/api/admin/ai-usage").json()
        item = next(row for row in snapshot["materials"] if row["material_id"] == guide_id)
        self.assertEqual(item["provider_calls"], 2)
        self.assertEqual(item["known_cost_microusd"], 1_500)
        self.assertEqual(item["unpriced_calls"], 1)
        self.assertEqual(item["cost_status"], "partial")


if __name__ == "__main__":
    unittest.main()
