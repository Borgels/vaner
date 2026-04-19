from __future__ import annotations

from dataclasses import dataclass

from vaner.intent.frontier import ExplorationFrontier
from vaner.intent.scoring_policy import ScoringPolicy


@dataclass
class RecordingPolicy(ScoringPolicy):
    called: bool = False
    last_layer: str = ""

    def compute_score(
        self,
        *,
        graph_proximity: float,
        arc_probability: float,
        coverage_gap: float,
        pattern_strength: float,
        freshness_factor: float,
        depth: int,
        layer: str = "operational",
    ) -> float:
        self.called = True
        self.last_layer = layer
        # Sentinel value to prove the frontier delegated to policy.
        return 0.321


def test_frontier_score_delegates_to_scoring_policy() -> None:
    policy = RecordingPolicy()
    frontier = ExplorationFrontier(min_priority=0.01, scoring_policy=policy)

    score = frontier._score(
        source="arc",
        graph_proximity=0.2,
        arc_probability=0.7,
        coverage_gap=0.4,
        pattern_strength=0.1,
        freshness_decay=0.9,
        depth=1,
        layer="strategic",
    )

    assert policy.called is True
    assert policy.last_layer == "strategic"
    assert score == 0.321
