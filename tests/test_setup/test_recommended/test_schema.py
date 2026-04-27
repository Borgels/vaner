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


# ---------------------------------------------------------------------------
# Hardening (0.8.8): id-shape validation, family slug, length caps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "has space",
        "with;semicolon",
        "../path-traversal",
        "with\nnewline",
        "with\x00null",
        "really" + "x" * 200,  # > 192 char ceiling
        "rm -rf /",
        "$(touch /tmp/pwn)",
        "`backticks`",
        "name|pipe",
        ".starts-with-dot",
        "_starts-with-underscore",
    ],
)
def test_model_id_shape_rejects_dangerous_strings(bad_id: str) -> None:
    """Anything not matching the model-id regex must fail validation."""
    with pytest.raises(ValidationError):
        _model(id=bad_id)


@pytest.mark.parametrize(
    "good_id",
    [
        "qwen2.5:32b-instruct-q4_K_M",
        "Qwen/Qwen2.5-32B-Instruct-GGUF",
        "deepseek-r1:7b",
        "a",
        "x" * 192,
    ],
)
def test_model_id_shape_accepts_real_world_ids(good_id: str) -> None:
    _model(id=good_id, ollama_id=good_id)


def test_ollama_id_and_huggingface_id_validated_too() -> None:
    """Both nullable id fields share the same regex gate."""
    with pytest.raises(ValidationError):
        _model(ollama_id="bad name with spaces")
    with pytest.raises(ValidationError):
        _model(huggingface_id="bad/../traversal")


def test_family_slug_validation() -> None:
    """family must be lowercase alphanumeric with `.+-`. No spaces, no caps."""
    with pytest.raises(ValidationError):
        _model(family="UPPERCASE")
    with pytest.raises(ValidationError):
        _model(family="has space")
    with pytest.raises(ValidationError):
        _model(family="rm -rf")
    # Real-world families must pass.
    for ok in ("qwen", "qwen2.5", "llama3.1", "deepseek-coder", "phi-3.5"):
        _model(family=ok)


def test_params_b_clamped_to_sane_range() -> None:
    """Params >10000 B is rejected — guards against overflow attacks."""
    with pytest.raises(ValidationError):
        _model(params_b=999_999.0)
    # Within range.
    _model(params_b=8000.0)


def test_context_length_clamped() -> None:
    with pytest.raises(ValidationError):
        _model(context_length=999_999_999)


def test_popularity_rank_clamped() -> None:
    with pytest.raises(ValidationError):
        _model(popularity_rank=10_000_000)


def test_registry_models_capped_at_200() -> None:
    """A malicious data.json with thousands of entries is rejected at load."""
    too_many = tuple(_model(id=f"fake:{i}b", popularity_rank=i + 1) for i in range(1, 250))
    with pytest.raises(ValidationError):
        Registry(
            schema_version=1,
            generated_at=datetime.now(tz=UTC),
            generator="test",
            sources=(),
            models=too_many,  # type: ignore[arg-type]
        )


def test_registry_sources_capped() -> None:
    too_many = tuple(RegistrySource(name=f"src{i}", snapshot_at=datetime.now(tz=UTC)) for i in range(20))
    with pytest.raises(ValidationError):
        Registry(
            schema_version=1,
            generated_at=datetime.now(tz=UTC),
            generator="test",
            sources=too_many,
            models=(),
        )


def test_registry_source_note_length_cap() -> None:
    """Note field is bounded so a malicious source can't inflate the JSON."""
    with pytest.raises(ValidationError):
        RegistrySource(
            name="ollama-library",
            snapshot_at=datetime.now(tz=UTC),
            note="x" * 1024,
        )
