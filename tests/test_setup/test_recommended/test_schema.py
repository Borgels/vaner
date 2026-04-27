# SPDX-License-Identifier: Apache-2.0
"""Tests for vaner.setup.recommended.schema (Pydantic registry)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from vaner.setup.recommended.schema import (
    RecommendedModel,
    Registry,
    RegistrySource,
)


def _model(**overrides: object) -> RecommendedModel:
    base: dict[str, object] = {
        "id": "synthetic:7b-instruct-q4",
        "family": "synthetic",
        "params_b": 7.0,
        "min_effective_gb_q4": 5.2,
        "intent_lean": ("coding", "writing"),
        "ollama_id": "synthetic:7b-instruct-q4",
        "huggingface_id": None,
        "context_length": 8192,
        "popularity_rank": 1,
        "rank_source": "ollama-library",
    }
    base.update(overrides)
    return RecommendedModel.model_validate(base)


def test_recommended_model_round_trips() -> None:
    """A model serialised + reloaded yields an identical instance."""
    original = _model()
    payload = original.model_dump(mode="json")
    restored = RecommendedModel.model_validate(payload)
    assert restored == original


def test_recommended_model_rejects_zero_params() -> None:
    with pytest.raises(ValidationError):
        _model(params_b=0.0)


def test_recommended_model_rejects_unknown_rank_source() -> None:
    with pytest.raises(ValidationError):
        _model(rank_source="lemonade")


def test_recommended_model_rejects_extras() -> None:
    """The schema is closed — typo'd fields must fail loudly."""
    with pytest.raises(ValidationError):
        RecommendedModel.model_validate(
            {
                **_model().model_dump(mode="json"),
                "wat": "this is not a real field",
            }
        )


def test_intent_lean_coerces_list_to_tuple() -> None:
    """JSON arrays come in as lists; the schema stores them as tuples."""
    m = _model(intent_lean=["research", "writing"])
    assert m.intent_lean == ("research", "writing")


def test_registry_round_trips() -> None:
    reg = Registry(
        schema_version=1,
        generated_at=datetime(2026, 4, 27, 19, 0, tzinfo=UTC),
        generator="refresh_recommended_models.py@deadbeefcafe",
        sources=(
            RegistrySource(
                name="ollama-library",
                snapshot_at=datetime(2026, 4, 27, 18, 59, tzinfo=UTC),
            ),
        ),
        models=(_model(), _model(id="other:13b-instruct", popularity_rank=2)),
    )
    payload = reg.model_dump(mode="json")
    restored = Registry.model_validate(payload)
    assert restored == reg


def test_empty_registry_is_valid() -> None:
    """A freshly-loaded empty registry is the daemon's safe fallback."""
    reg = Registry(
        schema_version=1,
        generated_at=datetime.now(tz=UTC),
        generator="loader.py:empty-fallback",
        sources=(),
        models=(),
    )
    payload = reg.model_dump(mode="json")
    Registry.model_validate(payload)


def test_intent_lean_rejects_unknown_slug() -> None:
    with pytest.raises(ValidationError):
        _model(intent_lean=("not-a-real-intent",))
