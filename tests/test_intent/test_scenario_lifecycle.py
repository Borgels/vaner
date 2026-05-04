from __future__ import annotations

from vaner.intent.scenario_lifecycle import compute_scenario_lifecycle
from vaner.models.scenario import EvidenceRef, Scenario


def test_old_unsupported_scenario_archives_from_live_map() -> None:
    now = 10_000.0
    scenario = Scenario(
        id="old",
        kind="change",
        score=0.92,
        confidence=0.92,
        freshness="stale",
        created_at=0.0,
        last_refreshed_at=1_000.0,
        last_reinforced_at=1_000.0,
    )

    lifecycle = compute_scenario_lifecycle(scenario, now=now)

    assert lifecycle.relevance < 0.40
    assert lifecycle.visibility == "archived"
    assert lifecycle.lifecycle_motion == "fading"


def test_ready_prepared_context_can_stay_visible_while_aging() -> None:
    now = 10_000.0
    scenario = Scenario(
        id="ready",
        kind="change",
        score=0.88,
        confidence=0.88,
        freshness="stale",
        prepared_context="Prepared context",
        evidence=[EvidenceRef(key="a", source_path="src/a.py", weight=0.8)],
        created_at=0.0,
        last_refreshed_at=1_000.0,
        last_reinforced_at=1_000.0,
    )

    lifecycle = compute_scenario_lifecycle(scenario, now=now)

    assert lifecycle.readiness == "ready"
    assert lifecycle.visibility != "archived"


def test_pinned_scenario_does_not_archive_but_shows_cooling() -> None:
    now = 10_000.0
    scenario = Scenario(
        id="pinned",
        kind="research",
        score=0.55,
        confidence=0.55,
        freshness="stale",
        pinned=1,
        created_at=0.0,
        last_refreshed_at=1_000.0,
        last_reinforced_at=1_000.0,
    )

    lifecycle = compute_scenario_lifecycle(scenario, now=now)

    assert lifecycle.visibility == "cooling"
    assert lifecycle.archived_at is None
    assert "pinned" in lifecycle.visibility_reason
