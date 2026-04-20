from __future__ import annotations

from vaner.models.scenario import Scenario


def scenario_score(scenario: Scenario) -> float:
    freshness_bonus = {"fresh": 0.2, "recent": 0.1, "stale": -0.1}.get(scenario.freshness, 0.0)
    cost_penalty = {"low": 0.08, "medium": 0.03, "high": -0.05}.get(scenario.cost_to_expand, 0.0)
    outcome_bias = {"useful": 0.12, "partial": 0.04, "irrelevant": -0.12}.get(scenario.last_outcome or "", 0.0)
    evidence_bonus = min(len(scenario.evidence), 8) * 0.015
    entity_bonus = min(len(scenario.entities), 6) * 0.01
    raw_score = scenario.confidence + freshness_bonus + cost_penalty + outcome_bias + evidence_bonus + entity_bonus
    return round(min(1.0, max(0.0, raw_score)), 4)
