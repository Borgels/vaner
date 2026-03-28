"""Artifact relevance scoring for the vaner prompt-time broker.

Uses BM25 (Okapi BM25) scoring with stopword filtering to rank artifact
candidates against a query. BM25 is a TF-IDF-style algorithm that adds
document-length normalization, preventing short noisy documents from
outscoring rich source files on single-term matches.

Designed to run in <5ms on a typical artifact store of 100-200 entries.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# BM25 hyper-parameters (standard values)
_BM25_K1 = 1.5  # term saturation — higher = more reward for repeated terms
_BM25_B = 0.75  # length normalization — 0 = none, 1 = full

# Multiplier applied to IDF when a query term matches a component of source_path
_PATH_BOOST = 2.0

# Common English stopwords that add noise to relevance scoring
STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "how", "what",
    "where", "when", "why", "who", "which", "that", "this", "these",
    "those", "it", "its", "not", "no", "nor", "so", "yet", "both",
    "either", "neither", "each", "few", "more", "most", "other", "some",
    "such", "than", "too", "very", "just", "also", "after", "before",
    "if", "then", "there", "here",
})


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric, filter stopwords and short tokens."""
    tokens = re.findall(r"[a-z0-9_]+", text.lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) > 2]


@dataclass
class ScoredArtifact:
    artifact: object  # Artefact instance
    score: float
    matched_terms: list[str]


def build_idf_index(artifacts: list) -> dict[str, float]:
    """Pre-compute IDF scores for all terms across corpus. Cache-friendly.

    Smoothed IDF = log(1 + N / (1 + df)) — always positive, handles small corpora.
    """
    total_docs = len(artifacts)
    if total_docs == 0:
        return {}

    doc_freq: dict[str, int] = {}
    for artifact in artifacts:
        doc_text = getattr(artifact, "content", "")
        doc_terms = set(tokenize(doc_text))
        for term in doc_terms:
            doc_freq[term] = doc_freq.get(term, 0) + 1

    return {
        term: math.log(1 + total_docs / (1 + freq))
        for term, freq in doc_freq.items()
    }


def score_single(
    query_tokens: list[str],
    artifact,  # Artefact
    idf_index: dict[str, float],
    avgdl: float = 100.0,
) -> tuple[float, list[str]]:
    """Score a single artifact using BM25. Returns (score, matched_terms).

    Scoring is done on content only; source_path contributes a separate path bonus.
    Document length normalization prevents short noisy docs from inflating scores.
    """
    content = getattr(artifact, "content", "")
    source_path = getattr(artifact, "source_path", "")

    content_tokens = tokenize(content)
    doc_len = len(content_tokens) if content_tokens else 1

    # Raw term frequency in content
    term_counts: dict[str, int] = {}
    for t in content_tokens:
        term_counts[t] = term_counts.get(t, 0) + 1

    # Path components for path-boost check
    path_parts = set(re.findall(r"[a-z0-9_]+", source_path.lower()))

    score = 0.0
    matched: list[str] = []

    for qt in query_tokens:
        idf = idf_index.get(qt, math.log(2.0))  # small positive default if unseen

        # BM25 content score
        tf_raw = term_counts.get(qt, 0)
        if tf_raw > 0:
            # BM25 TF component with length normalization
            norm_tf = (tf_raw * (_BM25_K1 + 1)) / (
                tf_raw + _BM25_K1 * (1 - _BM25_B + _BM25_B * doc_len / avgdl)
            )
            term_score = idf * norm_tf
        else:
            term_score = 0.0

        # Path bonus: additive, based on IDF weight of the term
        # Rewarded separately from content so short docs can't abuse it
        if qt in path_parts:
            term_score += idf * _PATH_BOOST

        if term_score > 0.0:
            score += term_score
            if qt not in matched:
                matched.append(qt)

    return score, matched


def score_artifacts(
    query: str,
    artifacts: list,  # list[Artefact]
    max_results: int = 5,
    min_score: float = 0.05,
) -> list[ScoredArtifact]:
    """Score and rank artifacts against a query using BM25.

    Algorithm:
    1. Tokenize query (stopword filtered)
    2. Compute smoothed IDF for each query term across the corpus
    3. Score each artifact with BM25 (content TF × IDF, length-normalized)
    4. Add path bonus if query term matches source_path components (additive, not multiplicative)
    5. Filter by min_score, return top max_results sorted by score descending

    Returns list sorted by score descending.
    """
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    # Filter dead-letter artifacts (from job store)
    live_artifacts = [
        a for a in artifacts
        if getattr(a, "status", None) != "dead_letter"
    ]

    if not live_artifacts:
        return []

    idf_index = build_idf_index(live_artifacts)

    # Average document length across corpus (for BM25 length normalization)
    lengths = [len(tokenize(getattr(a, "content", ""))) or 1 for a in live_artifacts]
    avgdl = sum(lengths) / len(lengths)

    results: list[ScoredArtifact] = []
    for artifact in live_artifacts:
        score, matched = score_single(query_tokens, artifact, idf_index, avgdl)
        if score >= min_score:
            results.append(ScoredArtifact(artifact=artifact, score=score, matched_terms=matched))

    results.sort(key=lambda x: x.score, reverse=True)
    return results[:max_results]
