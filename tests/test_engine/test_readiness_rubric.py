# SPDX-License-Identifier: Apache-2.0
"""Phase 4 Phase A.5 — readiness state-machine rubric tests.

The rubric: no prediction reaches `ready` without passing through the full
pipeline (grounding → evidence_gathering → drafting). Staleness is the only
universal escape.
"""

from __future__ import annotations

import pytest

from vaner.intent.prediction import PredictionSpec, prediction_id
from vaner.intent.prediction_registry import InvalidTransitionError, PredictionRegistry


def _spec(label: str = "draft a test") -> PredictionSpec:
    pid = prediction_id("arc", "anchor", label)
    return PredictionSpec(
        id=pid,
        label=label,
        description=f"{label} description",
        source="arc",
        anchor="anchor",
        confidence=0.7,
        hypothesis_type="likely_next",
        specificity="concrete",
    )


def test_happy_path_requires_every_stage():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    reg.enroll(_spec(), initial_weight=1.0)
    spec = _spec()

    # queued → grounding (triggered by attach_scenario)
    reg.attach_scenario(spec.id, "scen-1")
    assert reg.get(spec.id).run.readiness == "grounding"  # type: ignore[union-attr]

    # grounding → evidence_gathering
    reg.transition(spec.id, "evidence_gathering")
    assert reg.get(spec.id).run.readiness == "evidence_gathering"  # type: ignore[union-attr]

    # evidence_gathering → drafting
    reg.transition(spec.id, "drafting")
    assert reg.get(spec.id).run.readiness == "drafting"  # type: ignore[union-attr]

    # drafting → ready
    reg.transition(spec.id, "ready")
    assert reg.get(spec.id).run.readiness == "ready"  # type: ignore[union-attr]


def test_cannot_skip_grounding_to_ready():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    with pytest.raises(InvalidTransitionError):
        reg.transition(spec.id, "ready")


def test_cannot_skip_from_grounding_to_ready():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    reg.attach_scenario(spec.id, "scen-1")  # -> grounding
    with pytest.raises(InvalidTransitionError):
        reg.transition(spec.id, "ready")


def test_cannot_skip_from_evidence_gathering_to_ready():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    reg.attach_scenario(spec.id, "scen-1")
    reg.transition(spec.id, "evidence_gathering")
    with pytest.raises(InvalidTransitionError):
        reg.transition(spec.id, "ready")


def test_stale_is_terminal():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    reg.transition(spec.id, "stale")
    with pytest.raises(InvalidTransitionError):
        reg.transition(spec.id, "grounding")
    with pytest.raises(InvalidTransitionError):
        reg.transition(spec.id, "ready")


def test_stale_reachable_from_every_non_terminal_state():
    # From every non-terminal state, stale must be reachable in one hop.
    # This is the escape path used when a new user turn arrives.
    for setup in [
        lambda r, s: None,  # queued
        lambda r, s: r.attach_scenario(s.id, "x"),  # grounding
        lambda r, s: (r.attach_scenario(s.id, "x"), r.transition(s.id, "evidence_gathering")),
        lambda r, s: (
            r.attach_scenario(s.id, "x"),
            r.transition(s.id, "evidence_gathering"),
            r.transition(s.id, "drafting"),
        ),
        lambda r, s: (
            r.attach_scenario(s.id, "x"),
            r.transition(s.id, "evidence_gathering"),
            r.transition(s.id, "drafting"),
            r.transition(s.id, "ready"),
        ),
    ]:
        reg = PredictionRegistry(cycle_token_pool=1_000)
        spec = _spec()
        reg.enroll(spec, initial_weight=1.0)
        setup(reg, spec)
        reg.transition(spec.id, "stale")
        assert reg.get(spec.id).run.readiness == "stale"  # type: ignore[union-attr]


def test_backwards_transitions_rejected():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    reg.attach_scenario(spec.id, "scen-1")
    reg.transition(spec.id, "evidence_gathering")
    with pytest.raises(InvalidTransitionError):
        reg.transition(spec.id, "grounding")  # backwards
