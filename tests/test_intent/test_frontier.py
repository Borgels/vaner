# SPDX-License-Identifier: Apache-2.0
"""Tests for ExplorationFrontier — the admission/ordering/feedback core."""

from __future__ import annotations

import time

import pytest

from vaner.intent.frontier import (
    ExplorationFrontier,
    ExplorationScenario,
    depth_discount,
    file_set_fingerprint,
    freshness_decay,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scenario(
    files: list[str],
    source: str = "graph",
    priority: float = 0.5,
    depth: int = 0,
    anchor: str = "anc",
) -> ExplorationScenario:
    return ExplorationScenario(
        id=file_set_fingerprint(files),
        file_paths=files,
        anchor=anchor,
        source=source,
        priority=priority,
        depth=depth,
        reason="test",
    )


# ---------------------------------------------------------------------------
# file_set_fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_order_independent() -> None:
    assert file_set_fingerprint(["a.py", "b.py"]) == file_set_fingerprint(["b.py", "a.py"])


def test_fingerprint_dedup() -> None:
    assert file_set_fingerprint(["a.py", "a.py"]) == file_set_fingerprint(["a.py"])


# ---------------------------------------------------------------------------
# depth_discount
# ---------------------------------------------------------------------------

def test_depth_discount_zero_is_full() -> None:
    assert depth_discount(0) == 1.0


def test_depth_discount_decreases() -> None:
    assert depth_discount(1) < depth_discount(0)
    assert depth_discount(3) < depth_discount(1)


def test_depth_discount_floor() -> None:
    assert depth_discount(100) >= 0.1


# ---------------------------------------------------------------------------
# freshness_decay
# ---------------------------------------------------------------------------

def test_freshness_recent_is_high() -> None:
    assert freshness_decay(time.time()) > 0.9


def test_freshness_old_is_low() -> None:
    assert freshness_decay(time.time() - 3600) < 0.5


# ---------------------------------------------------------------------------
# ExplorationScenario.is_trivial
# ---------------------------------------------------------------------------

def test_is_trivial_graph_depth0() -> None:
    s = _scenario(["a.py"], source="graph", depth=0)
    assert s.is_trivial()


def test_is_trivial_arc_depth0_not_trivial() -> None:
    s = _scenario(["a.py"], source="arc", depth=0)
    assert not s.is_trivial()


def test_is_trivial_graph_depth1_not_trivial() -> None:
    s = _scenario(["a.py"], source="graph", depth=1)
    assert not s.is_trivial()


# ---------------------------------------------------------------------------
# ExplorationFrontier — push / pop basics
# ---------------------------------------------------------------------------

def test_push_and_pop_order() -> None:
    f = ExplorationFrontier(max_size=10, min_priority=0.01)
    lo = _scenario(["a.py"], priority=0.3)
    hi = _scenario(["b.py"], priority=0.9)
    f.push(lo)
    f.push(hi)
    first = f.pop()
    assert first is not None
    assert first.file_paths == ["b.py"]  # highest priority first


def test_pop_empty_returns_none() -> None:
    f = ExplorationFrontier()
    assert f.pop() is None


def test_duplicate_fingerprint_rejected() -> None:
    f = ExplorationFrontier(min_priority=0.01)
    s = _scenario(["a.py", "b.py"])
    assert f.push(s)
    assert not f.push(s)  # same id, already pending
    assert f.pending_count == 1


def test_duplicate_upgrade_higher_priority() -> None:
    """Same fingerprint with higher priority should upgrade the pending entry."""
    f = ExplorationFrontier(min_priority=0.01)
    s_low = _scenario(["a.py"], priority=0.3)
    s_high = _scenario(["a.py"], priority=0.8)
    f.push(s_low)
    f.push(s_high)  # upgrade
    popped = f.pop()
    assert popped is not None
    assert popped.priority == pytest.approx(0.8, abs=0.01)


def test_upgrade_applies_multiplier_consistently() -> None:
    """Upgrade path must apply the source multiplier to the effective priority,
    matching the behaviour of a fresh admission."""
    f = ExplorationFrontier(min_priority=0.01)
    f._multipliers["pattern"] = 1.5
    s_low = _scenario(["a.py"], source="pattern", priority=0.3)
    # Effective after admission: 0.3 * 1.5 = 0.45
    f.push(s_low)
    s_high = _scenario(["a.py"], source="pattern", priority=0.7)
    # Effective after upgrade: 0.7 * 1.5 = 1.05, capped implicitly
    f.push(s_high)  # should upgrade since 1.05 > 0.45
    popped = f.pop()
    assert popped is not None
    assert popped.priority == pytest.approx(1.05, abs=0.01)


def test_max_depth_rejection() -> None:
    f = ExplorationFrontier(max_depth=2, min_priority=0.01)
    deep = _scenario(["a.py"], depth=3)
    assert not f.push(deep)


def test_min_priority_rejection() -> None:
    f = ExplorationFrontier(min_priority=0.5)
    low = _scenario(["a.py"], priority=0.1)
    assert not f.push(low)


def test_max_size_rejection() -> None:
    f = ExplorationFrontier(max_size=2, min_priority=0.01)
    f.push(_scenario(["a.py"]))
    f.push(_scenario(["b.py"]))
    assert not f.push(_scenario(["c.py"]))


# ---------------------------------------------------------------------------
# Jaccard deduplication
# ---------------------------------------------------------------------------

def test_jaccard_dedup_blocks_similar() -> None:
    f = ExplorationFrontier(dedup_threshold=0.7, min_priority=0.01)
    s1 = _scenario(["a.py", "b.py", "c.py"])
    # 75% overlap with s1 (3 shared / 4 union)
    s2 = ExplorationScenario(
        id=file_set_fingerprint(["a.py", "b.py", "c.py", "d.py"]),
        file_paths=["a.py", "b.py", "c.py", "d.py"],
        anchor="x",
        source="graph",
        priority=0.5,
        depth=0,
        reason="test",
    )
    assert f.push(s1)
    assert not f.push(s2)  # 75% Jaccard overlap → rejected


def test_jaccard_allows_distinct() -> None:
    f = ExplorationFrontier(dedup_threshold=0.7, min_priority=0.01)
    s1 = _scenario(["a.py", "b.py"])
    s2 = _scenario(["c.py", "d.py"])
    assert f.push(s1)
    assert f.push(s2)


# ---------------------------------------------------------------------------
# mark_explored / is_saturated
# ---------------------------------------------------------------------------

def test_mark_explored_prevents_readmission() -> None:
    f = ExplorationFrontier(min_priority=0.01)
    s = _scenario(["a.py"])
    f.push(s)
    popped = f.pop()
    assert popped is not None
    f.mark_explored(popped.id)
    assert f.explored_count == 1
    assert not f.push(s)


def test_mark_explored_updates_coverage_via_covered_files() -> None:
    """Coverage tracking must work through the pop → mark_explored flow.

    pop() removes the scenario from _pending, so mark_explored must receive
    the files explicitly to update _covered_file_set correctly.
    """
    f = ExplorationFrontier(saturation_coverage=0.5, min_priority=0.01)
    f._total_available = 2
    s = _scenario(["a.py", "b.py"])
    f.push(s)
    popped = f.pop()
    assert popped is not None
    # Without covered_files, coverage would not be updated
    assert len(f._covered_file_set) == 0
    f.mark_explored(popped.id, covered_files=popped.file_paths)
    assert "a.py" in f._covered_file_set
    assert "b.py" in f._covered_file_set
    # 2/2 = 100% > 50% threshold → saturated
    assert f.is_saturated()


def test_mark_explored_without_covered_files_no_crash() -> None:
    """Calling mark_explored without covered_files is still valid (e.g. on failure)."""
    f = ExplorationFrontier(min_priority=0.01)
    s = _scenario(["a.py"])
    f.push(s)
    popped = f.pop()
    assert popped is not None
    f.mark_explored(popped.id)  # no covered_files passed
    assert f.explored_count == 1
    assert len(f._covered_file_set) == 0  # nothing updated


def test_is_saturated_empty_queue() -> None:
    f = ExplorationFrontier()
    assert f.is_saturated()


def test_is_saturated_coverage_threshold() -> None:
    f = ExplorationFrontier(saturation_coverage=0.5, min_priority=0.01)
    f._total_available = 4
    f._covered_file_set = {"a.py", "b.py"}  # 50% coverage
    assert f.is_saturated()


def test_not_saturated_below_threshold() -> None:
    f = ExplorationFrontier(saturation_coverage=0.9, min_priority=0.01)
    f._total_available = 10
    f._covered_file_set = {"a.py"}  # 10% coverage
    s = _scenario(["b.py"])
    f.push(s)
    assert not f.is_saturated()


# ---------------------------------------------------------------------------
# Feedback / multipliers
# ---------------------------------------------------------------------------

def test_feedback_hit_boosts_multiplier() -> None:
    f = ExplorationFrontier()
    original = f.source_multipliers["graph"]
    f.record_feedback("graph", hit=True)
    assert f.source_multipliers["graph"] > original


def test_feedback_miss_reduces_multiplier() -> None:
    f = ExplorationFrontier()
    original = f.source_multipliers["arc"]
    f.record_feedback("arc", hit=False)
    assert f.source_multipliers["arc"] < original


def test_feedback_cap_upper() -> None:
    f = ExplorationFrontier()
    for _ in range(200):
        f.record_feedback("graph", hit=True)
    assert f.source_multipliers["graph"] <= 2.0


def test_feedback_cap_lower() -> None:
    f = ExplorationFrontier()
    for _ in range(200):
        f.record_feedback("graph", hit=False)
    assert f.source_multipliers["graph"] >= 0.3


# ---------------------------------------------------------------------------
# source_multipliers affect effective priority in push
# ---------------------------------------------------------------------------

def test_multiplier_scales_effective_priority() -> None:
    f = ExplorationFrontier(min_priority=0.01)
    # Boost llm_branch above its default 0.9 multiplier
    f._multipliers["llm_branch"] = 2.0
    s = _scenario(["x.py"], source="llm_branch", priority=0.4)
    f.push(s)
    popped = f.pop()
    assert popped is not None
    # effective priority = 0.4 * 2.0 = 0.8
    assert popped.priority == pytest.approx(0.8, abs=0.01)


def test_layer_bonus_applied_once_for_effective_priority() -> None:
    f = ExplorationFrontier(min_priority=0.01)
    strategic_priority = f._score(
        source="arc",
        graph_proximity=0.0,
        arc_probability=1.0,
        coverage_gap=0.5,
        pattern_strength=0.0,
        freshness_decay=1.0,
        depth=0,
        layer="strategic",
    )
    scenario = ExplorationScenario(
        id=file_set_fingerprint(["strategic.py"]),
        file_paths=["strategic.py"],
        anchor="planning",
        source="arc",
        priority=strategic_priority,
        depth=0,
        reason="strategic test",
        layer="strategic",
    )
    assert f.push(scenario)
    popped = f.pop()
    assert popped is not None
    # If layer is applied once, push() should not add another strategic bonus.
    assert popped.priority == pytest.approx(strategic_priority, abs=0.01)


# ---------------------------------------------------------------------------
# seed_from_arc (cold-start path)
# ---------------------------------------------------------------------------

def test_seed_from_arc_cold_start() -> None:
    from vaner.intent.arcs import ConversationArcModel

    f = ExplorationFrontier(min_priority=0.01)
    arc = ConversationArcModel()
    available = [
        "tests/test_engine.py",
        "tests/test_store.py",
        "src/engine.py",
        "src/store.py",
    ]
    admitted = f.seed_from_arc(arc, recent_queries=[], available_paths=available)
    assert admitted >= 1, "cold-start fallback should admit at least one arc scenario"


def test_seed_from_workflow_phase_creates_strategic_scenarios() -> None:
    from vaner.intent.arcs import ConversationArcModel

    f = ExplorationFrontier(min_priority=0.01)
    arc = ConversationArcModel()
    recent_queries = [
        "implement the review pipeline",
        "run code review on the patch",
        "run tests for the patch",
    ]
    available = [
        "src/review_engine.py",
        "tests/test_review_flow.py",
        "src/test_runner.py",
        "docs/architecture.md",
    ]
    admitted = f.seed_from_workflow_phase(arc, recent_queries=recent_queries, available_paths=available)
    assert admitted >= 1
    scenario = f.pop()
    assert scenario is not None
    assert scenario.layer == "strategic"


# ---------------------------------------------------------------------------
# seed_from_patterns
# ---------------------------------------------------------------------------

def test_seed_from_patterns() -> None:
    f = ExplorationFrontier(min_priority=0.01)
    patterns = [
        {
            "predicted_keys": ["file_summary:src/engine.py", "file_summary:src/store.py"],
            "confirmation_count": 5,
            "trigger_keywords": "engine",
        }
    ]
    admitted = f.seed_from_patterns(patterns)
    assert admitted == 1
    s = f.pop()
    assert s is not None
    assert "src/engine.py" in s.file_paths


def test_seed_from_prompt_macros() -> None:
    f = ExplorationFrontier(min_priority=0.01)
    prompt_macros = [
        {
            "macro_key": "run code review",
            "example_query": "run code review on this patch",
            "category": "review",
            "use_count": 4,
            "confidence": 0.9,
        }
    ]
    available = [
        "redacted-doc",
        "src/review_engine.py",
        "src/review_comment_parser.py",
        "tests/test_review_flow.py",
    ]
    admitted = f.seed_from_prompt_macros(prompt_macros, available)
    assert admitted == 1
    scenario = f.pop()
    assert scenario is not None
    assert scenario.source == "pattern"
    assert any("review" in path for path in scenario.file_paths)


# ---------------------------------------------------------------------------
# ExplorationConfig validation
# ---------------------------------------------------------------------------

def test_exploration_config_llm_gate_valid() -> None:
    from vaner.models.config import ExplorationConfig
    cfg = ExplorationConfig(llm_gate="none")
    assert cfg.llm_gate == "none"
    cfg2 = ExplorationConfig(llm_gate="all")
    assert cfg2.llm_gate == "all"


def test_exploration_config_llm_gate_invalid() -> None:
    from pydantic import ValidationError
    from vaner.models.config import ExplorationConfig
    with pytest.raises(ValidationError):
        ExplorationConfig(llm_gate="non-trivial")  # hyphen instead of underscore


def test_exploration_config_presets_have_correct_gates() -> None:
    from vaner.models.config import ExplorationConfig
    assert ExplorationConfig.conservative().llm_gate == "none"
    assert ExplorationConfig.normal().llm_gate == "non_trivial"
    assert ExplorationConfig.aggressive().llm_gate == "non_trivial"
    assert ExplorationConfig.maximum().llm_gate == "all"
