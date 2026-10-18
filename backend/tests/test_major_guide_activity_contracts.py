"""Backend contracts, opt-in instructions, deterministic fallbacks, and export regressions."""

from __future__ import annotations

import io
import shutil
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from google.genai import types
from pydantic import ValidationError
from pypdf import PdfReader

from app.api.schemas import StudyGuideBlock, StudyGuideSectionV2
from app.config import Settings
from app.services.gemini_content import (
    GeminiContentService,
    _normalized_study_guide,
    _provider_response_schema,
    _StudyGuideSectionBatch,
)
from app.services.guide_templates import guide_block_alt_text, guide_block_fallback
from app.services.study_files import compiler_path, create_guide_files, render_guide_latex
from app.services.study_guide_tools import guide_tool_catalog_prompt, guide_tool_instructions


def activity(template_id: str, parameters: dict, *, kind: str = "interactive_model") -> dict:
    return {
        "type": kind,
        "template_id": template_id,
        "title": f"Example {template_id}",
        "parameters": parameters,
    }


def stress_example() -> list[list[int]]:
    return [[30, -40, 20], [-40, -10, 80], [20, 80, 100]]


def settings() -> Settings:
    return Settings(
        _env_file=None,
        sqlite_path=":memory:",
        file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
    )


class MajorGuideActivitySchemaTests(unittest.TestCase):
    def test_graph_traversal_accepts_count_bounds_and_rejects_bad_references(self) -> None:
        minimum = activity(
            "graph_traversal_v1",
            {
                "algorithm": "bfs",
                "start_node": "n0",
                "nodes": [{"id": "n0", "label": "Start"}, {"id": "n1", "label": "Next"}],
                "edges": [{"source": "n0", "target": "n1"}],
            },
        )
        self.assertEqual(StudyGuideBlock.model_validate(minimum).parameters.algorithm, "bfs")

        nodes = [{"id": f"n{index}", "label": f"Node {index}"} for index in range(24)]
        edges = [
            {"source": f"n{source}", "target": f"n{target}"}
            for source in range(24)
            for target in range(24)
            if source != target
        ][:48]
        maximum = activity(
            "graph_traversal_v1",
            {"algorithm": "dfs", "start_node": "n0", "nodes": nodes, "edges": edges},
        )
        self.assertEqual(len(StudyGuideBlock.model_validate(maximum).parameters.edges), 48)

        invalid_parameters = (
            {"algorithm": "bfs", "start_node": "n0", "nodes": nodes[:1], "edges": edges[:1]},
            {
                "algorithm": "bfs",
                "start_node": "n0",
                "nodes": nodes + [{"id": "n24", "label": "Extra"}],
                "edges": edges[:1],
            },
            {"algorithm": "bfs", "start_node": "n0", "nodes": nodes[:2], "edges": []},
            {"algorithm": "bfs", "start_node": "n0", "nodes": nodes[:2], "edges": edges[:49]},
            {"algorithm": "bfs", "start_node": "missing", "nodes": nodes[:2], "edges": edges[:1]},
            {
                "algorithm": "breadth_first",
                "start_node": "n0",
                "nodes": nodes[:2],
                "edges": edges[:1],
            },
            {
                "algorithm": "bfs",
                "start_node": "n0",
                "nodes": [{"id": "same", "label": "A"}, {"id": "same", "label": "B"}],
                "edges": [{"source": "same", "target": "same"}],
            },
            {
                "algorithm": "bfs",
                "start_node": "n0",
                "nodes": nodes[:2],
                "edges": [{"source": "n0", "target": "n9"}],
            },
            {
                "algorithm": "bfs",
                "start_node": "n0",
                "nodes": nodes[:2],
                "edges": [{"source": "n0", "target": "n1"}, {"source": "n0", "target": "n1"}],
            },
            {
                "algorithm": "bfs",
                "start_node": "n0",
                "nodes": [{"id": "n0", "label": ""}, {"id": "n1", "label": "Next"}],
                "edges": [{"source": "n0", "target": "n1"}],
            },
            {
                "algorithm": "bfs",
                "start_node": "n0",
                "nodes": [
                    {"id": "n0", "label": "Start\nNode"},
                    {"id": "n1", "label": "Next"},
                ],
                "edges": [{"source": "n0", "target": "n1"}],
            },
        )
        for parameters in invalid_parameters:
            with self.subTest(parameters=parameters), self.assertRaises(ValidationError):
                StudyGuideBlock.model_validate(activity("graph_traversal_v1", parameters))

    def test_stress_tensor_requires_finite_bounded_numeric_symmetric_3x3_values(self) -> None:
        valid = activity("stress_cube_3d_v1", {"stress_mpa": stress_example()})
        self.assertEqual(StudyGuideBlock.model_validate(valid).parameters.stress_mpa[0][1], -40)
        boundary = activity(
            "stress_cube_3d_v1",
            {"stress_mpa": [[1000, -1000, 0], [-1000, -1000, 0], [0, 0, 1000]]},
        )
        StudyGuideBlock.model_validate(boundary)

        invalid = (
            [[30, -40, 20], [-39, -10, 80], [20, 80, 100]],
            [[30, -40], [-40, -10]],
            [[30, -40, 20], [-40, -10, 80]],
            [[30, "-40", 20], [-40, -10, 80], [20, 80, 100]],
            [[30, True, 20], [True, -10, 80], [20, 80, 100]],
            [[30, float("nan"), 20], [float("nan"), -10, 80], [20, 80, 100]],
            [[1000.01, 0, 0], [0, 0, 0], [0, 0, 0]],
            [[-1000.01, 0, 0], [0, 0, 0], [0, 0, 0]],
        )
        for tensor in invalid:
            with self.subTest(tensor=tensor), self.assertRaises(ValidationError):
                StudyGuideBlock.model_validate(
                    activity("stress_cube_3d_v1", {"stress_mpa": tensor})
                )
        with self.assertRaises(ValidationError):
            StudyGuideBlock.model_validate(
                activity(
                    "stress_cube_3d_v1", {"stress_mpa": stress_example(), "code": "not allowed"}
                )
            )

    def test_molecule_3d_and_python_and_simcir_accept_only_fixed_catalogs(self) -> None:
        for molecule_id in ("water", "carbon_dioxide", "methane", "ammonia"):
            with self.subTest(molecule_id=molecule_id):
                block = activity(
                    "molecule_3d_v1", {"molecule_id": molecule_id}, kind="labeled_diagram"
                )
                self.assertEqual(
                    StudyGuideBlock.model_validate(block).parameters.molecule_id, molecule_id
                )
        for circuit_id in ("and_gate", "or_gate", "half_adder"):
            with self.subTest(circuit_id=circuit_id):
                block = activity("simcir_circuit_v1", {"circuit_id": circuit_id})
                self.assertEqual(
                    StudyGuideBlock.model_validate(block).parameters.circuit_id, circuit_id
                )

        python_cases = (
            {"algorithm": "linear_search", "values": [-100, 0, 100], "target": 100},
            {"algorithm": "binary_search", "values": [-100, 0, 100], "target": 0},
            {"algorithm": "insertion_sort", "values": [100, -100]},
        )
        for parameters in python_cases:
            with self.subTest(parameters=parameters):
                StudyGuideBlock.model_validate(activity("python_algorithm_trace_v1", parameters))

        invalid_blocks = (
            activity("molecule_3d_v1", {"molecule_id": "benzene"}, kind="labeled_diagram"),
            activity(
                "molecule_3d_v1", {"molecule_id": "water", "xyz": "custom"}, kind="labeled_diagram"
            ),
            activity("molecule_3d_v1", {"molecule_id": "water"}),
            activity("simcir_circuit_v1", {"circuit_id": "custom"}),
            activity("simcir_circuit_v1", {"circuit_id": "and_gate", "devices": []}),
            activity("simcir_circuit_v1", {"circuit_id": "and_gate"}, kind="labeled_diagram"),
            activity("python_algorithm_trace_v1", {"algorithm": "eval", "values": [1, 2]}),
            activity(
                "python_algorithm_trace_v1",
                {"algorithm": "linear_search", "values": [1], "target": 1},
            ),
            activity(
                "python_algorithm_trace_v1",
                {"algorithm": "linear_search", "values": [1, 2], "target": 101},
            ),
            activity(
                "python_algorithm_trace_v1",
                {"algorithm": "binary_search", "values": [2, 1], "target": 1},
            ),
            activity("python_algorithm_trace_v1", {"algorithm": "binary_search", "values": [1, 2]}),
            activity(
                "python_algorithm_trace_v1",
                {"algorithm": "insertion_sort", "values": [2, 1], "target": 1},
            ),
            activity(
                "python_algorithm_trace_v1", {"algorithm": "insertion_sort", "values": [1, 1.5]}
            ),
            activity(
                "python_algorithm_trace_v1",
                {"algorithm": "insertion_sort", "values": list(range(17))},
            ),
            activity(
                "python_algorithm_trace_v1",
                {"algorithm": "insertion_sort", "values": [1, 2], "code": "x=1"},
            ),
        )
        for block in invalid_blocks:
            with self.subTest(block=block), self.assertRaises(ValidationError):
                StudyGuideBlock.model_validate(block)


