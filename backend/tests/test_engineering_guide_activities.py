"""Contract, fallback, and export tests for the engineering/CS guide activities."""

from __future__ import annotations

import io
import unittest
from uuid import uuid4

from pydantic import ValidationError
from pypdf import PdfReader

from app.api.schemas import StudyGuideBlock
from app.config import Settings
from app.services.guide_templates import guide_block_alt_text, guide_block_fallback
from app.services.study_files import (
    compiler_path,
    create_guide_files,
    render_guide_latex,
)


def block(template_id: str, parameters: dict) -> dict:
    return {
        "type": "interactive_model",
        "template_id": template_id,
        "title": template_id.replace("_v1", "").replace("_", " ").title(),
        "parameters": parameters,
    }


def engineering_blocks() -> list[dict]:
    return [
        block("force_components_v1", {"force_n": 100, "angle_deg": 30}),
        block("linear_graph_v1", {"slope": -2, "intercept": 3}),
        block(
            "first_order_response_v1",
            {"initial_value": 0, "final_value": 10, "time_constant_s": 2},
        ),
        block("resistor_divider_v1", {"voltage_v": 12, "r1_ohm": 1000, "r2_ohm": 2000}),
        block("binary_search_trace_v1", {"values": [1, 4, 9, 16, 25], "target": 17}),
    ]


def settings() -> Settings:
    return Settings(
        _env_file=None,
        sqlite_path=":memory:",
        file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
    )


