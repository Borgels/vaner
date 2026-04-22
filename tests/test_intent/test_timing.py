# SPDX-License-Identifier: Apache-2.0
"""Tests for the activity timing model that sizes adaptive cycle budgets."""

from __future__ import annotations

from vaner.intent.timing import ActivityTimingModel


def test_empty_model_has_no_signal() -> None:
    model = ActivityTimingModel()
    obs = model.observe(now=1000.0)
    assert obs.ema_gap_seconds is None
    assert obs.estimated_seconds_until_next_prompt is None
    assert obs.sample_count == 0
    assert obs.active_session is False


def test_rebuild_from_history_computes_mean_and_ema() -> None:
    model = ActivityTimingModel(ema_alpha=0.5, active_session_gap_seconds=120.0)
    # Gaps: 10, 10, 20, 20 → mean 15
    timestamps = [100.0, 110.0, 120.0, 140.0, 160.0]
    model.rebuild_from_history(timestamps)
    obs = model.observe(now=165.0)
    assert obs.sample_count == 4
    assert obs.mean_gap_seconds is not None
    assert abs(obs.mean_gap_seconds - 15.0) < 0.01
    assert obs.ema_gap_seconds is not None
    assert obs.active_session is True
    assert obs.last_prompt_age_seconds == 5.0


def test_rebuild_ignores_session_boundary_gaps() -> None:
    """Gaps longer than the active-session threshold are excluded from the EMA."""
    model = ActivityTimingModel(active_session_gap_seconds=60.0)
    # 10s gap, then a 3600s (overnight) gap, then another 10s gap.
    timestamps = [100.0, 110.0, 3710.0, 3720.0]
    model.rebuild_from_history(timestamps)
    obs = model.observe(now=3725.0)
    # Only the two 10s gaps should contribute.
    assert obs.sample_count == 2
    assert abs(obs.mean_gap_seconds - 10.0) < 0.01


def test_record_prompt_updates_ema_live() -> None:
    model = ActivityTimingModel(ema_alpha=0.5)
    model.record_prompt(timestamp=100.0)
    model.record_prompt(timestamp=110.0)  # gap=10
    model.record_prompt(timestamp=120.0)  # gap=10
    obs = model.observe(now=125.0)
    assert obs.ema_gap_seconds is not None
    assert abs(obs.ema_gap_seconds - 10.0) < 0.01
    assert obs.sample_count == 2


def test_eta_shrinks_with_age() -> None:
    """ETA = EMA minus age, clamped to bounds."""
    model = ActivityTimingModel(ema_alpha=0.5, min_gap_floor_seconds=1.0)
    model.rebuild_from_history([100.0, 160.0])  # one 60s gap

    obs_fresh = model.observe(now=160.0)  # age=0
    obs_half = model.observe(now=190.0)  # age=30

    assert obs_fresh.estimated_seconds_until_next_prompt is not None
    assert obs_half.estimated_seconds_until_next_prompt is not None
    assert obs_half.estimated_seconds_until_next_prompt < obs_fresh.estimated_seconds_until_next_prompt


def test_eta_floors_at_min_gap() -> None:
    model = ActivityTimingModel(
        ema_alpha=0.5,
        min_gap_floor_seconds=3.0,
        active_session_gap_seconds=60.0,
    )
    model.rebuild_from_history([100.0, 105.0])  # 5s gap
    # Age well past the active threshold → inactive session → ETA saturated
    # (effectively "plenty of time").
    obs = model.observe(now=200.0)
    assert obs.active_session is False
    # During inactive sessions the residual is forced to the cap, so the ETA
    # clamps to max_gap_cap_seconds rather than to the min floor.
    assert obs.estimated_seconds_until_next_prompt == model.max_gap_cap_seconds


def test_budget_cycle_shrinks_for_active_short_cadence() -> None:
    """Active sessions with short gaps → shorter adaptive budgets."""
    model = ActivityTimingModel(ema_alpha=0.5)
    # Establish a 20s cadence.
    model.rebuild_from_history([100.0, 120.0, 140.0, 160.0])

    active_budget = model.budget_seconds_for_cycle(
        hard_cap_seconds=300.0,
        soft_min_seconds=1.0,
        utilisation_fraction=0.8,
        now=160.0,
    )
    assert active_budget < 300.0  # shrunk below the hard cap
    assert active_budget >= 1.0


def test_budget_expands_back_to_hard_cap_when_idle() -> None:
    model = ActivityTimingModel(ema_alpha=0.5, active_session_gap_seconds=60.0)
    model.rebuild_from_history([100.0, 110.0])  # 10s gap, active baseline
    # Now query far in the future — user has gone idle.
    budget = model.budget_seconds_for_cycle(
        hard_cap_seconds=300.0,
        soft_min_seconds=1.0,
        utilisation_fraction=0.8,
        now=10_000.0,
    )
    assert budget == 300.0


def test_budget_returns_hard_cap_without_history() -> None:
    model = ActivityTimingModel()
    budget = model.budget_seconds_for_cycle(hard_cap_seconds=180.0, soft_min_seconds=5.0, now=1.0)
    assert budget == 180.0


def test_reset_clears_state() -> None:
    model = ActivityTimingModel()
    model.record_prompt(timestamp=100.0)
    model.record_prompt(timestamp=110.0)
    model.reset()
    obs = model.observe(now=120.0)
    assert obs.ema_gap_seconds is None
    assert obs.sample_count == 0


def test_non_monotonic_timestamps_are_ignored() -> None:
    model = ActivityTimingModel()
    # A later timestamp followed by an earlier one — the earlier should be dropped.
    model.rebuild_from_history([100.0, 110.0, 80.0, 120.0])
    obs = model.observe(now=125.0)
    # Gaps accepted: 110-100=10, 120-110=10. Bad sample filtered.
    assert obs.sample_count == 2
