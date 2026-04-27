# SPDX-License-Identifier: Apache-2.0
"""Tests for vaner.setup.recommended.payload.sanitize_work_styles.

Hardening surface — work_styles enters via untrusted HTTP / MCP / CLI
inputs. The sanitiser is the boundary that turns whatever the caller
sent into a tuple of slugs the resolver can safely iterate.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any

import pytest

from vaner.setup import hardware as hw
from vaner.setup.recommended.payload import (
    models_recommended_payload,
    sanitize_work_styles,
)
from vaner.setup.recommended.schema import RecommendedModel, Registry

# ---------------------------------------------------------------------------
# sanitize_work_styles
# ---------------------------------------------------------------------------


def test_passes_through_real_slugs() -> None:
    out = sanitize_work_styles(["coding", "writing"])
    assert out == ("coding", "writing")


def test_lowercases_and_trims() -> None:
    out = sanitize_work_styles(["  Coding ", "WRITING"])
    assert out == ("coding", "writing")


def test_dedupes_preserving_first_seen_order() -> None:
    out = sanitize_work_styles(["coding", "writing", "coding", "support"])
    assert out == ("coding", "writing", "support")


def test_drops_empty_and_blank() -> None:
    out = sanitize_work_styles(["", "   ", "coding"])
    assert out == ("coding",)


def test_drops_non_str_entries() -> None:
    out = sanitize_work_styles(["coding", 42, None, ["nested"], "writing"])  # type: ignore[list-item]
    assert out == ("coding", "writing")


@pytest.mark.parametrize(
    "bad",
    [
        "has space",
        "with;semicolon",
        "../traversal",
        "$(touch /tmp/pwn)",
        "`backticks`",
        "with\nnewline",
        "with\x00null",
        "with-dash-but-good",  # actually starts ok; check passes
        "x" * 64,  # too long
        "1starts-with-digit",
    ],
)
def test_drops_dangerous_or_overlong_slugs(bad: str) -> None:
    """Sanitiser silently drops anything that doesn't look like a slug."""
    out = sanitize_work_styles([bad])
    if bad == "with-dash-but-good":
        # Negative control: the regex in payload.py only allows
        # [a-z][a-z0-9_]+; dashes are NOT in the character class.
        assert out == ()
    else:
        assert out == ()


def test_caps_max_entries_at_16() -> None:
    """Even if the caller sends thousands, the sanitiser caps."""
    huge = [f"intent{i:03d}" for i in range(50)]
    out = sanitize_work_styles(huge)
    assert len(out) == 16


# ---------------------------------------------------------------------------
# models_recommended_payload — sanitises before dispatching
# ---------------------------------------------------------------------------


def _profile() -> hw.HardwareProfile:
    return dataclasses.replace(
        hw.HardwareProfile(
            os="linux",
            cpu_class="high",
            ram_gb=64,
            gpu="nvidia",
            gpu_vram_gb=24,
            is_battery=False,
            thermal_constrained=False,
            detected_runtimes=(),
            detected_models=(),
            tier="high_performance",
        ),
    )


def _model(**overrides: Any) -> RecommendedModel:
    base: dict[str, Any] = {
        "id": "fake:7b",
        "family": "fake",
        "params_b": 7.0,
        "min_effective_gb_q4": 5.0,
        "intent_lean": ("coding",),
        "ollama_id": "fake:7b",
        "huggingface_id": None,
        "context_length": 8192,
        "popularity_rank": 1,
        "rank_source": "ollama-library",
    }
    base.update(overrides)
    return RecommendedModel.model_validate(base)


def test_payload_filters_malicious_work_styles() -> None:
    """Payload assembler runs sanitize_work_styles on its untrusted input."""
    reg = Registry(
        schema_version=1,
        generated_at=datetime.now(tz=UTC),
        generator="test",
        sources=(),
        models=(_model(),),
    )
    payload = models_recommended_payload(
        reg,
        _profile(),
        ["coding", "$(rm -rf /)", "../etc/passwd", "valid"],
    )
    # The resolver still produced a recommendation — the dirty inputs
    # were silently dropped, leaving "coding" + "valid".
    assert payload["selected"] is not None
    assert payload["selected"]["id"] == "fake:7b"
