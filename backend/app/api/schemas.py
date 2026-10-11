"""Validated HTTP request and response contracts."""

from __future__ import annotations

from datetime import date as calendar_date
from datetime import datetime, timezone
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.live_modes import TutorLanguage, TutorMode


class LiveSessionRequest(BaseModel):
    """Select authorized class context and a predefined server-owned tutor mode."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    class_id: UUID | None = None
    mode: TutorMode = TutorMode.CONVERSATION
    language: TutorLanguage = TutorLanguage.AUTO


class CalculatorRequest(BaseModel):
    """Bounded numeric or symbolic expression from a Live calculator call."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    expression: str = Field(min_length=1, max_length=256, strict=True)


class CalculatorResponse(BaseModel):
    """Exact or symbolic calculator result returned as text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result: str = Field(min_length=1, max_length=512)


class LeanProofRequest(BaseModel):
    """A formal proposition and tactic proof checked by the backend's Lean runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    proposition: str = Field(min_length=1, max_length=4_000, strict=True)
    proof: str = Field(min_length=1, max_length=8_000, strict=True)


class LeanProofResponse(BaseModel):
    """Outcome of checking a formal proof; diagnostics are bounded and sanitized."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["verified", "rejected", "timed_out"]
    diagnostics: str = Field(min_length=1, max_length=4_000)


class LiveSessionTicketResponse(BaseModel):
    """One-use WebSocket ticket; it does not authorize direct model access."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ticket: str = Field(min_length=32, max_length=128)
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
    document: dict | None = None


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
    date: calendar_date | None = None
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
StudyQuestionKind = Literal["multiple_choice", "true_false", "short_answer"]


class StudyOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(default=10, ge=4, le=50)
    difficulty: StudyDifficulty = "mixed"
    time_limit_minutes: int | None = Field(default=None, ge=1, le=300)
    question_types: list[StudyQuestionKind] | None = Field(
        default=None, min_length=1, max_length=3
    )

    @field_validator("question_types")
    @classmethod
    def require_unique_question_types(
        cls, value: list[StudyQuestionKind] | None
    ) -> list[StudyQuestionKind] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("Question types must be unique.")
        return value


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
    download_urls: dict[str, str] = Field(default_factory=dict)


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


AssessmentStatus = Literal["processing", "ready", "needs_review", "failed"]
AssessmentGradingMode = Literal[
    "not_graded", "answer_key", "class_notes_estimated", "needs_review"
]


class AssessmentQuestionAI(BaseModel):
    """One question and any student response or key detected in an imported test."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=5000)
    choices: list[ChoiceContent] = Field(default_factory=list, max_length=20)
    points: float = Field(default=1, gt=0, le=100)
    student_response: str | None = Field(default=None, max_length=5000)
    response_status: Literal["answered", "blank", "unreadable"]
    answer_key: str | None = Field(default=None, max_length=5000)
    answer_key_status: Literal["present", "missing", "unreadable"]

    @model_validator(mode="after")
    def validate_detected_fields(self) -> AssessmentQuestionAI:
        choice_ids = [choice.id for choice in self.choices]
        if len(choice_ids) != len(set(choice_ids)):
            raise ValueError("Question choice IDs must be unique.")
        if self.response_status == "answered" and not self.student_response:
            raise ValueError("An answered question must include the detected response.")
        if self.response_status != "answered" and self.student_response:
            raise ValueError("Only answered questions may include a detected response.")
        if self.answer_key_status == "present" and not self.answer_key:
            raise ValueError("A present answer key must include its detected answer.")
        if self.answer_key_status != "present" and self.answer_key:
            raise ValueError("Only present answer keys may include a detected answer.")
        return self


class AssessmentExtractionAIOutput(BaseModel):
    """Structured extraction from an uploaded existing test."""

    model_config = ConfigDict(extra="forbid")

    document_readable: bool
    questions: list[AssessmentQuestionAI] = Field(default_factory=list, max_length=100)


class AssessmentQuestionGradeAI(BaseModel):
    """Model grade for one detected answer, validated before persistence."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question_id: str = Field(min_length=1, max_length=100)
    earned_points: float = Field(ge=0, le=100)
    correct_answer: str | None = Field(default=None, max_length=5000)
    feedback: str = Field(min_length=1, max_length=5000)
    confidence: float = Field(ge=0, le=1)
    sources: list[SourceCitation] = Field(default_factory=list, max_length=10)


class AssessmentGradingAIOutput(BaseModel):
    """Structured per-question grading result."""

    model_config = ConfigDict(extra="forbid")

    overall_confidence: float = Field(ge=0, le=1)
    grades: list[AssessmentQuestionGradeAI] = Field(min_length=1, max_length=100)


