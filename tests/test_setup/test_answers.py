# SPDX-License-Identifier: Apache-2.0
"""WS1 — SetupAnswers dataclass tests (0.8.6)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from vaner.setup.answers import SetupAnswers


def _valid_answers() -> SetupAnswers:
    return SetupAnswers(
        work_styles=("writing", "research"),
        priority="quality",
        compute_posture="balanced",
        cloud_posture="hybrid_when_worth_it",
        background_posture="idle_more",
    )


def test_round_trip_preserves_fields() -> None:
    answers = _valid_answers()
    assert answers.work_styles == ("writing", "research")
    assert answers.priority == "quality"
    assert answers.compute_posture == "balanced"
    assert answers.cloud_posture == "hybrid_when_worth_it"
    assert answers.background_posture == "idle_more"


def test_empty_work_styles_raises() -> None:
    with pytest.raises(ValueError, match="work_styles must contain"):
        SetupAnswers(
            work_styles=(),
            priority="balanced",
            compute_posture="balanced",
            cloud_posture="ask_first",
            background_posture="normal",
        )


def test_dataclass_is_frozen() -> None:
    answers = _valid_answers()
    with pytest.raises(FrozenInstanceError):
        answers.priority = "speed"  # type: ignore[misc]


def test_single_work_style_is_valid() -> None:
    answers = SetupAnswers(
        work_styles=("mixed",),
        priority="balanced",
        compute_posture="balanced",
        cloud_posture="ask_first",
        background_posture="normal",
    )
    assert answers.work_styles == ("mixed",)


def test_setup_answers_is_hashable() -> None:
    # Frozen + tuple work_styles means SetupAnswers is hashable.
    # Important for cache keys in the selection algorithm.
    a1 = _valid_answers()
    a2 = _valid_answers()
    assert hash(a1) == hash(a2)
    assert a1 == a2
