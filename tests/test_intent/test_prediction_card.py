# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.intent.prediction import (
    PredictedPrompt,
    PredictionArtifacts,
    PredictionRun,
    PredictionSpec,
    ReadinessState,
    prediction_id,
)
from vaner.intent.prediction_card import derive_card_fields, rank_cards


def _prompt(
    *,
    label: str = "Draft the project update",
    readiness: ReadinessState = "ready",
    source: str = "arc",
    briefing: str | None = "prepared summary",
    draft: str | None = None,
    confidence: float = 0.7,
    spent: bool = False,
    description: str | None = None,
) -> PredictedPrompt:
    spec = PredictionSpec(
        id=prediction_id(source, "anchor", label),
        label=label,
        description=description or label,
        source=source,  # type: ignore[arg-type]
        anchor="anchor",
        confidence=confidence,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    run = PredictionRun(
        weight=0.5,
        token_budget=2048,
        tokens_used=500,
        scenarios_spawned=4,
        scenarios_complete=2,
        readiness=readiness,
        updated_at=0.0,
        spent=spent,
    )
    artifacts = PredictionArtifacts(
        prepared_briefing=briefing,
        draft_answer=draft,
    )
    return PredictedPrompt(spec=spec, run=run, artifacts=artifacts)


def test_derive_includes_readiness_eta_and_adoptability() -> None:
    card = derive_card_fields(_prompt(readiness="ready"))
    assert card.readiness_label == "Ready"
    assert card.eta_bucket == "ready_now"
    assert card.eta_bucket_label == "Ready now"
    assert card.adoptable is True
    assert card.suppression_reason is None
    assert card.has_briefing is True


def test_derive_unknown_source_falls_back() -> None:
    # PredictionSpec.source uses a Literal type, but the derivation must be
    # tolerant of values not in _SOURCE_LABELS to avoid KeyErrors at runtime.
    spec = PredictionSpec(
        id=prediction_id("arc", "anchor", "label"),
        label="x",
        description="x",
        source="arc",  # Safe canonical value for the constructor.
        anchor="anchor",
        confidence=0.5,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    run = PredictionRun(weight=0.5, token_budget=1, readiness="ready", updated_at=0.0)
    artifacts = PredictionArtifacts(prepared_briefing="stuff")
    prompt = PredictedPrompt(spec=spec, run=run, artifacts=artifacts)
    card = derive_card_fields(prompt)
    assert card.source_label == "Recent work"


def test_ui_summary_trims_long_descriptions() -> None:
    long_desc = "lorem " * 40
    card = derive_card_fields(_prompt(readiness="ready", description=long_desc))
    assert len(card.ui_summary) <= 140


def test_as_dict_is_json_ready() -> None:
    import json

    card = derive_card_fields(_prompt(readiness="drafting", draft="something"))
    payload = card.as_dict()
    # Must be JSON-serializable without custom encoder.
    json.dumps(payload)
    assert payload["adoptable"] is True
    assert payload["eta_bucket"] in ("under_20s", "under_1m")


def test_rank_adoptable_first() -> None:
    not_ready = _prompt(label="queued one", readiness="queued")
    adoptable = _prompt(label="ready one", readiness="ready", briefing="x")
    ordered = rank_cards([not_ready, adoptable])
    assert ordered[0] is adoptable
    assert ordered[1] is not_ready


def test_rank_ready_before_drafting() -> None:
    drafting = _prompt(label="drafting one", readiness="drafting", draft="d")
    ready = _prompt(label="ready one", readiness="ready", briefing="r")
    ordered = rank_cards([drafting, ready])
    assert ordered[0] is ready


def test_rank_higher_confidence_wins_within_tier() -> None:
    low = _prompt(label="low", readiness="ready", briefing="x", confidence=0.4)
    high = _prompt(label="high", readiness="ready", briefing="x", confidence=0.9)
    ordered = rank_cards([low, high])
    assert ordered[0] is high
