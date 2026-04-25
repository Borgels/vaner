# SPDX-License-Identifier: Apache-2.0

"""Payload shape tests for the extended _serialize_prediction_for_mcp.

0.8.5 WS5 adds card-model fields (readiness_label, eta_bucket, adoptable,
suppression_reason, source_label, ui_summary) + optional rank. These
tests pin the new fields are always present so MCP Apps clients, the
text fallback, and the contract-crate mirrors stay in lockstep.
"""

from __future__ import annotations

import importlib
import importlib.util

import pytest

if importlib.util.find_spec("mcp") is None:  # pragma: no cover - CI matrix dependent
    pytest.skip("mcp package is unavailable in this test environment", allow_module_level=True)

from vaner.intent.prediction import (
    PredictedPrompt,
    PredictionArtifacts,
    PredictionRun,
    PredictionSpec,
    ReadinessState,
    prediction_id,
)

_server = importlib.import_module("vaner.mcp.server")
_serialize_prediction_for_mcp = _server._serialize_prediction_for_mcp


def _prompt(
    *,
    readiness: ReadinessState = "ready",
    briefing: str | None = "prepared summary",
    draft: str | None = None,
) -> PredictedPrompt:
    spec = PredictionSpec(
        id=prediction_id("arc", "anchor", "Draft the project update"),
        label="Draft the project update",
        description="Suspected next move based on recent conversation",
        source="arc",
        anchor="anchor",
        confidence=0.72,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    run = PredictionRun(
        weight=0.5,
        token_budget=2048,
        tokens_used=600,
        scenarios_spawned=4,
        scenarios_complete=3,
        readiness=readiness,
        updated_at=0.0,
    )
    artifacts = PredictionArtifacts(prepared_briefing=briefing, draft_answer=draft)
    return PredictedPrompt(spec=spec, run=run, artifacts=artifacts)


def test_payload_contains_card_fields() -> None:
    payload = _serialize_prediction_for_mcp(_prompt())
    assert payload["readiness_label"] == "Ready"
    assert payload["eta_bucket"] == "ready_now"
    assert payload["eta_bucket_label"] == "Ready now"
    assert payload["adoptable"] is True
    assert payload["suppression_reason"] is None
    assert payload["source_label"] == "Recent work"
    assert payload["ui_summary"]


def test_payload_omits_rank_by_default() -> None:
    payload = _serialize_prediction_for_mcp(_prompt())
    assert "rank" not in payload


def test_payload_includes_rank_when_passed() -> None:
    payload = _serialize_prediction_for_mcp(_prompt(), rank=3)
    assert payload["rank"] == 3


def test_not_adoptable_carries_suppression_reason() -> None:
    payload = _serialize_prediction_for_mcp(_prompt(readiness="queued", briefing=None))
    assert payload["adoptable"] is False
    assert payload["suppression_reason"] == "not_ready_yet"


def test_scenarios_spawned_round_trips() -> None:
    payload = _serialize_prediction_for_mcp(_prompt())
    assert payload["scenarios_spawned"] == 4
    assert payload["scenarios_complete"] == 3
