"""Predefined tutoring modes; clients cannot supply their own instructions."""

from enum import StrEnum


class TutorMode(StrEnum):
    CONVERSATION = "conversation"
    CHECK_STEPS = "check_steps"
    QUIZ_REASONING = "quiz_reasoning"


class TutorLanguage(StrEnum):
    """Languages a student can lock the tutor to. `auto` means no lock."""

    AUTO = "auto"
    ENGLISH = "en"
    SPANISH = "es"
    FRENCH = "fr"
    GERMAN = "de"
    PORTUGUESE = "pt"
    ITALIAN = "it"
    CHINESE = "zh"
    JAPANESE = "ja"
    KOREAN = "ko"
    HINDI = "hi"
    ARABIC = "ar"


# English name and the language's own name, used in the fixed instruction below.
LANGUAGE_NAMES: dict[TutorLanguage, tuple[str, str]] = {
    TutorLanguage.ENGLISH: ("English", "English"),
    TutorLanguage.SPANISH: ("Spanish", "Español"),
    TutorLanguage.FRENCH: ("French", "Français"),
    TutorLanguage.GERMAN: ("German", "Deutsch"),
    TutorLanguage.PORTUGUESE: ("Portuguese", "Português"),
    TutorLanguage.ITALIAN: ("Italian", "Italiano"),
    TutorLanguage.CHINESE: ("Mandarin Chinese", "中文"),
    TutorLanguage.JAPANESE: ("Japanese", "日本語"),
    TutorLanguage.KOREAN: ("Korean", "한국어"),
    TutorLanguage.HINDI: ("Hindi", "हिन्दी"),
    TutorLanguage.ARABIC: ("Arabic", "العربية"),
}
