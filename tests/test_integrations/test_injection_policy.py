# SPDX-License-Identifier: Apache-2.0

from vaner.integrations.injection import (
    ContextInjectionMode,
    InjectionInputs,
    should_inject,
)


def _inp(**overrides) -> InjectionInputs:
    defaults = {
        "mode": ContextInjectionMode.POLICY_HYBRID,
        "has_fresh_adopted_package_in_context": False,
        "has_active_predictions": True,
        "top_prediction_is_ready": False,
        "top_prediction_is_fresh": True,
        "top_prediction_confidence": 0.8,
        "current_context_used_fraction": 0.30,
        "max_context_fraction": 0.20,
        "query_is_vaner_relevant": True,
    }
    defaults.update(overrides)
    return InjectionInputs(**defaults)


def test_none_mode_suppresses() -> None:
    d = should_inject(_inp(mode=ContextInjectionMode.NONE))
    assert not (d.emit_digest or d.emit_adopted_package)
    assert d.suppressed_reason == "mode_none"


def test_fresh_adopted_package_suppresses_everything() -> None:
    d = should_inject(_inp(has_fresh_adopted_package_in_context=True))
    assert not d.emit_digest and not d.emit_adopted_package
    assert d.suppressed_reason == "fresh_adopted_package_present"


def test_context_budget_exhaustion_suppresses() -> None:
    d = should_inject(_inp(current_context_used_fraction=0.95, max_context_fraction=0.20))
    assert d.suppressed_reason == "context_budget_exhausted"


def test_irrelevant_turn_suppresses() -> None:
    d = should_inject(_inp(query_is_vaner_relevant=False))
    assert d.suppressed_reason == "irrelevant_for_turn"


def test_digest_only_emits_digest_when_predictions_exist() -> None:
    d = should_inject(_inp(mode=ContextInjectionMode.DIGEST_ONLY))
    assert d.emit_digest
    assert not d.emit_adopted_package


def test_digest_only_suppresses_when_no_predictions() -> None:
    d = should_inject(_inp(mode=ContextInjectionMode.DIGEST_ONLY, has_active_predictions=False))
    assert d.suppressed_reason == "no_active_predictions"


def test_adopted_package_only_requires_ready_fresh() -> None:
    d = should_inject(
        _inp(
            mode=ContextInjectionMode.ADOPTED_PACKAGE_ONLY,
            top_prediction_is_ready=True,
            top_prediction_is_fresh=True,
        )
    )
    assert d.emit_adopted_package


def test_adopted_package_only_suppresses_when_stale() -> None:
    d = should_inject(
        _inp(
            mode=ContextInjectionMode.ADOPTED_PACKAGE_ONLY,
            top_prediction_is_ready=True,
            top_prediction_is_fresh=False,
        )
    )
    assert d.suppressed_reason == "no_fresh_adopted_package"


def test_top_match_auto_include_requires_high_confidence() -> None:
    low = should_inject(
        _inp(
            mode=ContextInjectionMode.TOP_MATCH_AUTO_INCLUDE,
            top_prediction_is_ready=True,
            top_prediction_is_fresh=True,
            top_prediction_confidence=0.5,
        )
    )
    assert low.suppressed_reason == "top_match_confidence_low"
    high = should_inject(
        _inp(
            mode=ContextInjectionMode.TOP_MATCH_AUTO_INCLUDE,
            top_prediction_is_ready=True,
            top_prediction_is_fresh=True,
            top_prediction_confidence=0.9,
        )
    )
    assert high.emit_adopted_package


def test_policy_hybrid_prefers_adopted_when_ready_and_confident() -> None:
    d = should_inject(
        _inp(
            mode=ContextInjectionMode.POLICY_HYBRID,
            top_prediction_is_ready=True,
            top_prediction_is_fresh=True,
            top_prediction_confidence=0.85,
        )
    )
    assert d.emit_adopted_package
    assert not d.emit_digest


def test_policy_hybrid_falls_back_to_digest() -> None:
    d = should_inject(
        _inp(
            mode=ContextInjectionMode.POLICY_HYBRID,
            top_prediction_is_ready=False,
            top_prediction_confidence=0.6,
        )
    )
    assert d.emit_digest
    assert not d.emit_adopted_package


def test_client_controlled_suppresses() -> None:
    d = should_inject(_inp(mode=ContextInjectionMode.CLIENT_CONTROLLED))
    assert d.suppressed_reason == "client_controlled"
