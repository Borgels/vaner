# SPDX-License-Identifier: Apache-2.0
"""WS9 — `deep_run_defaults_for` pure-function tests (0.8.6)."""

from __future__ import annotations

import pytest

from vaner.intent.deep_run_defaults import DeepRunDefaults, deep_run_defaults_for
from vaner.setup.answers import SetupAnswers
from vaner.setup.catalog import PROFILE_CATALOG, bundle_by_id
from vaner.setup.policy import VanerPolicyBundle


def _answers(
    *,
    background_posture: str = "normal",
) -> SetupAnswers:
    """Construct a minimal SetupAnswers for the parametrised tests."""

    return SetupAnswers(
        work_styles=("mixed",),
        priority="balanced",
        compute_posture="balanced",
        cloud_posture="ask_first",
        background_posture=background_posture,  # type: ignore[arg-type]
    )


def test_pure_function_determinism() -> None:
    bundle = bundle_by_id("hybrid_balanced")
    setup = _answers()
    a = deep_run_defaults_for(bundle, setup)
    b = deep_run_defaults_for(bundle, setup)
    assert a == b


def test_local_only_bundle_yields_local_only_locality() -> None:
    bundle = bundle_by_id("local_lightweight")  # local_cloud_posture="local_only"
    defaults = deep_run_defaults_for(bundle, _answers())
    assert defaults.locality == "local_only"


def test_high_spend_yields_higher_cost_cap() -> None:
    low = bundle_by_id("hybrid_balanced")  # spend_profile="low"
    high = bundle_by_id("hybrid_quality")  # spend_profile="medium"
    setup = _answers()
    low_defaults = deep_run_defaults_for(low, setup)
    high_defaults = deep_run_defaults_for(high, setup)
    assert high_defaults.cost_cap_usd > low_defaults.cost_cap_usd


def test_horizon_bias_picks_max_weight_key() -> None:
    base = bundle_by_id("hybrid_balanced")
    # Inject a bundle with a clear winner for likely_next.
    custom = VanerPolicyBundle(
        id=base.id,
        label=base.label,
        description=base.description,
        local_cloud_posture=base.local_cloud_posture,
        runtime_profile=base.runtime_profile,
        spend_profile=base.spend_profile,
        latency_profile=base.latency_profile,
        privacy_profile=base.privacy_profile,
        prediction_horizon_bias={
            "likely_next": 9.0,
            "long_horizon": 1.0,
            "finish_partials": 1.0,
            "balanced": 1.0,
        },
        drafting_aggressiveness=base.drafting_aggressiveness,
        exploration_ratio=base.exploration_ratio,
        persistence_strength=base.persistence_strength,
        goal_weighting=base.goal_weighting,
        context_injection_default=base.context_injection_default,
        deep_run_profile=base.deep_run_profile,
    )
    defaults = deep_run_defaults_for(custom, _answers())
    assert defaults.horizon_bias == "likely_next"


def test_horizon_bias_alphabetical_tie_break() -> None:
    base = bundle_by_id("hybrid_balanced")
    # All four equal — alphabetical order: balanced < finish_partials <
    # likely_next < long_horizon, so "balanced" should win.
    custom = VanerPolicyBundle(
        id=base.id,
        label=base.label,
        description=base.description,
        local_cloud_posture=base.local_cloud_posture,
        runtime_profile=base.runtime_profile,
        spend_profile=base.spend_profile,
        latency_profile=base.latency_profile,
        privacy_profile=base.privacy_profile,
        prediction_horizon_bias={
            "likely_next": 1.0,
            "long_horizon": 1.0,
            "finish_partials": 1.0,
            "balanced": 1.0,
        },
        drafting_aggressiveness=base.drafting_aggressiveness,
        exploration_ratio=base.exploration_ratio,
        persistence_strength=base.persistence_strength,
        goal_weighting=base.goal_weighting,
        context_injection_default=base.context_injection_default,
        deep_run_profile=base.deep_run_profile,
    )
    defaults = deep_run_defaults_for(custom, _answers())
    assert defaults.horizon_bias == "balanced"


def test_aggressive_background_posture_broadens_focus() -> None:
    bundle = bundle_by_id("hybrid_balanced")
    defaults = deep_run_defaults_for(bundle, _answers(background_posture="deep_run_aggressive"))
    assert defaults.focus == "all_recent"


def test_minimal_background_posture_narrows_focus_to_workspace() -> None:
    bundle = bundle_by_id("hybrid_balanced")
    defaults = deep_run_defaults_for(bundle, _answers(background_posture="minimal"))
    assert defaults.focus == "current_workspace"


def test_reasons_explain_each_field() -> None:
    bundle = bundle_by_id("deep_research")
    defaults = deep_run_defaults_for(bundle, _answers(background_posture="deep_run_aggressive"))
    # One reason per derived field: preset, horizon_bias, locality, cost_cap, focus = 5.
    assert len(defaults.reasons) == 5
    joined = " ".join(defaults.reasons).lower()
    assert "preset" in joined
    assert "horizon_bias" in joined
    assert "locality" in joined
    assert "cost_cap_usd" in joined
    assert "focus" in joined


def test_source_bundle_id_round_trips() -> None:
    bundle = bundle_by_id("local_heavy")
    defaults = deep_run_defaults_for(bundle, _answers())
    assert defaults.source_bundle_id == "local_heavy"


@pytest.mark.parametrize("bundle", PROFILE_CATALOG, ids=[b.id for b in PROFILE_CATALOG])
def test_every_catalog_bundle_yields_well_formed_defaults(bundle: VanerPolicyBundle) -> None:
    defaults = deep_run_defaults_for(bundle, _answers())
    assert isinstance(defaults, DeepRunDefaults)
    assert defaults.preset in ("conservative", "balanced", "aggressive")
    assert defaults.horizon_bias in (
        "likely_next",
        "long_horizon",
        "finish_partials",
        "balanced",
    )
    assert defaults.locality in ("local_only", "local_preferred", "allow_cloud")
    assert defaults.focus in ("active_goals", "current_workspace", "all_recent")
    assert defaults.cost_cap_usd >= 0.0
    assert defaults.source_bundle_id == bundle.id


def test_zero_spend_yields_zero_cost_cap() -> None:
    bundle = bundle_by_id("local_lightweight")  # spend_profile="zero"
    defaults = deep_run_defaults_for(bundle, _answers())
    assert defaults.cost_cap_usd == 0.0
