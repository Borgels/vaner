# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import math
import time

from vaner.models.artefact import Artefact


def _recency_bonus(artefact: Artefact, decay_half_life_seconds: int = 1800) -> float:
    baseline = artefact.last_accessed or artefact.generated_at
    age_seconds = max(0.0, time.time() - baseline)
    decay = math.exp(-age_seconds / decay_half_life_seconds)
    return 0.1 + (0.9 * decay)


def score_artefact(prompt: str, artefact: Artefact) -> float:
    prompt_terms = {term.lower() for term in prompt.split() if len(term) > 2}
    text = f"{artefact.source_path} {artefact.content}".lower()
    hits = sum(1 for term in prompt_terms if term in text)
    recency_bonus = _recency_bonus(artefact)
    return float(hits) + recency_bonus


def select_artefacts(
    prompt: str,
    artefacts: list[Artefact],
    top_n: int = 8,
    preferred_paths: set[str] | None = None,
    preferred_keys: set[str] | None = None,
) -> list[Artefact]:
    preferred_paths = preferred_paths or set()
    preferred_keys = preferred_keys or set()

    def _score(artefact: Artefact) -> float:
        score = score_artefact(prompt, artefact)
        if artefact.source_path in preferred_paths:
            score += 0.8
        if artefact.key in preferred_keys:
            score += 0.8
        return score

    scored = sorted(
        artefacts,
        key=_score,
        reverse=True,
    )
    return scored[:top_n]
