"""Deterministic, bounded quality checks for generated version-2 study guides."""

from __future__ import annotations

import json
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.api.schemas import StudyGuideContent
from app.services.study_files import _safe_math

MAX_SERIALIZED_BYTES = 256_000
MAX_TEXT_CHARACTERS = 100_000
MAX_REPORTED_ISSUES = 100
MAX_JSON_DEPTH = 64

QualityIssueCode = Literal[
    "invalid_content",
    "non_json_content",
    "non_finite_number",
    "serialized_size_exceeded",
    "text_size_exceeded",
    "unsupported_schema_version",
    "malformed_math_delimiters",
    "unsafe_math_expression",
    "duplicate_section_ordinal",
    "duplicate_section_content",
    "additional_issues_omitted",
]

_ISSUE_MESSAGES: dict[QualityIssueCode, str] = {
    "invalid_content": "The study guide does not match the supported structured format.",
    "non_json_content": "The study guide contains data that cannot be represented as JSON.",
    "non_finite_number": "The study guide contains a non-finite numeric value.",
    "serialized_size_exceeded": "The study guide is larger than the supported size limit.",
    "text_size_exceeded": "The study guide text is larger than the supported text limit.",
    "unsupported_schema_version": "Only version-2 study guides can be reviewed by this checker.",
    "malformed_math_delimiters": "A mathematical expression has missing or mismatched delimiters.",
    "unsafe_math_expression": (
        "A mathematical expression uses notation that cannot be safely rendered."
    ),
    "duplicate_section_ordinal": (
        "Section headings should not repeat numbering added by the document."
    ),
    "duplicate_section_content": (
        "A paragraph substantially repeats material already covered in an earlier section."
    ),
    "additional_issues_omitted": (
        "Additional findings were omitted because the issue limit was reached."
    ),
}

_ORDINAL_HEADING = re.compile(
    r"^\s*(?:(?:section|part)\s+)?\d+(?:\s+of\s+\d+)?\s*(?::|[.)]|-)\s*",
    re.IGNORECASE,
)
_PROSE_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_MIN_DUPLICATE_PROSE_TOKENS = 24
_DUPLICATE_PROSE_JACCARD = 0.82


class StudyDocumentQualityIssue(BaseModel):
    """One stable finding with a safe message and an optional typed-content path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: QualityIssueCode
    message: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def message_matches_code(self) -> StudyDocumentQualityIssue:
        if self.message != _ISSUE_MESSAGES[self.code]:
            raise ValueError("Quality issue messages must use the safe server-owned text.")
        return self


class StudyDocumentQualityReview(BaseModel):
    """Bounded result of deterministic checks, independent from model review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["clean", "review_required"]
    issues: list[StudyDocumentQualityIssue] = Field(max_length=MAX_REPORTED_ISSUES)

    @model_validator(mode="after")
    def status_matches_findings(self) -> StudyDocumentQualityReview:
        expected = "review_required" if self.issues else "clean"
        if self.status != expected:
            raise ValueError("Review status must match its issue list.")
        return self


class _SizeLimitExceeded(Exception):
    pass


class _TextLimitExceeded(Exception):
    pass


class _NonJsonValue(Exception):
    pass


class _NonFiniteNumber(Exception):
    pass


def _measure_json(value: object) -> tuple[int, int]:
    """Return serialized UTF-8 bytes and string-value characters without encoding a huge tree."""
    byte_count = 0
    text_count = 0
    ancestors: set[int] = set()

    def add(size: int, text_chars: int = 0) -> None:
        nonlocal byte_count, text_count
        byte_count += size
        text_count += text_chars
        if byte_count > MAX_SERIALIZED_BYTES:
            raise _SizeLimitExceeded
        if text_count > MAX_TEXT_CHARACTERS:
            raise _TextLimitExceeded

    def walk(node: object, depth: int, count_text: bool = True) -> None:
        if depth > MAX_JSON_DEPTH:
            raise _NonJsonValue
        if node is None:
            add(4)
        elif isinstance(node, bool):
            add(4 if node else 5)
        elif isinstance(node, int):
            if node.bit_length() > MAX_SERIALIZED_BYTES * 4:
                raise _SizeLimitExceeded
            try:
                add(len(str(node)))
            except ValueError as exc:
                raise _NonJsonValue from exc
        elif isinstance(node, float):
            if not math.isfinite(node):
                raise _NonFiniteNumber
            add(len(json.dumps(node, allow_nan=False, separators=(",", ":"))))
        elif isinstance(node, str):
            if len(node) > MAX_SERIALIZED_BYTES:
                raise _SizeLimitExceeded
            if count_text and len(node) > MAX_TEXT_CHARACTERS:
                raise _TextLimitExceeded
            try:
                encoded = json.dumps(node, ensure_ascii=False, separators=(",", ":"))
                add(
                    len(encoded.encode("utf-8", errors="strict")),
                    len(node) if count_text else 0,
                )
            except (UnicodeEncodeError, ValueError) as exc:
                raise _NonJsonValue from exc
        elif type(node) is list:
            marker = id(node)
            if marker in ancestors:
                raise _NonJsonValue
            ancestors.add(marker)
            add(2 + max(0, len(node) - 1))
            for child in node:
                walk(child, depth + 1, count_text)
            ancestors.remove(marker)
        elif type(node) is dict:
            marker = id(node)
            if marker in ancestors:
                raise _NonJsonValue
            ancestors.add(marker)
            add(2 + max(0, len(node) - 1) + len(node))
            for key, child in node.items():
                if not isinstance(key, str):
                    raise _NonJsonValue
                try:
                    key_json = json.dumps(key, ensure_ascii=False, separators=(",", ":"))
                    add(len(key_json.encode("utf-8", errors="strict")))
                except (UnicodeEncodeError, ValueError) as exc:
                    raise _NonJsonValue from exc
                walk(child, depth + 1, count_text and key != "svg")
            ancestors.remove(marker)
        else:
            raise _NonJsonValue

    walk(value, 0)
    return byte_count, text_count


