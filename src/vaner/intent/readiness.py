# SPDX-License-Identifier: Apache-2.0
"""User-facing readiness labels + coarse ETA buckets + adoptability rules.

These pure functions turn the internal `PredictedPrompt` state machine
into UI-friendly signals for the MCP Apps dashboard, the text fallback,
and the desktop clients' card rendering. Keep them free of engine
imports so the Rust `vaner-contract` mirror can stay aligned.
"""

from __future__ import annotations

from typing import Literal

from vaner.intent.prediction import PredictedPrompt, ReadinessState

EtaBucket = Literal["ready_now", "under_20s", "under_1m", "working", "maturing"]
"""Coarse ETA bucket. We intentionally avoid exact-second promises — the
buckets are user-facing labels, not wall-clock predictions."""


_READINESS_LABELS: dict[ReadinessState, str] = {
    "queued": "Queued",
    "grounding": "Grounding",
    "evidence_gathering": "Gathering evidence",
    "drafting": "Drafting",
    "ready": "Ready",
    "stale": "Stale",
}


_ETA_BUCKET_LABELS: dict[EtaBucket, str] = {
    "ready_now": "Ready now",
    "under_20s": "~10–20s",
    "under_1m": "~1 min",
    "working": "Working",
    "maturing": "Maturing in background",
}


def readiness_label(state: ReadinessState) -> str:
    """User-facing string for a readiness state. Falls back to title-case."""
    return _READINESS_LABELS.get(state, state.title())


def eta_bucket_label(bucket: EtaBucket | None) -> str | None:
    if bucket is None:
        return None
    return _ETA_BUCKET_LABELS.get(bucket, bucket)


def eta_bucket(prompt: PredictedPrompt) -> EtaBucket | None:
    """Estimate an ETA bucket for *prompt* from its run state.

    We avoid wall-clock predictions because the spawn/completion rhythm
    is model-dependent and the user doesn't need precision — they need a
    sense of whether to wait or move on.

    Returns ``None`` for stale predictions (they shouldn't be surfaced as
    ETA-bearing).
    """
    run = prompt.run
    if run.readiness == "ready":
        return "ready_now"
    if run.readiness == "stale":
        return None

    spawned = max(0, run.scenarios_spawned)
    complete = max(0, min(run.scenarios_complete, spawned))
    progress = complete / spawned if spawned else 0.0
    token_budget = max(1, run.token_budget)
    tokens_pct = min(1.0, run.tokens_used / token_budget)

    if run.readiness == "drafting":
        if progress >= 0.8 and tokens_pct >= 0.5:
            return "under_20s"
        return "under_1m"
    if run.readiness == "evidence_gathering":
        if progress >= 0.5:
            return "under_1m"
        return "working"
    # queued, grounding.
    return "maturing"


def is_adoptable(prompt: PredictedPrompt) -> bool:
    """A prediction is adoptable when it has something material to hand off.

    The rules: readiness is drafting or ready (so artifacts exist), a
    non-empty briefing or draft is present, and the prediction hasn't
    been spent by a previous adoption. Freshness is enforced upstream via
    invalidation.py; this check only looks at the prompt's intrinsic
    state.
    """
    run = prompt.run
    if run.spent:
        return False
    if run.readiness not in ("drafting", "ready"):
        return False
    artifacts = prompt.artifacts
    has_material = bool(
        (artifacts.prepared_briefing and artifacts.prepared_briefing.strip()) or (artifacts.draft_answer and artifacts.draft_answer.strip())
    )
    return has_material


def suppression_reason(prompt: PredictedPrompt) -> str | None:
    """Why the UI should disable Adopt on this prompt. ``None`` → adoptable."""
    if prompt.run.spent:
        return "already_adopted"
    if prompt.run.readiness == "stale":
        return "stale"
    if prompt.run.readiness not in ("drafting", "ready"):
        return "not_ready_yet"
    if not is_adoptable(prompt):
        return "no_prepared_material"
    return None
