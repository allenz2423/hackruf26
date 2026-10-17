from __future__ import annotations

import unittest
from unittest.mock import Mock
from uuid import uuid4

from pydantic import ValidationError

from app.api.schemas import StudyGuideSectionV2
from app.config import Settings
from app.services.gemini_content import (
    STUDY_GUIDE_STYLE_INSTRUCTIONS,
    GeminiContentService,
    _provider_response_schema,
    _StudyGuideSectionBatch,
)
from app.services.study_guide_tools import (
    GUIDE_TOOLS,
    guide_tool_catalog_prompt,
    guide_tool_instructions,
)
from app.services.study_prompts import STUDY_MATERIAL_SYSTEM_PROMPTS


def section(upload_id: str, text: str = "A source-grounded explanation.") -> StudyGuideSectionV2:
    return StudyGuideSectionV2.model_validate(
        {
            "heading": "Core idea",
            "sources": [{"upload_id": upload_id}],
            "blocks": [{"type": "paragraph", "text": text}],
        }
    )


def batch(upload_id: str, *, request: list[str] | None = None, text: str = "Draft."):
    return _StudyGuideSectionBatch(
        format="study_guide",
        schema_version=2,
        sections=[section(upload_id, text)],
        requested_tool_ids=request or [],
    )


class StudyGuideToolAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.upload_id = str(uuid4())
        self.service = GeminiContentService(
            None,
            Settings(
                _env_file=None,
                sqlite_path=":memory:",
                file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
            ),
        )

    def test_catalog_is_compact_and_matches_supported_registry(self) -> None:
        catalog = guide_tool_catalog_prompt()
        self.assertEqual(len(GUIDE_TOOLS), 15)
        for tool in GUIDE_TOOLS:
            with self.subTest(tool=tool.id):
                self.assertIn(f"- {tool.id}: {tool.name} (fits ", catalog)
                self.assertIn(f"): {tool.purpose}", catalog)
        self.assertNotIn("parameters `{", catalog)
        self.assertIn("chemistry and biochemistry", catalog)

    def test_visual_authoring_contract_targets_phone_size_and_teachable_animation(self) -> None:
        prompt = STUDY_MATERIAL_SYSTEM_PROMPTS["study_guide"]
        custom_svg = guide_tool_instructions(["custom_svg"])

        self.assertIn("360-unit-wide viewBox", prompt)
        self.assertIn("18–22 units for primary labels", prompt)
        self.assertIn("at least four meaningful", prompt)
        self.assertIn("<animate attributeName=", prompt)
        self.assertIn(
            "include at least four such activities in distinct sections",
            STUDY_GUIDE_STYLE_INSTRUCTIONS["interactive"],
        )
        self.assertIn("select a meaningful stage", STUDY_GUIDE_STYLE_INSTRUCTIONS["interactive"])
        self.assertIn("360-unit-wide", custom_svg)
        self.assertIn("repeatCount", custom_svg)
        self.assertIn("one or two sentences", custom_svg)
        self.assertIn("smallest practical viewBox", custom_svg)
        self.assertIn("precise graph edge", custom_svg)
        self.assertIn("trace each route from start to finish", custom_svg)

    def test_requested_instructions_are_allowlisted_and_catalog_ordered(self) -> None:
        instructions = guide_tool_instructions(["circuit_divider", "math_graph"])
        self.assertLess(instructions.index("math_graph"), instructions.index("circuit_divider"))
        self.assertIn("linear_graph_v1", instructions)
        self.assertIn("resistor_divider_v1", instructions)
        self.assertNotIn("first_order_response_v1", instructions)
        self.assertEqual(guide_tool_instructions([]), "")
        with self.assertRaises(ValueError):
            guide_tool_instructions(["javascript"])

    def test_cell_biology_access_exposes_only_the_fixed_source_supported_map(self) -> None:
        instructions = guide_tool_instructions(["cell_biology"])
        self.assertIn("animal_cell_map_v1", instructions)
        self.assertIn("nucleus", instructions)
        self.assertIn("explicitly supported by the supplied source", instructions)
        self.assertIn("illustrative", instructions)
        self.assertNotIn("linear_graph_v1", instructions)

    def test_statistics_access_explains_synthetic_population_and_assignment(self) -> None:
        instructions = guide_tool_instructions(["sampling_basics"])
        self.assertIn("sampling_demo_v1", instructions)
        self.assertIn("not source data", instructions)
        self.assertIn("random sampling selects who enters a study", instructions)
        self.assertIn("random assignment allocates selected participants", instructions)
        self.assertNotIn("animal_cell_map_v1", instructions)

    def test_response_schema_exposes_bounded_tool_request_ids(self) -> None:
        payload_schema = _provider_response_schema(_StudyGuideSectionBatch)
        request_schema = payload_schema["properties"]["requested_tool_ids"]
        self.assertEqual(request_schema["type"], "array")
        allowed = request_schema["items"]["enum"]
        self.assertEqual(allowed, [tool.id for tool in GUIDE_TOOLS])
        with self.assertRaises(ValidationError):
            _StudyGuideSectionBatch.model_validate(
                {
                    "format": "study_guide",
                    "sections": [
                        {
                            "heading": "Bad request",
                            "blocks": [{"type": "paragraph", "text": "Text."}],
                        }
                    ],
                    "requested_tool_ids": ["javascript"],
                }
            )

    def test_draft_request_adds_only_requested_detail_to_revision(self) -> None:
        self.service._generate_json = Mock(
            side_effect=[
                batch(self.upload_id, request=["circuit_divider"], text="Circuit theory."),
                batch(self.upload_id, text="A divider has a bounded output voltage."),
            ]
        )

        result = self.service._generate_study_guide_sections(
            base_prompt="Create a short guide from the source.",
            count=1,
            system_instruction="Use the given source only.",
            allowed_source_ids={self.upload_id},
        )

        self.assertEqual(result["sections"][0]["heading"], "Core idea")
        draft_call, revision_call = self.service._generate_json.call_args_list
        self.assertIn(
            "Available activity tools (brief catalog)",
            draft_call.kwargs["system_instruction"],
        )
        self.assertNotIn("Requested activity-tool instructions", draft_call.args[0])
        self.assertIn("requested_tool_ids", draft_call.kwargs["system_instruction"])
        revision_prompt = revision_call.args[0]
        self.assertIn("Requested activity-tool instructions", revision_prompt)
        self.assertIn("resistor_divider_v1", revision_prompt)
        self.assertNotIn("linear_graph_v1", revision_prompt)

    def test_cell_map_instruction_is_added_only_after_explicit_request(self) -> None:
        self.service._generate_json = Mock(
            side_effect=[
                batch(self.upload_id, request=["cell_biology"], text="Cells have organelles."),
                batch(self.upload_id, text="Select source-supported organelles in the schematic."),
            ]
        )

        self.service._generate_study_guide_sections(
            base_prompt="Create a guide from the supplied Human Biology source.",
            count=1,
            system_instruction="Use the given source only.",
            allowed_source_ids={self.upload_id},
        )

        revision_prompt = self.service._generate_json.call_args_list[1].args[0]
        self.assertIn("animal_cell_map_v1", revision_prompt)
        self.assertIn("Do not add model-authored labels", revision_prompt)
        self.assertNotIn("linear_graph_v1", revision_prompt)

    def test_sampling_instructions_are_added_only_after_explicit_request(self) -> None:
        self.service._generate_json = Mock(
            side_effect=[
                batch(self.upload_id, request=["sampling_basics"], text="Sampling concepts."),
                batch(self.upload_id, text="Sampling selects study participants."),
            ]
        )

        self.service._generate_study_guide_sections(
            base_prompt="Create a guide from the supplied statistics source.",
            count=1,
            system_instruction="Use the given source only.",
            allowed_source_ids={self.upload_id},
        )

        revision_prompt = self.service._generate_json.call_args_list[1].args[0]
        self.assertIn("sampling_demo_v1", revision_prompt)
        self.assertIn("not source data", revision_prompt)
        self.assertNotIn("animal_cell_map_v1", revision_prompt)

    def test_no_request_still_generates_text_guide_without_tool_details(self) -> None:
        self.service._generate_json = Mock(
            side_effect=[batch(self.upload_id), batch(self.upload_id, text="Revised text.")]
        )

        result = self.service._generate_study_guide_sections(
            base_prompt="Create a short guide from the source.",
            count=1,
            system_instruction="Use the given source only.",
            allowed_source_ids={self.upload_id},
        )

        self.assertEqual(result["sections"][0]["body"], "Revised text.")
        revision_prompt = self.service._generate_json.call_args_list[1].args[0]
        self.assertNotIn("Requested activity-tool instructions", revision_prompt)

    def test_tool_in_draft_gets_exact_instructions_even_without_a_self_request(self) -> None:
        draft = _StudyGuideSectionBatch(
            format="study_guide",
            sections=[
                StudyGuideSectionV2.model_validate(
                    {
                        "heading": "Cell structure",
                        "sources": [{"upload_id": self.upload_id}],
                        "blocks": [
                            {
                                "type": "labeled_diagram",
                                "title": "Animal cell",
                                "template_id": "animal_cell_map_v1",
                                "parameters": {"focus_ids": ["nucleus"]},
                                "sources": [{"upload_id": self.upload_id}],
                            },
                            {
                                "type": "paragraph",
                                "text": "The nucleus stores genetic information.",
                            },
                        ],
                    }
                )
            ],
        )
        self.service._generate_json = Mock(
            side_effect=[draft, batch(self.upload_id, text="The revised cell explanation.")]
        )

        self.service._generate_study_guide_sections(
            base_prompt="Create a guide from the supplied biology source.",
            count=1,
            system_instruction="Use the given source only.",
            allowed_source_ids={self.upload_id},
        )

        revision_prompt = self.service._generate_json.call_args_list[1].args[0]
        self.assertIn("animal_cell_map_v1", revision_prompt)
        self.assertIn("Do not add model-authored labels", revision_prompt)


if __name__ == "__main__":
    unittest.main()
