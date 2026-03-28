"""Tests for artifact scoring."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from vaner_tools.scoring import score_artifacts, tokenize

# ---------------------------------------------------------------------------
# Mock Artefact
# ---------------------------------------------------------------------------


@dataclass
class MockArtefact:
    source_path: str
    content: str
    kind: str = "file_summary"
    key: str = ""
    source_mtime: float = 0.0
    generated_at: float = 0.0
    model: str = "test"
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"


# ---------------------------------------------------------------------------
# 1. Stopwords filtered
# ---------------------------------------------------------------------------


def test_tokenize_filters_stopwords():
    result = tokenize("how does the cache work")
    assert "how" not in result
    assert "does" not in result
    assert "the" not in result
    assert "cache" in result
    assert "work" in result
    assert result == ["cache", "work"]


def test_tokenize_filters_short_tokens():
    # tokens <= 2 chars should be filtered
    result = tokenize("a go do it")
    assert result == []


def test_tokenize_lowercases():
    result = tokenize("ArtefactStore Cache")
    assert "artefactstore" in result or "artefact" in result or "cache" in result
    for t in result:
        assert t == t.lower()


# ---------------------------------------------------------------------------
# 2. TF-IDF ranks correctly
# ---------------------------------------------------------------------------


def test_tfidf_ranks_by_frequency():
    """Artifact A mentions 'artefact_store' 5 times; B mentions it once.
    Query 'artefact store' should rank A above B.
    """
    content_a = "artefact_store artefact_store artefact_store artefact_store artefact_store other stuff here"
    content_b = "artefact_store some unrelated content about something else entirely"
    content_c = "completely unrelated content about networking protocols and sockets"

    a = MockArtefact(source_path="libs/something.py", content=content_a)
    b = MockArtefact(source_path="libs/other.py", content=content_b)
    c = MockArtefact(source_path="libs/unrelated.py", content=content_c)

    results = score_artifacts("artefact store", [a, b, c], max_results=5, min_score=0.0)
    assert len(results) >= 2
    paths = [r.artifact.source_path for r in results]
    assert paths.index("libs/something.py") < paths.index("libs/other.py"), (
        "Artifact A (more mentions) should rank above artifact B"
    )


# ---------------------------------------------------------------------------
# 3. Path boost works
# ---------------------------------------------------------------------------


def test_path_boost_ranks_path_match_higher():
    """Artifact at artefact_store.py path should rank above same-content artifact at different path."""
    shared_content = "handles staleness and caching for the repository"

    path_match = MockArtefact(
        source_path="libs/vaner-tools/src/vaner_tools/artefact_store.py",
        content=shared_content,
    )
    no_path_match = MockArtefact(
        source_path="apps/studio-agent/src/agent/graph.py",
        content=shared_content,
    )

    results = score_artifacts(
        "artefact_store staleness",
        [path_match, no_path_match],
        max_results=5,
        min_score=0.0,
    )
    assert len(results) == 2
    assert results[0].artifact.source_path == "libs/vaner-tools/src/vaner_tools/artefact_store.py", (
        "Path-matching artifact should rank first"
    )
    assert results[0].score > results[1].score


# ---------------------------------------------------------------------------
# 4. Noise artifact filters out
# ---------------------------------------------------------------------------


def test_noise_artifact_ranks_below_source():
    """egg-info top_level.txt with single word 'agent' should rank below a real source file."""
    noise = MockArtefact(
        source_path="apps/studio-agent.egg-info/top_level.txt",
        content="agent",
    )
    real = MockArtefact(
        source_path="apps/studio-agent/src/agent/graph.py",
        content=(
            "LangGraph broker agent for Vaner. Handles tool calls, context loading, "
            "artefact store integration and state machine transitions."
        ),
    )

    results = score_artifacts(
        "how does the broker agent load context from the artefact store",
        [noise, real],
        max_results=5,
        min_score=0.0,
    )
    paths = [r.artifact.source_path for r in results]
    if len(paths) >= 2:
        assert paths.index("apps/studio-agent/src/agent/graph.py") < paths.index(
            "apps/studio-agent.egg-info/top_level.txt"
        ), "Real source file should rank above egg-info noise"
    elif len(paths) == 1:
        # Noise artifact may have been filtered entirely — that's even better
        assert paths[0] == "apps/studio-agent/src/agent/graph.py"


# ---------------------------------------------------------------------------
# 5. Empty query returns empty
# ---------------------------------------------------------------------------


def test_empty_query_returns_empty():
    artifacts = [
        MockArtefact(source_path="libs/foo.py", content="some content"),
        MockArtefact(source_path="libs/bar.py", content="other content"),
    ]
    result = score_artifacts("", artifacts)
    assert result == []


def test_whitespace_only_query_returns_empty():
    artifacts = [MockArtefact(source_path="libs/foo.py", content="content")]
    result = score_artifacts("   ", artifacts)
    assert result == []


def test_stopwords_only_query_returns_empty():
    artifacts = [MockArtefact(source_path="libs/foo.py", content="how does the work")]
    # All tokens are stopwords — nothing meaningful to match
    result = score_artifacts("how does the", artifacts)
    assert result == []


# ---------------------------------------------------------------------------
# 6. min_score filter
# ---------------------------------------------------------------------------


def test_min_score_filter():
    """Artifacts scoring below min_score threshold should be excluded."""
    # Create artifact with very weak content match
    weak = MockArtefact(
        source_path="docs/readme.md",
        content="general information about the project and setup",
    )
    strong = MockArtefact(
        source_path="libs/vaner-tools/src/vaner_tools/artefact_store.py",
        content=(
            "artefact_store artefact_store artefact_store staleness cache "
            "artefact_store source_mtime generated_at"
        ),
    )

    # With a high min_score, weak match should be excluded
    results = score_artifacts(
        "artefact_store staleness",
        [weak, strong],
        max_results=5,
        min_score=0.1,
    )
    paths = [r.artifact.source_path for r in results]
    # Strong match should be present
    assert "libs/vaner-tools/src/vaner_tools/artefact_store.py" in paths
    # All results must meet threshold
    for r in results:
        assert r.score >= 0.1, f"Result {r.artifact.source_path} has score {r.score} below min"


def test_min_score_zero_returns_all_matched():
    """min_score=0 should not filter anything that has any match."""
    artifacts = [
        MockArtefact(source_path="libs/a.py", content="cache staleness rules"),
        MockArtefact(source_path="libs/b.py", content="unrelated networking sockets"),
    ]
    results = score_artifacts("cache", artifacts, max_results=10, min_score=0.0)
    paths = [r.artifact.source_path for r in results]
    assert "libs/a.py" in paths


# ---------------------------------------------------------------------------
# 7. Performance: 200 artifacts in <5ms
# ---------------------------------------------------------------------------


def test_performance_200_artifacts():
    """Scoring 200 artifacts should complete in under 5ms."""
    artifacts = [
        MockArtefact(
            source_path=f"libs/module_{i}/src/vaner_tools/component_{i}.py",
            content=(
                f"This module handles component {i} operations including "
                f"staleness checks, cache management, and artefact scoring "
                f"for the vaner platform. It integrates with the broker agent "
                f"to provide context at prompt time. Index: {i}."
            ),
        )
        for i in range(200)
    ]

    query = "how does the artefact store handle staleness and cache management"

    start = time.perf_counter()
    results = score_artifacts(query, artifacts, max_results=5)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 5.0, f"Scoring 200 artifacts took {elapsed_ms:.2f}ms (limit: 5ms)"
    assert len(results) <= 5


# ---------------------------------------------------------------------------
# 8. Dead-letter artifacts are excluded
# ---------------------------------------------------------------------------


def test_dead_letter_artifacts_excluded():
    """Artifacts with status='dead_letter' should be skipped entirely."""
    live = MockArtefact(source_path="libs/live.py", content="cache staleness scoring artefact")
    dead = MockArtefact(
        source_path="libs/dead.py",
        content="cache staleness scoring artefact dead_letter job",
        status="dead_letter",
    )

    results = score_artifacts("cache staleness", [live, dead], max_results=5, min_score=0.0)
    paths = [r.artifact.source_path for r in results]
    assert "libs/dead.py" not in paths
    assert "libs/live.py" in paths


# ---------------------------------------------------------------------------
# 9. max_results respected
# ---------------------------------------------------------------------------


def test_max_results_respected():
    artifacts = [
        MockArtefact(source_path=f"libs/mod_{i}.py", content=f"cache staleness scoring {i}")
        for i in range(20)
    ]
    results = score_artifacts("cache staleness", artifacts, max_results=3)
    assert len(results) <= 3


# ---------------------------------------------------------------------------
# 10. matched_terms populated correctly
# ---------------------------------------------------------------------------


def test_matched_terms_populated():
    artifact = MockArtefact(
        source_path="libs/artefact_store.py",
        content="artefact_store handles staleness checks for the cache system",
    )
    results = score_artifacts("artefact_store staleness", [artifact], min_score=0.0)
    assert len(results) == 1
    matched = results[0].matched_terms
    assert "artefact_store" in matched or "staleness" in matched
