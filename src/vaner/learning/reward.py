# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


def _clamp(value: float, *, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _tier_signal(cache_tier: str) -> float:
    normalized = cache_tier.strip().lower()
    if normalized == "full_hit":
        return 1.0
    if normalized == "partial_hit":
        return 0.45
    if normalized == "warm_start":
        return 0.1
    return -0.35


@dataclass(slots=True)
class RewardInput:
    cache_tier: str
    similarity: float
    quality_lift: float | None = None
    host_outcome: float | None = None
    judge_score: float | None = None
    latency_ms: float | None = None
    weight_overrides: dict[str, float] = field(default_factory=dict)
    rating: Literal["useful", "partial", "wrong", "irrelevant"] | None = None
    correction_strength: float = 0.0
    contradiction_signal: float = 0.0


@dataclass(slots=True)
class RewardOutcome:
    reward_total: float
    reward_components: dict[str, float]


_DEFAULT_WEIGHTS: dict[str, float] = {
    "tier": 0.36,
    "similarity": 0.22,
    "quality_lift": 0.22,
    "host_outcome": 0.08,
    "judge_score": 0.07,
    "latency": 0.05,
    "rating": 0.15,
    "contradiction": 0.08,
}


def compute_reward(inputs: RewardInput) -> RewardOutcome:
    """Normalize heterogeneous feedback into a single bounded reward signal."""
    weights = {**_DEFAULT_WEIGHTS, **inputs.weight_overrides}
    similarity = _clamp(float(inputs.similarity), lo=0.0, hi=1.0)
    quality = _clamp(float(inputs.quality_lift or 0.0), lo=-1.0, hi=1.0)

    raw_components: dict[str, float] = {
        "tier": _tier_signal(inputs.cache_tier),
        "similarity": (similarity * 2.0) - 1.0,
        "quality_lift": quality,
    }
    if inputs.host_outcome is not None:
        raw_components["host_outcome"] = _clamp(float(inputs.host_outcome), lo=-1.0, hi=1.0)
    if inputs.judge_score is not None:
        judge = _clamp(float(inputs.judge_score), lo=0.0, hi=1.0)
        raw_components["judge_score"] = (judge * 2.0) - 1.0
    if inputs.latency_ms is not None:
        latency_ms = max(0.0, float(inputs.latency_ms))
        # 0ms => 1.0, 3s => -1.0, and clamp for slower requests.
        raw_components["latency"] = _clamp(1.0 - ((latency_ms / 3000.0) * 2.0), lo=-1.0, hi=1.0)
    if inputs.rating is not None:
        rating_map = {"useful": 1.0, "partial": 0.3, "irrelevant": -0.3, "wrong": -1.0}
        raw_components["rating"] = rating_map.get(inputs.rating, 0.0)
    contradiction = _clamp(float(inputs.contradiction_signal or 0.0), lo=0.0, hi=1.0)
    if contradiction > 0.0:
        raw_components["contradiction"] = -contradiction

    active_weight_total = 0.0
    weighted_sum = 0.0
    weighted_components: dict[str, float] = {}
    for name, raw in raw_components.items():
        w = max(0.0, float(weights.get(name, 0.0)))
        if w == 0.0:
            continue
        active_weight_total += w
        contrib = raw * w
        weighted_components[f"{name}_raw"] = raw
        weighted_components[f"{name}_weighted"] = contrib
        weighted_sum += contrib

    if active_weight_total <= 0.0:
        return RewardOutcome(reward_total=0.0, reward_components={"reward_total": 0.0})

    reward_total = _clamp(weighted_sum / active_weight_total, lo=-1.0, hi=1.0)
    weighted_components["reward_total"] = reward_total
    return RewardOutcome(reward_total=reward_total, reward_components=weighted_components)
