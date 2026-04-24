# SPDX-License-Identifier: Apache-2.0
"""Learnable scoring policy for the exploration frontier.

``ScoringPolicy`` is the single source of truth for all numeric parameters
that govern how the frontier scores, discounts, and learns from scenarios.
Hard-coded constants are the initial values; over time the engine nudges them
toward better parameters based on observed utility signals.

Design notes
------------
- Every parameter has a documented meaning and initial value.
- Adaptation is small and bounded so the engine drifts rather than oscillates.
- Parameters are persisted to the store so learning survives restarts.
- ``MaturityPhase`` gates adaptation rate: large steps when cold, tiny when mature.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# Weights for the 5-component priority score (must sum to ≤ 1.0; normalised)
# [graph_proximity, arc_probability, coverage_gap, pattern_strength, freshness]
_DEFAULT_SCORE_WEIGHTS: list[float] = [0.35, 0.20, 0.25, 0.10, 0.10]

# Source multipliers — initial bias before any learning
_DEFAULT_SOURCE_MULTIPLIERS: dict[str, float] = {
    "graph": 1.0,
    "arc": 1.0,
    "pattern": 1.2,
    "llm_branch": 0.9,
    "skill": 1.1,
}

# Layer multipliers — strategic scenarios are slightly prioritized.
_DEFAULT_LAYER_MULTIPLIERS: dict[str, float] = {
    "operational": 1.0,
    "tactical": 1.05,
    "strategic": 1.12,
}

# Adaptation step sizes per MaturityPhase
_ADAPT_STEPS: dict[str, float] = {
    "cold_start": 0.04,
    "warming": 0.02,
    "learning": 0.01,
    "mature": 0.003,
}


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass
class ScoringPolicy:
    """All learnable parameters for frontier scoring and feedback.

    Fields
    ------
    score_weights       5-element list: [graph_proximity, arc_probability,
                        coverage_gap, pattern_strength, freshness_decay].
                        Used as blend coefficients in ``frontier_score()``.
    depth_decay_rate    Controls how fast LLM-branch depth penalises priority.
                        Larger → deeper branches are penalised more heavily.
                        Default 0.35 gives ~0.53 discount at depth 3.
    freshness_half_life Seconds for a 50% freshness decay. Shorter = prefer
                        recently-accessed context units; longer = allow stale
                        ones to stay competitive. Default 300 s (5 minutes).
    source_multipliers  Per-source priority multipliers. Updated from hit/miss
                        signals to emphasise the most predictive source for
                        this user+repo pair.
    feedback_hit_rate   Multiplicative boost applied on a hit (e.g. 1.05 = +5%).
    feedback_miss_rate  Multiplicative reduction applied on a miss (e.g. 0.95 = -5%).
    branch_priority_decay
                        Fraction of parent priority inherited by an LLM follow-on
                        branch scenario. Default 0.7.
    updated_at          Timestamp of the last parameter update (for logging).
    """

    score_weights: list[float] = field(default_factory=lambda: list(_DEFAULT_SCORE_WEIGHTS))
    depth_decay_rate: float = 0.35
    freshness_half_life: float = 300.0
    source_multipliers: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_SOURCE_MULTIPLIERS))
    layer_multipliers: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_LAYER_MULTIPLIERS))
    feedback_hit_rate: float = 1.05
    feedback_miss_rate: float = 0.95
    branch_priority_decay: float = 0.70
    cache_full_hit_path_threshold: float = 0.70
    cache_partial_hit_path_threshold: float = 0.30
    cache_full_hit_similarity_threshold: float = 0.70
    cache_partial_hit_similarity_threshold: float = 0.30
    cache_warm_similarity_threshold: float = 0.10
    updated_at: float = field(default_factory=time.time)

    # -----------------------------------------------------------------------
    # Scoring helpers
    # -----------------------------------------------------------------------

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
        """Composite priority score applying policy weights and depth discount."""
        w = self.score_weights
        raw = w[0] * graph_proximity + w[1] * arc_probability + w[2] * coverage_gap + w[3] * pattern_strength + w[4] * freshness_factor
        layer_multiplier = self.layer_multipliers.get(layer, self.layer_multipliers.get("operational", 1.0))
        return raw * self.depth_discount(depth) * layer_multiplier

    def depth_discount(self, depth: int) -> float:
        """Priority multiplier based on LLM branching depth."""
        return max(0.1, 1.0 / (1.0 + depth * self.depth_decay_rate))

    def freshness_factor(self, last_access_ts: float) -> float:
        """Returns a freshness value in [0.1, 1.0] based on access recency."""
        age_seconds = time.time() - last_access_ts
        if age_seconds <= 0:
            return 1.0
        return max(0.1, 2.0 ** (-age_seconds / self.freshness_half_life))

    def source_priority(self, source: str, raw_priority: float) -> float:
        """Apply the source multiplier to a raw priority score."""
        return raw_priority * self.source_multipliers.get(source, 1.0)

    # -----------------------------------------------------------------------
    # Online adaptation
    # -----------------------------------------------------------------------

    def record_source_feedback(self, source: str, *, hit: bool) -> None:
        """Adjust the per-source multiplier on a hit or miss."""
        current = self.source_multipliers.get(source, 1.0)
        if hit:
            self.source_multipliers[source] = min(2.0, current * self.feedback_hit_rate)
        else:
            self.source_multipliers[source] = max(0.3, current * self.feedback_miss_rate)
        self.updated_at = time.time()

    def adapt_weights(
        self,
        *,
        active_signals: list[bool],
        hit: bool,
        phase: str = "mature",
    ) -> None:
        """Nudge score_weights based on which signals were active at hit/miss time.

        ``active_signals`` is a 5-element bool list matching the weight order:
        [graph_proximity_active, arc_active, coverage_gap_active,
         pattern_active, freshness_active].

        On a hit:  slightly increase weights for active signals.
        On a miss: slightly decrease weights for active signals
                   (inactive ones are implicitly raised by normalisation).

        Step size is gated by ``phase`` to avoid over-reacting early on.
        """
        if len(active_signals) != 5:
            return
        step = _ADAPT_STEPS.get(phase, 0.005)
        w = list(self.score_weights)
        for i, active in enumerate(active_signals):
            if active:
                w[i] = w[i] * (1.0 + step) if hit else w[i] * (1.0 - step)
            else:
                # Passive regularisation: unactivated dimensions drift up slightly
                if not hit:
                    w[i] = w[i] * (1.0 + step * 0.3)
        # Normalise to keep weights summing to 1.0 and prevent drift to zero
        total = sum(w)
        if total > 0:
            w = [max(0.01, wi / total) for wi in w]
            # Clamping can inflate the sum above 1.0; renormalise again.
            total2 = sum(w)
            if total2 > 0:
                w = [wi / total2 for wi in w]
        self.score_weights = w
        self.updated_at = time.time()

    def adapt_depth_decay(self, *, deep_hit: bool, phase: str = "mature") -> None:
        """If deep-branch scenarios consistently hit, relax depth discount."""
        step = _ADAPT_STEPS.get(phase, 0.005)
        if deep_hit:
            self.depth_decay_rate = max(0.05, self.depth_decay_rate * (1.0 - step))
        else:
            self.depth_decay_rate = min(2.0, self.depth_decay_rate * (1.0 + step * 0.5))
        self.updated_at = time.time()

    def adapt_freshness(self, *, stale_hit: bool, phase: str = "mature") -> None:
        """If stale scenarios consistently miss, shorten the freshness half-life."""
        step = _ADAPT_STEPS.get(phase, 0.005)
        if stale_hit:
            self.freshness_half_life = min(3600.0, self.freshness_half_life * (1.0 + step))
        else:
            self.freshness_half_life = max(30.0, self.freshness_half_life * (1.0 - step))
        self.updated_at = time.time()

    def adapt_cache_thresholds(self, *, reward_total: float, phase: str = "mature") -> None:
        """Conservative threshold adaptation from reward outcomes."""
        step = _ADAPT_STEPS.get(phase, 0.005) * 0.5
        if reward_total >= 0.3:
            self.cache_full_hit_path_threshold = max(0.55, self.cache_full_hit_path_threshold - step)
            self.cache_full_hit_similarity_threshold = max(0.55, self.cache_full_hit_similarity_threshold - step)
        elif reward_total <= -0.2:
            self.cache_full_hit_path_threshold = min(0.90, self.cache_full_hit_path_threshold + step)
            self.cache_full_hit_similarity_threshold = min(0.90, self.cache_full_hit_similarity_threshold + step)
        self.cache_partial_hit_path_threshold = min(
            self.cache_full_hit_path_threshold - 0.05,
            max(0.15, self.cache_partial_hit_path_threshold),
        )
        self.cache_partial_hit_similarity_threshold = min(
            self.cache_full_hit_similarity_threshold - 0.05,
            max(0.15, self.cache_partial_hit_similarity_threshold),
        )
        self.cache_warm_similarity_threshold = min(
            self.cache_partial_hit_similarity_threshold - 0.05,
            max(0.02, self.cache_warm_similarity_threshold),
        )
        self.updated_at = time.time()

    # -----------------------------------------------------------------------
    # Serialisation
    # -----------------------------------------------------------------------

    def serialize(self) -> str:
        return json.dumps(
            {
                "score_weights": self.score_weights,
                "depth_decay_rate": self.depth_decay_rate,
                "freshness_half_life": self.freshness_half_life,
                "source_multipliers": self.source_multipliers,
                "layer_multipliers": self.layer_multipliers,
                "feedback_hit_rate": self.feedback_hit_rate,
                "feedback_miss_rate": self.feedback_miss_rate,
                "branch_priority_decay": self.branch_priority_decay,
                "cache_full_hit_path_threshold": self.cache_full_hit_path_threshold,
                "cache_partial_hit_path_threshold": self.cache_partial_hit_path_threshold,
                "cache_full_hit_similarity_threshold": self.cache_full_hit_similarity_threshold,
                "cache_partial_hit_similarity_threshold": self.cache_partial_hit_similarity_threshold,
                "cache_warm_similarity_threshold": self.cache_warm_similarity_threshold,
                "updated_at": self.updated_at,
            }
        )

    @classmethod
    def deserialize(cls, data: str) -> ScoringPolicy:
        try:
            obj = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            return cls()
        policy = cls()
        if isinstance(obj.get("score_weights"), list) and len(obj["score_weights"]) == 5:
            try:
                policy.score_weights = [float(x) for x in obj["score_weights"]]
            except (TypeError, ValueError):
                policy.score_weights = list(_DEFAULT_SCORE_WEIGHTS)
        if isinstance(obj.get("depth_decay_rate"), (int, float)):
            try:
                policy.depth_decay_rate = float(obj["depth_decay_rate"])
            except (TypeError, ValueError):
                policy.depth_decay_rate = 0.35
        if isinstance(obj.get("freshness_half_life"), (int, float)):
            try:
                policy.freshness_half_life = float(obj["freshness_half_life"])
            except (TypeError, ValueError):
                policy.freshness_half_life = 300.0
        if isinstance(obj.get("source_multipliers"), dict):
            parsed: dict[str, float] = {}
            for k, v in obj["source_multipliers"].items():
                try:
                    parsed[str(k)] = max(0.3, min(2.0, float(v)))
                except (TypeError, ValueError):
                    continue
            if parsed:
                policy.source_multipliers = parsed
        if isinstance(obj.get("layer_multipliers"), dict):
            parsed_layers: dict[str, float] = {}
            for k, v in obj["layer_multipliers"].items():
                try:
                    parsed_layers[str(k)] = max(0.5, min(2.0, float(v)))
                except (TypeError, ValueError):
                    continue
            if parsed_layers:
                policy.layer_multipliers = parsed_layers
        if isinstance(obj.get("feedback_hit_rate"), (int, float)):
            try:
                policy.feedback_hit_rate = float(obj["feedback_hit_rate"])
            except (TypeError, ValueError):
                policy.feedback_hit_rate = 1.05
        if isinstance(obj.get("feedback_miss_rate"), (int, float)):
            try:
                policy.feedback_miss_rate = float(obj["feedback_miss_rate"])
            except (TypeError, ValueError):
                policy.feedback_miss_rate = 0.95
        if isinstance(obj.get("branch_priority_decay"), (int, float)):
            try:
                policy.branch_priority_decay = float(obj["branch_priority_decay"])
            except (TypeError, ValueError):
                policy.branch_priority_decay = 0.70
        if isinstance(obj.get("cache_full_hit_path_threshold"), (int, float)):
            policy.cache_full_hit_path_threshold = max(0.55, min(0.95, float(obj["cache_full_hit_path_threshold"])))
        if isinstance(obj.get("cache_partial_hit_path_threshold"), (int, float)):
            policy.cache_partial_hit_path_threshold = max(0.10, min(0.90, float(obj["cache_partial_hit_path_threshold"])))
        if isinstance(obj.get("cache_full_hit_similarity_threshold"), (int, float)):
            policy.cache_full_hit_similarity_threshold = max(0.55, min(0.95, float(obj["cache_full_hit_similarity_threshold"])))
        if isinstance(obj.get("cache_partial_hit_similarity_threshold"), (int, float)):
            policy.cache_partial_hit_similarity_threshold = max(0.10, min(0.90, float(obj["cache_partial_hit_similarity_threshold"])))
        if isinstance(obj.get("cache_warm_similarity_threshold"), (int, float)):
            policy.cache_warm_similarity_threshold = max(0.02, min(0.85, float(obj["cache_warm_similarity_threshold"])))
        if isinstance(obj.get("updated_at"), (int, float)):
            try:
                policy.updated_at = float(obj["updated_at"])
            except (TypeError, ValueError):
                policy.updated_at = time.time()
        return policy

    def merge_source_multipliers(self, multipliers: dict[str, float]) -> None:
        """Apply externally accumulated multipliers (e.g. from a prior cycle)."""
        for src, mult in multipliers.items():
            try:
                self.source_multipliers[src] = max(0.3, min(2.0, float(mult)))
            except (TypeError, ValueError):
                continue
        self.updated_at = time.time()

    @classmethod
    def from_policy_defaults(cls, defaults: dict[str, object] | None) -> ScoringPolicy:
        """Build from shipped policy defaults.

        These are cold-start hints only. Local online policy state should
        supersede them after first-run adaptation.
        """
        if not defaults:
            return cls()
        try:
            return cls.deserialize(json.dumps(defaults))
        except Exception:
            return cls()


def depth_discount_from_policy(depth: int, policy: ScoringPolicy) -> float:
    return policy.depth_discount(depth)


def freshness_decay_from_policy(last_access_ts: float, policy: ScoringPolicy) -> float:
    return policy.freshness_factor(last_access_ts)
