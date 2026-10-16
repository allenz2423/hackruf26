from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from pydantic import ValidationError

from app.api.schemas import StudyGuideBlock
from app.services.safe_svg import SVG_NAMESPACE, sanitize_svg


class SafeSvgTests(unittest.TestCase):
    def test_keeps_accessible_geometry_local_paint_and_bounded_animation(self) -> None:
        svg = f"""<svg xmlns="{SVG_NAMESPACE}" viewBox="0 0 200 100" onload="alert(1)">
          <title>Ball trajectory</title><desc>A ball moves along its path.</desc>
          <defs>
            <linearGradient id="sky">
              <stop offset="0%" stop-color="#abcdef"/>
            </linearGradient>
          </defs>
          <circle id="ball" cx="10" cy="80" r="5" fill="url(#sky)">
            <animate attributeName="cx" from="10" to="190" dur="2s" repeatCount="indefinite"/>
          </circle>
        </svg>"""

        sanitized = sanitize_svg(svg)
        root = ET.fromstring(sanitized)
        circle = root.find(f"{{{SVG_NAMESPACE}}}circle")

        self.assertIsNotNone(root.find(f"{{{SVG_NAMESPACE}}}title"))
        self.assertIsNotNone(root.find(f"{{{SVG_NAMESPACE}}}desc"))
        self.assertIsNotNone(circle)
        self.assertEqual(circle.get("fill"), "url(#sky)")
        self.assertIsNotNone(circle.find(f"{{{SVG_NAMESPACE}}}animate"))
        self.assertNotIn("onload", sanitized)

    def test_drops_executable_markup_events_styles_and_external_references(self) -> None:
        svg = f"""<svg xmlns="{SVG_NAMESPACE}" viewBox="0 0 100 100">
          <script>alert(1)</script>
          <foreignObject><iframe src="https://example.com"/></foreignObject>
          <image href="https://example.com/pixel.png"/>
          <a href="javascript:alert(1)"><text>unsafe link</text></a>
          <rect x="1" y="2" width="20" height="30" onclick="alert(1)"
                style="background:url(https://example.com/x)" fill="url(https://example.com/x)"/>
        </svg>"""

        sanitized = sanitize_svg(svg)
        root = ET.fromstring(sanitized)

        self.assertEqual(
            [element.tag.rsplit("}", 1)[-1] for element in root.iter()], ["svg", "rect"]
        )
        rect = root.find(f"{{{SVG_NAMESPACE}}}rect")
        self.assertEqual(rect.attrib, {"x": "1", "y": "2", "width": "20", "height": "30"})
        self.assertNotRegex(
            sanitized.lower(), r"script|foreignobject|https:|javascript:|onclick|style="
        )

    def test_rejects_invalid_roots_xml_and_document_entities(self) -> None:
        for svg in (
            "<html><svg viewBox='0 0 1 1'/></html>",
            "<svg viewBox='0 0 1 1'><circle></svg>",
            f"<!DOCTYPE svg [<!ENTITY x 'boom'>]><svg xmlns='{SVG_NAMESPACE}' viewBox='0 0 1 1'/>",
            "<svg viewBox='0 0 1000001 1'/>",
            "<svg viewBox='0 0 -1 1'/>",
        ):
            with self.subTest(svg=svg), self.assertRaises(ValueError):
                sanitize_svg(svg)

    def test_infers_a_missing_viewbox_from_bounded_dimensions(self) -> None:
        sanitized = sanitize_svg(f'<svg xmlns="{SVG_NAMESPACE}" width="200" height="100"/>')
        root = ET.fromstring(sanitized)

        self.assertEqual(root.get("viewBox"), "0 0 200 100")

    def test_bounds_elements_nesting_text_and_source_size(self) -> None:
        oversized_elements = (
            f'<svg xmlns="{SVG_NAMESPACE}" viewBox="0 0 1 1">' + "<circle/>" * 600 + "</svg>"
        )
        oversized_nesting = (
            f'<svg xmlns="{SVG_NAMESPACE}" viewBox="0 0 1 1">' + "<g>" * 33 + "</g>" * 33 + "</svg>"
        )
        oversized_text = (
            f'<svg xmlns="{SVG_NAMESPACE}" viewBox="0 0 1 1"><text>{"x" * 8_001}</text></svg>'
        )
        oversized_source = f'<svg xmlns="{SVG_NAMESPACE}" viewBox="0 0 1 1">{" " * 60_001}</svg>'

        for svg in (oversized_elements, oversized_nesting, oversized_text, oversized_source):
            with self.subTest(length=len(svg)), self.assertRaises(ValueError):
                sanitize_svg(svg)

    def test_recursive_local_use_is_removed(self) -> None:
        svg = f"""<svg xmlns="{SVG_NAMESPACE}" viewBox="0 0 100 100">
          <defs><g id="loop"><use href="#loop"/></g></defs><use href="#loop"/>
        </svg>"""

        root = ET.fromstring(sanitize_svg(svg))

        self.assertFalse(list(root.iter(f"{{{SVG_NAMESPACE}}}use")))

    def test_study_guide_contract_persists_only_sanitized_svg(self) -> None:
        block = StudyGuideBlock.model_validate(
            {
                "type": "svg_diagram",
                "svg": f"<svg xmlns='{SVG_NAMESPACE}' viewBox='0 0 20 20'>"
                "<script>alert(1)</script><text x='1' y='10'>Safe label</text></svg>",
                "title": "A small diagram",
                "alt_text": "A labeled vector.",
                "fallback_text": "The vector points to the right.",
            }
        )

        self.assertNotIn("<script", block.svg)
        self.assertIn("Safe label", block.svg)

        with self.assertRaises(ValidationError):
            StudyGuideBlock.model_validate(
                {
                    "type": "svg_diagram",
                    "svg": "<html/>",
                    "title": "Invalid",
                    "alt_text": "Invalid drawing.",
                    "fallback_text": "A text alternative.",
                }
            )


if __name__ == "__main__":
    unittest.main()
