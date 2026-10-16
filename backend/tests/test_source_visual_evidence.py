"""Regression tests for citation-scoped visual rendering and safe diagnostics."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pypdf import PdfWriter

from app.services.source_visual_evidence import (
    VisualEvidenceUnavailableError,
    extract_cited_source_visuals,
)


class SourceVisualEvidenceTests(unittest.TestCase):
    def test_render_failure_is_tagged_with_safe_source_locator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "source.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with pdf_path.open("wb") as output:
                writer.write(output)
            source = {
                "id": "upload-id",
                "title": "Private source title",
                "original_path": pdf_path,
            }
            excerpt = {
                "upload_id": "upload-id",
                "title": "Private source title",
                "locator": {"page": 1},
            }
            with (
                patch("app.services.source_visual_evidence.shutil.which", return_value="pdftoppm"),
                patch(
                    "app.services.source_visual_evidence.subprocess.run",
                    return_value=SimpleNamespace(returncode=1),
                ),
            ):
                with self.assertRaises(VisualEvidenceUnavailableError) as raised:
                    extract_cited_source_visuals([source], [excerpt])

        self.assertEqual(raised.exception.source_format, ".pdf")
        self.assertEqual(raised.exception.locator_kind, "page")
        self.assertEqual(raised.exception.locator_index, 1)
        self.assertNotIn("Private source title", str(raised.exception))

    def test_missing_full_page_renderer_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "source.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with pdf_path.open("wb") as output:
                writer.write(output)
            with patch("app.services.source_visual_evidence.shutil.which", return_value=None):
                with self.assertRaises(VisualEvidenceUnavailableError) as raised:
                    extract_cited_source_visuals(
                        [{"id": "upload-id", "original_path": pdf_path}],
                        [{"upload_id": "upload-id", "locator": {"page": 1}}],
                    )

        self.assertEqual(raised.exception.source_format, ".pdf")
        self.assertEqual(raised.exception.locator_kind, "page")
        self.assertEqual(raised.exception.locator_index, 1)

    def test_missing_cited_original_fails_closed_from_source_metadata(self) -> None:
        missing_path = Path("/private/missing/source.pdf")
        source = {
            "id": "upload-id",
            "title": "Private source title",
            "original_filename": "source.pdf",
            "mime_type": "application/pdf",
            "original_path": missing_path,
        }
        excerpt = {"upload_id": "upload-id", "locator": {"page": 4}}

        with self.assertRaises(VisualEvidenceUnavailableError) as raised:
            extract_cited_source_visuals([source], [excerpt])

        self.assertEqual(raised.exception.source_format, ".pdf")
        self.assertEqual(raised.exception.locator_kind, "page")
        self.assertEqual(raised.exception.locator_index, 4)
        self.assertNotIn("Private source title", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
