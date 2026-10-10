"""Validated HTTP request and response contracts."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LiveTokenRequest(BaseModel):
    """Only a server-authorized class context may be selected by the caller."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    class_id: UUID | None = None


class CalculatorRequest(BaseModel):
    """Bounded numeric or symbolic expression from a Live calculator call."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    expression: str = Field(min_length=1, max_length=256, strict=True)


class CalculatorResponse(BaseModel):
    """Exact or symbolic calculator result returned as text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result: str = Field(min_length=1, max_length=512)


class LiveTokenResponse(BaseModel):
    """Short-lived bearer credential returned to the requesting browser."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str = Field(min_length=1)
    model: str = Field(min_length=1)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def require_timezone_aware_expiration(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone.")
        return value.astimezone(timezone.utc)


class HealthResponse(BaseModel):
    status: str = "ok"


class ClassCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)


class ClassPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)


class ClassSummary(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime
    upload_count: int = 0
    material_count: int = 0


NoteKind = Literal["notes", "document", "syllabus", "slides", "lecture_video", "lecture_audio"]
NoteStatus = Literal["processing", "ready", "failed"]
SourceType = Literal["file", "pasted_text"]


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def validate_time_range(self) -> TranscriptSegment:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Transcript segment end must be after its start.")
        return self


class UploadSummary(BaseModel):
    id: UUID
    class_id: UUID
    title: str
    kind: NoteKind
    source_type: SourceType
    original_filename: str | None = None
    mime_type: str | None = None
    file_size_bytes: int | None = None
    duration_seconds: int | None = None
    status: NoteStatus
    processing_progress: int = Field(ge=0, le=100)
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime


class UploadDetail(UploadSummary):
    extracted_text: str | None = None
    summary: str | None = None
    key_topics: list[str] = Field(default_factory=list)
    transcript_segments: list[TranscriptSegment] = Field(default_factory=list)
    syllabus_outline: dict | None = None
    file_url: str | None = None
    file_url_expires_at: datetime | None = None


class UploadPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    kind: NoteKind | None = None
    extracted_text: str | None = Field(default=None, max_length=1_000_000)


class SyllabusTopic(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=300)
    week: int | None = Field(default=None, ge=1, le=100)


class SyllabusExam(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    date: date | None = None
    topics: list[str] = Field(default_factory=list, max_length=100)


class SyllabusOutline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_id: UUID | None
    topics: list[SyllabusTopic] = Field(default_factory=list, max_length=200)
    exams: list[SyllabusExam] = Field(default_factory=list, max_length=50)


class SyllabusOutlineUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topics: list[SyllabusTopic] = Field(default_factory=list, max_length=200)
    exams: list[SyllabusExam] = Field(default_factory=list, max_length=50)


class AllMaterialsScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["all"]


class UploadsMaterialsScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["uploads"]
    upload_ids: list[UUID] = Field(min_length=1, max_length=100)


class TopicsMaterialsScope(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["topics"]
    topics: list[str] = Field(min_length=1, max_length=50)


class ExamMaterialsScope(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["exam"]
    exam_name: str = Field(min_length=1, max_length=200)


MaterialsScope = Annotated[
    Union[AllMaterialsScope, UploadsMaterialsScope, TopicsMaterialsScope, ExamMaterialsScope],
    Field(discriminator="kind"),
]
StudyMaterialKind = Literal["quiz", "practice_exam", "study_guide", "flashcards"]
StudyDifficulty = Literal["mixed", "easier", "harder"]


class StudyOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(default=10, ge=4, le=50)
    difficulty: StudyDifficulty = "mixed"
    time_limit_minutes: int | None = Field(default=None, ge=1, le=300)


class StudyMaterialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: StudyMaterialKind
    scope: MaterialsScope = Field(default_factory=lambda: AllMaterialsScope(kind="all"))
    options: StudyOptions = Field(default_factory=StudyOptions)


class LatestAttempt(BaseModel):
    score_percent: float = Field(ge=0, le=100)
    completed_at: datetime


class MaterialSummary(BaseModel):
    id: UUID
    class_id: UUID
    kind: StudyMaterialKind
    title: str
    scope: dict
    options: dict
    status: Literal["generating", "ready", "failed"]
    item_count: int = Field(ge=0)
    failure_code: str | None = None
    created_at: datetime
    generated_at: datetime | None = None
    latest_attempt: LatestAttempt | None = None


class MaterialDetail(MaterialSummary):
    content: dict | None = None


class AttemptSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, str] = Field(min_length=1, max_length=100)


class QuestionAttemptResult(BaseModel):
    question_id: str
    correct: bool
    submitted_answer: str | None = None
    correct_answer: str
    explanation: str
    sources: list[dict] = Field(default_factory=list)


class AttemptResult(BaseModel):
    attempt_id: UUID
    score_percent: float = Field(ge=0, le=100)
    correct_count: int = Field(ge=0)
    question_results: list[QuestionAttemptResult]


class StudyMaterialFlagCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question_id: str = Field(min_length=1, max_length=100)
    reason: str | None = Field(default=None, max_length=1000)


class SourceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: UUID
    page: int | None = Field(default=None, ge=1)
    slide: int | None = Field(default=None, ge=1)
    timestamp_seconds: float | None = Field(default=None, ge=0)


class ChoiceContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=1000)


class QuestionContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    kind: Literal["multiple_choice", "true_false", "short_answer"]
    prompt: str = Field(min_length=1, max_length=5000)
    choices: list[ChoiceContent] = Field(default_factory=list, max_length=8)
    answer: str = Field(min_length=1, max_length=5000)
    accepted_answers: list[str] = Field(default_factory=list, max_length=20)
    explanation: str = Field(min_length=1, max_length=5000)
    rubric: str | None = Field(default=None, max_length=2000)
    sources: list[SourceCitation] = Field(default_factory=list, max_length=10)


class QuizContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[QuestionContent] = Field(min_length=1, max_length=50)


class GuideSectionContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20_000)
    key_terms: list[str] = Field(default_factory=list, max_length=50)
    sources: list[SourceCitation] = Field(default_factory=list, max_length=20)


class StudyGuideContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sections: list[GuideSectionContent] = Field(min_length=1, max_length=50)


class FlashcardContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    front: str = Field(min_length=1, max_length=2000)
    back: str = Field(min_length=1, max_length=5000)
    sources: list[SourceCitation] = Field(default_factory=list, max_length=10)


class FlashcardsContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cards: list[FlashcardContent] = Field(min_length=1, max_length=50)


class SyllabusTopicAI(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    week: int | None = Field(default=None, ge=1, le=100)


class SyllabusExamAI(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    date: date | None = None
    topics: list[str] = Field(default_factory=list, max_length=100)


class SyllabusAIOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topics: list[SyllabusTopicAI] = Field(default_factory=list, max_length=200)
    exams: list[SyllabusExamAI] = Field(default_factory=list, max_length=50)


class LectureAIOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=20_000)
    key_topics: list[str] = Field(default_factory=list, max_length=100)
    transcript_segments: list[TranscriptSegment] = Field(default_factory=list, max_length=20_000)


class NoteSummaryAIOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=20_000)
    key_topics: list[str] = Field(default_factory=list, max_length=100)


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class TokenIssueResult(BaseModel):
    """Internal validated result produced by the Gemini token service."""

    access_token: str = Field(min_length=1)
    model: str = Field(min_length=1)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def require_timezone_aware_expiration(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("token expiration must include a timezone")
        return value.astimezone(timezone.utc)
