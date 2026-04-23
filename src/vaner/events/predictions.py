# SPDX-License-Identifier: Apache-2.0
"""Typed events for the Phase 4 prediction registry.

The registry emits a generic ``PredictionEvent(kind, prediction_id, payload, ts)``.
This module defines the typed dataclasses each ``kind`` corresponds to, plus a
small ``SnapshotRebuilder`` that replays an event log and reconstructs a
snapshot compatible with ``PredictionRegistry.all()``.

The typed surface is what Phase C's HTTP SSE stage, MCP tools, and Phase D's
desktop/cockpit panes consume. Using a typed schema lets the UI evolve without
inspecting dict keys at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from vaner.intent.prediction import (
    PredictedPrompt,
    PredictionArtifacts,
    PredictionRun,
    PredictionSpec,
    ReadinessState,
)
from vaner.intent.prediction_registry import PredictionEvent

EventKind = Literal[
    "prediction.enrolled",
    "prediction.readiness_changed",
    "prediction.progress",
    "prediction.artifact_added",
    "prediction.staled",
]


@dataclass(frozen=True, slots=True)
class PredictionEnrolled:
    prediction_id: str
    ts: float
    label: str
    source: str
    confidence: float
    weight: float
    token_budget: int
    hypothesis_type: str
    specificity: str


@dataclass(frozen=True, slots=True)
class PredictionReadinessChanged:
    prediction_id: str
    ts: float
    from_state: ReadinessState
    to_state: ReadinessState
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PredictionProgress:
    prediction_id: str
    ts: float
    tokens_used: int
    token_budget: int
    scenarios_complete: int
    evidence_score: float
    model_calls: int


@dataclass(frozen=True, slots=True)
class PredictionArtifactAdded:
    prediction_id: str
    ts: float
    kind: Literal["scenario", "draft", "briefing", "thinking"]


@dataclass(frozen=True, slots=True)
class PredictionStaled:
    prediction_id: str
    ts: float
    reason: str = ""


TypedPredictionEvent = PredictionEnrolled | PredictionReadinessChanged | PredictionProgress | PredictionArtifactAdded | PredictionStaled


def event_from_registry(event: PredictionEvent) -> TypedPredictionEvent:
    """Convert a registry-emitted generic event into its typed counterpart."""
    payload = event.payload or {}
    if event.kind == "prediction.enrolled":
        return PredictionEnrolled(
            prediction_id=event.prediction_id,
            ts=event.ts,
            label=str(payload.get("label", "")),
            source=str(payload.get("source", "")),
            confidence=float(payload.get("confidence", 0.0)),
            weight=float(payload.get("weight", 0.0)),
            token_budget=int(payload.get("token_budget", 0)),
            hypothesis_type=str(payload.get("hypothesis_type", "possible_branch")),
            specificity=str(payload.get("specificity", "category")),
        )
    if event.kind == "prediction.readiness_changed":
        return PredictionReadinessChanged(
            prediction_id=event.prediction_id,
            ts=event.ts,
            from_state=str(payload.get("from_state", "queued")),  # type: ignore[arg-type]
            to_state=str(payload.get("to_state", "queued")),  # type: ignore[arg-type]
            reason=str(payload.get("reason", "")),
        )
    if event.kind == "prediction.progress":
        return PredictionProgress(
            prediction_id=event.prediction_id,
            ts=event.ts,
            tokens_used=int(payload.get("tokens_used", 0)),
            token_budget=int(payload.get("token_budget", 0)),
            scenarios_complete=int(payload.get("scenarios_complete", 0)),
            evidence_score=float(payload.get("evidence_score", 0.0)),
            model_calls=int(payload.get("model_calls", 0)),
        )
    if event.kind == "prediction.artifact_added":
        kind = str(payload.get("kind", "scenario"))
        return PredictionArtifactAdded(
            prediction_id=event.prediction_id,
            ts=event.ts,
            kind=kind,  # type: ignore[arg-type]
        )
    if event.kind == "prediction.staled":
        return PredictionStaled(
            prediction_id=event.prediction_id,
            ts=event.ts,
            reason=str(payload.get("reason", "")),
        )
    raise ValueError(f"unknown prediction event kind: {event.kind!r}")


# ---------------------------------------------------------------------------
# Snapshot rebuilder — derived-state view from the event log
# ---------------------------------------------------------------------------


@dataclass
class _PartialPrediction:
    """Minimal accumulator used by the snapshot rebuilder.

    The snapshot sees only enrolled + progress + readiness events — it does
    not hold the spec/artifact details the registry itself stores, so it
    reconstructs a lean view sufficient for HTTP/MCP surfaces.
    """

    prediction_id: str
    label: str
    source: str
    confidence: float
    weight: float
    token_budget: int
    tokens_used: int = 0
    model_calls: int = 0
    scenarios_complete: int = 0
    evidence_score: float = 0.0
    readiness: ReadinessState = "queued"
    hypothesis_type: str = "possible_branch"
    specificity: str = "category"
    artifact_kinds: set[str] = field(default_factory=set)
    updated_at: float = 0.0

    def as_predicted_prompt(self) -> PredictedPrompt:
        spec = PredictionSpec(
            id=self.prediction_id,
            label=self.label,
            description="",
            source=self.source,  # type: ignore[arg-type]
            anchor="",
            confidence=self.confidence,
            hypothesis_type=self.hypothesis_type,  # type: ignore[arg-type]
            specificity=self.specificity,  # type: ignore[arg-type]
            created_at=self.updated_at,
        )
        run = PredictionRun(
            weight=self.weight,
            token_budget=self.token_budget,
            tokens_used=self.tokens_used,
            model_calls=self.model_calls,
            scenarios_complete=self.scenarios_complete,
            readiness=self.readiness,
            updated_at=self.updated_at,
        )
        artifacts = PredictionArtifacts(
            scenario_ids=[],
            evidence_score=self.evidence_score,
        )
        return PredictedPrompt(spec=spec, run=run, artifacts=artifacts)


class SnapshotRebuilder:
    """Replay a sequence of typed prediction events into a snapshot.

    This is the symmetric to the registry's emit: a pure-function view that
    HTTP clients, MCP tools, or the desktop app can derive locally without
    server round trips. The resulting snapshot matches the shape the registry
    exposes via ``active()`` — enough for UI and adoption decisions.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, _PartialPrediction] = {}

    def apply(self, event: TypedPredictionEvent) -> None:
        if isinstance(event, PredictionEnrolled):
            self._by_id[event.prediction_id] = _PartialPrediction(
                prediction_id=event.prediction_id,
                label=event.label,
                source=event.source,
                confidence=event.confidence,
                weight=event.weight,
                token_budget=event.token_budget,
                hypothesis_type=event.hypothesis_type,
                specificity=event.specificity,
                updated_at=event.ts,
            )
            return
        partial = self._by_id.get(event.prediction_id)
        if partial is None:
            # Out-of-order or filtered log — skip.
            return
        partial.updated_at = max(partial.updated_at, event.ts)
        if isinstance(event, PredictionReadinessChanged):
            partial.readiness = event.to_state
        elif isinstance(event, PredictionProgress):
            partial.tokens_used = event.tokens_used
            partial.token_budget = event.token_budget
            partial.scenarios_complete = event.scenarios_complete
            partial.evidence_score = event.evidence_score
            partial.model_calls = event.model_calls
        elif isinstance(event, PredictionArtifactAdded):
            partial.artifact_kinds.add(event.kind)
        elif isinstance(event, PredictionStaled):
            partial.readiness = "stale"

    def apply_many(self, events: list[TypedPredictionEvent]) -> None:
        for event in events:
            self.apply(event)

    def snapshot(self) -> list[PredictedPrompt]:
        """All predictions (including stale)."""
        return [p.as_predicted_prompt() for p in self._by_id.values()]

    def active(self) -> list[PredictedPrompt]:
        """Non-terminal predictions only."""
        return [p.as_predicted_prompt() for p in self._by_id.values() if p.readiness != "stale"]
