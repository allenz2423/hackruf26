"""Validated HTTP request and response contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from datetime import date as calendar_date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.live_modes import TutorLanguage, TutorMode


class LiveSessionRequest(BaseModel):
    """Select authorized class context and a predefined server-owned tutor mode."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    class_id: UUID | None = None
    # Limit the tutor's class context to these uploads; omitted means every ready upload.
    upload_ids: tuple[UUID, ...] | None = Field(default=None, max_length=100)
    mode: TutorMode = TutorMode.CONVERSATION
    language: TutorLanguage = TutorLanguage.AUTO
    use_saved_memory: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def uploads_need_a_class(self) -> LiveSessionRequest:
        if self.upload_ids is not None and self.class_id is None:
            raise ValueError("upload_ids requires class_id.")
        if self.mode == TutorMode.QUIZ_REASONING and (
            self.class_id is not None or self.upload_ids is not None
        ):
            raise ValueError("Quiz reasoning voice sessions cannot include class uploads.")
        return self


class TutorRecapContent(BaseModel):
    """Small observations about a session, never its full transcript."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    summary: str = Field(min_length=1, max_length=800)
    topics: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(max_length=6)
    observed_progress: list[Annotated[str, Field(min_length=1, max_length=240)]] = Field(
        max_length=4
    )
    practice_targets: list[Annotated[str, Field(min_length=1, max_length=240)]] = Field(
        max_length=4
    )
    next_step: str = Field(max_length=400)


class TutorMemoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    class_id: UUID | None = None
    enabled: bool = Field(strict=True)


class TutorRecap(BaseModel):
    id: UUID
    created_at: datetime
    content: TutorRecapContent


class TutorMemoryState(BaseModel):
    enabled: bool = False
    saved_count: int = 0
    items: list[TutorRecap] = Field(default_factory=list)


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
        return value.astimezone(UTC)


class HealthResponse(BaseModel):
    status: str = "ok"


# The class card colors and icons the browser's "Edit class" offers.
# Keep in step with frontend/src/class-style.js. A null icon means "show the class initial".
ClassColor = Literal[
    "slate", "teal", "moss", "ochre", "rust", "berry", "plum", "indigo", "graphite"
]
ClassIcon = Literal[
    "sigma", "calculator", "flask", "atom", "dna", "leaf", "globe", "landmark",
    "scale", "languages", "book", "code", "chart", "music", "palette", "lightbulb",
]


class ClassCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    color: ClassColor | None = None
    icon: ClassIcon | None = None


class ClassPatch(BaseModel):
    """Change a class's name, color, and/or icon. Only fields that are sent are changed."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    color: ClassColor | None = None
    icon: ClassIcon | None = None


class ClassSummary(BaseModel):
    id: UUID
    name: str
    color: ClassColor | None = None
    icon: ClassIcon | None = None
    created_at: datetime
    updated_at: datetime
    upload_count: int = 0
    material_count: int = 0
    is_owner: bool = False
    capabilities: list[str] = Field(default_factory=list)


ClassCapability = Literal[
    "read", "create", "edit", "delete", "delete_others", "invite", "manage_members",
    "manage_permissions"
]


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expires_in_days: int = Field(default=7, ge=1, le=90)
    max_uses: int = Field(default=1, ge=1, le=100)
    capabilities: list[ClassCapability] = Field(default_factory=lambda: ["read"], min_length=1)

    @field_validator("capabilities")
    @classmethod
    def unique_capabilities(cls, value: list[ClassCapability]) -> list[ClassCapability]:
        if len(value) != len(set(value)):
            raise ValueError("Invitation capabilities must be unique.")
        if "read" not in value:
            raise ValueError("Every class invitation must include read access.")
        return value