class AssessmentSummary(BaseModel):
    id: UUID
    class_id: UUID
    note_id: UUID
    title: str
    original_filename: str | None = None
    status: AssessmentStatus
    has_student_responses: bool
    question_count: int = Field(ge=0)
    grading_mode: AssessmentGradingMode
    score_percent: float | None = Field(default=None, ge=0, le=100)
    grade_confidence: float | None = Field(default=None, ge=0, le=1)
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None = None


class AssessmentDetail(AssessmentSummary):
    questions: list[AssessmentQuestionAI] = Field(default_factory=list)
    grading_result: dict | None = None
    review_reason: str | None = None
    file_url: str | None = None
    file_url_expires_at: datetime | None = None


class QuestionContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    kind: StudyQuestionKind
    prompt: str = Field(min_length=1, max_length=5000)
    choices: list[ChoiceContent] = Field(default_factory=list, max_length=8)
    answer: str = Field(min_length=1, max_length=5000)
    accepted_answers: list[str] = Field(default_factory=list, max_length=20)
    explanation: str = Field(min_length=1, max_length=5000)
    rubric: str | None = Field(default=None, max_length=2000)
    sources: list[SourceCitation] = Field(default_factory=list, max_length=10)


class QuizContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["quiz"]
    questions: list[QuestionContent] = Field(min_length=1, max_length=50)


class PracticeExamContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["practice_exam"]
    instructions: str = Field(min_length=1, max_length=2000)
    questions: list[QuestionContent] = Field(min_length=1, max_length=50)


class GuideSectionContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20_000)
    key_terms: list[str] = Field(default_factory=list, max_length=50)
    sources: list[SourceCitation] = Field(default_factory=list, max_length=20)


class StudyGuideContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["study_guide"]
    sections: list[GuideSectionContent] = Field(min_length=1, max_length=50)


DocumentDetailLevel = Literal["concise", "balanced", "detailed"]


class UploadDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detail_level: DocumentDetailLevel = "balanced"


class UploadDocumentChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sections: list[GuideSectionContent] = Field(min_length=1, max_length=12)


class UploadDocumentContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["upload_document"] = "upload_document"
    detail_level: DocumentDetailLevel
    sections: list[GuideSectionContent] = Field(min_length=1, max_length=1000)


class FlashcardContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    front: str = Field(min_length=1, max_length=2000)
    back: str = Field(min_length=1, max_length=5000)
    sources: list[SourceCitation] = Field(default_factory=list, max_length=10)


class FlashcardsContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["flashcards"]
    cards: list[FlashcardContent] = Field(min_length=1, max_length=50)


class SyllabusTopicAI(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    week: int | None = Field(default=None, ge=1, le=100)


class SyllabusExamAI(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    date: calendar_date | None = None
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




class SignupRequest(BaseModel):
    """Create an account with a username, optional email, and a password."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128, strict=True)
    email: str | None = Field(default=None, max_length=254)
    display_name: str | None = Field(default=None, max_length=60)

    @field_validator("username", mode="before")
    @classmethod
    def strip_username(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("email", "display_name", mode="before")
    @classmethod
    def blank_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        from app.services.auth import EMAIL_PATTERN

        if value is not None and not EMAIL_PATTERN.match(value):
            raise ValueError("Enter a valid email address.")
        return value


class LoginRequest(BaseModel):
    """Log in with a username or an email address."""

    model_config = ConfigDict(extra="forbid")

    identifier: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128, strict=True)

    @field_validator("identifier", mode="before")
    @classmethod
    def strip_identifier(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


# The ids the browser's "Create your own Jot" offers. Keep in step with frontend/src/avatar.js.
AvatarColor = Literal["slate", "ocean", "mint", "moss", "sunny", "tangerine", "coral", "berry", "grape"]
AvatarFace = Literal["happy", "wink", "sleepy", "surprised", "grin"]
AvatarAccessory = Literal["none", "glasses", "cap", "headphones", "party", "crown"]


class Avatar(BaseModel):
    """A customised Jot: three ids from fixed lists, never free text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    color: AvatarColor
    face: AvatarFace
    accessory: AvatarAccessory


class AuthUser(BaseModel):
    """The account fields the browser may see; never a password or hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    username: str
    display_name: str
    email: str | None = None
    is_demo: bool = False
    avatar: Avatar | None = None


class MeResponse(BaseModel):
    """The signed-in account, or null for a guest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user: AuthUser | None = None


class ProfileUpdate(BaseModel):
    """Change the username and/or email. Only fields that are sent are changed."""

    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(default=None, min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    email: str | None = Field(default=None, max_length=254)
    avatar: Avatar | None = None

    @field_validator("username", mode="before")
    @classmethod
    def strip_username(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("email", mode="before")
    @classmethod
    def blank_email_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        from app.services.auth import EMAIL_PATTERN

        if value is not None and not EMAIL_PATTERN.match(value):
            raise ValueError("Enter a valid email address.")
        return value


class PasswordChange(BaseModel):
    """Prove the current password, then set a new one."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128, strict=True)
    new_password: str = Field(min_length=8, max_length=128, strict=True)
