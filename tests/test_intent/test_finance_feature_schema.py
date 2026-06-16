from __future__ import annotations

import time

from vaner.intent.features import feature_vector_for_artefact
from vaner.intent.trainer import FEATURE_KEYS, FEATURE_SCHEMA_VERSION
from vaner.models.artefact import Artefact, ArtefactKind


def test_finance_feature_slice_is_ex_ante_and_schema_aligned() -> None:
    artefact = Artefact(
        key="finance:candidate",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path="finance",
        source_mtime=time.time(),
        generated_at=time.time(),
        model="unit",
        content="finance candidate",
    )
    features = {
        "finance_public_market_plane": 1.0,
        "finance_horizon_days": 30.0,
        "finance_option_strategy_entry_debit": 2.15,
        "finance_option_strategy_leg_count": 2.0,
        "finance_option_strategy_is_debit_spread": 1.0,
        "finance_option_strategy_min_abs_delta": 0.25,
        "finance_option_strategy_max_abs_delta": 0.45,
        "finance_option_strategy_mean_iv": 0.28,
        "finance_liquidity_penalty": 0.12,
        # Outcome labels must not become runtime model inputs.
        "finance_forward_return": 4.0,
        "finance_risk_adjusted_score": 4.0,
        "finance_should_keep_label": 1.0,
    }

    vector = feature_vector_for_artefact(features, artefact)
    keyed = dict(zip(FEATURE_KEYS, vector, strict=True))

    assert FEATURE_SCHEMA_VERSION == "v5"
    assert len(vector) == len(FEATURE_KEYS)
    assert keyed["finance_option_strategy_entry_debit"] == 2.15
    assert keyed["finance_option_strategy_is_debit_spread"] == 1.0
    assert "finance_forward_return" not in FEATURE_KEYS
    assert "finance_risk_adjusted_score" not in FEATURE_KEYS
    assert "finance_should_keep_label" not in FEATURE_KEYS
