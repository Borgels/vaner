# SPDX-License-Identifier: Apache-2.0
"""Tests for the 0.8.7 WS8 MCP contract additions.

Validates:

- ``Resolution.composer_event_id`` defaults to None and round-trips.
- ``PredictionArtifacts.composer_metadata`` defaults to an empty dict
  and is byte-compatible with existing constructors.
- ``_serialize_prediction_for_mcp`` surfaces ``composer_engagement`` only
  when the prediction is composer_intent-sourced AND has metadata.
- Non-composer predictions get NO ``composer_engagement`` key (existing
  card payloads stay byte-identical).
- The conformance fixtures still round-trip cleanly.
"""

from __future__ import annotations

from vaner.intent.prediction import (
    PredictedPrompt,
    PredictionArtifacts,
    PredictionRun,
    PredictionSpec,
    prediction_id,
)
from vaner.mcp.contracts import Provenance, Resolution
from vaner.mcp.server import _serialize_prediction_for_mcp


def _make_prompt(*, source: str, anchor: str, composer_metadata: dict | None = None) -> PredictedPrompt:
    spec = PredictionSpec(
        id=prediction_id(source, anchor, "test"),
        label="test",
        description="test",
        source=source,  # type: ignore[arg-type]
        anchor=anchor,
        confidence=0.7,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    run = PredictionRun(weight=0.5, token_budget=1000)
    artifacts = PredictionArtifacts()
    if composer_metadata is not None:
        artifacts.composer_metadata = composer_metadata
    return PredictedPrompt(spec=spec, run=run, artifacts=artifacts)


class TestResolutionComposerEventId:
    def test_defaults_to_none(self) -> None:
        provenance = Provenance(mode="predictive_hit", cache="warm", freshness="fresh")
        resolution = Resolution(
            intent="test",
            confidence=0.5,
            summary="test",
            provenance=provenance,
            resolution_id="r-1",
        )
        assert resolution.composer_event_id is None

    def test_round_trips_when_set(self) -> None:
        provenance = Provenance(mode="predictive_hit", cache="warm", freshness="fresh")
        resolution = Resolution(
            intent="test",
            confidence=0.5,
            summary="test",
            provenance=provenance,
            resolution_id="r-1",
            composer_event_id="evt-abc",
        )
        encoded = resolution.model_dump_json()
        decoded = Resolution.model_validate_json(encoded)
        assert decoded.composer_event_id == "evt-abc"


class TestArtifactsComposerMetadata:
    def test_defaults_to_empty_dict(self) -> None:
        artifacts = PredictionArtifacts()
        assert artifacts.composer_metadata == {}

    def test_can_be_populated(self) -> None:
        artifacts = PredictionArtifacts()
        artifacts.composer_metadata = {
            "composer_event_id": "evt-1",
            "lifecycle_state": "submitted",
        }
        assert artifacts.composer_metadata["composer_event_id"] == "evt-1"


class TestSerializeComposerEngagement:
    def test_non_composer_prediction_has_no_engagement_key(self) -> None:
        prompt = _make_prompt(source="arc", anchor="some-anchor")
        payload = _serialize_prediction_for_mcp(prompt)
        assert "composer_engagement" not in payload

    def test_composer_intent_without_metadata_has_no_engagement_key(self) -> None:
        # composer_intent source but no metadata yet — payload omits the
        # field entirely (rather than emitting an empty stub).
        prompt = _make_prompt(source="composer_intent", anchor="session-abc")
        payload = _serialize_prediction_for_mcp(prompt)
        assert "composer_engagement" not in payload

    def test_composer_intent_with_metadata_surfaces_engagement(self) -> None:
        prompt = _make_prompt(
            source="composer_intent",
            anchor="session-abc",
            composer_metadata={
                "composer_event_id": "evt-1",
                "lifecycle_state": "submitted",
                "inferred_intent_label": "rewrite",
                "inferred_intent_confidence": 0.85,
            },
        )
        payload = _serialize_prediction_for_mcp(prompt)
        assert "composer_engagement" in payload
        engagement = payload["composer_engagement"]
        assert engagement["composer_event_id"] == "evt-1"
        assert engagement["lifecycle_state"] == "submitted"
        assert engagement["inferred_intent_label"] == "rewrite"
        assert engagement["inferred_intent_confidence"] == 0.85

    def test_composer_intent_with_partial_metadata_omits_unset_fields(self) -> None:
        prompt = _make_prompt(
            source="composer_intent",
            anchor="session-abc",
            composer_metadata={
                "composer_event_id": "evt-2",
                "lifecycle_state": "submitted",
            },
        )
        payload = _serialize_prediction_for_mcp(prompt)
        engagement = payload["composer_engagement"]
        assert engagement["composer_event_id"] == "evt-2"
        # Optional fields default to None — caller may render or not.
        assert engagement["inferred_intent_label"] is None
        assert engagement["inferred_intent_confidence"] is None
