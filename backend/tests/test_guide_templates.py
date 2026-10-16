"""Validation and deterministic fallbacks for versioned guide blocks."""

from __future__ import annotations

import unittest
from uuid import uuid4

from google.genai import types
from pydantic import ValidationError

from app.api.schemas import StudyGuideBlock, StudyGuideContent
from app.services.gemini_content import (
    ContentGenerationFailure,
    _normalized_study_guide,
    _provider_response_schema,
    _StudyGuideSectionBatch,
)
from app.services.guide_templates import guide_block_alt_text, guide_block_fallback


def paragraph_section(upload_id: str) -> dict:
    return {
        "heading": "Vectors",
        "sources": [{"upload_id": upload_id}],
        "blocks": [{"type": "paragraph", "text": "Resolve the vector into components."}],
    }


class GuideBlockValidationTests(unittest.TestCase):
    def test_provider_schema_is_accepted_by_google_sdk(self) -> None:
        response_schema = _provider_response_schema(_StudyGuideSectionBatch)
        config = types.GenerateContentConfig(response_schema=response_schema)
        self.assertIsNotNone(config.response_schema)

    def test_interactive_template_parameters_are_typed_and_bounded(self) -> None:
        valid_force = {
            "type": "interactive_model",
            "template_id": "force_components_v1",
            "title": "Resolve a force",
            "parameters": {"force_n": 100, "angle_deg": 30},
        }
        block = StudyGuideBlock.model_validate(valid_force)
        self.assertEqual(block.parameters.force_n, 100)

        for parameters in (
            {"force_n": True, "angle_deg": 30},
            {"force_n": float("nan"), "angle_deg": 30},
            {"force_n": 100, "angle_deg": 90.05},
            {"force_n": 100, "angle_deg": 30, "unexpected": "field"},
        ):
            with self.subTest(parameters=parameters), self.assertRaises(ValidationError):
                StudyGuideBlock.model_validate({**valid_force, "parameters": parameters})

    def test_all_block_shapes_reject_mismatched_or_untrusted_payloads(self) -> None:
        invalid_blocks = (
            {"type": "paragraph", "text": "Text", "template_id": "force_components_v1"},
            {"type": "formula", "expression": "x = 1", "text": "extra prose"},
            {
                "type": "interactive_model",
                "template_id": "not_an_app_template",
                "title": "Unknown",
                "parameters": {},
            },
            {
                "type": "labeled_diagram",
                "template_id": "molecule_2d_v1",
                "title": "Molecule",
                "parameters": {"molecule_id": "custom"},
            },
            {
                "type": "example_steps",
                "title": "Example",
                "steps": [{"title": "Only step", "explanation": "Incomplete."}],
            },
        )
        for block in invalid_blocks:
            with self.subTest(block=block), self.assertRaises(ValidationError):
                StudyGuideBlock.model_validate(block)

    def test_process_pathways_bound_count_and_unique_stage_ids(self) -> None:
        payload = {
            "type": "labeled_diagram",
            "template_id": "process_pathway_v1",
            "title": "Cellular respiration",
            "parameters": {
                "stages": [
                    {"id": "start", "title": "Glycolysis", "explanation": "Glucose is split."},
                    {"id": "start", "title": "Later stage", "explanation": "Continue."},
                ]
            },
        }
        with self.assertRaises(ValidationError):
            StudyGuideBlock.model_validate(payload)
        payload["parameters"]["stages"][1]["id"] = "later"
        self.assertEqual(StudyGuideBlock.model_validate(payload).type, "labeled_diagram")

    def test_animal_cell_map_accepts_only_unique_fixed_organelle_ids(self) -> None:
        payload = {
            "type": "labeled_diagram",
            "template_id": "animal_cell_map_v1",
            "title": "Cell structures in the source",
            "parameters": {"focus_ids": ["nucleus", "mitochondria", "ribosomes"]},
        }
        block = StudyGuideBlock.model_validate(payload)
        self.assertEqual(block.parameters.focus_ids, ["nucleus", "mitochondria", "ribosomes"])
        for focus_ids in (
            [],
            ["nucleus", "nucleus"],
            ["nucleus", "made_up_organelle"],
            ["nucleus"] * 10,
        ):
            with self.subTest(focus_ids=focus_ids), self.assertRaises(ValidationError):
                StudyGuideBlock.model_validate({**payload, "parameters": {"focus_ids": focus_ids}})

    def test_guide_schema_requires_version_to_match_section_shape(self) -> None:
        upload_id = str(uuid4())
        v2_section = paragraph_section(upload_id)
        StudyGuideContent.model_validate(
            {"format": "study_guide", "schema_version": 2, "sections": [v2_section]}
        )
        with self.assertRaises(ValidationError):
            StudyGuideContent.model_validate(
                {"format": "study_guide", "schema_version": 1, "sections": [v2_section]}
            )
        with self.assertRaises(ValidationError):
            StudyGuideContent.model_validate(
                {
                    "format": "study_guide",
                    "schema_version": 2,
                    "sections": [{"heading": "Legacy", "body": "Old guide."}],
                }
            )


