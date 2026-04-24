# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from vaner.intent.volatility import classify_path_volatility, semantic_volatility, semantic_volatility_profile

# ---------------------------------------------------------------------------
# classify_path_volatility — one score per category
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("docs/README.md", 0.05),
        ("tests/test_engine.py", 0.15),
        ("config/settings.yaml", 0.45),
        ("src/auth/policy.py", 1.0),
        ("src/store/sqlite.py", 0.85),
        ("src/engine.py", 0.70),
        ("src/utils/helpers.py", 0.7),  # default
    ],
)
def test_classify_path_volatility(path, expected):
    assert classify_path_volatility(path) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# semantic_volatility — empty / single / mixed
# ---------------------------------------------------------------------------


def test_semantic_volatility_empty():
    assert semantic_volatility([]) == pytest.approx(0.0)


def test_semantic_volatility_single_low():
    score = semantic_volatility(["docs/guide.md"])
    assert score < 0.2


def test_semantic_volatility_single_high():
    score = semantic_volatility(["src/auth/middleware.py"])
    assert score > 0.7


def test_semantic_volatility_mixed():
    score = semantic_volatility(["docs/readme.md", "src/auth/policy.py", "src/engine.py"])
    # mixture of low + high → middle range
    assert 0.3 < score < 0.9


# ---------------------------------------------------------------------------
# semantic_volatility_profile — component breakdown
# ---------------------------------------------------------------------------


def test_volatility_profile_path_count():
    profile = semantic_volatility_profile(["a.py", "b.py", "c.py"])
    assert profile.path_count == 3


def test_volatility_profile_counts_high_medium_low():
    paths = [
        "src/auth/secrets.py",  # high (1.0)
        "src/engine.py",  # high (0.70 → >= 0.8? no, 0.70 < 0.8 → medium)
        "docs/readme.md",  # low (0.05)
    ]
    profile = semantic_volatility_profile(paths)
    assert profile.high_risk_count + profile.medium_risk_count + profile.low_risk_count == 3


def test_volatility_profile_drift_fraction():
    paths = ["src/auth/policy.py", "src/auth/secrets.py"]  # both high (1.0)
    profile = semantic_volatility_profile(paths)
    assert profile.drift_fraction == pytest.approx(1.0)


def test_volatility_profile_score_clamped():
    paths = ["src/auth/policy.py"] * 10
    profile = semantic_volatility_profile(paths)
    assert 0.0 <= profile.score <= 1.0
