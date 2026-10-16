"""Bounded chemistry diagrams and process-pathway contract regressions."""

from __future__ import annotations

import io
import unittest
from unittest.mock import Mock
from uuid import uuid4

from pydantic import ValidationError
from pypdf import PdfReader

from app.api.schemas import StudyGuideBlock, StudyGuideSectionV2
from app.config import Settings
from app.services.gemini_content import GeminiContentService, _StudyGuideSectionBatch
from app.services.guide_templates import guide_block_alt_text, guide_block_fallback
from app.services.study_files import compiler_path, create_guide_files
from app.services.study_guide_tools import (
    guide_tool_catalog_prompt,
    guide_tool_instructions,
)


def molecule_block(molecule_id: str = "water") -> StudyGuideBlock:
    return StudyGuideBlock.model_validate(
        {
            "type": "labeled_diagram",
            "template_id": "molecule_2d_v1",
            "title": "Molecular structure",
            "parameters": {"molecule_id": molecule_id},
        }
    )


def pathway_block(stages: list[dict] | None = None) -> StudyGuideBlock:
    if stages is None:
        stages = [
            {"id": "first", "title": "First stage", "explanation": "Begin the process."},
            {"id": "second", "title": "Second stage", "explanation": "Continue the process."},
        ]
    return StudyGuideBlock.model_validate(
        {
            "type": "labeled_diagram",
            "template_id": "process_pathway_v1",
            "title": "Ordered process",
            "parameters": {"stages": stages},
        }
    )


