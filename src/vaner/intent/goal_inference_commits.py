# SPDX-License-Identifier: Apache-2.0
"""WS2 — commit-subject clustering → goal candidates.

Deferred from the 0.8.1 "workspace goals" milestone and folded into 0.8.2
alongside artefact-based inference. Reads recent commit subjects via
:func:`vaner.daemon.signals.git_reader.read_commit_subjects` and groups
them by shared conventional-commit scope or by the most informative
term, then emits :class:`GoalCandidate` records with
``source="commit_cluster"``.

The clusterer is deliberately deterministic and dependency-free — it
uses scope parsing + stopword-filtered keyword co-occurrence rather than
embeddings. A future iteration can layer an injected LLM summariser on
top when one is available; the structural path is the floor.

Branch-name hints live in :mod:`vaner.intent.branch_parser`. Together
with :mod:`vaner.intent.goal_inference_queries` and
:mod:`vaner.intent.goal_inference_artefacts`, these feed the unified
:func:`vaner.intent.goal_inference.merge_hints` coordinator which the
engine consumes in place of the pre-0.8.2 single-source goal path.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from vaner.intent.goal_inference import GoalCandidate
from vaner.intent.goals import GoalEvidence

# Conventional-commit prefix regex: ``feat(scope): subject`` →
# ``(prefix="feat", scope="scope", subject="subject")``. Accepts the set
# of prefixes the codebase actually uses (see the project CHANGELOG).
_CONVENTIONAL_RE = re.compile(
    r"^(?P<prefix>feat|fix|chore|refactor|docs|test|perf|ci|build|style)"
    r"(?:\((?P<scope>[^)]+)\))?"
    r"(?P<bang>!?):\s*(?P<subject>.+?)\s*$"
)

# Heuristic stopwords for the non-conventional-commit path. Extends the
# English stopword list with words that appear constantly in commit
# subjects and carry no clustering signal.
_STOPWORDS: frozenset[str] = frozenset(
    {
        # short / function words
        "a",
        "an",
        "and",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "into",
        "that",
        "this",
        "we",
        "our",
        "your",
        "you",
        "i",
        "me",
        # commit-filler verbs
        "add",
        "added",
        "adds",
        "adding",
        "fix",
        "fixed",
        "fixes",
        "fixing",
        "update",
        "updated",
        "updates",
        "updating",
        "remove",
        "removed",
        "removes",
        "removing",
        "refactor",
        "refactored",
        "clean",
        "cleanup",
        "tweak",
        "tweaks",
        "change",
        "changes",
        "changed",
        "move",
        "moved",
        "moves",
        "rename",
        "renames",
        "renamed",
        "bump",
        "bumps",
        "bumped",
        "misc",
        "more",
        "some",
        "also",
        "just",
        # boilerplate
        "wip",
        "todo",
        "tbd",
    }
)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")

# Tunables — kept as module-level constants so tests can override them
# without monkeypatching internals.
DEFAULT_MIN_COMMITS_PER_CLUSTER = 2
DEFAULT_MAX_HINTS = 3
DEFAULT_BASE_CONFIDENCE = 0.55
DEFAULT_PER_COMMIT_BOOST = 0.05
DEFAULT_MAX_CONFIDENCE = 0.75


@dataclass(frozen=True, slots=True)
class _Subject:
    """Parsed form of one commit subject line."""

    raw: str
    scope: str | None
    tokens: tuple[str, ...]


def _parse_subject(line: str) -> _Subject:
    """Split a commit subject into ``(scope, tokens)``.

    When the subject matches conventional-commit form, ``scope`` is the
    parenthesised scope (``feat(auth): …`` → ``"auth"``) and tokens come
    from the remainder. Non-conventional subjects have ``scope=None``
    and the full line is tokenized.
    """

    match = _CONVENTIONAL_RE.match(line)
    if match:
        scope = match.group("scope") or None
        body = match.group("subject")
    else:
        scope = None
        body = line
    tokens = tuple(tok.lower() for tok in _WORD_RE.findall(body) if tok.lower() not in _STOPWORDS)
    return _Subject(raw=line, scope=scope, tokens=tokens)


def _cluster_by_scope(subjects: list[_Subject]) -> dict[str, list[_Subject]]:
    """Group subjects by conventional-commit scope. Subjects without a
    scope are left out (they feed the keyword-co-occurrence path)."""

    clusters: dict[str, list[_Subject]] = {}
    for s in subjects:
        if not s.scope:
            continue
        clusters.setdefault(s.scope, []).append(s)
    return clusters


def _cluster_by_keyword(
    subjects: list[_Subject],
    *,
    min_commits: int,
    excluded_keys: set[str],
) -> dict[str, list[_Subject]]:
    """Group subjects by the most frequent non-stopword token they share.

    Each subject is assigned to its most distinctive token (the highest-
    frequency token it carries), subject to:
    - the token must not already be a scope-based cluster key, and
    - the token must appear in at least ``min_commits`` subjects.

    Returns a dict ``{token: [subjects, …]}`` with at least one group
    meeting the floor, or empty when no shared theme emerges.
    """

    token_freq: Counter[str] = Counter()
    for s in subjects:
        for tok in set(s.tokens):
            token_freq[tok] += 1

    eligible = {tok for tok, count in token_freq.items() if count >= min_commits and tok not in excluded_keys}
    if not eligible:
        return {}

    groups: dict[str, list[_Subject]] = {}
    for s in subjects:
        best: tuple[int, str] | None = None
        for tok in s.tokens:
            if tok not in eligible:
                continue
            freq = token_freq[tok]
            if best is None or freq > best[0]:
                best = (freq, tok)
        if best is not None:
            groups.setdefault(best[1], []).append(s)
    return groups


def _title_from_scope(scope: str) -> str:
    """Render a conventional-commit scope as a human-readable title."""

    return scope.replace("-", " ").replace("_", " ").strip().title()


def _title_from_keyword(keyword: str) -> str:
    """Render a keyword as a human-readable title."""

    return keyword.replace("-", " ").replace("_", " ").strip().title()


def _confidence_for_cluster_size(n_commits: int) -> float:
    return min(
        DEFAULT_MAX_CONFIDENCE,
        DEFAULT_BASE_CONFIDENCE + DEFAULT_PER_COMMIT_BOOST * max(0, n_commits - 2),
    )


def cluster_commit_subjects(
    subjects: list[str],
    *,
    min_commits: int = DEFAULT_MIN_COMMITS_PER_CLUSTER,
    max_hints: int = DEFAULT_MAX_HINTS,
) -> list[GoalCandidate]:
    """Cluster commit subjects into goal candidates.

    Two-stage heuristic:

    1. Conventional-commit scopes (``feat(auth): …``) form clusters
       directly — scopes are intentional labels the user wrote, so they
       outrank keyword clusters when both exist.
    2. Remaining subjects are clustered by their most distinctive shared
       non-stopword token. Token clusters must hit the ``min_commits``
       floor to become hints.

    Returns up to ``max_hints`` candidates, ranked by cluster size. Each
    candidate carries one ``GoalEvidence(kind="commit_sha")`` per
    backing commit — WS3 reconciliation uses the commit-sha list to
    update the goal's ``last_observed_at`` when the underlying commits
    change.

    The function is synchronous and dependency-free. A future iteration
    may take an injected LLM summariser to refine cluster titles; the
    structural path is the reliable floor.
    """

    cleaned = [s.strip() for s in subjects if s and s.strip()]
    if not cleaned:
        return []

    parsed = [_parse_subject(s) for s in cleaned]

    scope_clusters = _cluster_by_scope(parsed)
    unscoped = [s for s in parsed if not s.scope]
    keyword_clusters = _cluster_by_keyword(
        unscoped,
        min_commits=min_commits,
        excluded_keys=set(scope_clusters.keys()),
    )

    # Rank clusters by size; scope clusters are prioritised with a
    # tie-break bonus so intentional labels beat inferred themes.
    ranked: list[tuple[int, str, str, list[_Subject]]] = []
    for scope, group in scope_clusters.items():
        if len(group) >= min_commits:
            ranked.append((len(group) + 1, "scope", scope, group))
    for keyword, group in keyword_clusters.items():
        ranked.append((len(group), "keyword", keyword, group))
    ranked.sort(key=lambda entry: entry[0], reverse=True)

    candidates: list[GoalCandidate] = []
    for _, kind, key, group in ranked[:max_hints]:
        title = _title_from_scope(key) if kind == "scope" else _title_from_keyword(key)
        if not title:
            continue
        evidence = tuple(GoalEvidence(kind="commit_sha", value=_fake_sha(s.raw), weight=1.0) for s in group)
        candidates.append(
            GoalCandidate(
                title=title,
                source="commit_cluster",
                confidence=_confidence_for_cluster_size(len(group)),
                description=_cluster_description(group),
                evidence=evidence,
            )
        )
    return candidates


def _fake_sha(subject_line: str) -> str:
    """Produce a stable pseudo-sha from a subject line.

    ``read_commit_subjects`` returns subject *text*, not SHAs, so we
    hash the subject line itself for evidence identity. A caller that
    already has SHAs can post-process the evidence list to substitute
    real ones.
    """

    import hashlib

    return hashlib.sha1(subject_line.encode("utf-8")).hexdigest()[:12]


def _cluster_description(group: list[_Subject]) -> str:
    """Human-readable description listing the clustered subjects."""

    lines = [f"- {s.raw}" for s in group[:5]]
    if len(group) > 5:
        lines.append(f"- … and {len(group) - 5} more")
    return "Recent commits clustered by shared scope/theme:\n" + "\n".join(lines)
