"""Bounded structured-model calls for opt-in reasoning-first quiz mode."""

from __future__ import annotations

import json
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.gemini_content import _provider_response_schema
from app.services.study_prompts import (
    QUIZ_REASONING_EVALUATOR_PROMPT,
    QUIZ_REASONING_FEEDBACK_PROMPT,
)


class ReasoningDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["sufficient", "insufficient"]
    reason: str = Field(min_length=1, max_length=500)
    follow_up: str | None = Field(max_length=300)

    @model_validator(mode="after")
    def validate_decision(self) -> ReasoningDecision:
        if self.status == "insufficient" and not self.follow_up:
            raise ValueError("An insufficient result needs a follow-up question.")
        if self.status == "sufficient" and self.follow_up is not None:
            raise ValueError("A sufficient result cannot include a follow-up.")
        return self


class ReasoningFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    answer_feedback: str = Field(min_length=1, max_length=1200)
    reasoning_feedback: str = Field(min_length=1, max_length=1800)


class QuizReasoningService:
    """Use the configured text model; never log or retain input or output text."""

    def __init__(self, client: genai.Client, model: str) -> None:
        self._client = client
        self._model = model

    def evaluate(self, context: dict) -> ReasoningDecision:
        return self._generate(
            context,
            ReasoningDecision,
            QUIZ_REASONING_EVALUATOR_PROMPT,
            max_output_tokens=700,
        )

    def feedback(self, context: dict) -> ReasoningFeedback:
        return self._generate(
            context,
            ReasoningFeedback,
            QUIZ_REASONING_FEEDBACK_PROMPT,
            max_output_tokens=1200,
        )

    def _generate(
        self, context: dict, schema: type[BaseModel], instruction: str, *, max_output_tokens: int
    ):
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=json.dumps(context, ensure_ascii=False),
                config=types.GenerateContentConfig(
                    system_instruction=instruction,
                    response_mime_type="application/json",
                    response_json_schema=_provider_response_schema(schema),
                    temperature=0.1,
                    max_output_tokens=max_output_tokens,
                ),
            )
            text = getattr(response, "text", None)
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Empty structured response")
            return schema.model_validate_json(text)
        except Exception as exc:
            # Provider details can include request content; expose no raw message.
            raise QuizReasoningFailure from exc


class QuizReasoningFailure(Exception):
    """The model call timed out or returned an invalid structured result."""
