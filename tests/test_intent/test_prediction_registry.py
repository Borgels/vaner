# SPDX-License-Identifier: Apache-2.0
"""Tests for PredictionRegistry — the in-cycle lifecycle manager."""

from __future__ import annotations

import pytest

from vaner.intent.prediction import (
    PredictionSpec,
    is_transition_allowed,
    prediction_id,
)
from vaner.intent.prediction_registry import (
    InvalidTransitionError,
    PredictionEvent,
    PredictionRegistry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(
    label: str = "Write tests for the handler",
    *,
    confidence: float = 0.8,
    source: str = "arc",
    anchor: str = "tests/handler",
    hypothesis: str = "likely_next",
    specificity: str = "concrete",
) -> PredictionSpec:
    pid = prediction_id(source, anchor, label)
    return PredictionSpec(
        id=pid,
        label=label,
        description=f"{label} (description)",
        source=source,  # type: ignore[arg-type]
        anchor=anchor,
        confidence=confidence,
        hypothesis_type=hypothesis,  # type: ignore[arg-type]
        specificity=specificity,  # type: ignore[arg-type]
        created_at=0.0,
    )


class _RecordingListener:
    def __init__(self) -> None:
        self.events: list[PredictionEvent] = []

    def __call__(self, event: PredictionEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events]


# ---------------------------------------------------------------------------
# State-machine legality
# ---------------------------------------------------------------------------


def test_state_machine_allowed_transitions():
    assert is_transition_allowed("queued", "grounding")
    assert is_transition_allowed("grounding", "evidence_gathering")
    assert is_transition_allowed("evidence_gathering", "drafting")
    assert is_transition_allowed("drafting", "ready")
    # stale is the universal escape
    assert is_transition_allowed("queued", "stale")
    assert is_transition_allowed("drafting", "stale")
    assert is_transition_allowed("ready", "stale")


def test_state_machine_disallowed_transitions():
    # Cannot skip stages
    assert not is_transition_allowed("queued", "ready")
    assert not is_transition_allowed("queued", "drafting")
    assert not is_transition_allowed("grounding", "ready")
    # Cannot go backwards
    assert not is_transition_allowed("ready", "drafting")
    assert not is_transition_allowed("evidence_gathering", "grounding")
    # Stale is terminal
    assert not is_transition_allowed("stale", "grounding")
    assert not is_transition_allowed("stale", "ready")


# ---------------------------------------------------------------------------
# Enrolment
# ---------------------------------------------------------------------------


def test_enroll_sets_initial_weight_and_token_budget():
    reg = PredictionRegistry(cycle_token_pool=10_000)
    spec = _spec(confidence=0.8)
    prompt = reg.enroll(spec, initial_weight=0.5)
    assert prompt.id == spec.id
    assert prompt.run.weight == pytest.approx(0.5)
    assert prompt.run.token_budget == 5_000
    assert prompt.run.readiness == "queued"
    assert prompt.artifacts.scenario_ids == []


def test_enroll_rejects_duplicate_id():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    with pytest.raises(ValueError):
        reg.enroll(spec, initial_weight=1.0)


def test_enroll_applies_floor_to_zero_weight():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec()
    prompt = reg.enroll(spec, initial_weight=0.0)
    # Floor guarantees non-starvation
    assert prompt.run.weight >= PredictionRegistry.MIN_FLOOR_WEIGHT


def test_enroll_batch_distributes_weight_by_confidence_with_floor():
    reg = PredictionRegistry(cycle_token_pool=10_000)
    specs = [
        _spec(label="A", confidence=0.9, anchor="a"),
        _spec(label="B", confidence=0.5, anchor="b"),
        _spec(label="C", confidence=0.4, anchor="c"),
    ]
    prompts = reg.enroll_batch(specs)
    assert len(prompts) == 3
    floor = PredictionRegistry.MIN_FLOOR_WEIGHT
    floored = [max(floor, s.confidence) for s in specs]
    total = sum(floored)
    expected = [f / total for f in floored]
    for prompt, expected_w in zip(prompts, expected, strict=True):
        assert prompt.run.weight == pytest.approx(expected_w)
    assert sum(p.run.weight for p in prompts) == pytest.approx(1.0)
    # Monotonicity: higher confidence → higher weight
    assert prompts[0].run.weight > prompts[1].run.weight > prompts[2].run.weight


def test_enroll_batch_guarantees_floor_even_when_normalized_share_is_below_floor():
    """If a prediction's normalized share falls below MIN_FLOOR_WEIGHT, the per-enroll
    floor clamps it up. The weight sum may exceed 1.0 as a result — the floor is a
    hard starvation guarantee, not a probability-mass constraint.
    """
    reg = PredictionRegistry(cycle_token_pool=10_000)
    floor = PredictionRegistry.MIN_FLOOR_WEIGHT
    specs = [
        _spec(label="A", confidence=0.9, anchor="a"),
        _spec(label="B", confidence=0.5, anchor="b"),
        _spec(label="C", confidence=0.01, anchor="c"),
    ]
    prompts = reg.enroll_batch(specs)
    for prompt in prompts:
        assert prompt.run.weight >= floor


# ---------------------------------------------------------------------------
# Attach / record
# ---------------------------------------------------------------------------


def test_attach_scenario_transitions_queued_to_grounding():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    reg.attach_scenario(spec.id, "scen-1")
    prompt = reg.get(spec.id)
    assert prompt is not None
    assert prompt.run.readiness == "grounding"
    assert prompt.run.scenarios_spawned == 1
    assert "scen-1" in prompt.artifacts.scenario_ids


def test_attach_scenario_is_idempotent():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    reg.attach_scenario(spec.id, "scen-1")
    reg.attach_scenario(spec.id, "scen-1")  # duplicate
    prompt = reg.get(spec.id)
    assert prompt is not None
    assert prompt.run.scenarios_spawned == 1


def test_record_call_accumulates_tokens_and_calls():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    reg.record_call(spec.id, tokens_used=120)
    reg.record_call(spec.id, tokens_used=40)
    prompt = reg.get(spec.id)
    assert prompt is not None
    assert prompt.run.model_calls == 2
    assert prompt.run.tokens_used == 160


def test_attach_artifact_populates_fields():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    reg.attach_artifact(spec.id, draft="hello", briefing="brief", thinking="I wonder")
    prompt = reg.get(spec.id)
    assert prompt is not None
    assert prompt.artifacts.draft_answer == "hello"
    assert prompt.artifacts.prepared_briefing == "brief"
    assert prompt.artifacts.thinking_traces == ["I wonder"]


# ---------------------------------------------------------------------------
# Transition legality
# ---------------------------------------------------------------------------


def test_transition_rejects_illegal_leap():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    # queued -> ready is illegal (must pass through grounding/evidence_gathering/drafting)
    with pytest.raises(InvalidTransitionError):
        reg.transition(spec.id, "ready")


def test_transition_full_happy_path():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    reg.attach_scenario(spec.id, "scen-1")  # -> grounding
    reg.transition(spec.id, "evidence_gathering")
    reg.transition(spec.id, "drafting")
    reg.transition(spec.id, "ready")
    prompt = reg.get(spec.id)
    assert prompt is not None
    assert prompt.run.readiness == "ready"


# ---------------------------------------------------------------------------
# Stale cascade
# ---------------------------------------------------------------------------


def test_stale_all_marks_non_terminal_only():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    a = _spec(label="A", anchor="a")
    b = _spec(label="B", anchor="b")
    c = _spec(label="C", anchor="c")
    reg.enroll(a, initial_weight=0.33)
    reg.enroll(b, initial_weight=0.33)
    reg.enroll(c, initial_weight=0.33)
    # Force C into stale already
    reg.transition(c.id, "stale", reason="pre-staled")
    staled = reg.stale_all(reason="new user turn")
    assert set(staled) == {a.id, b.id}
    assert reg.get(a.id).run.readiness == "stale"  # type: ignore[union-attr]
    assert reg.get(b.id).run.readiness == "stale"  # type: ignore[union-attr]
    assert reg.get(c.id).run.readiness == "stale"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Rebalance arithmetic
# ---------------------------------------------------------------------------


def test_rebalance_shifts_weight_to_higher_yield():
    reg = PredictionRegistry(cycle_token_pool=10_000)
    high_yield = _spec(label="high", anchor="h", confidence=0.5)
    low_yield = _spec(label="low", anchor="l", confidence=0.5)
    reg.enroll_batch([high_yield, low_yield])
    # Simulate both using tokens; high one produces more evidence
    reg.record_call(high_yield.id, tokens_used=100)
    reg.record_evidence(high_yield.id, delta_score=10.0)
    reg.record_call(low_yield.id, tokens_used=100)
    reg.record_evidence(low_yield.id, delta_score=1.0)
    new_weights = reg.rebalance()
    assert new_weights[high_yield.id] > new_weights[low_yield.id]
    assert new_weights[low_yield.id] >= PredictionRegistry.MIN_FLOOR_WEIGHT
    # Budgets re-scaled
    assert reg.get(high_yield.id).run.token_budget > reg.get(low_yield.id).run.token_budget  # type: ignore[union-attr]


def test_rebalance_skips_terminal_predictions():
    reg = PredictionRegistry(cycle_token_pool=10_000)
    a = _spec(label="A", anchor="a", confidence=0.5)
    b = _spec(label="B", anchor="b", confidence=0.5)
    reg.enroll_batch([a, b])
    # Make B terminal (stale)
    reg.transition(b.id, "stale")
    reg.record_call(a.id, tokens_used=100)
    reg.record_evidence(a.id, delta_score=5.0)
    new_weights = reg.rebalance()
    assert a.id in new_weights
    assert b.id not in new_weights  # terminal excluded


def test_rebalance_no_active_predictions_returns_empty():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    assert reg.rebalance() == {}


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


def test_events_emitted_on_lifecycle_changes():
    listener = _RecordingListener()
    reg = PredictionRegistry(cycle_token_pool=1_000, listener=listener)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    reg.attach_scenario(spec.id, "scen-1")
    reg.record_call(spec.id, tokens_used=50)
    reg.attach_artifact(spec.id, draft="d")
    reg.transition(spec.id, "evidence_gathering")
    reg.transition(spec.id, "stale", reason="turn")

    kinds = listener.kinds()
    assert "prediction.enrolled" in kinds
    assert "prediction.readiness_changed" in kinds  # queued -> grounding
    assert "prediction.progress" in kinds
    assert "prediction.artifact_added" in kinds
    assert "prediction.staled" in kinds


# ---------------------------------------------------------------------------
# WS1.c — MIN_TOKEN_BUDGET floor, thinking-trace ring buffer, async lock
# ---------------------------------------------------------------------------


def test_token_budget_respects_min_floor_when_pool_is_small():
    """Even with a tiny cycle_token_pool, each enrolled prediction must get at
    least MIN_TOKEN_BUDGET tokens — otherwise the budget is smaller than any
    real LLM call can use."""
    reg = PredictionRegistry(cycle_token_pool=10)  # absurdly small
    spec = _spec()
    prompt = reg.enroll(spec, initial_weight=1.0)
    assert prompt.run.token_budget >= PredictionRegistry.MIN_TOKEN_BUDGET


def test_rebalance_also_respects_min_token_budget():
    reg = PredictionRegistry(cycle_token_pool=10)
    a = _spec(label="A", anchor="a")
    b = _spec(label="B", anchor="b")
    reg.enroll_batch([a, b])
    reg.record_call(a.id, tokens_used=5)
    reg.record_evidence(a.id, delta_score=3.0)
    reg.rebalance()
    for prompt in reg.active():
        assert prompt.run.token_budget >= PredictionRegistry.MIN_TOKEN_BUDGET


def test_thinking_traces_are_capped_at_max_entries():
    """The trace ring buffer keeps only the most recent MAX_THINKING_TRACES."""
    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    for i in range(PredictionRegistry.MAX_THINKING_TRACES + 5):
        reg.attach_artifact(spec.id, thinking=f"trace {i}")
    prompt = reg.get(spec.id)
    assert prompt is not None
    assert len(prompt.artifacts.thinking_traces) == PredictionRegistry.MAX_THINKING_TRACES
    # Newest (highest index) is kept; oldest are evicted
    assert prompt.artifacts.thinking_traces[-1] == f"trace {PredictionRegistry.MAX_THINKING_TRACES + 4}"
    assert prompt.artifacts.thinking_traces[0] == "trace 5"  # 0..4 evicted


def test_thinking_traces_are_truncated_when_too_long():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    huge = "x" * (PredictionRegistry.MAX_THINKING_TRACE_BYTES + 1_000)
    reg.attach_artifact(spec.id, thinking=huge)
    prompt = reg.get(spec.id)
    assert prompt is not None
    # Truncated to the byte cap + ellipsis
    stored = prompt.artifacts.thinking_traces[0]
    assert len(stored) <= PredictionRegistry.MAX_THINKING_TRACE_BYTES + 1
    assert stored.endswith("…")


def test_registry_exposes_asyncio_lock_for_concurrent_access():
    """The registry offers a lock that engine concurrent paths can use to
    serialise mutation sequences like rebalance."""
    import asyncio

    reg = PredictionRegistry(cycle_token_pool=1_000)
    assert isinstance(reg.lock, asyncio.Lock)


# ---------------------------------------------------------------------------
# WS3.d — record_adoption boosts evidence for next rebalance
# ---------------------------------------------------------------------------


def test_record_adoption_boosts_evidence_score():
    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    baseline = reg.get(spec.id).artifacts.evidence_score  # type: ignore[union-attr]
    reg.record_adoption(spec.id)
    after = reg.get(spec.id).artifacts.evidence_score  # type: ignore[union-attr]
    assert after > baseline


def test_record_adoption_emits_artifact_event():
    listener = _RecordingListener()
    reg = PredictionRegistry(cycle_token_pool=1_000, listener=listener)
    spec = _spec()
    reg.enroll(spec, initial_weight=1.0)
    reg.record_adoption(spec.id)
    kinds = listener.kinds()
    assert "prediction.artifact_added" in kinds
    # The adoption event is tagged with kind="adoption"
    adoption_events = [e for e in listener.events if e.kind == "prediction.artifact_added" and e.payload.get("kind") == "adoption"]
    assert len(adoption_events) == 1


def test_record_adoption_is_safe_on_unknown_id():
    """No crash, no side effect."""
    reg = PredictionRegistry(cycle_token_pool=1_000)
    reg.record_adoption("no-such-prediction")  # should silently no-op
    # Snapshot empty
    assert reg.all() == []


def test_record_adoption_shifts_rebalance_weight():
    """An adopted prediction should receive more weight on the next rebalance."""
    reg = PredictionRegistry(cycle_token_pool=10_000)
    a = _spec(label="A", anchor="a")
    b = _spec(label="B", anchor="b")
    reg.enroll_batch([a, b])
    # Both do equal work...
    for pid in (a.id, b.id):
        reg.record_call(pid, tokens_used=100)
        reg.record_evidence(pid, delta_score=1.0)
    # ...but only A is adopted.
    reg.record_adoption(a.id)
    new_weights = reg.rebalance()
    assert new_weights[a.id] > new_weights[b.id]
