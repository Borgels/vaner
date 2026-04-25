# SPDX-License-Identifier: Apache-2.0
"""WS1 — enum literal alias tests (0.8.6).

The aliases themselves are ``Literal`` types and have no runtime
behaviour to test directly. We exercise their interaction with the
``SetupConfig`` Pydantic model: every valid value round-trips, and an
invalid value raises :class:`pydantic.ValidationError`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vaner.models.config import SetupConfig
from vaner.setup.enums import (
    BackgroundPosture,
    CloudPosture,
    ComputePosture,
    HardwareTier,
    Priority,
    WorkStyle,
)

# ---------------------------------------------------------------------------
# Literal-membership checks. ``Literal`` doesn't expose its args via a
# stable public API; we use ``typing.get_args`` which is documented and
# stable across 3.11+.
# ---------------------------------------------------------------------------


def _literal_values(alias: object) -> tuple[str, ...]:
    from typing import get_args

    return tuple(get_args(alias))


def test_work_style_values() -> None:
    assert set(_literal_values(WorkStyle)) == {
        "writing",
        "research",
        "planning",
        "support",
        "learning",
        "coding",
        "general",
        "mixed",
        "unsure",
    }


def test_priority_values() -> None:
    assert set(_literal_values(Priority)) == {
        "balanced",
        "speed",
        "quality",
        "privacy",
        "cost",
        "low_resource",
    }


def test_compute_posture_values() -> None:
    assert set(_literal_values(ComputePosture)) == {"light", "balanced", "available_power"}


def test_cloud_posture_values() -> None:
    assert set(_literal_values(CloudPosture)) == {
        "local_only",
        "ask_first",
        "hybrid_when_worth_it",
        "best_available",
    }


def test_background_posture_values() -> None:
    assert set(_literal_values(BackgroundPosture)) == {
        "minimal",
        "normal",
        "idle_more",
        "deep_run_aggressive",
    }


def test_hardware_tier_values() -> None:
    assert set(_literal_values(HardwareTier)) == {
        "light",
        "capable",
        "high_performance",
        "unknown",
    }


# ---------------------------------------------------------------------------
# Pydantic validation: each enum value works as a SetupConfig field
# value; invalid values raise ValidationError.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", _literal_values(WorkStyle))
def test_setup_config_accepts_each_work_style(value: str) -> None:
    cfg = SetupConfig(work_styles=[value])  # type: ignore[list-item]
    assert cfg.work_styles == [value]


@pytest.mark.parametrize("value", _literal_values(Priority))
def test_setup_config_accepts_each_priority(value: str) -> None:
    cfg = SetupConfig(priority=value)  # type: ignore[arg-type]
    assert cfg.priority == value


def test_setup_config_rejects_invalid_priority() -> None:
    with pytest.raises(ValidationError):
        SetupConfig(priority="not-a-priority")  # type: ignore[arg-type]


def test_setup_config_rejects_invalid_work_style() -> None:
    with pytest.raises(ValidationError):
        SetupConfig(work_styles=["not-a-work-style"])  # type: ignore[list-item]


def test_setup_config_rejects_invalid_cloud_posture() -> None:
    with pytest.raises(ValidationError):
        SetupConfig(cloud_posture="not-a-cloud-posture")  # type: ignore[arg-type]


def test_setup_config_rejects_invalid_background_posture() -> None:
    with pytest.raises(ValidationError):
        SetupConfig(background_posture="not-a-background-posture")  # type: ignore[arg-type]