def _is_escaped(text: str, index: int) -> bool:
    slash_count = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        slash_count += 1
        cursor -= 1
    return slash_count % 2 == 1


def _math_delimiter_at(text: str, index: int) -> tuple[str, bool] | None:
    """Return (delimiter, opens) at an unescaped math delimiter position."""
    if text[index] == "$" and not _is_escaped(text, index):
        return ("$$", True) if text.startswith("$$", index) else ("$", True)
    if text[index] != "\\" or _is_escaped(text, index):
        return None
    for opener, closer in ((r"\(", r"\)"), (r"\[", r"\]")):
        if text.startswith(opener, index):
            return opener, True
        if text.startswith(closer, index):
            return closer, False
    return None


def _math_spans(text: str) -> tuple[list[str], bool]:
    """Find matched math bodies and report unmatched, crossing, or nested delimiters."""
    spans: list[str] = []
    malformed = False
    active_open: str | None = None
    expected_close: str | None = None
    body_start = 0
    index = 0
    while index < len(text):
        if active_open == "$" and text[index] == "\n":
            malformed = True
            active_open = expected_close = None
        delimiter = _math_delimiter_at(text, index)
        if delimiter is None:
            index += 1
            continue
        token, opens = delimiter
        if active_open is None:
            if not opens:
                malformed = True
            else:
                active_open = token
                expected_close = {"$": "$", "$$": "$$", r"\(": r"\)", r"\[": r"\]"}[token]
                body_start = index + len(token)
        elif token == expected_close:
            spans.append(text[body_start:index])
            active_open = expected_close = None
        else:
            malformed = True
            # Drop the broken pair and continue scanning after this marker so a later
            # independent, valid expression can still receive allowlist validation.
            active_open = expected_close = None
        index += len(token)
    if active_open is not None:
        malformed = True
    return spans, malformed


def _add_issue(
    issues: list[StudyDocumentQualityIssue],
    code: QualityIssueCode,
    location: str | None = None,
) -> None:
    if len(issues) < MAX_REPORTED_ISSUES - 1:
        issues.append(
            StudyDocumentQualityIssue(
                code=code,
                message=_ISSUE_MESSAGES[code],
                location=location,
            )
        )
    elif not any(issue.code == "additional_issues_omitted" for issue in issues):
        issues.append(
            StudyDocumentQualityIssue(
                code="additional_issues_omitted",
                message=_ISSUE_MESSAGES["additional_issues_omitted"],
            )
        )


def _text_values(node: object, path: str = ""):
    """Yield authored strings from a validated block, excluding IDs and citations."""
    ignored_fields = {"id", "sources", "template_id", "type", "molecule_id"}
    if isinstance(node, dict):
        for key, child in node.items():
            if key in ignored_fields:
                continue
            child_path = f"{path}.{key}" if path else key
            yield from _text_values(child, child_path)
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from _text_values(child, f"{path}[{index}]")
    elif isinstance(node, str):
        yield path, node


def _check_math_text(
    value: str,
    location: str,
    issues: list[StudyDocumentQualityIssue],
    *,
    raw_expression: bool = False,
) -> None:
    spans, malformed = _math_spans(value)
    if malformed:
        _add_issue(issues, "malformed_math_delimiters", location)
    for expression in spans:
        if not _safe_math(expression):
            _add_issue(issues, "unsafe_math_expression", location)
    if raw_expression and not _safe_math(value):
        _add_issue(issues, "unsafe_math_expression", location)


def _size_issue_code(value: object) -> QualityIssueCode | None:
    try:
        _measure_json(value)
    except _NonFiniteNumber:
        return "non_finite_number"
    except _SizeLimitExceeded:
        return "serialized_size_exceeded"
    except _TextLimitExceeded:
        return "text_size_exceeded"
    except (_NonJsonValue, RecursionError, OverflowError, TypeError, ValueError):
        return "non_json_content"
    return None


