from __future__ import annotations

import time
from dataclasses import dataclass

from vaner.models.scenario import (
    Scenario,
    ScenarioLifecycleMotion,
    ScenarioReadiness,
    ScenarioVisibility,
)

PROMINENT_RELEVANCE = 0.85
WARMING_RELEVANCE = 0.65
COOLING_RELEVANCE = 0.40


@dataclass(frozen=True)
class ScenarioLifecycle:
    relevance: float
    visible_priority: float
    readiness: ScenarioReadiness
    visibility: ScenarioVisibility
    lifecycle_motion: ScenarioLifecycleMotion
    freshness_factor: float
    last_reinforced_at: float
    archived_at: float | None
    visibility_reason: str


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def freshness_factor(age_seconds: float, freshness: str) -> float:
    age = max(0.0, age_seconds)
    if freshness == "fresh" and age <= 60:
        return 1.0
    if age <= 300:
        return 0.9
    if age <= 1800:
        return 0.9 - ((age - 300) / 1500) * 0.35
    if age <= 7200:
        return 0.55 - ((age - 1800) / 5400) * 0.4
    return 0.08


def readiness_state(scenario: Scenario) -> tuple[ScenarioReadiness, float]:
    if scenario.prepared_context.strip():
        return "ready", 1.0
    if scenario.evidence:
        return "warming", min(0.86, 0.46 + len(scenario.evidence) * 0.08)
    if scenario.expanded_at is not None:
        return "warming", 0.54
    return "unprepared", 0.38


def compute_scenario_lifecycle(scenario: Scenario, *, now: float | None = None) -> ScenarioLifecycle:
    current = time.time() if now is None else now
    reinforced_at = float(scenario.last_reinforced_at or scenario.last_refreshed_at or scenario.created_at or current)
    age = max(0.0, current - reinforced_at)
    freshness = freshness_factor(age, scenario.freshness)
    readiness, readiness_weight = readiness_state(scenario)
    confidence = clamp01(float(scenario.confidence or 0.0) or float(scenario.score or 0.0))

    outcome_bias = {"useful": 0.10, "partial": 0.04, "irrelevant": -0.18, "wrong": -0.36}.get(scenario.last_outcome or "", 0.0)
    evidence_bonus = min(0.08, len(scenario.evidence) * 0.015)
    entity_bonus = min(0.06, len(scenario.entities) * 0.01)
    gap_penalty = min(0.16, len(scenario.coverage_gaps) * 0.04)
    contradiction_penalty = clamp01(float(scenario.contradiction_signal)) * 0.30
    prior_bonus = min(0.08, int(scenario.prior_successes) * 0.025)

    raw_relevance = (
        confidence * 0.38
        + freshness * 0.33
        + readiness_weight * 0.20
        + evidence_bonus
        + entity_bonus
        + prior_bonus
        + outcome_bias
        - gap_penalty
        - contradiction_penalty
    )
    relevance = clamp01(raw_relevance)

    reason = "fresh signals support this scenario"
    if scenario.last_outcome in {"irrelevant", "wrong"}:
        reason = f"cooled after {scenario.last_outcome} feedback"
    elif scenario.contradiction_signal >= 0.5:
        reason = "cooled by contradiction signals"
    elif freshness < 0.25:
        reason = "no recent reinforcing signals"
    elif readiness == "ready":
        reason = "prepared context is ready"
    elif readiness == "warming":
        reason = "evidence is still warming"

    if age > 1800 and readiness != "ready" and scenario.last_outcome != "useful":
        relevance = min(relevance, 0.39)
        reason = "archived from the live map after stale unsupported signals"

    archived_at = scenario.archived_at
    if scenario.pinned:
        if relevance < WARMING_RELEVANCE:
            visibility: ScenarioVisibility = "cooling"
        elif relevance >= PROMINENT_RELEVANCE:
            visibility = "prominent"
        else:
            visibility = "warming"
        if relevance < WARMING_RELEVANCE:
            reason = "pinned but stale"
        archived_at = None
    elif relevance >= PROMINENT_RELEVANCE:
        visibility = "prominent"
        archived_at = None
    elif relevance >= WARMING_RELEVANCE:
        visibility = "warming"
        archived_at = None
    elif relevance >= COOLING_RELEVANCE:
        visibility = "cooling"
        archived_at = None
    else:
        visibility = "archived"
        archived_at = archived_at or current

    if visibility == "archived":
        motion: ScenarioLifecycleMotion = "fading"
    elif scenario.last_outcome in {"irrelevant", "wrong"} or freshness < 0.45 or relevance < WARMING_RELEVANCE:
        motion = "falling"
    elif age <= 90 or scenario.last_outcome in {"useful", "partial"}:
        motion = "rising"
    else:
        motion = "stable"

    visible_priority = round(clamp01(relevance * (0.55 + readiness_weight * 0.30 + confidence * 0.15)), 4)
    return ScenarioLifecycle(
        relevance=round(relevance, 4),
        visible_priority=visible_priority,
        readiness=readiness,
        visibility=visibility,
        lifecycle_motion=motion,
        freshness_factor=round(freshness, 4),
        last_reinforced_at=reinforced_at,
        archived_at=archived_at,
        visibility_reason=reason,
    )


def apply_scenario_lifecycle(scenario: Scenario, *, now: float | None = None) -> Scenario:
    lifecycle = compute_scenario_lifecycle(scenario, now=now)
    return scenario.model_copy(
        update={
            "relevance": lifecycle.relevance,
            "visible_priority": lifecycle.visible_priority,
            "readiness": lifecycle.readiness,
            "visibility": lifecycle.visibility,
            "lifecycle_motion": lifecycle.lifecycle_motion,
            "last_reinforced_at": lifecycle.last_reinforced_at,
            "archived_at": lifecycle.archived_at,
            "visibility_reason": lifecycle.visibility_reason,
        }
    )
