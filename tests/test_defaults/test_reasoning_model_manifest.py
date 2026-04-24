# SPDX-License-Identifier: Apache-2.0
"""WS2.c — reasoning-model manifest lookup tests."""

from __future__ import annotations

from vaner.defaults.loader import load_reasoning_model_patterns, reasoning_defaults_for_model


def test_manifest_loads_nonempty_patterns():
    patterns = load_reasoning_model_patterns()
    assert patterns, "reasoning_models.patterns should be non-empty in the shipped manifest"
    for entry in patterns:
        assert "match" in entry
        assert entry["reasoning_mode"] in {"off", "allowed", "required", "provider_default"}
        assert isinstance(entry["extra_body"], dict)


def test_a3b_matches_specific_pattern_not_generic_qwen3():
    """Longest-match-wins: the A3B entry is more specific than 'qwen3'."""
    entry = reasoning_defaults_for_model("Qwen/Qwen3.5-35B-A3B-FP8")
    assert entry is not None
    assert entry["match"] == "qwen3.5-35b-a3b"
    assert entry["reasoning_mode"] == "allowed"


def test_qwen3_base_falls_back_to_generic_pattern():
    entry = reasoning_defaults_for_model("qwen3:8b")
    assert entry is not None
    assert entry["match"] == "qwen3"


def test_deepseek_r1_recognised():
    entry = reasoning_defaults_for_model("deepseek-r1-distill-qwen-32b")
    assert entry is not None
    assert entry["match"] == "deepseek-r1"


def test_non_reasoning_model_returns_none():
    assert reasoning_defaults_for_model("gpt-4o") is None
    assert reasoning_defaults_for_model("llama3.2:3b") is None
    assert reasoning_defaults_for_model("") is None


def test_matching_is_case_insensitive():
    a = reasoning_defaults_for_model("QWEN3:8B")
    b = reasoning_defaults_for_model("qwen3:8b")
    assert a is not None and b is not None
    assert a["match"] == b["match"]