class ChemistryGuideActivityTests(unittest.TestCase):
    def test_molecule_schema_accepts_only_fixed_structure_ids(self) -> None:
        for molecule_id in ("water", "carbon_dioxide", "methane", "ammonia"):
            with self.subTest(molecule_id=molecule_id):
                self.assertEqual(molecule_block(molecule_id).parameters.molecule_id, molecule_id)

        valid = {
            "type": "labeled_diagram",
            "template_id": "molecule_2d_v1",
            "title": "Molecule",
            "parameters": {"molecule_id": "water"},
        }
        invalid_payloads = (
            {"molecule_id": "benzene"},
            {"molecule_id": "water", "atoms": [{"element": "C"}]},
            {"molecule_id": True},
            {"molecule_id": "water", "unexpected": "field"},
        )
        for parameters in invalid_payloads:
            with self.subTest(parameters=parameters), self.assertRaises(ValidationError):
                StudyGuideBlock.model_validate({**valid, "parameters": parameters})

    def test_process_pathway_bounds_stage_count_fields_and_identity(self) -> None:
        stages_at_bounds = [
            {"id": f"stage_{index}", "title": "Step", "explanation": "A short explanation."}
            for index in range(12)
        ]
        self.assertEqual(len(pathway_block(stages_at_bounds).parameters.stages), 12)

        invalid_stages = (
            [],
            [{"id": "only", "title": "One", "explanation": "Incomplete."}],
            [
                {"id": f"stage_{index}", "title": "Step", "explanation": "Text."}
                for index in range(13)
            ],
            [
                {"id": "same", "title": "One", "explanation": "First."},
                {"id": "same", "title": "Two", "explanation": "Second."},
            ],
            [
                {"id": "bad/id", "title": "One", "explanation": "First."},
                {"id": "later", "title": "Two", "explanation": "Second."},
            ],
            [
                {"id": "first", "title": 42, "explanation": "First."},
                {"id": "later", "title": "Two", "explanation": "Second."},
            ],
            [
                {"id": "first", "title": "One", "explanation": "x" * 601},
                {"id": "later", "title": "Two", "explanation": "Second."},
            ],
            [
                {"id": "first", "title": "One", "explanation": "x" * 2_000},
                {"id": "later", "title": "Two", "explanation": "x" * 2_001},
            ],
        )
        for stages in invalid_stages:
            with self.subTest(stage_count=len(stages)), self.assertRaises(ValidationError):
                pathway_block(stages)

        with self.assertRaises(ValidationError):
            StudyGuideBlock.model_validate(
                {
                    "type": "labeled_diagram",
                    "template_id": "process_pathway_v1",
                    "title": "Untrusted template",
                    "parameters": {
                        "stages": [
                            {"id": "first", "title": "One", "explanation": "A."},
                            {"id": "second", "title": "Two", "explanation": "B."},
                        ]
                    },
                    "extra": "rejected",
                }
            )

    def test_fixed_fallbacks_and_alt_text_are_stable_and_descriptive(self) -> None:
        for molecule_id, expected, alt_fragment in (
            ("water", "Water (H₂O)", "bond angles are not to scale"),
            ("carbon_dioxide", "Carbon dioxide (CO₂)", "double bond to each"),
            ("methane", "Methane (CH₄)", "does not represent methane's 3D"),
            ("ammonia", "Ammonia (NH₃)", "does not represent ammonia's 3D"),
        ):
            with self.subTest(molecule_id=molecule_id):
                block = molecule_block(molecule_id).model_dump(mode="json")
                self.assertTrue(guide_block_fallback(block).startswith(expected + ": "))
                self.assertEqual(guide_block_fallback(block), guide_block_fallback(block))
                self.assertIn(expected.split(" (")[0], guide_block_alt_text(block))
                self.assertIn(alt_fragment, guide_block_alt_text(block))

        pathway = pathway_block().model_dump(mode="json")
        fallback = guide_block_fallback(pathway)
        self.assertEqual(
            fallback,
            "Process pathway: First stage: Begin the process. "
            "→ Second stage: Continue the process.",
        )
        self.assertEqual(fallback, guide_block_fallback(pathway))
        self.assertIn("2 selectable stages", guide_block_alt_text(pathway))

    def test_tool_detail_is_opt_in_and_only_requested_tools_are_expanded(self) -> None:
        catalog = guide_tool_catalog_prompt()
        self.assertIn("molecule_diagram", catalog)
        self.assertIn("process_pathway", catalog)
        self.assertNotIn("`molecule_2d_v1`", catalog)

        instructions = guide_tool_instructions(["process_pathway", "molecule_diagram"])
        self.assertIn("molecule_2d_v1", instructions)
        self.assertIn("process_pathway_v1", instructions)
        self.assertIn("not a 3D geometry", instructions)
        self.assertIn("2–12 ordered stages", instructions)
        self.assertNotIn("animal_cell_map_v1", instructions)

        upload_id = str(uuid4())
        source_section = StudyGuideSectionV2.model_validate(
            {
                "heading": "Chemistry",
                "sources": [{"upload_id": upload_id}],
                "blocks": [{"type": "paragraph", "text": "The source describes molecules."}],
            }
        )
        def make_batch(requested: list[str]) -> _StudyGuideSectionBatch:
            return _StudyGuideSectionBatch(
                format="study_guide",
                schema_version=2,
                sections=[source_section],
                requested_tool_ids=requested,
            )
        service = GeminiContentService(
            None,
            Settings(
                _env_file=None,
                sqlite_path=":memory:",
                file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
            ),
        )
        service._generate_json = Mock(
            side_effect=[make_batch(["molecule_diagram", "process_pathway"]), make_batch([])]
        )
        service._generate_study_guide_sections(
            base_prompt="Write a source-grounded chemistry guide.",
            count=1,
            system_instruction="Use the supplied source.",
            allowed_source_ids={upload_id},
        )
        draft_call, revision_call = service._generate_json.call_args_list
        self.assertIn(
            "Available activity tools (brief catalog)",
            draft_call.kwargs["system_instruction"],
        )
        self.assertNotIn("molecule_2d_v1", draft_call.args[0])
        self.assertIn("molecule_2d_v1", revision_call.args[0])
        self.assertIn("process_pathway_v1", revision_call.args[0])
        self.assertNotIn("animal_cell_map_v1", revision_call.args[0])

    @unittest.skipUnless(
        compiler_path(Settings(
            _env_file=None,
            file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
        )),
        "Tectonic is not installed",
    )
    def test_static_latex_and_pdf_preserve_molecule_and_all_pathway_stages(self) -> None:
        settings = Settings(
            _env_file=None,
            file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
        )
        content = {
            "format": "study_guide",
            "schema_version": 2,
            "sections": [
                {
                    "heading": "Chemistry activities",
                    "body": "The molecule and pathway remain available as text.",
                    "sources": [],
                    "blocks": [
                        {
                            "type": "labeled_diagram",
                            "template_id": "molecule_2d_v1",
                            "title": "Water structure",
                            "parameters": {"molecule_id": "water"},
                        },
                        {
                            "type": "labeled_diagram",
                            "template_id": "process_pathway_v1",
                            "title": "Water cycle",
                            "parameters": {
                                "stages": [
                                    {
                                        "id": "evaporation",
                                        "title": "Evaporation",
                                        "explanation": "Liquid water changes to vapor.",
                                    },
                                    {
                                        "id": "condensation",
                                        "title": "Condensation",
                                        "explanation": "Water vapor forms droplets.",
                                    },
                                ]
                            },
                        },
                    ],
                }
            ],
        }
        files = create_guide_files(
            settings,
            title="Chemistry guide",
            class_name="Chemistry",
            material_id=uuid4(),
            content=content,
            sources=[],
            strict_math=True,
        )

        latex = files["latex"].decode("utf-8")
        self.assertIn(r"\begin{picture}", latex)
        self.assertIn("Water (H₂O)", latex)
        self.assertIn("Evaporation", latex)
        self.assertIn("Condensation", latex)
        self.assertTrue(files["pdf"].startswith(b"%PDF-"))
        extracted = "".join(
            page.extract_text() or ""
            for page in PdfReader(io.BytesIO(files["pdf"])).pages
        )
        self.assertIn("Water (H₂O)", extracted)
        self.assertIn("Evaporation", extracted)
        self.assertIn("Condensation", extracted)


if __name__ == "__main__":
    unittest.main()
