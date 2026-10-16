"""Deterministic checks for generated study-guide rendering risks."""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError

from app.api.schemas import StudyGuideContent
from app.services import study_document_quality as quality
from app.services.study_document_quality import (
    StudyDocumentQualityIssue,
    StudyDocumentQualityReview,
    review_study_guide_quality,
)


def guide(*, heading: str = "Vectors", text: str = "Resolve a vector into components.") -> dict:
    return {
        "format": "study_guide",
        "schema_version": 2,
        "sections": [
            {
                "heading": heading,
                "key_terms": ["vector"],
                "sources": [{"upload_id": str(uuid4()), "page": 2}],
                "blocks": [{"type": "paragraph", "text": text}],
            }
        ],
    }


class StudyDocumentQualityTests(unittest.TestCase):
    def codes(self, result: StudyDocumentQualityReview) -> set[str]:
        return {issue.code for issue in result.issues}

    def test_clean_version_two_guide_passes(self) -> None:
        content = guide(text=r"The relation is $F_x=F\cos\theta$.")

        result = review_study_guide_quality(content)

        self.assertEqual(result.status, "clean")
        self.assertEqual(result.issues, [])

    def test_typed_pydantic_guide_is_reviewed(self) -> None:
        content = StudyGuideContent.model_validate(guide())
        result = review_study_guide_quality(content)
        self.assertEqual(result.status, "clean")

    def test_missing_or_mismatched_math_delimiters_are_reported(self) -> None:
        malformed_samples = (
            "This equation is $x + 1.",
            r"This equation is \[x + 1\).",
        )
        for text in malformed_samples:
            with self.subTest(text=text):
                result = review_study_guide_quality(guide(text=text))
                self.assertIn("malformed_math_delimiters", self.codes(result))
                self.assertEqual(result.issues[0].location, "sections[0].blocks[0].text")

    def test_escaped_currency_marker_is_not_treated_as_math(self) -> None:
        result = review_study_guide_quality(guide(text=r"The price is \$5."))
        self.assertEqual(result.status, "clean")

    def test_forbidden_math_command_is_reported_without_echoing_it(self) -> None:
        result = review_study_guide_quality(guide(text=r"Do not render $x+\input{secret}$."))

        unsafe = [issue for issue in result.issues if issue.code == "unsafe_math_expression"]
        self.assertEqual(len(unsafe), 1)
        self.assertNotIn("input", unsafe[0].message)

    def test_raw_formula_expression_uses_existing_safe_math_allowlist(self) -> None:
        content = guide()
        content["sections"][0]["blocks"] = [
            {"type": "paragraph", "text": "A force has components."},
            {"type": "formula", "expression": r"\input{payload}"},
        ]

        result = review_study_guide_quality(content)

        self.assertIn("unsafe_math_expression", self.codes(result))
        self.assertIn(
            "sections[0].blocks[1].expression", [issue.location for issue in result.issues]
        )

    def test_pre_numbered_heading_is_flagged(self) -> None:
        result = review_study_guide_quality(guide(heading="1 of 10: Vector components"))

        self.assertIn("duplicate_section_ordinal", self.codes(result))
        self.assertEqual(result.issues[0].location, "sections[0].heading")

    def test_substantially_repeated_prose_is_flagged_at_later_authored_field(self) -> None:
        text = (
            "The circular flow diagram illustrates a simple economy consisting solely of "
            "businesses and individuals. Individuals provide labor that enables businesses to "
            "produce goods and services, while businesses provide income in exchange for labor. "
            "That income is spent on the goods and services produced."
        )
        content = guide(text=text)
        repeated = deepcopy(content["sections"][0])
        repeated["heading"] = "Income and Output in the Circular Flow"
        content["sections"].append(repeated)

        result = review_study_guide_quality(content)

        duplicates = [
            issue for issue in result.issues if issue.code == "duplicate_section_content"
        ]
        self.assertEqual(
            [issue.location for issue in duplicates], ["sections[1].blocks[0].text"]
        )

    def test_related_but_distinct_prose_is_not_flagged(self) -> None:
        content = guide(
            text=(
                "Gross domestic product measures the market value of goods and services "
                "produced in a particular period and is used as an indicator of economic activity."
            )
        )
        second = deepcopy(content["sections"][0])
        second["heading"] = "Market Prices and Imputations"
        second["blocks"][0]["text"] = (
            "When market prices do not fully reflect a service's value, national accounts may "
            "estimate its value from comparable market transactions. This process is called "
            "imputation and helps account for selected nonmarket services."
        )
        content["sections"].append(second)

        result = review_study_guide_quality(content)

        self.assertNotIn("duplicate_section_content", self.codes(result))

    def test_serialized_size_limit_is_enforced(self) -> None:
        content = guide()
        with patch.object(quality, "MAX_SERIALIZED_BYTES", 40):
            result = review_study_guide_quality(content)

        self.assertIn("serialized_size_exceeded", self.codes(result))
        self.assertLessEqual(len(result.issues), quality.MAX_REPORTED_ISSUES)

    def test_canonical_serialized_defaults_are_included_in_size_limit(self) -> None:
        content = guide()
        raw_bytes = len(json.dumps(content, separators=(",", ":")).encode())
        canonical = StudyGuideContent.model_validate(content).model_dump(mode="json")
        canonical_bytes = len(json.dumps(canonical, separators=(",", ":")).encode())
        self.assertGreater(canonical_bytes, raw_bytes)

        with patch.object(quality, "MAX_SERIALIZED_BYTES", raw_bytes + 1):
            result = review_study_guide_quality(content)

        self.assertIn("serialized_size_exceeded", self.codes(result))

    def test_total_text_limit_is_enforced_separately(self) -> None:
        content = guide(text="A short paragraph.")
        with patch.object(quality, "MAX_TEXT_CHARACTERS", 5):
            result = review_study_guide_quality(content)

        self.assertIn("text_size_exceeded", self.codes(result))

    def test_non_finite_numbers_are_rejected_before_schema_validation(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                content = guide()
                content["sections"][0]["sources"][0]["timestamp_seconds"] = value

                result = review_study_guide_quality(content)

                self.assertEqual(self.codes(result), {"non_finite_number"})

    def test_non_json_and_cyclic_inputs_are_reported_safely(self) -> None:
        with self.subTest("unsupported value"):
            content = guide()
            content["unexpected"] = {"payload": {1, 2, 3}}
            result = review_study_guide_quality(content)
            self.assertEqual(self.codes(result), {"non_json_content"})

        with self.subTest("cycle"):
            content = guide()
            content["cycle"] = content
            result = review_study_guide_quality(content)
            self.assertEqual(self.codes(result), {"non_json_content"})

    def test_invalid_structure_and_legacy_version_receive_safe_findings(self) -> None:
        invalid = review_study_guide_quality({"format": "study_guide", "schema_version": 2})
        self.assertEqual(self.codes(invalid), {"invalid_content"})

        legacy = guide()
        legacy["schema_version"] = 1
        legacy["sections"] = [{"heading": "Legacy", "body": "Older text."}]
        result = review_study_guide_quality(legacy)
        self.assertEqual(self.codes(result), {"unsupported_schema_version"})

    def test_review_models_enforce_safe_messages_and_consistent_status(self) -> None:
        with self.assertRaises(ValidationError):
            StudyDocumentQualityIssue(code="unsafe_math_expression", message="Unsafe: raw text")
        with self.assertRaises(ValidationError):
            StudyDocumentQualityReview(
                status="clean",
                issues=[
                    StudyDocumentQualityIssue(
                        code="invalid_content",
                        message="The study guide does not match the supported structured format.",
                    )
                ],
            )

    def test_issue_list_is_bounded(self) -> None:
        content = guide()
        content["sections"] = [
            {
                "heading": f"{index + 1}: Numbered heading",
                "sources": [{"upload_id": str(uuid4())}],
                "blocks": [{"type": "paragraph", "text": "$x+\\input{bad}$"}],
            }
            for index in range(60)
        ]

        result = review_study_guide_quality(content)

        self.assertLessEqual(len(result.issues), quality.MAX_REPORTED_ISSUES)
        self.assertIn("additional_issues_omitted", self.codes(result))


if __name__ == "__main__":
    unittest.main()
