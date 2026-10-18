from __future__ import annotations

import io
import unittest
import xml.etree.ElementTree as ET
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
    audit_svg_phone_text,
    phone_text_audit_not_worse,
    render_svg_animation_previews,
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


class SvgPhoneTextAuditTests(unittest.TestCase):
    def test_measures_labels_after_scaling_to_the_phone_card(self) -> None:
        audit = audit_svg_phone_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 800">'
            '<text x="10" y="30" font-size="18">small</text>'
            '<text x="10" y="70" font-size="24">minimum</text></svg>'
        )

        self.assertEqual(audit.rendered_text_runs, 2)
        self.assertEqual(audit.undersized_text_runs, 1)
        self.assertEqual(audit.smallest_text_px, 9.0)
        self.assertEqual(audit.scale, 0.5)
        self.assertIn("below 12 CSS px", audit.prompt_diagnostic() or "")

    def test_inherits_font_size_and_accounts_for_nested_group_scale(self) -> None:
        audit = audit_svg_phone_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 200">'
            '<g font-size="24" transform="scale(0.5 2)">'
            '<text x="10" y="30">readable</text>'
            '<text x="10" y="70"><tspan font-size="22">small</tspan></text>'
            '</g></svg>'
        )

        self.assertEqual(audit.rendered_text_runs, 2)
        self.assertEqual(audit.undersized_text_runs, 1)
        self.assertEqual(audit.smallest_text_px, 11.0)

    def test_ignores_nonvisible_accessibility_text_and_hidden_labels(self) -> None:
        audit = audit_svg_phone_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 200">'
            '<title>Accessible title</title><desc>Long description</desc>'
            '<text display="none">hidden</text><text visibility="hidden">hidden</text>'
            '<text opacity="0">hidden</text><text x="10" y="30">Visible</text></svg>'
        )

        self.assertEqual(audit.rendered_text_runs, 1)
        self.assertFalse(audit.has_small_text)

    def test_edit_must_not_make_phone_text_audit_worse(self) -> None:
        readable = audit_svg_phone_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 800">'
            '<text font-size="20">label</text></svg>'
        )
        smaller = audit_svg_phone_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 800">'
            '<text font-size="12">label</text></svg>'
        )

        self.assertTrue(phone_text_audit_not_worse(readable, readable))
        self.assertFalse(phone_text_audit_not_worse(readable, smaller))


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


