"""The model's box_2d marks become the browser's normalized regions."""

from __future__ import annotations

import unittest

from app.services.live_config import annotation_items_for_client, annotation_tool


class AnnotationConversionTests(unittest.TestCase):
    def test_box_2d_becomes_a_normalized_region(self) -> None:
        items, errors = annotation_items_for_client(
            [{"kind": "rectangle", "label": "Title", "box_2d": [100, 250, 160, 750]}]
        )
        self.assertEqual(errors, [])
        [mark] = items
        self.assertEqual((mark["kind"], mark["label"]), ("rectangle", "Title"))
        region = mark["region"]
        self.assertAlmostEqual(region["x"], 0.25)
        self.assertAlmostEqual(region["y"], 0.10)
        self.assertAlmostEqual(region["width"], 0.50)
        self.assertAlmostEqual(region["height"], 0.06)

    def test_edges_stay_inside_the_frame(self) -> None:
        [mark], _ = annotation_items_for_client(
            [{"kind": "rectangle", "label": "All", "box_2d": [0, 300, 1000, 1000]}]
        )
        region = mark["region"]
        self.assertLessEqual(region["x"] + region["width"], 1)
        self.assertLessEqual(region["y"] + region["height"], 1)
        # Out-of-range numbers are clamped rather than trusted.
        [clamped], _ = annotation_items_for_client(
            [{"kind": "pointer", "label": "x", "box_2d": [-50, -10, 1200, 400]}]
        )
        self.assertEqual((clamped["region"]["x"], clamped["region"]["y"]), (0.0, 0.0))

    def test_malformed_boxes_are_reported_not_forwarded(self) -> None:
        for bad in (
            {"kind": "rectangle", "label": "a", "box_2d": [1, 2, 3]},
            {"kind": "rectangle", "label": "a", "box_2d": [500, 500, 400, 600]},
            {"kind": "rectangle", "label": "a", "box_2d": ["1", "2", "3", "4"]},
            {"kind": "rectangle", "label": "a", "box_2d": [True, 0, 10, 10]},
            {"kind": "rectangle", "label": "a", "region": {"x": 0, "y": 0, "width": 1, "height": 1}},
            "not an item",
        ):
            with self.subTest(item=bad):
                items, errors = annotation_items_for_client([bad])
                self.assertEqual(items, [])
                self.assertEqual(len(errors), 1)
        self.assertEqual(annotation_items_for_client("nope"), ([], ["items must be a list."]))

    def test_the_tool_asks_for_box_2d(self) -> None:
        [declaration] = annotation_tool().function_declarations
        item = declaration.parameters.properties["items"].items
        self.assertIn("box_2d", item.required)
        self.assertNotIn("region", item.properties)


if __name__ == "__main__":
    unittest.main()