class InvitationRedeem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    code: str = Field(min_length=24, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class MemberCapabilitiesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    capabilities: list[ClassCapability] = Field(min_length=1)

    @field_validator("capabilities")
    @classmethod
    def unique_capabilities(cls, value: list[ClassCapability]) -> list[ClassCapability]:
        if len(value) != len(set(value)):
            raise ValueError("Capabilities must be unique.")
        if "read" not in value:
            raise ValueError("Active class members must retain read access.")
        return value


class ResourcePublicationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    state: Literal["draft", "scheduled", "published"]
    release_at: datetime | None = None

    @model_validator(mode="after")
    def validate_release(self) -> ResourcePublicationUpdate:
        if self.state == "scheduled":
            if self.release_at is None or self.release_at.tzinfo is None:
                raise ValueError("A scheduled release requires a timezone-aware release_at.")
            object.__setattr__(self, "release_at", self.release_at.astimezone(UTC))
        elif self.release_at is not None:
            raise ValueError("release_at is only valid for scheduled resources.")
        return self


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


class ConceptCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    canonical_name: str = Field(min_length=1, max_length=160, strict=True)
    description: str = Field(min_length=1, max_length=1_500, strict=True)
    parent_concept_id: UUID | None = None
    source_chunk_revision_ids: list[UUID] = Field(default_factory=list, max_length=8)


class ConceptPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    expected_version: int = Field(ge=1)
    canonical_name: str | None = Field(default=None, min_length=1, max_length=160, strict=True)
    description: str | None = Field(default=None, min_length=1, max_length=1_500, strict=True)
    parent_concept_id: UUID | None = None

    @model_validator(mode="after")
    def require_a_change(self) -> ConceptPatch:
        if not (self.model_fields_set - {"expected_version"}):
            raise ValueError("Provide at least one concept field to update.")
        return self


class ConceptAliasCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=160, strict=True)
    language: str = Field(
        default="und", min_length=2, max_length=35,
        pattern=r"^(?:und|[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*)$", strict=True,
    )


class ConceptDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["approve", "reject", "deprecate"]
    expected_version: int = Field(ge=1)


class ConceptMerge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_concept_id: UUID
    expected_version: int = Field(ge=1)


class ConceptEdgeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    from_concept_id: UUID
    to_concept_id: UUID
    type: Literal["part_of", "prerequisite_for", "related_to", "applied_in"]
    necessity: Literal["essential", "helpful"] | None = None
    provenance: Literal["explicit", "inferred"]
    explanation: str = Field(min_length=1, max_length=1_000, strict=True)
    source_chunk_revision_ids: list[UUID] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_necessity(self) -> ConceptEdgeCreate:
        if self.type == "prerequisite_for" and self.necessity is None:
            raise ValueError("Prerequisite edges require an essential/helpful necessity.")
        if self.type != "prerequisite_for" and self.necessity is not None:
            raise ValueError("Necessity applies only to prerequisite edges.")
        return self


class ConceptExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    upload_id: UUID


class ConceptExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    status: Literal["queued", "running", "complete", "failed", "superseded"]


class ConceptGraphAIAlias(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=160)
    language: str = Field(
        default="und", min_length=2, max_length=35,
        pattern=r"^(?:und|[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*)$",
    )


class ConceptGraphAIConcept(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    key: str = Field(min_length=1, max_length=64)
    canonical_name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1_500)
    aliases: list[ConceptGraphAIAlias] = Field(default_factory=list, max_length=8)
    parent_key: str | None = Field(default=None, max_length=64)
    existing_concept_id: UUID | None = None
    provenance: Literal["explicit", "inferred"]
    source_chunk_revision_ids: list[UUID] = Field(min_length=1, max_length=8)


class ConceptGraphAIEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    from_key: str = Field(min_length=1, max_length=64)
    to_key: str = Field(min_length=1, max_length=64)
    type: Literal["part_of", "prerequisite_for", "related_to", "applied_in"]
    necessity: Literal["essential", "helpful"] | None = None
    provenance: Literal["explicit", "inferred"]
    explanation: str = Field(min_length=1, max_length=1_000)
    source_chunk_revision_ids: list[UUID] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_necessity(self) -> ConceptGraphAIEdge:
        if (self.type == "prerequisite_for") != (self.necessity is not None):
            raise ValueError("Only prerequisite edges have a necessity value.")
        return self


class ConceptGraphAIOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    concepts: list[ConceptGraphAIConcept] = Field(max_length=24)
    edges: list[ConceptGraphAIEdge] = Field(max_length=48)


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
    resource_scope: Literal["class_owned", "student_contribution"] = "class_owned"
    created_by: UUID | None = None
    publication_state: Literal["draft", "scheduled", "published"] = "published"
    release_at: datetime | None = None
    source_index_status: Literal["unindexed", "processing", "ready", "failed"] = "unindexed"


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
    AllMaterialsScope | UploadsMaterialsScope | TopicsMaterialsScope | ExamMaterialsScope,
    Field(discriminator="kind"),
]
StudyMaterialKind = Literal["quiz", "practice_exam", "study_guide", "flashcards"]
StudyDifficulty = Literal["mixed", "easier", "harder"]
StudyQuestionKind = Literal["multiple_choice", "true_false", "short_answer"]


class StudyOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(default=10, ge=4, le=100)
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
    operation_id: UUID | None = None
    kind: StudyMaterialKind
    scope: MaterialsScope = Field(default_factory=lambda: AllMaterialsScope(kind="all"))
    options: StudyOptions = Field(default_factory=StudyOptions)
    additional_instructions: str | None = Field(default=None, max_length=2000)
    language: str = Field(
        default="en", min_length=2, max_length=35,
        pattern=r"^(?:auto|[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*)$",
    )
    destination: Literal["personal", "class_contribution"] = "personal"
    feedback_release_policy: Literal["after_completion", "manual", "never"] | None = None

    @field_validator("additional_instructions", mode="before")
    @classmethod
    def normalize_additional_instructions(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @model_validator(mode="after")
    def instructions_only_apply_to_assessments(self) -> StudyMaterialCreate:
        if self.additional_instructions and self.kind not in {"quiz", "practice_exam"}:
            raise ValueError(
                "Additional instructions are available only for quizzes and practice exams."
            )
        if self.language != "en" and self.kind not in {"quiz", "practice_exam"}:
            raise ValueError("Question language is available only for quizzes and practice exams.")
        return self


class LatestAttempt(BaseModel):
    score_percent: float = Field(ge=0, le=100)
    completed_at: datetime


class MaterialSummary(BaseModel):
    id: UUID
    class_id: UUID | None = None
    origin_class_id: UUID | None = None
    resource_scope: Literal["class_owned", "personal", "student_contribution"] = "class_owned"
    created_by: UUID | None = None
    publication_state: Literal["draft", "scheduled", "published"] | None = "published"
    release_at: datetime | None = None
    feedback_release_policy: Literal["after_completion", "manual", "never"] = "after_completion"
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
    source_warnings: list[Literal["changed", "missing", "truncated"]] = Field(default_factory=list)


class MaterialDetail(MaterialSummary):
    content: dict | None = None
    download_urls: dict[str, str] = Field(default_factory=dict)


class AttemptSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, str] = Field(min_length=1, max_length=100)
    run_id: UUID | None = None
    operation_id: UUID | None = None


class QuestionBankGenerationRequest(BaseModel):
    """Manager-authorized, source-grounded generation request."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    operation_id: UUID
    kind: Literal["quiz", "practice_exam"] = "quiz"
    concept_ids: list[UUID] = Field(default_factory=list, max_length=50)
    upload_ids: list[UUID] = Field(default_factory=list, max_length=50)
    count: int = Field(default=10, ge=4, le=100)
    level: Literal[1, 2, 3, 4] | None = None
    language: str = Field(default="en", min_length=2, max_length=35)
    question_types: list[StudyQuestionKind] = Field(
        default_factory=lambda: ["multiple_choice"], min_length=1, max_length=3
    )
    additional_instructions: str | None = Field(default=None, max_length=2000)
    feedback_release_policy: Literal["after_completion", "manual", "never"] | None = None

    @field_validator("concept_ids", "upload_ids")
    @classmethod
    def unique_uuid_selections(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("Selections must be unique.")
        return value

    @field_validator("question_types")
    @classmethod
    def unique_question_types(cls, value: list[StudyQuestionKind]) -> list[StudyQuestionKind]:
        if len(value) != len(set(value)):
            raise ValueError("Question types must be unique.")
        return value


class QuestionBankAIChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=1000)


class QuestionBankAIItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    assignment_id: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=5000)
    kind: StudyQuestionKind
    choices: list[QuestionBankAIChoice] = Field(default_factory=list, max_length=8)
    answer: str = Field(min_length=1, max_length=5000)
    accepted_answers: list[str] = Field(default_factory=list, max_length=20)
    explanation: str = Field(min_length=1, max_length=5000)
    rubric: str = Field(min_length=1, max_length=2000)
    level: int = Field(ge=1, le=4)
    language: str = Field(min_length=2, max_length=35)
    assessment_target: str = Field(min_length=1, max_length=300)
    concept_ids: list[str] = Field(min_length=1, max_length=8)
    source_chunk_revision_ids: list[str] = Field(min_length=1, max_length=8)


class QuestionBankAIGeneration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    questions: list[QuestionBankAIItem] = Field(min_length=1, max_length=100)


class QuestionBankAIReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    index: int = Field(ge=0, le=99)
    source_supported: bool
    concept_aligned: bool
    answer_correct: bool
    distractors_valid: bool
    unambiguous: bool
    level_aligned: bool
    language_valid: bool
    leakage_free: bool
    duplicate_family: bool
    reason: str = Field(min_length=1, max_length=500)


class QuestionBankAIReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reviews: list[QuestionBankAIReviewItem] = Field(min_length=1, max_length=100)


class QuestionAttemptResult(BaseModel):
    question_id: str
    correct: bool | None = None
    submitted_answer: str | None = None
    correct_answer: str | None = None
    explanation: str | None = None
    sources: list[dict] = Field(default_factory=list)


class AttemptResult(BaseModel):
    attempt_id: UUID
    score_percent: float | None = Field(default=None, ge=0, le=100)
    correct_count: int | None = Field(default=None, ge=0)
    feedback_available: bool = False
    question_results: list[QuestionAttemptResult]


class StudyTutorQuestion(BaseModel):
    """Untrusted review context for a browser-only quiz or sample."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    prompt: str = Field(min_length=1, max_length=5000, strict=True)
    choices: list[Annotated[str, Field(max_length=1000, strict=True)]] = Field(
        default_factory=list, max_length=8,
    )
    submitted_answer: str = Field(default="", max_length=5000, strict=True)
    correct_answer: str = Field(default="", max_length=5000, strict=True)
    explanation: str = Field(default="", max_length=5000, strict=True)


class StudyTutorMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=6000, strict=True)

    @model_validator(mode="after")
    def bound_user_text(self) -> StudyTutorMessage:
        if self.role == "user" and len(self.text) > 2000:
            raise ValueError("Study tutor questions must be at most 2000 characters.")
        return self


class StudyTutorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    material_id: UUID | None = None
    question_id: str | None = Field(default=None, min_length=1, max_length=100, strict=True)
    attempt_id: UUID | None = None
    question: StudyTutorQuestion | None = None
    messages: list[StudyTutorMessage] = Field(min_length=1, max_length=15)

    @model_validator(mode="after")
    def validate_context_and_history(self) -> StudyTutorRequest:
        if self.material_id is not None:
            if not self.question_id or self.attempt_id is None or self.question is not None:
                raise ValueError(
                    "A remote review needs question_id and attempt_id, without question."
                )
        elif self.question is None or self.question_id is not None or self.attempt_id is not None:
            raise ValueError("A local review needs question without remote IDs.")
        if self.messages[-1].role != "user":
            raise ValueError("The last message must be a user question.")
        if any(
            message.role != ("user" if index % 2 == 0 else "assistant")
            for index, message in enumerate(self.messages)
        ):
            raise ValueError("Messages must alternate user and assistant, starting with user.")
        if sum(len(message.text) for message in self.messages) > 24_000:
            raise ValueError("Study tutor history is too long.")
        return self


class StudyTutorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    reply: str = Field(min_length=1, max_length=6000)


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
    questions: list[QuestionContent] = Field(min_length=1, max_length=100)


class PracticeExamContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["practice_exam"]
    instructions: str = Field(min_length=1, max_length=2000)
    questions: list[QuestionContent] = Field(min_length=1, max_length=100)


class GuideSectionContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20_000)
    key_terms: list[str] = Field(default_factory=list, max_length=50)
    sources: list[SourceCitation] = Field(default_factory=list, max_length=20)


class StudyGuideContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["study_guide"]
    sections: list[GuideSectionContent] = Field(min_length=1, max_length=100)


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
    cards: list[FlashcardContent] = Field(min_length=1, max_length=100)


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
AvatarColor = Literal[
    "slate", "ocean", "mint", "moss", "sunny", "tangerine", "coral", "berry", "grape"
]
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
    is_admin: bool = False
    avatar: Avatar | None = None


class SignupResponse(BaseModel):
    """Account creation or an administrator-reviewed signup request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["active", "pending"]
    user: AuthUser | None = None
    message: str | None = None


class MeResponse(BaseModel):
    """The signed-in account, or null for a guest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user: AuthUser | None = None
    guest_access_enabled: bool = False
    signup_mode: Literal["approval", "open", "closed"] = "closed"


class SignupRequestSummary(BaseModel):
    """Public-safe signup request fields visible only to administrators."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    username: str
    email: str | None = None
    display_name: str | None = None
    created_at: datetime


class SignupRequestsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requests: list[SignupRequestSummary]


class SignupRequestDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    status: Literal["approved", "rejected"]
    username: str


class AIProviderSettingsUpdate(BaseModel):
    """Administrator-only OpenAI-compatible Chat Completions configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str = Field(min_length=1, max_length=2_048, strict=True)
    model: str = Field(min_length=1, max_length=200, strict=True)
    api_key: SecretStr | None = None
    clear_api_key: bool = False

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        secret = value.get_secret_value().strip()
        if not secret:
            return None
        if len(secret) > 4_096:
            raise ValueError("The API key must be 4096 characters or shorter.")
        return SecretStr(secret)


class AIProviderSettingsSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str | None = None
    model: str | None = None
    api_key_configured: bool = False
    provider_configured: bool = False
    settings_source: Literal["admin", "environment", "unconfigured"] = "unconfigured"


class AIProviderTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    success: bool
    message: str


class ProfileUpdate(BaseModel):
    """Change the username and/or email. Only fields that are sent are changed."""

    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(
        default=None, min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$"
    )
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