class MajorGuideActivityGenerationTests(unittest.TestCase):
    def test_compact_catalog_and_opt_in_tool_instruction_registry(self) -> None:
        catalog = guide_tool_catalog_prompt()
        for tool_id in (
            "graph_traversal",
            "stress_cube_3d",
            "molecule_3d",
            "python_algorithm",
            "simcir_circuit",
        ):
            with self.subTest(tool_id=tool_id):
                self.assertIn(f"- {tool_id}:", catalog)
        self.assertNotIn("graph_traversal_v1", catalog)
        self.assertNotIn("stress_mpa", catalog)

        instructions = guide_tool_instructions(
            {
                "graph_traversal",
                "stress_cube_3d",
                "molecule_3d",
                "python_algorithm",
                "simcir_circuit",
            }
        )
        for detail in (
            "graph_traversal_v1",
            "stress_cube_3d_v1",
            "molecule_3d_v1",
            "python_algorithm_trace_v1",
            "simcir_circuit_v1",
            "do not average",
            "Never supply code",
        ):
            with self.subTest(detail=detail):
                self.assertIn(detail, instructions)
        self.assertNotIn("molecule_2d_v1", instructions)
        with self.assertRaises(ValueError):
            guide_tool_instructions({"not_registered"})

        _StudyGuideSectionBatch.model_validate(
            {
                "format": "study_guide",
                "schema_version": 2,
                "sections": [
                    {
                        "heading": "CS",
                        "blocks": [{"type": "paragraph", "text": "A graph has vertices."}],
                        "mini_quiz": {
                            "question": "Which description matches this section?",
                            "choices": [
                                "A graph has vertices",
                                "A graph has no nodes",
                                "A chemical bond",
                                "A claim outside the section",
                            ],
                            "correct_index": 0,
                            "explanation": "The first choice matches the section's concept.",
                        },
                    }
                ],
                "requested_tool_ids": [
                    "graph_traversal",
                    "stress_cube_3d",
                    "molecule_3d",
                    "python_algorithm",
                    "simcir_circuit",
                ],
            }
        )
        with self.assertRaises(ValidationError):
            _StudyGuideSectionBatch.model_validate(
                {
                    "format": "study_guide",
                    "schema_version": 2,
                    "sections": [
                        {
                            "heading": "CS",
                            "blocks": [{"type": "paragraph", "text": "A graph has vertices."}],
                        }
                    ],
                    "requested_tool_ids": ["python"],
                }
            )

    def test_requested_tools_are_added_only_to_revision_instructions(self) -> None:
        upload_id = uuid4()
        section = StudyGuideSectionV2.model_validate(
            {
                "heading": "Computer science and mechanics",
                "sources": [{"upload_id": upload_id}],
                "mini_quiz": {
                    "question": "Which topic appears in the section?",
                    "choices": [
                        "Graphs and stress",
                        "Cell division",
                        "Thermodynamics only",
                        "An unrelated claim",
                    ],
                    "correct_index": 0,
                    "explanation": "The section covers graphs and stress.",
                },
                "blocks": [{"type": "paragraph", "text": "These notes cover graphs and stress."}],
            }
        )

        def batch(requested: list[str]) -> _StudyGuideSectionBatch:
            return _StudyGuideSectionBatch(
                format="study_guide",
                schema_version=2,
                sections=[section.model_dump(mode="json")],
                requested_tool_ids=requested,
            )

        service = GeminiContentService(None, settings())
        service._generate_json = Mock(
            side_effect=[
                batch(["graph_traversal", "stress_cube_3d", "python_algorithm", "simcir_circuit"]),
                batch([]),
            ]
        )
        service._generate_study_guide_sections(
            base_prompt="Write one guide section.",
            count=1,
            system_instruction="Use cited course sources.",
            allowed_source_ids={str(upload_id)},
        )
        draft_call, revision_call = service._generate_json.call_args_list
        self.assertIn(
            "Available activity tools (brief catalog)", draft_call.kwargs["system_instruction"]
        )
        self.assertNotIn("stress_cube_3d_v1", draft_call.args[0])
        for template_id in (
            "graph_traversal_v1",
            "stress_cube_3d_v1",
            "python_algorithm_trace_v1",
            "simcir_circuit_v1",
        ):
            with self.subTest(template_id=template_id):
                self.assertIn(template_id, revision_call.args[0])
        self.assertNotIn("molecule_3d_v1", revision_call.args[0])

    def test_google_provider_schema_accepts_expanded_response_contract(self) -> None:
        response_schema = _provider_response_schema(_StudyGuideSectionBatch)
        config = types.GenerateContentConfig(response_schema=response_schema)
        self.assertIsNotNone(config.response_schema)


class MajorGuideActivityFallbackAndExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_id = uuid4()
        self.source_id_two = uuid4()

    def blocks(self) -> list[dict]:
        graph = activity(
            "graph_traversal_v1",
            {
                "algorithm": "dfs",
                "start_node": "a",
                "nodes": [
                    {"id": "a", "label": "Root"},
                    {"id": "b", "label": "Left"},
                    {"id": "c", "label": "Right"},
                    {"id": "d", "label": "Leaf"},
                    {"id": "z", "label": "Unreachable & isolated"},
                ],
                "edges": [
                    {"source": "a", "target": "b"},
                    {"source": "a", "target": "c"},
                    {"source": "b", "target": "d"},
                ],
            },
        )
        stress = activity("stress_cube_3d_v1", {"stress_mpa": stress_example()})
        molecule = activity("molecule_3d_v1", {"molecule_id": "methane"}, kind="labeled_diagram")
        python = activity(
            "python_algorithm_trace_v1",
            {"algorithm": "binary_search", "values": [1, 3, 5, 7, 9], "target": 7},
        )
        circuit = activity("simcir_circuit_v1", {"circuit_id": "half_adder"})
        for block in (graph, stress, molecule, python, circuit):
            block["sources"] = [{"upload_id": str(self.source_id)}]
        return [graph, stress, molecule, python, circuit]

    def normalized_content(self) -> dict:
        blocks = [
            {
                "type": "paragraph",
                "text": (
                    "These cited notes explain graph traversal, stress tensors, and fixed "
                    "algorithms."
                ),
                "sources": [{"upload_id": str(self.source_id)}],
            },
            *self.blocks(),
        ]
        return _normalized_study_guide(
            [
                {
                    "heading": "Interactive course examples",
                    "sources": [{"upload_id": str(self.source_id)}],
                    "blocks": blocks,
                }
            ],
            {str(self.source_id)},
        )

    def svg_guide(self) -> dict:
        return {
            "title": "Biology lesson",
            "class_name": "BIO170",
            "material_id": uuid4(),
            "content": {
                "format": "study_guide",
                "schema_version": 2,
                "sections": [
                    {
                        "heading": "Membrane transport",
                        "blocks": [
                            {
                                "type": "svg_diagram",
                                "title": "Diffusion across a membrane",
                                "svg": (
                                    '<svg xmlns="http://www.w3.org/2000/svg" '
                                    'viewBox="0 0 240 120"><rect x="10" y="10" '
                                    'width="220" height="100" fill="#eef"/>'
                                    '<text x="30" y="65">Diffusion</text></svg>'
                                ),
                                "alt_text": "Particles move from high to low concentration.",
                                "fallback_text": "Diffusion moves particles down a gradient.",
                            }
                        ],
                    }
                ],
            },
            "sources": [],
        }

    def test_pdf_variant_embeds_svg_snapshot_and_standalone_latex_keeps_text_fallback(self) -> None:
        compiler_inputs: dict = {}

        def capture_compile(latex: str, _settings, *, assets=None, **_kwargs) -> bytes:
            compiler_inputs["latex"] = latex
            compiler_inputs["assets"] = assets
            return b"%PDF-test"

        with (
            patch("app.services.study_files.render_svg_preview_png", return_value=b"png-bytes"),
            patch("app.services.study_files.compile_guide_pdf", side_effect=capture_compile),
        ):
            files = create_guide_files(settings(), **self.svg_guide())

        self.assertIn(r"\includegraphics", compiler_inputs["latex"])
        self.assertIn("Text alternative: Particles move from high to low concentration.", compiler_inputs["latex"])
        self.assertEqual(compiler_inputs["assets"], {"jot-guide-diagram-001.png": b"png-bytes"})
        standalone_latex = files["latex"].decode("utf-8")
        self.assertNotIn(r"\includegraphics", standalone_latex)
        self.assertIn("Diffusion moves particles down a gradient.", standalone_latex)
        self.assertTrue(files["pdf"].startswith(b"%PDF-"))

    @unittest.skipUnless(
        compiler_path(settings()) and shutil.which("rsvg-convert"),
        "Tectonic and librsvg are required for image-export integration coverage",
    )
    def test_real_pdf_contains_svg_image_and_accessible_text_alternative(self) -> None:
        files = create_guide_files(settings(), **self.svg_guide())
        reader = PdfReader(io.BytesIO(files["pdf"]))
        page = next(page for page in reader.pages if page.images)
        self.assertGreaterEqual(len(page.images), 1)
        extracted = " ".join(page.extract_text().split())
        self.assertIn("Particles move from high to low concentration.", extracted)
        self.assertIn("Diffusion across a membrane", extracted)

    def test_fallback_and_alt_text_are_deterministic_and_numeric(self) -> None:
        content = self.normalized_content()
        blocks = content["sections"][0]["blocks"]
        for block in blocks[1:]:
            self.assertTrue(block["alt_text"])
            self.assertTrue(block["fallback_text"])
            self.assertEqual(block["fallback_text"], guide_block_fallback(block))
            self.assertEqual(block["alt_text"], guide_block_alt_text(block))

        by_template = {block["template_id"]: block for block in blocks}
        self.assertIn(
            "Root → Left → Leaf → Right", by_template["graph_traversal_v1"]["fallback_text"]
        )
        self.assertIn("1 node is unreachable", by_template["graph_traversal_v1"]["alt_text"])
        self.assertIn("σ₁ = 142.09", by_template["stress_cube_3d_v1"]["fallback_text"])
        self.assertIn("106.91 MPa", by_template["stress_cube_3d_v1"]["fallback_text"])
        self.assertIn("45 degrees", by_template["stress_cube_3d_v1"]["fallback_text"])
        self.assertIn(
            "tetrahedral molecular geometry", by_template["molecule_3d_v1"]["fallback_text"]
        )
        self.assertIn("found at index 3", by_template["python_algorithm_trace_v1"]["fallback_text"])
        self.assertIn("A=1 B=1 SUM=0 CARRY=1", by_template["simcir_circuit_v1"]["fallback_text"])

    def test_repeated_maximum_or_minimum_principal_stress_marks_orientation_nonunique(self) -> None:
        for tensor in (
            [[10, 0, 0], [0, 10, 0], [0, 0, 0]],
            [[10, 0, 0], [0, 0, 0], [0, 0, 0]],
        ):
            with self.subTest(tensor=tensor):
                payload = activity("stress_cube_3d_v1", {"stress_mpa": tensor})
                validated = StudyGuideBlock.model_validate(payload).model_dump(mode="json")
                fallback = guide_block_fallback(validated)
                self.assertIn(
                    "the plane orientation is not unique because an extreme principal "
                    "stress is repeated",
                    fallback,
                )

    def test_normalization_preserves_only_authorized_nested_source_citations(self) -> None:
        content = self.normalized_content()
        section = content["sections"][0]
        self.assertEqual(section["sources"][0]["upload_id"], str(self.source_id))
        for block in section["blocks"]:
            self.assertEqual(block["sources"][0]["upload_id"], str(self.source_id))

        bad_block = self.blocks()[0]
        bad_block["sources"] = [{"upload_id": str(self.source_id_two)}]
        with self.assertRaisesRegex(Exception, "invalid source citation"):
            _normalized_study_guide(
                [
                    {
                        "heading": "Invalid source",
                        "sources": [{"upload_id": str(self.source_id)}],
                        "blocks": [
                            {"type": "paragraph", "text": "A cited explanation."},
                            bad_block,
                        ],
                    }
                ],
                {str(self.source_id)},
            )

    def test_latex_export_has_safe_static_diagrams_trace_lists_and_citations(self) -> None:
        content = self.normalized_content()
        latex = render_guide_latex(
            title="Major activity examples",
            class_name="Computer science and mechanics",
            material_id=uuid4(),
            content=content,
            sources=[{"id": self.source_id, "title": "Course notes.pdf"}],
        )
        for marker in (
            r"\begin{picture}(104,88)",
            r"\textbf{Directed edges}",
            "Unreachable \\& isolated",
            r"\sigma$ (MPa)",
            r"\sigma_{1}=142.09 MPa",
            "106.91 MPa",
            "45 degrees",
            "tetrahedral molecular geometry",
            "No model-supplied code is executed",
            r"\textbf{SUM} & \textbf{CARRY}",
            "Source references",
            r"\hyperref[source:1]{[1]}",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, latex)
        self.assertNotIn("<script", latex.lower())
        self.assertNotIn("\nimport json", latex)

    @unittest.skipUnless(compiler_path(settings()), "Tectonic is not installed")
    def test_pdf_keeps_new_activity_fallbacks_and_citations(self) -> None:
        files = create_guide_files(
            settings(),
            title="Major activity examples",
            class_name="Computer science and mechanics",
            material_id=uuid4(),
            content=self.normalized_content(),
            sources=[{"id": self.source_id, "title": "Course notes.pdf"}],
        )
        self.assertTrue(files["pdf"].startswith(b"%PDF-"))
        extracted = " ".join(
            page.extract_text() or "" for page in PdfReader(io.BytesIO(files["pdf"])).pages
        )
        normalized = " ".join(extracted.split())
        for marker in (
            "Majoractivityexamples",
            "stress_cube_3d_v1",
            # Tectonic extracts math-mode unit letters with Unicode math fonts
            # and spacing (``142.09𝑀 𝑃 𝑎``); the source-level test above checks
            # the full value/unit expression in generated LaTeX.
            "142.09",
            "AND",
            "SUM",
            "CARRY",
            "Coursenotes.pdf",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, normalized)


if __name__ == "__main__":
    unittest.main()
