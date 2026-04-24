# SPDX-License-Identifier: Apache-2.0
"""0.8.2 WS2 — frontier ``artefact_alignment`` scoring term.

Asserts that scenarios whose file_paths intersect the configured
artefact-aligned path set earn the push-time multiplicative boost.
"""

from __future__ import annotations

from vaner.intent.frontier import ExplorationFrontier, ExplorationScenario


def _make_scenario(
    *,
    sid: str,
    paths: list[str],
    priority: float,
    source: str = "graph",
) -> ExplorationScenario:
    return ExplorationScenario(
        id=sid,
        file_paths=paths,
        anchor=paths[0] if paths else "",
        source=source,
        priority=priority,
        depth=0,
    )


def test_alignment_boost_applied_when_paths_overlap() -> None:
    frontier = ExplorationFrontier(max_depth=3, max_size=100, min_priority=0.0)
    frontier.set_artefact_aligned_paths({"src/a.py"})

    scenario = _make_scenario(sid="s1", paths=["src/a.py"], priority=0.5)
    admitted = frontier.push(scenario)
    assert admitted
    # Peek the pending pool to confirm the boost landed.
    pending = next(iter(frontier._pending.values()))
    # The pending record carries the *effective* priority (post-multiplier).
    # The source multiplier for "graph" defaults to 1.0, so any increase
    # beyond 0.5 is from the alignment boost.
    assert pending.priority > 0.5


def test_alignment_no_boost_when_paths_disjoint() -> None:
    frontier = ExplorationFrontier(max_depth=3, max_size=100, min_priority=0.0)
    frontier.set_artefact_aligned_paths({"src/a.py"})
    scenario = _make_scenario(sid="s2", paths=["src/unrelated.py"], priority=0.5)
    assert frontier.push(scenario)
    pending = next(iter(frontier._pending.values()))
    # No alignment term → priority equals the source multiplier × raw
    # priority, which for a graph source is 1.0 × 0.5 = 0.5.
    assert pending.priority == 0.5


def test_alignment_disabled_when_path_set_empty() -> None:
    frontier = ExplorationFrontier(max_depth=3, max_size=100, min_priority=0.0)
    # Default: no artefact alignment configured.
    scenario = _make_scenario(sid="s3", paths=["src/a.py"], priority=0.5)
    assert frontier.push(scenario)
    pending = next(iter(frontier._pending.values()))
    assert pending.priority == 0.5


def test_alignment_boost_configurable_via_setter() -> None:
    frontier = ExplorationFrontier(max_depth=3, max_size=100, min_priority=0.0)
    frontier.set_artefact_aligned_paths({"src/a.py"}, boost=1.5)
    scenario = _make_scenario(sid="s4", paths=["src/a.py"], priority=0.5)
    assert frontier.push(scenario)
    pending = next(iter(frontier._pending.values()))
    assert pending.priority == 0.75  # 0.5 * 1.5


def test_alignment_boost_floor_is_1() -> None:
    """Passing a boost < 1.0 should not demote aligned scenarios."""

    frontier = ExplorationFrontier(max_depth=3, max_size=100, min_priority=0.0)
    frontier.set_artefact_aligned_paths({"src/a.py"}, boost=0.5)
    scenario = _make_scenario(sid="s5", paths=["src/a.py"], priority=0.5)
    assert frontier.push(scenario)
    pending = next(iter(frontier._pending.values()))
    # Boost is clamped to 1.0 → priority stays at 0.5.
    assert pending.priority == 0.5
