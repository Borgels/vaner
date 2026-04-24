# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

DOMAINS: tuple[str, ...] = ("coding", "research", "writing", "ops")
MODES: tuple[str, ...] = ("understand", "implement", "debug", "validate", "plan", "explain")

_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "coding": ("code", "repo", "function", "class", "module", "refactor", "api", "test"),
    "research": ("research", "paper", "benchmark", "evidence", "study", "compare", "analysis"),
    "writing": ("write", "copy", "draft", "tone", "grammar", "edit", "summary"),
    "ops": ("deploy", "infra", "incident", "monitor", "latency", "runtime", "oncall", "k8s"),
}

_MODE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "understand": ("understand", "what", "where", "explain", "overview"),
    "implement": ("implement", "add", "create", "build", "ship"),
    "debug": ("debug", "error", "traceback", "fail", "bug", "fix"),
    "validate": ("test", "validate", "verify", "review", "audit"),
    "plan": ("plan", "roadmap", "design", "architecture", "strategy"),
    "explain": ("why", "explain", "rationale", "reason"),
}


@dataclass(frozen=True, slots=True)
class TaxonomyPosterior:
    domain_probs: dict[str, float]
    mode_probs: dict[str, float]
    domain: str
    mode: str


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(value)) for value in scores.values())
    if total <= 0.0:
        n = max(1, len(scores))
        return {key: 1.0 / n for key in scores}
    return {key: max(0.0, float(value)) / total for key, value in scores.items()}


def classify_taxonomy(query: str) -> TaxonomyPosterior:
    q = query.lower()
    domain_scores = {domain: 0.1 for domain in DOMAINS}
    mode_scores = {mode: 0.1 for mode in MODES}

    for domain, keywords in _DOMAIN_KEYWORDS.items():
        for keyword in keywords:
            if keyword in q:
                domain_scores[domain] += 1.0
    for mode, keywords in _MODE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in q:
                mode_scores[mode] += 1.0

    domain_probs = _normalize(domain_scores)
    mode_probs = _normalize(mode_scores)
    domain = max(domain_probs.items(), key=lambda item: item[1])[0]
    mode = max(mode_probs.items(), key=lambda item: item[1])[0]
    return TaxonomyPosterior(domain_probs=domain_probs, mode_probs=mode_probs, domain=domain, mode=mode)


EmbeddingCallable = Callable[[list[str]], Awaitable[list[list[float]]]]


def _cosine(a: list[float], b: list[float]) -> float:
    dot: float = sum(x * y for x, y in zip(a, b, strict=False))
    mag_a: float = sum(x * x for x in a) ** 0.5
    mag_b: float = sum(y * y for y in b) ** 0.5
    if mag_a <= 0.0 or mag_b <= 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


class EmbeddingTaxonomyClassifier:
    """Nearest-centroid taxonomy classifier with keyword fallback."""

    def __init__(
        self,
        *,
        domain_centroids: dict[str, list[float]] | None = None,
        mode_centroids: dict[str, list[float]] | None = None,
    ) -> None:
        self.domain_centroids = domain_centroids or {}
        self.mode_centroids = mode_centroids or {}

    async def classify(self, query: str, embed: EmbeddingCallable | None) -> TaxonomyPosterior:
        if embed is None or not self.domain_centroids or not self.mode_centroids:
            return classify_taxonomy(query)
        vectors = await embed([query])
        if not vectors:
            return classify_taxonomy(query)
        query_vec = vectors[0]
        domain_scores = {domain: max(0.0, _cosine(query_vec, centroid)) for domain, centroid in self.domain_centroids.items()}
        mode_scores = {mode: max(0.0, _cosine(query_vec, centroid)) for mode, centroid in self.mode_centroids.items()}
        if not domain_scores or not mode_scores:
            return classify_taxonomy(query)
        domain_probs = _normalize(domain_scores)
        mode_probs = _normalize(mode_scores)
        return TaxonomyPosterior(
            domain_probs=domain_probs,
            mode_probs=mode_probs,
            domain=max(domain_probs.items(), key=lambda item: item[1])[0],
            mode=max(mode_probs.items(), key=lambda item: item[1])[0],
        )
