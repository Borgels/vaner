# SPDX-License-Identifier: Apache-2.0
"""WS2 — query-history clustering → goal candidates.

Deferred from 0.8.1 and folded into 0.8.2. Reads recent user queries
from the ``query_history`` table and groups them by shared domain
vocabulary, emitting :class:`GoalCandidate` records with
``source="query_cluster"``.

Like :mod:`vaner.intent.goal_inference_commits`, the clusterer is
deterministic and dependency-free — it uses tokenization + stopword
filtering + co-occurrence rather than embeddings or k-means. The name
"query_cluster" is retained for consistency with the 0.8.1 roadmap
vocabulary even though the implementation does not use embedding
clustering. A future iteration can swap in an embedding-backed path via
the engine's existing embed callable without changing the module's
public surface.

Per-source confidence is calibrated lower than commit / branch / artefact
hints — repeated queries are weak evidence of a persistent goal (users
re-query for many reasons). ``merge_hints`` folds these in only as a
corroboration signal when stronger sources don't disagree.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from vaner.intent.goal_inference import GoalCandidate
from vaner.intent.goals import GoalEvidence

# Query-prose stopwords. Broader than commit-subject stopwords because
# natural-language queries carry more filler.
_STOPWORDS: frozenset[str] = frozenset(
    {
        # function / short words
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
        "my",
        "mine",
        "ours",
        "us",
        "are",
        "am",
        "was",
        "were",
        "been",
        "being",
        "has",
        "had",
        "have",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "might",
        "can",
        "may",
        "must",
        "shall",
        # query-filler vocabulary
        "how",
        "what",
        "when",
        "where",
        "why",
        "who",
        "which",
        "show",
        "tell",
        "explain",
        "find",
        "list",
        "describe",
        "please",
        "help",
        "need",
        "want",
        "looking",
        # generic nouns
        "thing",
        "things",
        "way",
        "ways",
        "stuff",
        "one",
        "some",
        "any",
        "case",
        "cases",
        "part",
        "parts",
        "piece",
        "pieces",
        # boilerplate
        "something",
        "anything",
        "everything",
        "nothing",
    }
)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")

# Tunables — lower confidence range than commits because queries are a
# noisier goal signal.
DEFAULT_MIN_QUERIES_PER_CLUSTER = 3
DEFAULT_MAX_HINTS = 3
DEFAULT_BASE_CONFIDENCE = 0.40
DEFAULT_PER_QUERY_BOOST = 0.05
DEFAULT_MAX_CONFIDENCE = 0.60
# Maximum queries the clusterer considers. The engine reads the most
# recent N; deeper history is typically stale for goal inference.
DEFAULT_MAX_QUERIES = 50


@dataclass(frozen=True, slots=True)
class _Query:
    """One tokenized query record."""

    text: str
    query_id: str
    tokens: tuple[str, ...]


def _tokenize_query(text: str) -> tuple[str, ...]:
    return tuple(tok.lower() for tok in _WORD_RE.findall(text) if tok.lower() not in _STOPWORDS)


def _parse_query(record: dict) -> _Query:
    """Accept either ``{query_text, id}`` rows from the store or plain
    string inputs (via :func:`cluster_query_texts`)."""

    text = str(record.get("query_text") or record.get("text") or "").strip()
    qid = str(record.get("id") or record.get("query_id") or "")
    return _Query(text=text, query_id=qid, tokens=_tokenize_query(text))


def _cluster_by_shared_token(
    queries: list[_Query],
    *,
    min_queries: int,
) -> dict[str, list[_Query]]:
    """Group queries by their most-distinctive shared token.

    A token is "distinctive" if it appears in ≥ ``min_queries`` queries
    and is not a stopword. Each query attaches to its highest-frequency
    eligible token.
    """

    token_freq: Counter[str] = Counter()
    for q in queries:
        for tok in set(q.tokens):
            token_freq[tok] += 1

    eligible = {tok for tok, count in token_freq.items() if count >= min_queries}
    if not eligible:
        return {}

    groups: dict[str, list[_Query]] = {}
    for q in queries:
        best: tuple[int, str] | None = None
        for tok in q.tokens:
            if tok not in eligible:
                continue
            freq = token_freq[tok]
            if best is None or freq > best[0]:
                best = (freq, tok)
        if best is not None:
            groups.setdefault(best[1], []).append(q)
    return groups


def _title_from_token(token: str) -> str:
    """Render a shared query token as a readable goal title."""

    return token.replace("-", " ").replace("_", " ").strip().title()


def _confidence_for_cluster_size(n_queries: int) -> float:
    return min(
        DEFAULT_MAX_CONFIDENCE,
        DEFAULT_BASE_CONFIDENCE + DEFAULT_PER_QUERY_BOOST * max(0, n_queries - DEFAULT_MIN_QUERIES_PER_CLUSTER),
    )


def _cluster_description(group: list[_Query]) -> str:
    lines = [f"- {q.text}" for q in group[:5]]
    if len(group) > 5:
        lines.append(f"- … and {len(group) - 5} more")
    return "Recent queries clustered by shared domain vocabulary:\n" + "\n".join(lines)


def cluster_query_history(
    rows: list[dict],
    *,
    min_queries: int = DEFAULT_MIN_QUERIES_PER_CLUSTER,
    max_hints: int = DEFAULT_MAX_HINTS,
    max_queries: int = DEFAULT_MAX_QUERIES,
) -> list[GoalCandidate]:
    """Cluster recent query-history rows into goal candidates.

    Intended to run on rows returned by
    :meth:`vaner.store.artefacts.ArtefactStore.list_query_history`.
    Accepts duck-typed dicts with ``query_text`` (or ``text``) and
    ``id`` (or ``query_id``) fields.

    Caps the input at ``max_queries`` (most recent), then groups by
    shared-token co-occurrence. Returns up to ``max_hints`` candidates,
    ranked by cluster size. Each candidate carries one
    ``GoalEvidence(kind="query_id")`` per backing query — WS3
    reconciliation uses these to trace goal evidence back to the exact
    prompts that seeded the cluster.
    """

    if not rows:
        return []

    trimmed = list(rows[:max_queries])
    queries = [_parse_query(r) for r in trimmed]
    queries = [q for q in queries if q.tokens]
    if len(queries) < min_queries:
        return []

    groups = _cluster_by_shared_token(queries, min_queries=min_queries)
    if not groups:
        return []

    ranked = sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)

    candidates: list[GoalCandidate] = []
    for token, group in ranked[:max_hints]:
        title = _title_from_token(token)
        if not title:
            continue
        evidence = tuple(GoalEvidence(kind="query_id", value=q.query_id or _fake_query_id(q.text), weight=1.0) for q in group)
        candidates.append(
            GoalCandidate(
                title=title,
                source="query_cluster",
                confidence=_confidence_for_cluster_size(len(group)),
                description=_cluster_description(group),
                evidence=evidence,
            )
        )
    return candidates


def cluster_query_texts(
    texts: list[str],
    *,
    min_queries: int = DEFAULT_MIN_QUERIES_PER_CLUSTER,
    max_hints: int = DEFAULT_MAX_HINTS,
) -> list[GoalCandidate]:
    """Convenience wrapper for callers that only have query text.

    Equivalent to :func:`cluster_query_history` with synthetic ids.
    Primarily for tests and tooling; production callers should pass
    full rows so ``query_id`` evidence values match the store.
    """

    rows = [{"query_text": t, "id": _fake_query_id(t)} for t in texts if t]
    return cluster_query_history(rows, min_queries=min_queries, max_hints=max_hints)


def _fake_query_id(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