class SvgAnimationPreviewTests(unittest.TestCase):
    def test_samples_finite_numeric_motion_at_start_midpoint_and_end(self) -> None:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
            '<circle cx="10" cy="20" r="4"><animate attributeName="cx" dur="4s" '
            'values="10;70" repeatCount="1"/></circle></svg>'
        )
        sampled_x: list[float] = []

        def capture(markup: str, **_kwargs: object) -> bytes:
            root = ET.fromstring(markup)
            circle = root.find("{http://www.w3.org/2000/svg}circle")
            assert circle is not None
            sampled_x.append(float(circle.get("cx", "nan")))
            return b"png"

        with patch(
            "app.services.study_guide_visual_preview.render_svg_preview_png",
            side_effect=capture,
        ):
            result = render_svg_animation_previews(svg)

        self.assertEqual([label for label, _ in result.frames], ["start", "midpoint", "end"])
        self.assertEqual(sampled_x, [10.0, 40.0, 70.0])
        self.assertEqual(result.unsupported_types, ())

    def test_flags_motion_paths_instead_of_claiming_their_frames_were_sampled(self) -> None:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
            '<circle cx="10" cy="20" r="4"><animateMotion dur="2s" '
            'path="M 0 0 L 50 0"/></circle></svg>'
        )
        with patch(
            "app.services.study_guide_visual_preview.render_svg_preview_png"
        ) as render:
            result = render_svg_animation_previews(svg)

        self.assertEqual(result.frames, ())
        self.assertIn("animateMotion_path_not_sampled", result.unsupported_types)
        render.assert_not_called()

    def test_does_not_claim_synchronized_frames_for_mixed_animation_durations(self) -> None:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
            '<circle cx="10" cy="20" r="4"><animate attributeName="cx" dur="2s" '
            'values="10;70"/></circle>'
            '<circle cx="20" cy="30" r="4"><animate attributeName="cx" dur="4s" '
            'values="20;80"/></circle></svg>'
        )
        with patch(
            "app.services.study_guide_visual_preview.render_svg_preview_png"
        ) as render:
            result = render_svg_animation_previews(svg)

        self.assertEqual(result.frames, ())
        self.assertIn("mixed_or_unreadable_animation_durations", result.unsupported_types)
        render.assert_not_called()

    def test_samples_supported_numeric_transform_animation(self) -> None:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
            '<rect x="0" y="0" width="10" height="10" transform="scale(2)">'
            '<animateTransform attributeName="transform" type="translate" from="10 0" '
            'to="30 0" dur="2s"/></rect></svg>'
        )
        sampled_transforms: list[str] = []

        def capture(markup: str, **_kwargs: object) -> bytes:
            root = ET.fromstring(markup)
            rect = root.find("{http://www.w3.org/2000/svg}rect")
            assert rect is not None
            sampled_transforms.append(rect.get("transform", ""))
            return b"png"

        with patch(
            "app.services.study_guide_visual_preview.render_svg_preview_png",
            side_effect=capture,
        ):
            result = render_svg_animation_previews(svg)

        self.assertEqual(sampled_transforms, ["translate(10 0)", "translate(20 0)", "translate(30 0)"])
        self.assertEqual(result.unsupported_types, ())

    def test_does_not_sample_non_linear_spline_timing_as_if_it_were_linear(self) -> None:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
            '<circle cx="10" cy="20" r="4"><animate attributeName="cx" dur="2s" '
            'values="10;70" calcMode="spline" keySplines="0 0 1 1"/></circle></svg>'
        )
        with patch(
            "app.services.study_guide_visual_preview.render_svg_preview_png"
        ) as render:
            result = render_svg_animation_previews(svg)

        self.assertEqual(result.frames, ())
        self.assertIn("spline_interpolation_approximated_as_linear", result.unsupported_types)
        render.assert_not_called()

    def test_does_not_sample_unmodeled_begin_or_repeat_duration_semantics(self) -> None:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
            '<circle cx="10" cy="20" r="4"><animate attributeName="cx" dur="2s" '
            'begin="0s;3s" repeatDur="5s" values="10;70"/></circle></svg>'
        )
        with patch(
            "app.services.study_guide_visual_preview.render_svg_preview_png"
        ) as render:
            result = render_svg_animation_previews(svg)

        self.assertEqual(result.frames, ())
        self.assertIn("unreadable_animation_start", result.unsupported_types)
        self.assertIn("advanced_animation_duration_not_sampled", result.unsupported_types)
        render.assert_not_called()

    def test_static_svg_has_no_animation_frames(self) -> None:
        result = render_svg_animation_previews(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
            '<circle cx="10" cy="20" r="4"/></svg>'
        )

        self.assertEqual(result.frames, ())
        self.assertEqual(result.unsupported_types, ())

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

    def test_small_text_measurement_is_sent_to_author_and_worse_edit_is_rejected(self) -> None:
        original_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 800">'
            '<text x="10" y="30" font-size="16">Initial label</text></svg>'
        )
        smaller_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 800">'
            '<text x="10" y="30" font-size="12">Smaller label</text></svg>'
        )
        sections = [
            {
                "heading": "Osmosis",
                "blocks": [
                    {
                        "id": "osmosis-figure",
                        "type": "svg_diagram",
                        "title": "Water movement",
                        "svg": original_svg,
                        "alt_text": "Water crosses a membrane.",
                        "fallback_text": "Water moves both ways with a net direction.",
                    }
                ],
            }
        ]
        self.service._generate_json = Mock(
            return_value=_StudyGuideVisualEditBatch(
                items=[
                    _StudyGuideVisualEdit(
                        block_id="osmosis-figure",
                        visual_note="The label could be clearer.",
                        replacement_svg=smaller_svg,
                    )
                ]
            )
        )

        with patch(
            "app.services.gemini_content.render_svg_preview_png", return_value=b"phone preview"
        ) as render_preview:
            self.service._self_preview_study_guide_svgs(sections)

        self.assertEqual(sections[0]["blocks"][0]["svg"], original_svg)
        self.assertEqual(render_preview.call_count, 1)
        prompt = str(self.service._generate_json.call_args.args[0][0])
        self.assertIn("below 12 CSS px", prompt)
        self.assertIn("smallest is about 8 px", prompt)

    def test_model_sees_all_sampled_animation_frames_before_it_can_edit(self) -> None:
        animated_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
            '<circle cx="10" cy="20" r="4"><animate attributeName="cx" dur="4s" '
            'values="10;70" repeatCount="1"/></circle></svg>'
        )
        sections = [
            {
                "heading": "Motion",
                "blocks": [
                    {
                        "id": "moving-dot",
                        "type": "svg_diagram",
                        "title": "Moving dot",
                        "svg": animated_svg,
                        "alt_text": "A dot moves right.",
                        "fallback_text": "The dot moves from left to right.",
                    }
                ],
            }
        ]
        self.service._generate_json = Mock(
            return_value=_StudyGuideVisualEditBatch(
                items=[
                    _StudyGuideVisualEdit(
                        block_id="moving-dot", visual_note="Motion is clear.", replacement_svg=None
                    )
                ]
            )
        )

        with (
            patch(
                "app.services.gemini_content.render_svg_preview_png", return_value=b"still"
            ) as static_renderer,
            patch(
                "app.services.study_guide_visual_preview.render_svg_preview_png",
                side_effect=[b"start", b"midpoint", b"end"],
            ),
        ):
            self.service._self_preview_study_guide_svgs(sections)

        parts = self.service._generate_json.call_args.args[0]
        content = _parts_to_chat_content(parts)
        image_parts = [item for item in content if item.get("type") == "image_url"]
        self.assertEqual(len(image_parts), 3)
        static_renderer.assert_not_called()
        for label in ("start", "midpoint", "end"):
            self.assertIn(label, str(parts))
        self.assertIn("objects must not collide", str(parts[0]))

    def test_preview_reports_when_animation_motion_was_not_sampled(self) -> None:
        motion_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
            '<circle cx="10" cy="20" r="4"><animateMotion dur="2s" '
            'path="M 0 0 L 50 0"/></circle></svg>'
        )
        sections = [
            {
                "heading": "Motion",
                "blocks": [
                    {
                        "id": "moving-dot",
                        "type": "svg_diagram",
                        "svg": motion_svg,
                        "title": "Moving dot",
                        "alt_text": "A dot moves.",
                        "fallback_text": "A dot moves along a path.",
                    }
                ],
            }
        ]
        self.service._generate_json = Mock(
            return_value=_StudyGuideVisualEditBatch(
                items=[
                    _StudyGuideVisualEdit(
                        block_id="moving-dot", visual_note="Keep it simple.", replacement_svg=None
                    )
                ]
            )
        )

        with patch("app.services.gemini_content.render_svg_preview_png", return_value=b"still"):
            self.service._self_preview_study_guide_svgs(sections)

        parts = self.service._generate_json.call_args.args[0]
        self.assertIn("animateMotion_path_not_sampled", str(parts))
        self.assertIn("simplify it to directly animated numeric SVG attributes", str(parts[0]))

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
                    "mini_quiz": {
                        "question": "What does the diagram connect?",
                        "choices": [
                            "Concept A to concept B",
                            "Two unrelated objects",
                            "A value to its unit",
                            "No relationship",
                        ],
                        "correct_index": 0,
                        "explanation": "The figure's text alternative describes A connecting to B.",
                    },
                    "blocks": blocks,
                }
            )
            return _StudyGuideSectionBatch(
                format="study_guide",
                sections=[section.model_dump(mode="json")],
                requested_tool_ids=[],
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
