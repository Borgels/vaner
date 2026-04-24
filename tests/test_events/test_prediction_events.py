# SPDX-License-Identifier: Apache-2.0
"""Tests for typed prediction events + SnapshotRebuilder.

Verifies that:
  1. PredictionRegistry emits events in the order the state-machine allows.
  2. Typed events preserve all relevant payload fields.
  3. SnapshotRebuilder replays the log into a snapshot matching registry.all().
"""

from __future__ import annotations

import pytest

from vaner.events.predictions import (
    PredictionArtifactAdded,
    PredictionEnrolled,
    PredictionProgress,
    PredictionReadinessChanged,
    PredictionStaled,
    SnapshotRebuilder,
    event_from_registry,
)
from vaner.intent.prediction import PredictionSpec, prediction_id
from vaner.intent.prediction_registry import PredictionEvent, PredictionRegistry


def _spec(label: str, *, confidence: float = 0.7, source: str = "arc") -> PredictionSpec:
    return PredictionSpec(
        id=prediction_id(source, "a", label),
        label=label,
        description=f"{label} description",
        source=source,  # type: ignore[arg-type]
        anchor="a",
        confidence=confidence,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )


class _Recorder:
    def __init__(self) -> None:
        self.events: list[PredictionEvent] = []

    def __call__(self, event: PredictionEvent) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# Event emission order
# ---------------------------------------------------------------------------


def test_registry_emits_events_in_state_machine_order():
    listener = _Recorder()
    reg = PredictionRegistry(cycle_token_pool=1_000, listener=listener)
    spec = _spec("A")
    reg.enroll(spec, initial_weight=1.0)
    reg.attach_scenario(spec.id, "scen-1")  # queued -> grounding
    reg.record_call(spec.id, tokens_used=50)
    reg.record_evidence(spec.id, delta_score=0.3)
    reg.transition(spec.id, "evidence_gathering")
    reg.attach_artifact(spec.id, draft="hello")
    reg.transition(spec.id, "drafting")
    reg.attach_artifact(spec.id, briefing="brief")
    reg.transition(spec.id, "ready")

    kinds = [e.kind for e in listener.events]
    # Ordering: enrolled first
    assert kinds[0] == "prediction.enrolled"
    # First readiness change is queued -> grounding (triggered by attach_scenario)
    first_change = next(i for i, k in enumerate(kinds) if k == "prediction.readiness_changed")
    assert listener.events[first_change].payload["from_state"] == "queued"
    assert listener.events[first_change].payload["to_state"] == "grounding"
    # Final readiness change is drafting -> ready
    last_change = len(kinds) - 1 - list(reversed(kinds)).index("prediction.readiness_changed")
    assert listener.events[last_change].payload["to_state"] == "ready"


# ---------------------------------------------------------------------------
# Typed-event conversion
# ---------------------------------------------------------------------------


def test_event_from_registry_produces_typed_variants():
    listener = _Recorder()
    reg = PredictionRegistry(cycle_token_pool=2_000, listener=listener)
    spec = _spec("A")
    reg.enroll(spec, initial_weight=1.0)
    reg.attach_scenario(spec.id, "scen-1")
    reg.record_call(spec.id, tokens_used=30)
    reg.attach_artifact(spec.id, draft="x")
    reg.transition(spec.id, "stale", reason="user turn")

    typed = [event_from_registry(e) for e in listener.events]
    enrolled = [e for e in typed if isinstance(e, PredictionEnrolled)]
    readiness = [e for e in typed if isinstance(e, PredictionReadinessChanged)]
    progress = [e for e in typed if isinstance(e, PredictionProgress)]
    artifacts = [e for e in typed if isinstance(e, PredictionArtifactAdded)]
    staled = [e for e in typed if isinstance(e, PredictionStaled)]

    assert len(enrolled) == 1
    assert enrolled[0].source == "arc"
    assert enrolled[0].label == "A"
    assert enrolled[0].token_budget > 0
    # Two readiness changes: queued->grounding, grounding->stale
    assert len(readiness) == 2
    assert readiness[-1].to_state == "stale"
    # At least one progress event from record_call
    assert progress and progress[0].tokens_used == 30
    # Artifact added: draft
    assert any(a.kind == "draft" for a in artifacts)
    # Staled event
    assert staled and staled[0].reason == "user turn"


def test_unknown_event_kind_errors():
    bad = PredictionEvent(kind="prediction.unknown", prediction_id="x", payload={}, ts=0.0)
    with pytest.raises(ValueError, match="unknown prediction event kind"):
        event_from_registry(bad)


# ---------------------------------------------------------------------------
# SnapshotRebuilder equivalence with registry state
# ---------------------------------------------------------------------------


def test_snapshot_rebuilder_matches_registry_active_snapshot():
    listener = _Recorder()
    reg = PredictionRegistry(cycle_token_pool=1_000, listener=listener)

    a = _spec("A", source="arc")
    b = _spec("B", source="pattern")
    reg.enroll_batch([a, b])
    reg.attach_scenario(a.id, "scen-a")
    reg.record_call(a.id, tokens_used=40)
    reg.transition(a.id, "evidence_gathering")
    reg.transition(b.id, "stale", reason="tmp")

    typed = [event_from_registry(e) for e in listener.events]
    rebuilder = SnapshotRebuilder()
    rebuilder.apply_many(typed)

    rebuilt_active = {p.id: p for p in rebuilder.active()}
    registry_active = {p.id: p for p in reg.active()}
    assert set(rebuilt_active.keys()) == set(registry_active.keys())
    for pid, rebuilt in rebuilt_active.items():
        ref = registry_active[pid]
        assert rebuilt.run.readiness == ref.run.readiness
        assert rebuilt.run.tokens_used == ref.run.tokens_used
        assert rebuilt.run.token_budget == ref.run.token_budget


def test_snapshot_rebuilder_ignores_orphan_events():
    rebuilder = SnapshotRebuilder()
    # Apply a progress event for a prediction that was never enrolled.
    orphan = PredictionProgress(
        prediction_id="unknown",
        ts=0.0,
        tokens_used=100,
        token_budget=200,
        scenarios_complete=1,
        evidence_score=0.5,
        model_calls=1,
    )
    rebuilder.apply(orphan)  # should be a silent no-op
    assert rebuilder.snapshot() == []
    assert rebuilder.active() == []


def test_snapshot_rebuilder_tracks_terminal_stale_correctly():
    listener = _Recorder()
    reg = PredictionRegistry(cycle_token_pool=1_000, listener=listener)
    a = _spec("A")
    reg.enroll(a, initial_weight=1.0)
    reg.transition(a.id, "stale")

    typed = [event_from_registry(e) for e in listener.events]
    rebuilder = SnapshotRebuilder()
    rebuilder.apply_many(typed)

    # Snapshot shows the stale prediction; active() excludes it.
    assert len(rebuilder.snapshot()) == 1
    assert rebuilder.snapshot()[0].run.readiness == "stale"
    assert rebuilder.active() == []
