# SPDX-License-Identifier: Apache-2.0
"""0.8.2 WS2 — unified goal-inference pipeline tests.

Covers :mod:`vaner.intent.goal_inference` (merge coordinator),
:mod:`vaner.intent.goal_inference_commits`,
:mod:`vaner.intent.goal_inference_queries`, and
:mod:`vaner.intent.goal_inference_artefacts`.
"""

from __future__ import annotations

import math

from vaner.intent.goal_inference import (
    GoalCandidate,
    merge_hints,
    normalize_title,
)
from vaner.intent.goal_inference_commits import cluster_commit_subjects
from vaner.intent.goal_inference_queries import cluster_query_texts
from vaner.intent.goals import GoalEvidence

# -------------------------------------------------------------------------
# merge_hints
# -------------------------------------------------------------------------


def test_merge_hints_dedup_by_normalized_title() -> None:
    candidates = [
        GoalCandidate(title="JWT migration", source="branch_name", confidence=0.8),
        GoalCandidate(title="  jwt   migration  ", source="commit_cluster", confidence=0.6),
    ]
    goals = merge_hints(candidates)
    assert len(goals) == 1
    assert goals[0].title == "JWT migration"  # primary wins display


def test_merge_hints_primary_source_priority() -> None:
    candidates = [
        GoalCandidate(title="Ship 0.8.2", source="commit_cluster", confidence=0.6),
        GoalCandidate(title="Ship 0.8.2", source="branch_name", confidence=0.8),
        GoalCandidate(title="Ship 0.8.2", source="user_declared", confidence=1.0),
    ]
    goals = merge_hints(candidates)
    assert goals[0].source == "user_declared"


def test_merge_hints_artefact_declared_outranks_branch_name() -> None:
    candidates = [
        GoalCandidate(title="Auth refactor", source="branch_name", confidence=0.85),
        GoalCandidate(title="Auth refactor", source="artefact_declared", confidence=0.85),
    ]
    goals = merge_hints(candidates)
    assert goals[0].source == "artefact_declared"


def test_merge_hints_corroboration_bonus() -> None:
    candidates = [
        GoalCandidate(title="x", source="branch_name", confidence=0.70),
        GoalCandidate(title="x", source="commit_cluster", confidence=0.50),
        GoalCandidate(title="x", source="query_cluster", confidence=0.40),
    ]
    goals = merge_hints(candidates)
    assert math.isclose(goals[0].confidence, 0.80, rel_tol=1e-6)


def test_merge_hints_evidence_union() -> None:
    candidates = [
        GoalCandidate(
            title="x",
            source="branch_name",
            confidence=0.8,
            evidence=(GoalEvidence(kind="branch_name", value="feat/x"),),
        ),
        GoalCandidate(
            title="x",
            source="commit_cluster",
            confidence=0.6,
            evidence=(GoalEvidence(kind="commit_sha", value="abc", weight=1.0),),
        ),
    ]
    goals = merge_hints(candidates)
    kinds = {e.kind for e in goals[0].evidence}
    assert kinds == {"branch_name", "commit_sha"}


def test_merge_hints_union_artefact_refs_and_files() -> None:
    candidates = [
        GoalCandidate(
            title="x",
            source="artefact_declared",
            confidence=0.9,
            artefact_refs=("aid1",),
            related_files=("a.py",),
        ),
        GoalCandidate(
            title="x",
            source="artefact_inferred",
            confidence=0.7,
            artefact_refs=("aid2",),
            related_files=("b.py",),
        ),
    ]
    goals = merge_hints(candidates)
    g = goals[0]
    assert set(g.artefact_refs) == {"aid1", "aid2"}
    assert set(g.related_files) == {"a.py", "b.py"}


def test_merge_hints_empty_input_returns_empty() -> None:
    assert merge_hints([]) == []


def test_merge_hints_drops_zero_confidence() -> None:
    candidates = [
        GoalCandidate(title="x", source="commit_cluster", confidence=0.0),
        GoalCandidate(title="y", source="branch_name", confidence=0.8),
    ]
    goals = merge_hints(candidates)
    titles = [g.title for g in goals]
    assert titles == ["y"]


def test_normalize_title_collapses_whitespace() -> None:
    assert normalize_title("  JWT   Migration  ") == "jwt migration"


# -------------------------------------------------------------------------
# commit clustering
# -------------------------------------------------------------------------


def test_commit_cluster_conventional_commit_scope() -> None:
    subjects = [
        "feat(auth): add JWT middleware",
        "feat(auth): wire refresh token",
        "fix(auth): handle expired token",
        "chore(release): v0.8.1",
        "docs: update readme",
    ]
    cands = cluster_commit_subjects(subjects)
    assert any(c.title == "Auth" for c in cands)
    auth = next(c for c in cands if c.title == "Auth")
    assert auth.source == "commit_cluster"
    assert len(auth.evidence) == 3


def test_commit_cluster_keyword_when_no_scope() -> None:
    subjects = [
        "improve classifier tuning",
        "add classifier fixture corpus",
        "tune classifier thresholds",
    ]
    cands = cluster_commit_subjects(subjects)
    assert any("Classifier" in c.title for c in cands)


def test_commit_cluster_respects_min_commits_floor() -> None:
    subjects = [
        "feat(auth): one",
        "feat(other): two",
    ]
    # Scope clusters of size 1 don't pass the floor.
    cands = cluster_commit_subjects(subjects, min_commits=2)
    assert cands == []


def test_commit_cluster_ignores_empty_input() -> None:
    assert cluster_commit_subjects([]) == []
    assert cluster_commit_subjects(["", "   "]) == []


# -------------------------------------------------------------------------
# query clustering
# -------------------------------------------------------------------------


def test_query_cluster_detects_shared_domain_term() -> None:
    qs = [
        "how does the database migration work",
        "database schema for the queries",
        "database connection pool configuration",
    ]
    cands = cluster_query_texts(qs)
    assert any("Database" in c.title for c in cands)


def test_query_cluster_respects_min_queries_floor() -> None:
    qs = [
        "only mention database once here",
        "another thing",
    ]
    # Below the 3-query floor.
    assert cluster_query_texts(qs) == []


def test_query_cluster_stopwords_filtered_out() -> None:
    qs = [
        "how does the thing work",
        "what does the thing do",
        "please tell me what thing is",
    ]
    # "the", "does", "what", "please" are stopwords; "thing" is a generic
    # stopword too in my list — so nothing should cluster strongly.
    cands = cluster_query_texts(qs)
    # If anything, it's not "the" or "thing".
    for c in cands:
        assert c.title.lower() not in {"the", "thing", "what"}


def test_query_cluster_confidence_is_lower_tier() -> None:
    qs = ["database foo", "database bar", "database baz"]
    cands = cluster_query_texts(qs)
    # Query clustering max confidence is 0.6 — well below commit / branch /
    # artefact priors. This keeps noisy repeated queries from overruling
    # stronger inference sources.
    for c in cands:
        assert c.confidence <= 0.60
