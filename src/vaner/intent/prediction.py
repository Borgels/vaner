# SPDX-License-Identifier: Apache-2.0
"""PredictedPrompt — a first-class predicted next prompt with its own compute contract.

A prediction owns four promises:
1. Target budget (dimensionless weight + derived token budget)
2. Current spend (model calls, tokens, scenarios completed)
3. Completion criterion (evidence floor + draft + no critical contradictions)
4. Arbitration policy (rebalance to observed yield, not initial confidence)

Confidence opens the door at admission and sets an initial weight. After the
first few LLM calls land, weight is rebalanced against observed yield.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Literal

ReadinessState = Literal[
    "queued",
    "grounding",
    "evidence_gathering",
    "drafting",
    "ready",
    "stale",
]

Source = Literal[
    "arc",
    "pattern",
    "llm_branch",
    "macro",
    "history",
    "goal",
    # 0.8.2 WS2 — a prediction anchored to a specific
    # :class:`IntentArtefactItem` (rather than a whole goal). ``anchor``
    # carries the item id; ``_merge_prediction_specs`` iterates items of
    # active artefact-backed goals where state ∈ {pending, in_progress,
    # stalled} and emits one spec per eligible item. Carries richer
    # ``anchor_units`` (related files + entities) than goal-level specs.
    "artefact_item",
]
HypothesisType = Literal["likely_next", "possible_branch", "long_tail"]
Specificity = Literal["concrete", "category", "anchor"]


# Allowed transitions for the readiness state machine.
# `stale` is reachable from every non-terminal state. `ready` is terminal.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"grounding", "stale"}),
    "grounding": frozenset({"evidence_gathering", "stale"}),
    "evidence_gathering": frozenset({"drafting", "stale"}),
    "drafting": frozenset({"ready", "stale"}),
    "ready": frozenset({"stale"}),
    "stale": frozenset(),
}


def is_transition_allowed(from_state: ReadinessState, to_state: ReadinessState) -> bool:
    """Return True if `from_state -> to_state` is a legal readiness transition."""
    return to_state in _ALLOWED_TRANSITIONS.get(from_state, frozenset())


def prediction_id(source: str, anchor: str, label: str) -> str:
    """Stable hash over (source, anchor, label). The identity key for a prediction."""
    payload = f"{source}|{anchor}|{label}".encode()
    return hashlib.sha1(payload).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class PredictionSpec:
    """The immutable identity + hypothesis of a predicted next prompt."""

    id: str
    label: str
    description: str
    source: Source
    anchor: str
    confidence: float
    hypothesis_type: HypothesisType
    specificity: Specificity
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class PredictionRun:
    """Mutable compute-contract state: budget, spend, readiness.

    WS6: ``last_seen_cycle`` tracks which cycle most recently re-observed this
    prediction (i.e. the spec was emitted by ``_merge_prediction_specs``);
    ``invalidation_reason`` is populated when a signal fires to stale the
    prediction; ``spent`` is flipped on adoption so the prediction doesn't
    resurface until the underlying evidence is invalidated.
    """

    weight: float
    token_budget: int
    tokens_used: int = 0
    model_calls: int = 0
    scenarios_spawned: int = 0
    scenarios_complete: int = 0
    readiness: ReadinessState = "queued"
    updated_at: float = field(default_factory=time.time)
    # WS6 persistence fields.
    last_seen_cycle: int = 0
    invalidation_reason: str = ""
    spent: bool = False


@dataclass(slots=True)
class PredictionArtifacts:
    """The producer side: scenarios, evidence, draft, briefing, thinking traces.

    WS6: ``file_content_hashes`` captures the per-file content hashes the
    briefing was assembled against. The invalidation sweep compares these
    against the current git_state and demotes / stales the prediction when
    the underlying files changed. Without this, the registry can't know
    whether a ``ready`` prediction is still valid after a file edit.
    """

    scenario_ids: list[str] = field(default_factory=list)
    evidence_score: float = 0.0
    draft_answer: str | None = None
    thinking_traces: list[str] = field(default_factory=list)
    prepared_briefing: str | None = None
    file_content_hashes: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class PredictedPrompt:
    """The first-class unit of prediction: spec + run + artifacts."""

    spec: PredictionSpec
    run: PredictionRun
    artifacts: PredictionArtifacts

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def readiness(self) -> ReadinessState:
        return self.run.readiness

    def is_terminal(self) -> bool:
        """``stale`` is the only lifecycle terminal state.

        ``ready`` predictions intentionally remain in ``active()`` — they're
        the whole point of the adoption surface. The HTTP/MCP clients want
        to SEE ready rows so users can click them; hiding them would defeat
        the purpose of the state machine.
        """
        return self.run.readiness == "stale"

    def budget_remaining(self) -> int:
        return max(0, self.run.token_budget - self.run.tokens_used)
