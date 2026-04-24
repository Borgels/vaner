# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

import pytest

from vaner.intent.profile import UserProfile

# ---------------------------------------------------------------------------
# observe() — EMA accumulation and mode tracking
# ---------------------------------------------------------------------------


def test_observe_pace_ema_updates():
    profile = UserProfile()
    t0 = time.time()
    profile.observe(mode="implement", depth=3, ts=t0)
    profile.observe(mode="implement", depth=3, ts=t0 + 60.0)
    assert profile.pace_ema_seconds > 0.0


def test_observe_first_query_sets_pace():
    profile = UserProfile()
    t0 = time.time()
    profile.observe(mode="debug", depth=2, ts=t0)
    profile.observe(mode="debug", depth=2, ts=t0 + 120.0)
    assert profile.pace_ema_seconds == pytest.approx(120.0, rel=0.01)


def test_observe_mode_mix_normalised():
    profile = UserProfile()
    t = time.time()
    profile.observe(mode="implement", depth=2, ts=t)
    profile.observe(mode="debug", depth=2, ts=t + 10)
    profile.observe(mode="implement", depth=2, ts=t + 20)
    assert sum(profile.mode_mix.values()) == pytest.approx(1.0, abs=1e-6)
    assert profile.mode_mix["implement"] > profile.mode_mix["debug"]


def test_observe_depth_preference_running_average():
    profile = UserProfile()
    t = time.time()
    profile.observe(mode="plan", depth=10, ts=t)
    profile.observe(mode="plan", depth=2, ts=t + 5)
    assert profile.depth_preference == pytest.approx(6.0, abs=0.01)


def test_observe_pivot_rate_increases_on_mode_change():
    profile = UserProfile()
    t = time.time()
    profile.observe(mode="implement", depth=1, ts=t)
    profile.observe(mode="debug", depth=1, ts=t + 5)
    assert profile.pivot_rate > 0.0


def test_observe_no_pivot_same_mode():
    profile = UserProfile()
    t = time.time()
    profile.observe(mode="implement", depth=1, ts=t)
    profile.observe(mode="implement", depth=1, ts=t + 5)
    assert profile.pivot_rate == pytest.approx(0.0)


# Persistence tests live in tests/test_store/test_profile_store.py
# (load/save moved out of UserProfile into UserProfileStore in 0.8.0).
