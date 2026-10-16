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