class EngineeringGuideActivityContractTests(unittest.TestCase):
    def test_supported_edge_values_and_all_five_payloads_validate(self) -> None:
        cases = [
            block("force_components_v1", {"force_n": 0, "angle_deg": 90}),
            block("linear_graph_v1", {"slope": -10, "intercept": 10}),
            block(
                "first_order_response_v1",
                {"initial_value": -1000, "final_value": 1000, "time_constant_s": 0.01},
            ),
            block("resistor_divider_v1", {"voltage_v": 24, "r1_ohm": 1, "r2_ohm": 1_000_000}),
            block("binary_search_trace_v1", {"values": [-10_000, 10_000], "target": -10_000}),
        ]

        for payload in cases:
            with self.subTest(template=payload["template_id"]):
                validated = StudyGuideBlock.model_validate(payload)
                self.assertEqual(validated.template_id, payload["template_id"])

    def test_invalid_or_unapproved_parameters_fail_closed(self) -> None:
        invalid = [
            block("force_components_v1", {"force_n": True, "angle_deg": 30}),
            block("force_components_v1", {"force_n": 100, "angle_deg": 90.05}),
            block("force_components_v1", {"force_n": 1001, "angle_deg": 30}),
            block("linear_graph_v1", {"slope": float("nan"), "intercept": 0}),
            block("linear_graph_v1", {"slope": 10.05, "intercept": 0}),
            block("linear_graph_v1", {"slope": 1, "intercept": 0, "script": "alert(1)"}),
            block(
                "first_order_response_v1",
                {"initial_value": 0.5, "final_value": 10, "time_constant_s": 2},
            ),
            block(
                "first_order_response_v1",
                {"initial_value": 0, "final_value": 10, "time_constant_s": 0},
            ),
            block("resistor_divider_v1", {"voltage_v": 12.05, "r1_ohm": 1000, "r2_ohm": 2000}),
            block("resistor_divider_v1", {"voltage_v": 12, "r1_ohm": 1.5, "r2_ohm": 2000}),
            block("binary_search_trace_v1", {"values": [1, 3, 2], "target": 2}),
            block("binary_search_trace_v1", {"values": [1, 2, 2], "target": 2}),
            block("binary_search_trace_v1", {"values": [1, 2.5, 3], "target": 2}),
            block("binary_search_trace_v1", {"values": list(range(21)), "target": 2}),
            block("binary_search_trace_v1", {"values": [1, 2], "target": True}),
            block("binary_search_trace_v99", {"slope": 1, "intercept": 0}),
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                StudyGuideBlock.model_validate(payload)

    def test_template_and_parameter_types_cannot_be_crossed(self) -> None:
        invalid_pairs = [
            ("force_components_v1", {"slope": 1, "intercept": 0}),
            ("linear_graph_v1", {"force_n": 10, "angle_deg": 30}),
            ("first_order_response_v1", {"voltage_v": 12, "r1_ohm": 1, "r2_ohm": 1}),
            ("resistor_divider_v1", {"initial_value": 0, "final_value": 1, "time_constant_s": 1}),
            ("binary_search_trace_v1", {"slope": 1, "intercept": 0}),
        ]
        for template_id, parameters in invalid_pairs:
            with self.subTest(template=template_id), self.assertRaises(ValidationError):
                StudyGuideBlock.model_validate(block(template_id, parameters))

    def test_fallbacks_report_defaults_and_text_alternatives(self) -> None:
        expected = [
            "horizontal component is 86.6 N",
            "y = -2x + 3",
            "After one time constant it is about 6.32",
            "output across R₂ is 8 V",
            "The target is not in the list.",
        ]
        for payload, fallback_fragment in zip(engineering_blocks(), expected, strict=True):
            with self.subTest(template=payload["template_id"]):
                validated = StudyGuideBlock.model_validate(payload)
                dumped = validated.model_dump(mode="json")
                self.assertIn(fallback_fragment, guide_block_fallback(dumped))
                self.assertTrue(guide_block_alt_text(dumped))

    def test_latex_contains_static_diagrams_equations_and_binary_search_trace(self) -> None:
        upload_id = uuid4()
        content = {
            "format": "study_guide",
            "schema_version": 2,
            "sections": [
                {
                    "heading": "Interactive engineering and CS",
                    "sources": [{"upload_id": str(upload_id)}],
                    "blocks": engineering_blocks(),
                }
            ],
        }
        latex = render_guide_latex(
            title="Engineering activities",
            class_name="Test class",
            material_id=uuid4(),
            content=content,
            sources=[{"id": upload_id, "title": "Notes.pdf"}],
        )

        self.assertEqual(latex.count(r"\begin{picture}(100,76)"), 4)
        for marker in (
            "horizontal component is 86.6 N",
            "The plotted linear relationship is",
            "After one time constant it is about 6.32",
            "Assumptions: ideal source, ideal wires, and no load",
            "output across",
            "The target is not in the list.",
            "Text alternative:",
            r"\begin{enumerate}",
            r"\hyperref[source:1]{[1]}",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, latex)

    @unittest.skipUnless(compiler_path(settings()), "Tectonic is not installed")
    def test_pdf_keeps_static_activity_content_without_browser_controls(self) -> None:
        upload_id = uuid4()
        files = create_guide_files(
            settings(),
            title="Engineering activities",
            class_name="Test class",
            material_id=uuid4(),
            content={
                "format": "study_guide",
                "schema_version": 2,
                "sections": [
                    {
                        "heading": "Interactive engineering and CS",
                        "sources": [{"upload_id": str(upload_id)}],
                        "blocks": engineering_blocks(),
                    }
                ],
            },
            sources=[{"id": upload_id, "title": "Notes.pdf"}],
        )
        self.assertTrue(files["pdf"].startswith(b"%PDF-"))
        reader = PdfReader(io.BytesIO(files["pdf"]))
        extracted = " ".join(page.extract_text() or "" for page in reader.pages)
        normalized = " ".join(extracted.split())
        self.assertNotIn("Next comparison", normalized)
        for marker in (
            "Engineering activities",
            "Force Components",
            "The plotted linear relationship",
            "After one time constant it is about 6.32",
            "output across",
            "The target is not in the list.",
            "Notes.pdf",
        ):
            with self.subTest(marker=marker):
                self.assertIn(" ".join(marker.split()), normalized)


if __name__ == "__main__":
    unittest.main()
