"""Presentation helpers for generated multiple-choice assessments."""

from __future__ import annotations

import random
from collections import defaultdict
from copy import deepcopy
from typing import Any


def balance_guide_mini_quiz_positions(
    sections: list[dict[str, Any]], *, seed: str
) -> list[dict[str, Any]]:
    """Balance mini-quiz answer positions across a guide and update each answer index."""
    result = deepcopy(sections)
    indexes_by_choice_count: dict[int, list[int]] = defaultdict(list)
    for index, section in enumerate(result):
        if not isinstance(section, dict):
            continue
        quiz = section.get("mini_quiz")
        if not isinstance(quiz, dict):
            continue
        choices = quiz.get("choices")
        correct_index = quiz.get("correct_index")
        if (
            not isinstance(choices, list)
            or not 2 <= len(choices) <= 6
            or not all(isinstance(choice, str) for choice in choices)
            or type(correct_index) is not int
            or not 0 <= correct_index < len(choices)
        ):
            continue
        indexes_by_choice_count[len(choices)].append(index)

    for choice_count, indexes in indexes_by_choice_count.items():
        rng = random.Random(f"{seed}:mini:{choice_count}")
        positions = list(range(choice_count)) * (len(indexes) // choice_count)
        positions.extend(range(len(indexes) % choice_count))
        rng.shuffle(positions)
        for index, target_position in zip(indexes, positions, strict=True):
            quiz = result[index]["mini_quiz"]
            choices = quiz["choices"]
            correct = choices[quiz["correct_index"]]
            distractors = [
                choice
                for choice_index, choice in enumerate(choices)
                if choice_index != quiz["correct_index"]
            ]
            rng.shuffle(distractors)
            distractors.insert(target_position, correct)
            quiz["choices"] = distractors
            quiz["correct_index"] = target_position

    return result
