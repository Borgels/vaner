# SPDX-License-Identifier: Apache-2.0
"""Tests for composer_intent prediction source + invalidation — 0.8.7 WS4.

Validates:

- ``Source`` literal admits ``"composer_intent"``.
- ``PredictionRun.compose_signal_strength`` defaults to 0.0 (existing
  call sites byte-identical).
- ``build_composer_cancelled_signal`` and ``build_composer_abandoned_signal``
  produce well-formed InvalidationSignal records.
- ``apply_invalidation_signals`` stales every ``composer_intent``-sourced
  prediction whose ``spec.anchor`` matches the cancelled / abandoned
  ``session_id``.
- The new branch DOES NOT touch non-``composer_intent`` predictions.
- The artefact_item-only guards at prediction_registry.py lines 622, 685
  remain at their previous occurrence count (regression guard).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from vaner.intent.invalidation import (
    InvalidationSignal,
    build_composer_abandoned_signal,
    build_composer_cancelled_signal,
)
from vaner.intent.prediction import (
    PredictionRun,
    PredictionSpec,
    prediction_id,
)
from vaner.intent.prediction_registry import PredictionRegistry


def _composer_spec(
    *,
    session_id: str,
    label: str = "rewrite the handler",
    confidence: float = 0.7,
) -> PredictionSpec:
    pid = prediction_id("composer_intent", session_id, label)
    return PredictionSpec(
        id=pid,
        label=label,
        description=label,
        source="composer_intent",
        anchor=session_id,
        confidence=confidence,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )


def _arc_spec(*, anchor: str = "tests/handler") -> PredictionSpec:
    pid = prediction_id("arc", anchor, "tests")
    return PredictionSpec(
        id=pid,
        label="tests",
        description="tests",
        source="arc",
        anchor=anchor,
        confidence=0.8,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )


class TestComposerIntentSourceLiteral:
    def test_composer_spec_constructs(self) -> None:
        spec = _composer_spec(session_id="s1")
        assert spec.source == "composer_intent"
        assert spec.anchor == "s1"

    def test_compose_signal_strength_defaults_to_zero(self) -> None:
        run = PredictionRun(weight=0.5, token_budget=1000)
        assert run.compose_signal_strength == 0.0

    def test_compose_signal_strength_is_writable(self) -> None:
        run = PredictionRun(weight=0.5, token_budget=1000)
        run.compose_signal_strength = 0.85
        assert run.compose_signal_strength == 0.85


class TestInvalidationBuilders:
    def test_cancelled_builder_shape(self) -> None:
        sig = build_composer_cancelled_signal("session-abc")
        assert isinstance(sig, InvalidationSignal)
        assert sig.kind == "composer_cancelled"
        assert sig.payload == {"session_id": "session-abc"}

    def test_abandoned_builder_shape(self) -> None:
        sig = build_composer_abandoned_signal("session-xyz")
        assert isinstance(sig, InvalidationSignal)
        assert sig.kind == "composer_abandoned"
        assert sig.payload == {"session_id": "session-xyz"}


class TestRegistryInvalidation:
    def test_cancelled_stales_matching_composer_intent_prediction(self) -> None:
        registry = PredictionRegistry()
        spec = _composer_spec(session_id="s1")
        registry.enroll(spec, initial_weight=0.5)

        outcomes = registry.apply_invalidation_signals([build_composer_cancelled_signal("s1")])

        assert outcomes.get(spec.id) == "staled"
        prompt = registry.get(spec.id)
        assert prompt is not None
        assert prompt.run.readiness == "stale"
        assert prompt.run.invalidation_reason.startswith("composer_cancelled:")

    def test_abandoned_stales_matching_composer_intent_prediction(self) -> None:
        registry = PredictionRegistry()
        spec = _composer_spec(session_id="s2")
        registry.enroll(spec, initial_weight=0.5)

        outcomes = registry.apply_invalidation_signals([build_composer_abandoned_signal("s2")])

        assert outcomes.get(spec.id) == "staled"
        prompt = registry.get(spec.id)
        assert prompt is not None
        assert prompt.run.readiness == "stale"
        assert prompt.run.invalidation_reason.startswith("composer_abandoned:")

    def test_session_mismatch_leaves_prediction_alone(self) -> None:
        registry = PredictionRegistry()
        spec = _composer_spec(session_id="s1")
        registry.enroll(spec, initial_weight=0.5)

        outcomes = registry.apply_invalidation_signals([build_composer_cancelled_signal("different-session")])

        assert spec.id not in outcomes
        prompt = registry.get(spec.id)
        assert prompt is not None
        assert prompt.run.readiness != "stale"

    def test_non_composer_predictions_are_untouched(self) -> None:
        registry = PredictionRegistry()
        composer_spec = _composer_spec(session_id="s1")
        arc_spec = _arc_spec(anchor="s1")  # same anchor, different source
        registry.enroll(composer_spec, initial_weight=0.5)
        registry.enroll(arc_spec, initial_weight=0.5)

        outcomes = registry.apply_invalidation_signals([build_composer_cancelled_signal("s1")])

        # Composer spec stales, arc spec untouched.
        assert outcomes.get(composer_spec.id) == "staled"
        assert arc_spec.id not in outcomes
        arc_prompt = registry.get(arc_spec.id)
        assert arc_prompt is not None
        assert arc_prompt.run.readiness != "stale"

    def test_empty_session_id_is_a_noop(self) -> None:
        registry = PredictionRegistry()
        spec = _composer_spec(session_id="s1")
        registry.enroll(spec, initial_weight=0.5)

        outcomes = registry.apply_invalidation_signals([InvalidationSignal(kind="composer_cancelled", payload={"session_id": ""})])

        assert outcomes == {}


class TestArtefactItemGuardsUnchanged:
    """Regression guard for plan §risk-2.

    The artefact_item-only guards at prediction_registry.py lines 622 and
    685 are correctly exclude-paths that bypass non-artefact_item sources.
    Adding ``composer_intent`` must not require touching them, so this
    test pins their occurrence count in place. A future refactor that
    rewrites these guards to enumerate sources would have to either keep
    composer_intent excluded (correct) or add explicit handling — either
    way, this test forces the diff to be intentional.
    """

    def test_artefact_item_string_appears_exactly_twice(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        target = repo / "src" / "vaner" / "intent"
        assert target.is_dir()
        result = subprocess.run(
            ["git", "grep", "-n", '"artefact_item"', str(target.relative_to(repo))],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
        # git grep returns 1 when no matches, 0 otherwise. Both are fine.
        assert result.returncode in (0, 1)
        # Filter to runtime paths (the prediction_registry guards live in
        # the registry module). The `Source` literal also names
        # "artefact_item" in prediction.py — that's a one-line definition,
        # not a guard. The two guard sites are in prediction_registry.py.
        guard_hits = [
            line for line in result.stdout.splitlines() if "/prediction_registry.py:" in line and 'spec.source != "artefact_item"' in line
        ]
        assert len(guard_hits) == 2, f"expected 2 artefact_item-only guards in prediction_registry.py; got {len(guard_hits)}: {guard_hits}"
