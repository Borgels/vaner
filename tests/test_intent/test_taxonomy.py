# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from vaner.intent.taxonomy import (
    DOMAINS,
    MODES,
    EmbeddingTaxonomyClassifier,
    TaxonomyPosterior,
    classify_taxonomy,
)

# ---------------------------------------------------------------------------
# classify_taxonomy — keyword hits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected_domain",
    [
        ("refactor this function module", "coding"),
        ("research paper benchmark analysis", "research"),
        ("write a draft with good grammar", "writing"),
        ("deploy to k8s and monitor latency", "ops"),
    ],
)
def test_classify_taxonomy_domain(query, expected_domain):
    result = classify_taxonomy(query)
    assert isinstance(result, TaxonomyPosterior)
    assert result.domain == expected_domain


@pytest.mark.parametrize(
    "query,expected_mode",
    [
        ("what does this module do, explain please", "understand"),
        ("implement and build the new feature", "implement"),
        ("debug error traceback fix the bug", "debug"),
        ("validate test verify the output", "validate"),
        ("plan the roadmap design architecture", "plan"),
        ("why is this the rationale", "explain"),
    ],
)
def test_classify_taxonomy_mode(query, expected_mode):
    result = classify_taxonomy(query)
    assert result.mode == expected_mode


def test_classify_taxonomy_probs_sum_to_one():
    result = classify_taxonomy("implement a new api function")
    assert sum(result.domain_probs.values()) == pytest.approx(1.0, abs=1e-6)
    assert sum(result.mode_probs.values()) == pytest.approx(1.0, abs=1e-6)


def test_classify_taxonomy_all_domains_present():
    result = classify_taxonomy("generic query")
    assert set(result.domain_probs.keys()) == set(DOMAINS)
    assert set(result.mode_probs.keys()) == set(MODES)


def test_classify_taxonomy_empty_query():
    result = classify_taxonomy("")
    # Uniform prior — all probs equal
    vals = list(result.domain_probs.values())
    assert max(vals) == pytest.approx(min(vals), abs=1e-6)


# ---------------------------------------------------------------------------
# EmbeddingTaxonomyClassifier — no-embed fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_classifier_no_embed_falls_back():
    classifier = EmbeddingTaxonomyClassifier()
    result = await classifier.classify("implement a new module", embed=None)
    assert result.domain == "coding"


@pytest.mark.asyncio
async def test_embedding_classifier_empty_centroids_falls_back():
    classifier = EmbeddingTaxonomyClassifier(domain_centroids={}, mode_centroids={})
    result = await classifier.classify("debug the error traceback", embed=None)
    assert result.mode == "debug"


# ---------------------------------------------------------------------------
# EmbeddingTaxonomyClassifier — centroid ranking with mock embed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_classifier_uses_centroids():
    # coding centroid: [1, 0, 0], research centroid: [0, 1, 0]
    # query embedding: [1, 0, 0] → closest to coding
    domain_centroids = {"coding": [1.0, 0.0, 0.0], "research": [0.0, 1.0, 0.0]}
    mode_centroids = {"implement": [1.0, 0.0], "debug": [0.0, 1.0]}
    classifier = EmbeddingTaxonomyClassifier(domain_centroids=domain_centroids, mode_centroids=mode_centroids)

    async def mock_embed(texts):
        return [[1.0, 0.0, 0.0]] * len(texts)

    result = await classifier.classify("anything", embed=mock_embed)
    assert result.domain == "coding"


@pytest.mark.asyncio
async def test_embedding_classifier_empty_embed_response_falls_back():
    classifier = EmbeddingTaxonomyClassifier(
        domain_centroids={"coding": [1.0, 0.0]},
        mode_centroids={"implement": [1.0, 0.0]},
    )

    async def mock_embed_empty(texts):
        return []

    result = await classifier.classify("build a function", embed=mock_embed_empty)
    assert result.domain == "coding"
