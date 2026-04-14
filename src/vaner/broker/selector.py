# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import math
import re
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


def _is_origin_question(prompt: str) -> bool:
    q = prompt.lower().strip()
    return (
        (q.startswith("where ") or q.startswith("how "))
        and any(term in q for term in ("checked", "implemented", "defined"))
    ) or q.startswith("what conditions")


def _origin_bonus(prompt: str, content: str) -> float:
    prompt_tokens = {token for token in re.findall(r"[a-z0-9_]+", prompt.lower()) if len(token) > 2}
    def_terms = set(re.findall(r"`([a-zA-Z_][a-zA-Z0-9_]*)`", content))
    def_terms |= set(re.findall(r"\*\*([a-zA-Z_][a-zA-Z0-9_]*)\*\*", content))
    def_terms |= set(re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\(", content))
    lowered_terms = {term.lower() for term in def_terms}

    bonus = 0.0
    for token in prompt_tokens:
        for term in lowered_terms:
            if token == term or token in term or term in token:
                bonus += 1.5
    return bonus


def select_artefacts(
    prompt: str,
    artefacts: list[Artefact],
    top_n: int = 8,
    preferred_paths: set[str] | None = None,
    preferred_keys: set[str] | None = None,
) -> list[Artefact]:
    preferred_paths = preferred_paths or set()
    preferred_keys = preferred_keys or set()
    apply_origin_rerank = _is_origin_question(prompt)

    def _score(artefact: Artefact) -> float:
        score = score_artefact(prompt, artefact)
        if apply_origin_rerank:
            score += _origin_bonus(prompt, artefact.content)
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