class GuideTemplateFallbackTests(unittest.TestCase):
    def test_fallbacks_cover_every_app_owned_template(self) -> None:
        blocks = [
            {
                "type": "interactive_model",
                "template_id": "force_components_v1",
                "parameters": {"force_n": 100, "angle_deg": 30},
            },
            {
                "type": "interactive_model",
                "template_id": "linear_graph_v1",
                "parameters": {"slope": 2, "intercept": -1},
            },
            {
                "type": "interactive_model",
                "template_id": "first_order_response_v1",
                "parameters": {"initial_value": 0, "final_value": 10, "time_constant_s": 2},
            },
            {
                "type": "interactive_model",
                "template_id": "resistor_divider_v1",
                "parameters": {"voltage_v": 12, "r1_ohm": 1000, "r2_ohm": 1000},
            },
            {
                "type": "interactive_model",
                "template_id": "binary_search_trace_v1",
                "parameters": {"values": [1, 4, 8, 12, 16], "target": 8},
            },
            {
                "type": "labeled_diagram",
                "template_id": "molecule_2d_v1",
                "parameters": {"molecule_id": "water"},
            },
            {
                "type": "labeled_diagram",
                "template_id": "process_pathway_v1",
                "parameters": {
                    "stages": [
                        {"id": "a", "title": "Input", "explanation": "Start."},
                        {"id": "b", "title": "Output", "explanation": "Finish."},
                    ]
                },
            },
            {
                "type": "labeled_diagram",
                "template_id": "animal_cell_map_v1",
                "parameters": {"focus_ids": ["nucleus", "mitochondria"]},
            },
            {
                "type": "interactive_model",
                "template_id": "sampling_demo_v1",
                "parameters": {
                    "method": "stratified",
                    "sample_size": 4,
                    "seed": 42,
                    "random_assignment": False,
                },
            },
        ]
        expected_fragments = (
            "horizontal component is 86.6 N",
            "y = 2x - 1",
            "After one time constant it is about 6.32",
            "output across R₂ is 6 V",
            "target is at index 2",
            "Water (H₂O)",
            "Input: Start. → Output: Finish.",
            "Nucleus and nuclear envelope",
            "App-authored synthetic illustration, not source data",
        )
        self.assertEqual(len(blocks), len(expected_fragments))
        for block, fragment in zip(blocks, expected_fragments, strict=True):
            with self.subTest(template=block["template_id"]):
                self.assertIn(fragment, guide_block_fallback(block))
                self.assertTrue(guide_block_alt_text(block))

    def test_normalization_assigns_server_ids_and_rejects_unauthorized_citations(self) -> None:
        upload_id = str(uuid4())
        section = paragraph_section(upload_id)
        normalized = _normalized_study_guide([section], {upload_id})
        self.assertEqual(normalized["schema_version"], 2)
        self.assertEqual(normalized["sections"][0]["id"], "section-1")
        self.assertEqual(normalized["sections"][0]["blocks"][0]["id"], "section-1-block-1")
        self.assertIn("Resolve the vector", normalized["sections"][0]["body"])

        with self.assertRaises(ContentGenerationFailure) as raised:
            _normalized_study_guide([section], {str(uuid4())})
        self.assertEqual(raised.exception.diagnostic_code, "invalid_citation")

    def test_block_local_citations_are_validated_against_allowed_sources(self) -> None:
        allowed_id, other_id = str(uuid4()), str(uuid4())
        section = paragraph_section(allowed_id)
        section["blocks"][0]["sources"] = [{"upload_id": other_id}]
        with self.assertRaises(ContentGenerationFailure) as raised:
            _normalized_study_guide([section], {allowed_id})
        self.assertEqual(raised.exception.diagnostic_code, "invalid_citation")

    def test_normalization_replaces_model_cell_copy_with_fixed_accessible_content(self) -> None:
        upload_id = str(uuid4())
        section = {
            "heading": "Cell structures",
            "sources": [{"upload_id": upload_id}],
            "blocks": [
                {"type": "paragraph", "text": "Human cells contain specialized structures."},
                {
                    "type": "labeled_diagram",
                    "template_id": "animal_cell_map_v1",
                    "title": "Animal cell overview",
                    "alt_text": "Model-authored alt text must not be trusted.",
                    "fallback_text": "Model-authored function must not be trusted.",
                    "parameters": {"focus_ids": ["nucleus", "mitochondria"]},
                },
            ],
        }

        normalized = _normalized_study_guide([section], {upload_id})
        diagram = normalized["sections"][0]["blocks"][1]
        self.assertIn("not to scale", diagram["alt_text"])
        self.assertIn("Nucleus and nuclear envelope", diagram["fallback_text"])
        self.assertIn("Mitochondria convert energy from food", diagram["fallback_text"])
        self.assertNotIn("Model-authored", diagram["alt_text"] + diagram["fallback_text"])


if __name__ == "__main__":
    unittest.main()
