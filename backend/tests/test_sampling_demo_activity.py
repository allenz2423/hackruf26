from __future__ import annotations

import unittest
from uuid import uuid4

from pydantic import ValidationError

from app.api.schemas import StudyGuideBlock
from app.services.guide_templates import (
    SAMPLING_DEMO_POPULATION,
    guide_block_alt_text,
    guide_block_fallback,
    sampling_demo_result,
)
from app.services.study_files import render_guide_latex


def valid_block_parameters() -> dict:
    return {
        "method": "stratified",
        "sample_size": 6,
        "seed": 17,
        "random_assignment": True,
    }


class SamplingDemoContractTests(unittest.TestCase):
    def test_fixed_population_is_bounded_and_explicitly_synthetic(self) -> None:
        self.assertEqual(len(SAMPLING_DEMO_POPULATION), 24)
        self.assertEqual(sum(row["front_row"] is True for row in SAMPLING_DEMO_POPULATION), 12)
        self.assertEqual(
            {row["stratum"] for row in SAMPLING_DEMO_POPULATION},
            {"Section A", "Section B"},
        )

    def test_schema_accepts_only_fixed_methods_and_bounded_even_sample_sizes(self) -> None:
        payload = {
            "type": "interactive_model",
            "template_id": "sampling_demo_v1",
            "title": "Explore sampling",
            "parameters": valid_block_parameters(),
        }
        self.assertEqual(StudyGuideBlock.model_validate(payload).template_id, "sampling_demo_v1")
        for changes in (
            {"method": "systematic"},
            {"sample_size": 3},
            {"sample_size": 14},
            {"sample_size": True},
            {"sample_size": 5},
            {"seed": 0},
            {"seed": "17"},
            {"random_assignment": "true"},
            {"population": [{"id": "model-chosen"}]},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                StudyGuideBlock.model_validate(
                    {**payload, "parameters": {**valid_block_parameters(), **changes}}
                )
        with self.assertRaises(ValidationError):
            StudyGuideBlock.model_validate(
                {**payload, "type": "labeled_diagram"}
            )

    def test_stratified_sample_uses_equal_counts_and_seeded_draw_repeats(self) -> None:
        first = sampling_demo_result("stratified", 8, 23, False)
        second = sampling_demo_result("stratified", 8, 23, False)
        first_ids = [row["id"] for row in first["selected"]]
        second_ids = [row["id"] for row in second["selected"]]
        self.assertEqual(first_ids, second_ids)
        self.assertEqual(first["stratum_counts"], {"Section A": 4, "Section B": 4})
        self.assertEqual(len(first["selected"]), 8)

    def test_boundary_sample_sizes_and_seeds_produce_complete_balanced_draws(self) -> None:
        for method in ("simple_random", "stratified", "front_row_convenience"):
            for sample_size, seed in ((4, 1), (12, 9999)):
                with self.subTest(method=method, sample_size=sample_size, seed=seed):
                    result = sampling_demo_result(method, sample_size, seed, True)
                    selected_ids = [str(row["id"]) for row in result["selected"]]
                    self.assertEqual(len(selected_ids), sample_size)
                    self.assertEqual(len(set(selected_ids)), sample_size)
                    self.assertEqual(sum(result["stratum_counts"].values()), sample_size)
                    self.assertEqual(
                        list(result["assignment"].values()).count("Treatment A"),
                        sample_size // 2,
                    )
                    self.assertEqual(
                        list(result["assignment"].values()).count("Treatment B"),
                        sample_size // 2,
                    )

    def test_schema_accepts_inclusive_sample_size_and_seed_boundaries(self) -> None:
        payload = {
            "type": "interactive_model",
            "template_id": "sampling_demo_v1",
            "title": "Explore sampling",
            "parameters": valid_block_parameters(),
        }
        for sample_size, seed in ((4, 1), (12, 9999)):
            with self.subTest(sample_size=sample_size, seed=seed):
                block = StudyGuideBlock.model_validate(
                    {
                        **payload,
                        "parameters": {
                            **valid_block_parameters(),
                            "sample_size": sample_size,
                            "seed": seed,
                        },
                    }
                )
                self.assertEqual(block.parameters.sample_size, sample_size)
                self.assertEqual(block.parameters.seed, seed)

    def test_convenience_sample_and_assignment_show_distinct_decisions(self) -> None:
        sampled_only = sampling_demo_result("front_row_convenience", 6, 7, False)
        assigned = sampling_demo_result("front_row_convenience", 6, 7, True)
        self.assertEqual(
            [row["id"] for row in sampled_only["selected"]],
            [row["id"] for row in assigned["selected"]],
        )
        self.assertTrue(all(row["front_row"] for row in assigned["selected"]))
        self.assertEqual(
            {
                label: list(assigned["assignment"].values()).count(label)
                for label in ("Treatment A", "Treatment B")
            },
            {"Treatment A": 3, "Treatment B": 3},
        )
        self.assertGreater(assigned["sample_mean"], assigned["population_mean"])

    def test_fallbacks_identify_illustration_and_distinguish_sampling_from_assignment(self) -> None:
        block = {
            "type": "interactive_model",
            "template_id": "sampling_demo_v1",
            "parameters": valid_block_parameters(),
        }
        fallback = guide_block_fallback(block)
        self.assertIn("app-authored synthetic illustration, not source data", fallback.lower())
        self.assertIn("sampling chooses who enters a study", fallback.lower())
        self.assertIn("random assignment chooses which treatment", fallback.lower())
        self.assertIn("Treatment A", fallback)
        self.assertIn("fictional students", guide_block_alt_text(block))

    def test_latex_export_includes_a_static_sample_table_and_synthetic_data_label(self) -> None:
        upload_id = str(uuid4())
        latex = render_guide_latex(
            title="Sampling methods",
            class_name="Mathematics and Statistics",
            material_id=uuid4(),
            content={
                "format": "study_guide",
                "schema_version": 2,
                "sections": [
                    {
                        "heading": "Sampling and assignment",
                        "sources": [{"upload_id": upload_id}],
                        "blocks": [
                            {"type": "paragraph", "text": "Sampling selects a study population."},
                            {
                                "type": "interactive_model",
                                "template_id": "sampling_demo_v1",
                                "title": "Sampling lab",
                                "parameters": valid_block_parameters(),
                            },
                        ],
                    }
                ],
            },
            sources=[{"id": upload_id, "title": "Introduction to Statistics"}],
            render_math=False,
        )
        self.assertIn(r"\begin{tabular}{llll}", latex)
        self.assertIn("App-authored synthetic illustration, not source data", latex)
        self.assertEqual(
            latex.count("App-authored synthetic illustration, not source data"),
            1,
        )
        self.assertNotIn(r"\(App-\)authored", latex)
        self.assertNotIn(r"\(app-\)authored", latex)
        self.assertIn("Treatment A", latex)
        self.assertRegex(latex, r"[AB]\d{2} & Section [AB] & \d+ & Treatment [AB]")


if __name__ == "__main__":
    unittest.main()
