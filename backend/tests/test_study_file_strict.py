"""Strict math validation and export regressions for generated study documents."""

from __future__ import annotations

import io
import subprocess
import unittest
from unittest.mock import patch
from uuid import uuid4

from pypdf import PdfReader

from app.config import Settings
from app.services.study_files import (
    StudyFileFailure,
    _body,
    _inline,
    _safe_math,
    compile_guide_pdf,
    compiler_path,
    create_guide_files,
)


def settings() -> Settings:
    return Settings(
        _env_file=None,
        file_url_signing_secret="test-signing-secret-with-at-least-32-characters",
    )


def guide(body: str) -> dict:
    return {
        "title": "Stress tensor",
        "class_name": "Mechanics",
        "material_id": uuid4(),
        "content": {
            "format": "study_guide",
            "sections": [{"heading": "Stress state", "body": body, "sources": []}],
        },
        "sources": [],
    }


class StrictStudyFileTests(unittest.TestCase):
    def test_pdf_body_preserves_headings_bold_emphasis_and_ordered_lists(self) -> None:
        rendered = _body(
            "### A focused heading\n"
            "Use **bold terms** when they carry meaning.\n\n"
            "1. Compare the first case.\n"
            "2) Explain the result.\n"
            "- Keep the comparison clear."
        )

        self.assertIn(r"\noindent\textbf{A focused heading}", rendered)
        self.assertIn(r"\textbf{bold terms}", rendered)
        self.assertIn(r"\begin{enumerate}", rendered)
        self.assertEqual(rendered.count(r"\item "), 3)
        self.assertIn(r"\end{enumerate}", rendered)
        self.assertIn(r"\begin{itemize}", rendered)
        self.assertIn(r"\end{itemize}", rendered)

    def test_blank_lines_preserve_paragraph_segmentation_in_pdf(self) -> None:
        rendered = _body("First idea.\n\nSecond idea.")

        self.assertEqual(rendered.count(r"\par"), 2)
        self.assertLess(rendered.index("First idea."), rendered.index("Second idea."))

    def test_inline_math_stops_before_closing_text_bracket(self) -> None:
        rendered = _inline("[" + r"\mathrm{H}^+" + "]: Hydrogen-ion concentration.")

        self.assertEqual(
            rendered,
            r"[\(\mathrm{H}^+\)]: Hydrogen-ion concentration.",
        )

    def test_compact_unicode_engineering_math_keeps_indices_decimals_and_units(self) -> None:
        rendered = _inline(
            "σ₃ = 1.4678 MPa. With σY = 300 MPa, Kc = 80 MPa√m, "
            "the check uses x^8 ≡x^4 + x^3 + x + 1, ΔG < 0, "
            "τxy, γxy, and 3.57 ± 0.005 \\pu{kg}. The unit is MPa√m."
        )

        self.assertIn(r"\(\sigma_{3} = 1.4678 MPa\)", rendered)
        self.assertIn(r"\sigma_{Y} = 300 MPa", rendered)
        self.assertIn(r"\sqrt{m}", rendered)
        self.assertIn(r"MPa\(\sqrt{m}\)", rendered)
        self.assertIn(r"\equiv{}x", rendered)
        self.assertIn(r"\Delta{}G < 0", rendered)
        self.assertIn(r"\tau_{xy}", rendered)
        self.assertIn(r"\gamma_{xy}", rendered)
        self.assertIn(r"\mathrm{kg}", rendered)

    def test_long_related_display_equations_are_stacked_and_aligned(self) -> None:
        formula = (
            r"n=\frac{4E}{3(1-\nu^2)},\qquad m=\frac{4}{1/r_1+1/r_2},"
            r"\qquad A=\frac{2}{m},\qquad B=\frac{1}{2}\left|"
            r"\frac{1}{r_1}-\frac{1}{r_2}\right|,\qquad p_0=\frac{1.5F}{\pi ab}"
        )
        rendered = _inline("$$" + formula + "$$", strict_math=True)

        self.assertIn(r"\begin{aligned}", rendered)
        self.assertIn(r"n &= \frac{4E}{3(1-\nu^2)}", rendered)
        self.assertIn(r"m &= \frac{4}{1/r_1+1/r_2}", rendered)
        self.assertIn("\\\\\n", rendered)

    @unittest.skipUnless(compiler_path(settings()), "Tectonic is not installed")
    def test_source_reference_list_flows_after_short_guide_content(self) -> None:
        upload_id = uuid4()
        arguments = guide("A short, complete explanation.")
        arguments["content"]["sections"][0]["sources"] = [
            {"upload_id": str(upload_id), "page": 2}
        ]
        arguments["sources"] = [{"id": upload_id, "title": "Lecture.pdf"}]
        files = create_guide_files(settings(), strict_math=True, **arguments)

        self.assertNotIn(
            "\\newpage\n\\section*{Source references}", files["latex"].decode("utf-8")
        )
        self.assertEqual(len(PdfReader(io.BytesIO(files["pdf"])).pages), 2)

    @unittest.skipUnless(compiler_path(settings()), "Tectonic is not installed")
    def test_compact_unicode_engineering_math_compiles_without_global_fallback(self) -> None:
        body = (
            "The reported principal stress is σ₃ = 1.4678 MPa. The yield strength is "
            "σY = 300 MPa. Fracture toughness is Kc = 80 MPa√m. For the polynomial, "
            "x^8 ≡x^4 + x^3 + x + 1. The reaction is spontaneous when ΔG < 0. "
            "The measured components are τxy and γxy; the mass is 3.57 ± 0.005 \\pu{kg}. "
            "The unit is MPa√m."
        )
        files = create_guide_files(settings(), strict_math=True, **guide(body))
        latex = files["latex"].decode("utf-8")
        extracted = " ".join(
            page.extract_text() or ""
            for page in PdfReader(io.BytesIO(files["pdf"])).pages
        )

        self.assertIn(r"\(\sigma_{3} = 1.4678 MPa\)", latex)
        self.assertIn(r"\sigma_{Y} = 300 MPa", latex)
        self.assertIn(r"\sqrt{m}", latex)
        self.assertIn(r"MPa\(\sqrt{m}\)", latex)
        self.assertIn(r"\equiv{}x", latex)
        self.assertIn(r"\Delta{}G < 0", latex)
        self.assertIn(r"\tau_{xy}", latex)
        self.assertIn(r"\gamma_{xy}", latex)
        self.assertIn(r"\mathrm{kg}", latex)
        self.assertIn("1.4678", extracted)
        self.assertIn("300", extracted)
        self.assertNotIn(r"\textbackslash{}sigma", latex)

    def test_compile_failure_uses_error_line_after_earlier_warnings(self) -> None:
        diagnostics = (
            b"warning: guide.tex:22: Overfull \\hbox detected\n"
            b"error: guide.tex:63: Undefined control sequence"
        )
        with (
            patch("app.services.study_files.compiler_path", return_value="/fake/tectonic"),
            patch(
                "app.services.study_files.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["tectonic"], returncode=1, stdout=b"", stderr=diagnostics
                ),
            ),
        ):
            with self.assertRaises(StudyFileFailure) as failure:
                compile_guide_pdf("\\begin{document}x\\end{document}", settings())

        self.assertEqual(failure.exception.line_number, 63)

    def test_hyphenated_prose_remains_plain_text_while_bare_subtraction_renders(self) -> None:
        prose = (
            "A six-carbon sugar has a non-reducing end, a two-dimensional cis-trans model, "
            "and low-to-high movement. The model's assumptions matter to students' answers."
        )
        self.assertEqual(_inline(prose, strict_math=True), prose)
        self.assertEqual(
            _inline("The difference is x-y.", strict_math=True),
            "The difference is \\(x-y\\).",
        )
        self.assertEqual(
            _inline("The transformed coordinate is x'.", strict_math=True),
            "The transformed coordinate is \\(x'\\).",
        )

    def test_common_missing_tex_command_separator_is_repaired(self) -> None:
        rendered = _body(r"Specific heat is $4.184 J/(g\cdotK)$.")

        self.assertIn(r"\(4.184 J/(g\cdot K)\)", rendered)
        self.assertNotIn(r"\cdotK", rendered)

    @unittest.skipUnless(compiler_path(settings()), "Tectonic is not installed")
    def test_repaired_inline_math_survives_full_pdf_export(self) -> None:
        files = create_guide_files(
            settings(),
            **guide(
                "Specific heat is 4.184 J/(g·K).\n\n"
                + "The hydrogen ion is [" + r"\mathrm{H}^+" + "]: concentration."
            ),
        )
        text = "\n".join(
            page.extract_text() or ""
            for page in PdfReader(io.BytesIO(files["pdf"])).pages
        )

        self.assertIn(r"\cdot{}K", files["latex"].decode("utf-8"))
        self.assertIn(r"\mathrm{H}^+", files["latex"].decode("utf-8"))
        self.assertNotIn(r"\textbackslash{}cdot", files["latex"].decode("utf-8"))
        self.assertNotIn(r"\textbackslash{}mathrm", files["latex"].decode("utf-8"))
        self.assertIn("4.184", text)
        self.assertIn("H", text)

    @unittest.skipUnless(compiler_path(settings()), "Tectonic is not installed")
    def test_formula_and_short_definition_list_stay_together_in_pdf(self) -> None:
        document = {
            "format": "study_guide",
            "schema_version": 2,
            "sections": [
                {
                    "heading": "Energy",
                    "key_terms": [],
                    "sources": [],
                    "blocks": [
                        {
                            "type": "formula",
                            "expression": r"\Delta G=\Delta H-T\Delta S",
                            "definitions": [
                                {"symbol": "G", "meaning": "Free energy"},
                                {"symbol": "H", "meaning": "Enthalpy"},
                                {"symbol": "T", "meaning": "Absolute temperature", "unit": "K"},
                                {"symbol": "S", "meaning": "Entropy"},
                            ],
                            "sources": [],
                        }
                    ],
                }
            ],
        }
        files = create_guide_files(
            settings(),
            title="Energy",
            class_name="Biology",
            material_id=uuid4(),
            content=document,
            sources=[],
            strict_math=True,
        )
        latex = files["latex"].decode("utf-8")
        self.assertRegex(
            latex,
            r"\\begin\{samepage\}[\s\S]*\\begin\{itemize\}[\s\S]*"
            r"\\end\{itemize\}[\s\S]*\\end\{samepage\}",
        )

    @unittest.skipUnless(compiler_path(settings()), "Tectonic is not installed")
    def test_takeaway_heading_and_text_stay_on_the_same_pdf_page(self) -> None:
        recap = (
            "Keep the central relationship in view when choosing a method. Check the "
            "assumptions, then explain what the result means for the question at hand."
        )
        content = {
            "format": "study_guide",
            "schema_version": 2,
            "sections": [
                {
                    "heading": "A page-break test",
                    "key_terms": [],
                    "sources": [],
                    "blocks": [
                        {
                            "id": "definition",
                            "type": "paragraph",
                            "text": (
                                "A system can be described by a small set of related variables. "
                                "A model makes their relationships explicit so that a learner can "
                                "predict what should change when one input changes."
                            ),
                            "sources": [],
                        },
                        {
                            "id": "mechanism",
                            "type": "paragraph",
                            "text": (
                                "First identify the starting conditions and the relevant rule. "
                                "Then trace how the rule connects the variables, keeping units "
                                "and assumptions visible throughout the reasoning."
                            ),
                            "sources": [],
                        },
                        {
                            "id": "interpretation",
                            "type": "paragraph",
                            "text": (
                                "Finally, compare the result with the original question. A "
                                "numerical answer is useful only when it has the expected units "
                                "and the interpretation follows from the model assumptions."
                            ),
                            "sources": [],
                        },
                        {"id": "recap", "type": "takeaway", "text": recap, "sources": []},
                    ],
                }
            ],
        }
        files = create_guide_files(
            settings(),
            title="Takeaway pagination",
            class_name="Test",
            material_id=uuid4(),
            content=content,
            sources=[],
            strict_math=True,
        )
        page_text = [
            page.extract_text() or "" for page in PdfReader(io.BytesIO(files["pdf"])).pages
        ]
        recap_pages = [
            index
            for index, text in enumerate(page_text)
            if "Keep the central relationship" in text or "what the result means" in text
        ]

        self.assertEqual(len(recap_pages), 1)
        self.assertIn("Key takeaway", page_text[recap_pages[0]])

    @unittest.skipUnless(compiler_path(settings()), "Tectonic is not installed")
    def test_malformed_equation_degrades_locally_without_erasing_other_math(self) -> None:
        files = create_guide_files(
            settings(),
            **guide(
                "Retain this equation: $x^2$.\n\n"
                "Show this malformed equation as text: $\\notacommand{x}$."
            ),
        )
        latex = files["latex"].decode("utf-8")
        text = "\n".join(
            page.extract_text() or ""
            for page in PdfReader(io.BytesIO(files["pdf"])).pages
        )

        self.assertIn(r"\(x^2\)", latex)
        self.assertIn("notacommand(x)", latex)
        self.assertNotIn(r"\textbackslash{}notacommand", latex)
        self.assertIn("x", text)

    @unittest.skipUnless(compiler_path(settings()), "Tectonic is not installed")
    def test_bmatrix_stress_tensor_compiles_in_strict_mode(self) -> None:
        formula = (
            r"\boldsymbol{\sigma}=\begin{bmatrix}"
            r"\sigma_{xx}&\tau_{xy}\\"
            r"\tau_{yx}&\sigma_{yy}"
            r"\end{bmatrix}"
        )
        self.assertTrue(_safe_math(formula))

        files = create_guide_files(
            settings(),
            strict_math=True,
            **guide("The stress tensor is $" + formula + "$ in this coordinate system."),
        )

        self.assertTrue(files["pdf"].startswith(b"%PDF-"))
        self.assertIn(r"\begin{bmatrix}", files["latex"].decode("utf-8"))
        extracted = "".join(
            page.extract_text() or ""
            for page in PdfReader(io.BytesIO(files["pdf"])).pages
        )
        self.assertIn("Stress state", extracted)
        self.assertIn("𝜎", extracted)
        self.assertIn("𝜏", extracted)
        self.assertIn("[", extracted)
        self.assertIn("]", extracted)

    def test_math_commands_and_environments_remain_allow_listed_and_balanced(self) -> None:
        for expression in (
            r"\input{secret}",
            r"\begin{document}x\end{document}",
            r"\begin{bmatrix}1&2\end{pmatrix}",
            r"\begin{bmatrix}1&2",
            r"\end{bmatrix}",
            r"\begin{bmatrix}1&2\end{bmatrix}\end{matrix}",
            r"x&y",
            r"\left(x+1\right]",
            r"\left(x+1",
        ):
            with self.subTest(expression=expression):
                self.assertFalse(_safe_math(expression))

    def test_standard_left_right_delimiter_pairs_are_safe_and_balanced(self) -> None:
        for expression in (
            r"\left(x+1\right)",
            r"\left\lfloor\frac{n-1}{2}\right\rfloor",
            r"\left\lceil\frac{n}{2}\right\rceil",
            r"\left\langle u,v\right\rangle",
            r"\left\lvert x\right\rvert",
            r"\left\lVert v\right\rVert",
            r"\left(\left\lfloor\frac{n-1}{2}\right\rfloor\right)",
        ):
            with self.subTest(expression=expression):
                self.assertTrue(_safe_math(expression))

    def test_escaped_percent_is_safe_and_compiles_in_a_percentage(self) -> None:
        expression = r"\frac{12}{108} \times 100 = 11.1\%"
        self.assertTrue(_safe_math(expression))

        files = create_guide_files(
            settings(),
            strict_math=True,
            **guide("The prevalence is $" + expression + "$ in this sample."),
        )

        self.assertTrue(files["pdf"].startswith(b"%PDF-"))
        self.assertIn(expression, files["latex"].decode("utf-8"))

    def test_left_right_delimiter_closers_must_match_the_opening_pair(self) -> None:
        for expression in (
            r"\right)",
            r"\left(x+1\right]",
            r"\left\lfloor x\right\rceil",
            r"\left(\left[x\right)",
            r"\left(x+1",
        ):
            with self.subTest(expression=expression):
                self.assertFalse(_safe_math(expression))

    def test_strict_mode_rejects_unsafe_math_before_compilation(self) -> None:
        with patch("app.services.study_files.compile_guide_pdf") as compile_pdf:
            with self.assertRaises(StudyFileFailure):
                create_guide_files(
                    settings(),
                    strict_math=True,
                    **guide(r"This must not run: $\input{/etc/passwd}$"),
                )

        compile_pdf.assert_not_called()

    def test_strict_mode_does_not_retry_after_math_compilation_failure(self) -> None:
        with patch(
            "app.services.study_files.compile_guide_pdf",
            side_effect=StudyFileFailure("Study-guide PDF compilation failed."),
        ) as compile_pdf:
            with self.assertRaises(StudyFileFailure):
                create_guide_files(
                    settings(), strict_math=True, **guide(r"The result is $x^2$.")
                )

        compile_pdf.assert_called_once()

    def test_default_mode_still_retries_without_math_after_compile_failure(self) -> None:
        with patch(
            "app.services.study_files.compile_guide_pdf",
            side_effect=[
                StudyFileFailure("Study-guide PDF compilation failed."),
                b"%PDF-test-fallback",
            ],
        ) as compile_pdf:
            files = create_guide_files(settings(), **guide(r"The result is $x^2$."))

        self.assertEqual(compile_pdf.call_count, 2)
        self.assertNotIn(r"\(x^2\)", files["latex"].decode("utf-8"))
        self.assertIn(r"\textasciicircum{}", files["latex"].decode("utf-8"))
        self.assertEqual(files["pdf"], b"%PDF-test-fallback")


if __name__ == "__main__":
    unittest.main()
