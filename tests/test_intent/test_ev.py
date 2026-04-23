# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from vaner.intent.ev import ev_score, jaccard_reuse

# ---------------------------------------------------------------------------
# jaccard_reuse
# ---------------------------------------------------------------------------


def test_jaccard_reuse_identical():
    assert jaccard_reuse(["a.py", "b.py"], ["a.py", "b.py"]) == pytest.approx(1.0)


def test_jaccard_reuse_disjoint():
    assert jaccard_reuse(["a.py"], ["b.py"]) == pytest.approx(0.0)


def test_jaccard_reuse_partial_overlap():
    result = jaccard_reuse(["a.py", "b.py"], ["b.py", "c.py"])
    # intersection={b.py}, union={a,b,c} → 1/3
    assert result == pytest.approx(1 / 3, abs=1e-6)


def test_jaccard_reuse_both_empty():
    assert jaccard_reuse([], []) == pytest.approx(1.0)


def test_jaccard_reuse_one_empty():
    assert jaccard_reuse(["a.py"], []) == pytest.approx(0.0)
    assert jaccard_reuse([], ["b.py"]) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# ev_score
# ---------------------------------------------------------------------------


def test_ev_score_zero_probability():
    result = ev_score(
        "predict next",
        posterior_p=0.0,
        payoff_seconds=10.0,
        reuse_potential=0.5,
        confidence_gain_per_second=0.1,
    )
    assert result == pytest.approx(0.0)


def test_ev_score_zero_payoff():
    result = ev_score(
        "predict next",
        posterior_p=0.8,
        payoff_seconds=0.0,
        reuse_potential=0.5,
        confidence_gain_per_second=0.1,
    )
    assert result == pytest.approx(0.0)


def test_ev_score_positive():
    result = ev_score(
        "predict next",
        posterior_p=1.0,
        payoff_seconds=1.0,
        reuse_potential=1.0,
        confidence_gain_per_second=1.0,
        temperature=1.0,
    )
    assert result == pytest.approx(1.0)


def test_ev_score_temperature_scales_down():
    base = ev_score(
        "h",
        posterior_p=0.8,
        payoff_seconds=5.0,
        reuse_potential=0.6,
        confidence_gain_per_second=0.2,
        temperature=1.0,
    )
    high_temp = ev_score(
        "h",
        posterior_p=0.8,
        payoff_seconds=5.0,
        reuse_potential=0.6,
        confidence_gain_per_second=0.2,
        temperature=2.0,
    )
    assert high_temp < base


def test_ev_score_negative_probability_clamped():
    result = ev_score(
        "h",
        posterior_p=-1.0,
        payoff_seconds=10.0,
        reuse_potential=0.5,
        confidence_gain_per_second=0.1,
    )
    assert result == pytest.approx(0.0)
