from __future__ import annotations

import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from PIL import Image

from app.api.schemas import StudyGuideSectionV2
from app.config import Settings
from app.services.gemini_content import (
    GeminiContentService,
    _StudyGuideSectionBatch,
    _StudyGuideVisualEdit,
    _StudyGuideVisualEditBatch,
)
from app.services.openai_compatible import _parts_to_chat_content
from app.services.study_guide_visual_preview import (
    SvgPreviewRenderError,
    render_svg_preview_png,
)


def png_bytes(width: int = 20, height: int = 10) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def make_service() -> GeminiContentService:
    return GeminiContentService(
        None,
        Settings(
            _env_file=None,
            sqlite_path=":memory:",
            file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
        ),
    )


class SvgPreviewRendererTests(unittest.TestCase):
    def test_renders_only_sanitized_svg_with_bounded_dimensions(self) -> None:
        output = png_bytes()
        completed = SimpleNamespace(returncode=0, stdout=output, stderr=b"")
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 10">'
            '<script>alert(1)</script><text x="1" y="8">Safe</text></svg>'
        )

        with (
            patch(
                "app.services.study_guide_visual_preview.shutil.which",
                return_value="/usr/bin/rsvg-convert",
            ),
            patch(
                "app.services.study_guide_visual_preview.subprocess.run",
                return_value=completed,
            ) as run,
        ):
            result = render_svg_preview_png(svg)

        self.assertEqual(result, output)
        self.assertEqual(run.call_args.args[0][0], "/usr/bin/rsvg-convert")
        self.assertIn("--width=1280", run.call_args.args[0])
        self.assertIn("--height=640", run.call_args.args[0])
        self.assertNotIn(b"<script", run.call_args.kwargs["input"])
        self.assertIn(b"Safe", run.call_args.kwargs["input"])
        self.assertEqual(run.call_args.kwargs["timeout"], 12)

    def test_requested_mobile_preview_uses_the_phone_card_scale(self) -> None:
        output = png_bytes(360, 180)
        with (
            patch("app.services.study_guide_visual_preview.shutil.which", return_value="rsvg-convert"),
            patch(
                "app.services.study_guide_visual_preview.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout=output, stderr=b""),
            ) as run,
        ):
            rendered = render_svg_preview_png(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 10"/>',
                max_width=360,
                max_height=420,
            )

        self.assertEqual(rendered, output)
        self.assertIn("--width=360", run.call_args.args[0])
        self.assertIn("--height=180", run.call_args.args[0])

    def test_fails_closed_when_renderer_is_missing_or_png_is_invalid(self) -> None:
        with patch("app.services.study_guide_visual_preview.shutil.which", return_value=None):
            with self.assertRaises(SvgPreviewRenderError):
                render_svg_preview_png(
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>'
                )

        with (
            patch(
                "app.services.study_guide_visual_preview.shutil.which", return_value="rsvg-convert"
            ),
            patch(
                "app.services.study_guide_visual_preview.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout=b"not a png", stderr=b""),
            ),
        ):
            with self.assertRaises(SvgPreviewRenderError):
                render_svg_preview_png(
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>'
                )

    def test_rejects_rendered_images_over_pixel_limit(self) -> None:
        with (
            patch(
                "app.services.study_guide_visual_preview.shutil.which", return_value="rsvg-convert"
            ),
            patch(
                "app.services.study_guide_visual_preview.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout=png_bytes(1280, 961), stderr=b""),
            ),
        ):
            with self.assertRaises(SvgPreviewRenderError):
                render_svg_preview_png(
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>'
                )


class StudyGuideVisualSelfPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = make_service()

    def test_same_author_model_sees_rendered_image_and_repairs_only_svg_in_place(self) -> None:
        original_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
            '<text x="5" y="20">Initial</text></svg>'
        )
        corrected_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
            '<text x="5" y="20">Corrected</text></svg>'
        )
        sections = [
            {
                "heading": "Memory hierarchy",
                "blocks": [
                    {
                        "id": "memory-map",
                        "type": "svg_diagram",
                        "title": "Memory map",
                        "svg": original_svg,
                        "alt_text": "Initial diagram.",
                        "fallback_text": "Registers, cache, and memory.",
                        "sources": [{"upload_id": "source-1"}],
                    },
                    {"type": "paragraph", "text": "The explanation remains intact."},
                ],
            }
        ]
        self.service._generate_json = Mock(
            side_effect=[
                _StudyGuideVisualEditBatch(
                    items=[
                        _StudyGuideVisualEdit(
                            block_id="memory-map",
                            visual_note="Labels need clearer spacing.",
                            replacement_svg=corrected_svg,
                        )
                    ]
                ),
                _StudyGuideVisualEditBatch(
                    items=[
                        _StudyGuideVisualEdit(
                            block_id="memory-map",
                            visual_note="The corrected diagram is legible.",
                            replacement_svg=None,
                        )
                    ]
                ),
            ]
        )

        with patch(
            "app.services.gemini_content.render_svg_preview_png",
            side_effect=[b"original rendered preview", b"corrected rendered preview"],
        ) as render_preview:
            result = self.service._self_preview_study_guide_svgs(sections)

        self.assertIs(result, sections)
        self.assertIn("Corrected", sections[0]["blocks"][0]["svg"])
        self.assertEqual(sections[0]["blocks"][1]["text"], "The explanation remains intact.")
        self.assertEqual(self.service._generate_json.call_count, 2)
        self.assertEqual(render_preview.call_count, 2)
        for call in render_preview.call_args_list:
            self.assertEqual(call.kwargs["max_width"], 360)
            self.assertEqual(call.kwargs["max_height"], 420)
        for call in self.service._generate_json.call_args_list:
            parts = call.args[0]
            self.assertIn("visual self-preview", parts[0].lower())
            content = _parts_to_chat_content(parts)
            self.assertTrue(any(item.get("type") == "image_url" for item in content))
        self.assertIn(
            "original rendered preview", str(self.service._generate_json.call_args_list[0])
        )
        self.assertIn(
            "corrected rendered preview", str(self.service._generate_json.call_args_list[1])
        )

    def test_model_without_svg_blocks_is_not_called(self) -> None:
        self.service._generate_json = Mock()
        sections = [{"heading": "Text only", "blocks": [{"type": "paragraph", "text": "Text."}]}]

        result = self.service._self_preview_study_guide_svgs(sections)

        self.assertIs(result, sections)
        self.service._generate_json.assert_not_called()

    def test_visual_review_happens_before_revised_batch_is_published(self) -> None:
        upload_id = str(uuid4())
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
            '<text x="5" y="20">A</text></svg>'
        )

        def batch(*, include_svg: bool) -> _StudyGuideSectionBatch:
            blocks = [{"type": "paragraph", "text": "The concept is source grounded."}]
            if include_svg:
                blocks.append(
                    {
                        "type": "svg_diagram",
                        "title": "Concept map",
                        "svg": svg,
                        "alt_text": "A concept map.",
                        "fallback_text": "A connects to B.",
                        "sources": [{"upload_id": upload_id}],
                    }
                )
            section = StudyGuideSectionV2.model_validate(
                {
                    "heading": "Concept map",
                    "sources": [{"upload_id": upload_id}],
                    "blocks": blocks,
                }
            )
            return _StudyGuideSectionBatch(
                format="study_guide", sections=[section], requested_tool_ids=[]
            )

        self.service._generate_json = Mock(
            side_effect=[
                batch(include_svg=False),
                batch(include_svg=True),
                _StudyGuideVisualEditBatch(
                    items=[
                        _StudyGuideVisualEdit(
                            block_id="preview-section-1-block-2",
                            visual_note="The diagram is legible.",
                            replacement_svg=None,
                        )
                    ]
                ),
            ]
        )
        previews: list[dict] = []

        with patch("app.services.gemini_content.render_svg_preview_png", return_value=b"png"):
            content = self.service._generate_study_guide_sections(
                base_prompt="Create a guide.",
                count=1,
                system_instruction="Use only the source.",
                allowed_source_ids={upload_id},
                guide_progress_callback=previews.append,
            )

        self.assertEqual(len(previews), 2)
        self.assertEqual(len(previews[0]["sections"][0]["blocks"]), 1)
        self.assertEqual(len(previews[1]["sections"][0]["blocks"]), 2)
        self.assertEqual(len(content["sections"][0]["blocks"]), 2)
        review_parts = self.service._generate_json.call_args_list[2].args[0]
        self.assertTrue(any(hasattr(part, "inline_data") for part in review_parts))

    def test_unavailable_visual_self_preview_keeps_sanitized_authored_diagram(self) -> None:
        original_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
            '<text x="5" y="20">Keep</text></svg>'
        )
        sections = [
            {
                "heading": "Section",
                "blocks": [
                    {
                        "type": "svg_diagram",
                        "svg": original_svg,
                        "title": "Diagram",
                        "alt_text": "Diagram.",
                        "fallback_text": "A diagram.",
                    }
                ],
            }
        ]
        self.service._generate_json = Mock(side_effect=RuntimeError("provider unavailable"))

        with patch("app.services.gemini_content.render_svg_preview_png", return_value=b"png"):
            self.service._self_preview_study_guide_svgs(sections)

        self.assertIn("Keep", sections[0]["blocks"][0]["svg"])


if __name__ == "__main__":
    unittest.main()
