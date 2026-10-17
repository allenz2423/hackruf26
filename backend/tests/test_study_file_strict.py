"""Strict math validation and export regressions for generated study documents."""

from __future__ import annotations

import io
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

    def test_hyphenated_prose_remains_plain_text_while_bare_subtraction_renders(self) -> None:
        prose = (
            "A six-carbon sugar has a non-reducing end, a two-dimensional cis-trans model, "
            "and low-to-high movement."
        )
        self.assertEqual(_inline(prose, strict_math=True), prose)
        self.assertEqual(
            _inline("The difference is x-y.", strict_math=True),
            "The difference is \\(x-y\\).",
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

        self.assertIn(r"\cdot K", files["latex"].decode("utf-8"))
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
