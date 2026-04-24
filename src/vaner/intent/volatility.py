# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VolatilityProfile:
    score: float
    drift_fraction: float
    path_count: int
    high_risk_count: int
    medium_risk_count: int
    low_risk_count: int


def classify_path_volatility(path: str) -> float:
    p = path.lower()
    if any(token in p for token in ("readme", "docs/", "changelog", ".md", "guide", "tutorial")):
        return 0.05
    if any(token in p for token in ("test", "spec", "fixtures", "benchmark", "golden")):
        return 0.15
    if any(token in p for token in ("config", ".toml", ".yaml", ".yml", ".json", "workflow", ".github/")):
        return 0.45
    if any(token in p for token in ("auth", "security", "middleware", "policy", "permissions", "secrets")):
        return 1.0
    if any(token in p for token in ("migration", "schema", "store", "sqlite", "db", "database")):
        return 0.85
    if any(token in p for token in ("engine", "router", "daemon", "mcp", "cli")):
        return 0.70
    return 0.7


def semantic_volatility(changed_paths: list[str]) -> float:
    return semantic_volatility_profile(changed_paths).score


def semantic_volatility_profile(changed_paths: list[str]) -> VolatilityProfile:
    if not changed_paths:
        return VolatilityProfile(
            score=0.0,
            drift_fraction=0.0,
            path_count=0,
            high_risk_count=0,
            medium_risk_count=0,
            low_risk_count=0,
        )
    values = [classify_path_volatility(path) for path in changed_paths]
    count = len(values)
    high = sum(1 for value in values if value >= 0.8)
    medium = sum(1 for value in values if 0.4 <= value < 0.8)
    low = count - high - medium
    base_score = sum(values) / max(1, count)
    drift_fraction = high / max(1, count)
    # Emphasize high-risk drift; this term is what the roadmap calls out.
    score = max(0.0, min(1.0, (0.7 * base_score) + (0.3 * drift_fraction)))
    return VolatilityProfile(
        score=score,
        drift_fraction=drift_fraction,
        path_count=count,
        high_risk_count=high,
        medium_risk_count=medium,
        low_risk_count=low,
    )
