"""Predefined tutoring modes; clients cannot supply their own instructions."""

from enum import StrEnum


class TutorMode(StrEnum):
    CONVERSATION = "conversation"
    CHECK_STEPS = "check_steps"
