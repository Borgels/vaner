# SPDX-License-Identifier: Apache-2.0
"""Conformance fixtures match the daemon's type contract.

Every JSON file under ``tests/conformance-fixtures/`` represents a real
shape the daemon emits. The test suite below validates the fixtures
against the daemon's own Pydantic models, so hand-authored drift from
the contract fails loudly.

Rust and Swift consumers read the same files and verify independently
against their own model layers — the contract bridge is the
*shape*, not a shared runtime.

If you intentionally change a response shape, regenerate or edit the
corresponding fixture file in the same PR. See the directory's
README.md for the full list.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vaner.mcp.contracts import Resolution

FIXTURES = Path(__file__).parent.parent / "conformance-fixtures"

PREDICTION_SHAPE_KEYS = {
    "id": str,
    "spec": dict,
    "run": dict,
    "artifacts": dict,
}
SPEC_KEYS = {
    "label": str,
    "source": str,
    "confidence": float,
    "hypothesis_type": str,
    "specificity": str,
    "created_at": float,
}
RUN_KEYS = {
    "weight": float,
    "token_budget": int,
    "tokens_used": int,
    "model_calls": int,
    "scenarios_spawned": int,
    "scenarios_complete": int,
    "readiness": str,
    "updated_at": float,
}
ARTIFACTS_KEYS = {
    "scenario_ids": list,
    "evidence_score": float,
    "has_draft": bool,
    "has_briefing": bool,
    "thinking_trace_count": int,
}


def _assert_shape(row: dict, expected: dict[str, type]) -> None:
    for key, expected_type in expected.items():
        assert key in row, f"missing key: {key}"
        value = row[key]
        if value is None:
            continue
        # float fields accept ints (JSON doesn't distinguish 0 vs 0.0)
        if expected_type is float and isinstance(value, int):
            continue
        assert isinstance(value, expected_type), f"key {key!r} expected {expected_type.__name__}, got {type(value).__name__}"


def _load(name: str) -> dict:
    path = FIXTURES / name
    return json.loads(path.read_text())


def test_predictions_active_envelope_shape():
    body = _load("predictions_active_sample.json")
    assert list(body.keys()) == ["predictions"], "envelope must be single `predictions` key"
    assert isinstance(body["predictions"], list)
    assert len(body["predictions"]) >= 1

    for row in body["predictions"]:
        _assert_shape(row, PREDICTION_SHAPE_KEYS)
        _assert_shape(row["spec"], SPEC_KEYS)
        _assert_shape(row["run"], RUN_KEYS)
        _assert_shape(row["artifacts"], ARTIFACTS_KEYS)
        # Enum-valued strings must be drawn from the documented sets
        assert row["spec"]["source"] in {"arc", "pattern", "llm_branch", "macro", "history", "goal"}
        assert row["spec"]["hypothesis_type"] in {"likely_next", "possible_branch", "long_tail"}
        assert row["spec"]["specificity"] in {"concrete", "category", "anchor"}
        assert row["run"]["readiness"] in {
            "queued",
            "grounding",
            "evidence_gathering",
            "drafting",
            "ready",
            "stale",
        }


def test_predictions_single_shape():
    row = _load("predictions_single_sample.json")
    _assert_shape(row, PREDICTION_SHAPE_KEYS)
    _assert_shape(row["spec"], SPEC_KEYS)
    _assert_shape(row["run"], RUN_KEYS)
    _assert_shape(row["artifacts"], ARTIFACTS_KEYS)


@pytest.mark.parametrize("fixture", ["adopt_response_rich.json", "adopt_response_minimal.json"])
def test_adopt_response_roundtrips_through_pydantic(fixture):
    """The Resolution Pydantic model must accept every shape the
    fixture represents. If validation fails the daemon's `Resolution`
    has drifted and the fixture needs updating (or the drift was
    unintended).

    Roundtrip-by-equality is intentionally NOT asserted — Pydantic
    fills in default fields (e.g. `provenance.memory: null`,
    `metrics: null`) that the fixture omits, and that's fine: Rust
    and Swift consumers use `#[serde(default)]` / `Codable` optionals
    for the same reason. Strict validation + field-presence is the
    contract; byte-identical roundtrip isn't.
    """
    body = _load(fixture)
    resolution = Resolution.model_validate(body)
    # Every fixture key must survive on the decoded object (as a field
    # or exposed attribute).
    for key in body:
        assert hasattr(resolution, key), f"Pydantic dropped attribute: {key}"
    # Spot-check the load-bearing fields — if these drift the client
    # ecosystem breaks.
    assert resolution.intent == body["intent"]
    assert resolution.confidence == body["confidence"]
    assert resolution.resolution_id == body["resolution_id"]
    assert resolution.adopted_from_prediction_id == body["adopted_from_prediction_id"]
    assert resolution.provenance.mode == body["provenance"]["mode"]


@pytest.mark.parametrize(
    "fixture,code",
    [
        ("error_codes/adopt_not_found.json", "not_found"),
        ("error_codes/adopt_engine_unavailable.json", "engine_unavailable"),
        ("error_codes/adopt_invalid_input.json", "invalid_input"),
    ],
)
def test_error_fixture_shape(fixture, code):
    body = _load(fixture)
    assert set(body.keys()) == {"code", "message"}, f"error body must be exactly code+message, got {sorted(body.keys())}"
    assert body["code"] == code
    assert isinstance(body["message"], str) and body["message"]


def test_every_fixture_is_registered():
    """Fail if a new JSON file appears in the fixtures directory
    without a test referencing it — prevents accidental orphans."""
    known = {
        "predictions_active_sample.json",
        "predictions_single_sample.json",
        "adopt_response_rich.json",
        "adopt_response_minimal.json",
        "error_codes/adopt_not_found.json",
        "error_codes/adopt_engine_unavailable.json",
        "error_codes/adopt_invalid_input.json",
    }
    found = {str(p.relative_to(FIXTURES)) for p in FIXTURES.rglob("*.json")}
    orphans = found - known
    missing = known - found
    assert not orphans, f"unregistered fixtures: {sorted(orphans)}"
    assert not missing, f"expected fixtures absent from disk: {sorted(missing)}"