def _guide_prose_values(
    section: dict, section_index: int
) -> list[tuple[str, list[str], set[str]]]:
    """Return authored prose paths used for conservative repetition checks."""
    values: list[tuple[str, list[str], set[str]]] = []
    blocks = section.get("blocks")
    if not isinstance(blocks, list):
        return values
    for block_index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        fields: list[tuple[str, str]] = []
        if block_type in {"paragraph", "takeaway"}:
            text = block.get("text")
            if isinstance(text, str):
                fields.append((f"blocks[{block_index}].text", text))
        elif block_type == "example_steps":
            steps = block.get("steps")
            if isinstance(steps, list):
                fields.extend(
                    (f"blocks[{block_index}].steps[{step_index}].explanation", explanation)
                    for step_index, step in enumerate(steps)
                    if isinstance(step, dict)
                    and isinstance((explanation := step.get("explanation")), str)
                )
        for relative_path, text in fields:
            tokens = [token.casefold() for token in _PROSE_TOKEN.findall(text)]
            if len(tokens) >= _MIN_DUPLICATE_PROSE_TOKENS:
                values.append(
                    (f"sections[{section_index}].{relative_path}", tokens, set(tokens))
                )
    return values


def _duplicate_prose_paths(sections: list[dict]) -> list[str]:
    """Find later prose fields that closely repeat prose in an earlier section.

    This favors precision over loose semantic similarity: only long authored paragraphs,
    takeaways, and worked-step explanations are compared across sections. Formulae,
    citations, headings, and renderer fallbacks cannot trigger a repair.
    """
    earlier: list[tuple[set[str], int]] = []
    duplicate_paths: list[str] = []
    seen_paths: set[str] = set()
    for section_index, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        current = _guide_prose_values(section, section_index)
        for path, _tokens, token_set in current:
            duplicate = False
            for earlier_set, earlier_index in earlier:
                if earlier_index == section_index:
                    continue
                intersection = len(token_set & earlier_set)
                union = len(token_set | earlier_set)
                jaccard = intersection / union if union else 0.0
                if jaccard >= _DUPLICATE_PROSE_JACCARD:
                    duplicate = True
                    break
            if duplicate and path not in seen_paths:
                duplicate_paths.append(path)
                seen_paths.add(path)
        earlier.extend(
            (token_set, section_index) for _path, _tokens, token_set in current
        )
    return duplicate_paths


def review_study_guide_quality(content: object) -> StudyDocumentQualityReview:
    """Check v2 guide structure and rendering risks without consulting a model."""
    if isinstance(content, StudyGuideContent):
        try:
            raw = content.model_dump(mode="json")
        except (TypeError, ValueError, OverflowError):
            raw = None
    elif type(content) is dict:
        raw = content
    else:
        raw = None

    issues: list[StudyDocumentQualityIssue] = []
    if raw is None or not isinstance(raw, dict):
        _add_issue(issues, "non_json_content")
        return StudyDocumentQualityReview(status="review_required", issues=issues)

    size_issue_code = _size_issue_code(raw)
    if size_issue_code is not None:
        _add_issue(issues, size_issue_code)
    if issues:
        return StudyDocumentQualityReview(status="review_required", issues=issues)

    try:
        parsed = StudyGuideContent.model_validate(raw)
    except (ValidationError, TypeError, ValueError, RecursionError, OverflowError):
        _add_issue(issues, "invalid_content")
        return StudyDocumentQualityReview(status="review_required", issues=issues)
    size_issue_code = _size_issue_code(parsed.model_dump(mode="json"))
    if size_issue_code is not None:
        _add_issue(issues, size_issue_code)
        return StudyDocumentQualityReview(status="review_required", issues=issues)
    if parsed.schema_version != 2:
        _add_issue(issues, "unsupported_schema_version")
        return StudyDocumentQualityReview(status="review_required", issues=issues)

    parsed_json = parsed.model_dump(mode="json")
    for section_index, section in enumerate(parsed.sections, 1):
        heading_location = f"sections[{section_index - 1}].heading"
        if _ORDINAL_HEADING.match(section.heading):
            _add_issue(issues, "duplicate_section_ordinal", heading_location)
        _check_math_text(section.heading, heading_location, issues)
        for term_index, term in enumerate(section.key_terms):
            _check_math_text(
                term,
                f"sections[{section_index - 1}].key_terms[{term_index}]",
                issues,
            )
        for block_index, block in enumerate(section.blocks, 1):
            block_location = f"sections[{section_index - 1}].blocks[{block_index - 1}]"
            block_data = block.model_dump(mode="json", exclude_none=True)
            for field_path, text in _text_values(block_data):
                if field_path == "svg":
                    continue
                location = f"{block_location}.{field_path}"
                _check_math_text(
                    text,
                    location,
                    issues,
                    raw_expression=field_path.endswith("expression"),
                )

    for location in _duplicate_prose_paths(parsed_json["sections"]):
        _add_issue(issues, "duplicate_section_content", location)

    return StudyDocumentQualityReview(
        status="review_required" if issues else "clean",
        issues=issues,
    )
