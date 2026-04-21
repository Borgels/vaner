from __future__ import annotations

from vaner.models.scenario import Scenario


def scenario_score(scenario: Scenario) -> float:
    freshness_bonus = {"fresh": 0.24, "recent": 0.08, "stale": -0.2}.get(scenario.freshness, 0.0)
    cost_bias = {"low": 0.06, "medium": 0.01, "high": -0.08}.get(scenario.cost_to_expand, 0.0)
    outcome_bias = {"useful": 0.14, "partial": 0.03, "irrelevant": -0.16}.get(scenario.last_outcome or "", 0.0)
    evidence_bonus = min(len(scenario.evidence), 10) * 0.012
    entity_bonus = min(len(scenario.entities), 8) * 0.018
    gap_penalty = min(0.2, len(scenario.coverage_gaps) * 0.05)
    raw_score = scenario.confidence + freshness_bonus + cost_bias + outcome_bias + evidence_bonus + entity_bonus - gap_penalty
    # Spread into the middle range to avoid clustering around 1.0.
    normalized = 1.0 / (1.0 + pow(2.718281828, -4.0 * (raw_score - 0.5)))
    return round(min(0.98, max(0.02, normalized)), 4)
