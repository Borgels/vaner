# SPDX-License-Identifier: Apache-2.0
"""WS1 — PROFILE_CATALOG tests (0.8.6)."""

from __future__ import annotations

import pytest

from vaner.setup.catalog import PROFILE_CATALOG, bundle_by_id
from vaner.setup.policy import VanerPolicyBundle


def test_catalog_has_seven_bundles() -> None:
    assert len(PROFILE_CATALOG) == 7


def test_catalog_ids_are_unique() -> None:
    ids = [bundle.id for bundle in PROFILE_CATALOG]
    assert len(ids) == len(set(ids))


def test_catalog_ids_match_spec() -> None:
    expected = {
        "local_lightweight",
        "local_balanced",
        "local_heavy",
        "hybrid_balanced",
        "hybrid_quality",
        "cost_saver",
        "deep_research",
    }
    actual = {bundle.id for bundle in PROFILE_CATALOG}
    assert actual == expected


def test_bundle_by_id_returns_correct_bundle() -> None:
    bundle = bundle_by_id("hybrid_balanced")
    assert isinstance(bundle, VanerPolicyBundle)
    assert bundle.id == "hybrid_balanced"
    assert bundle.label == "Hybrid Balanced"


def test_bundle_by_id_unknown_raises_keyerror() -> None:
    with pytest.raises(KeyError, match="unknown bundle id"):
        bundle_by_id("nonexistent")


def test_every_bundle_has_complete_horizon_bias_mapping() -> None:
    for bundle in PROFILE_CATALOG:
        assert set(bundle.prediction_horizon_bias.keys()) == {
            "likely_next",
            "long_horizon",
            "finish_partials",
            "balanced",
        }, f"bundle {bundle.id} has incomplete horizon-bias mapping"


def test_every_bundle_has_nonempty_label_and_description() -> None:
    for bundle in PROFILE_CATALOG:
        assert bundle.label, f"bundle {bundle.id} has empty label"
        assert bundle.description, f"bundle {bundle.id} has empty description"
