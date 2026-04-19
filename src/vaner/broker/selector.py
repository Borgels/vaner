# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from fnmatch import fnmatch

from vaner.models.artefact import Artefact


def _recency_bonus(artefact: Artefact, decay_half_life_seconds: int = 1800) -> float:
    baseline = artefact.last_accessed or artefact.generated_at
    age_seconds = max(0.0, time.time() - baseline)
    decay = math.exp(-age_seconds / decay_half_life_seconds)
    return 0.1 + (0.9 * decay)


_COMMON_WORDS = frozenset(
    "the and are for but not that with this from have been will can its also more"
    " use used using used using using into they their there when than then what"
    " all any get has had its may not new one out per set via was yet you"
    # short common programming words that dilute scoring
    " work works working longer seems look looks correct correctly already just"
    " which would could should need needs using between only still over under"
    .split()
)


def score_artefact(prompt: str, artefact: Artefact) -> float:
    # Extract identifiers: split on non-alphanumeric boundaries so
    # "col_insert()" → "col_insert", "Matrix.foo" → ["matrix", "foo"]
    raw_terms = re.findall(r"[a-z][a-z0-9_]{2,}", prompt.lower())
    text = f"{artefact.source_path} {artefact.content}".lower()

    score = 0.0
    for term in raw_terms:
        if term in _COMMON_WORDS:
            continue
        if term not in text:
            continue
        # Code identifiers (containing underscore) are stronger signals
        weight = 3.0 if "_" in term else 1.0
        score += weight

    recency_bonus = _recency_bonus(artefact)
    return score + recency_bonus


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


def _build_fts_query(prompt: str) -> str:
    """Build a safe FTS5 query string from a natural-language prompt."""
    tokens = re.findall(r"[a-z][a-z0-9_]{2,}", prompt.lower())
    filtered = [t for t in tokens if t not in _COMMON_WORDS][:15]
    return " ".join(filtered)


async def select_artefacts_fts(
    prompt: str,
    store: object,
    top_n: int = 8,
    preferred_paths: set[str] | None = None,
    preferred_keys: set[str] | None = None,
    scorer: Callable[[str, Artefact], float] | None = None,
    exclude_private: bool = False,
    path_bonuses: list[str] | None = None,
    path_excludes: list[str] | None = None,
) -> list[Artefact]:
    """Two-phase selection: FTS candidate retrieval, then scorer re-rank.

    Falls back to loading all artefacts when the FTS index returns no hits
    or when *store* does not expose ``select_artefacts_fts``.
    """
    from vaner.store.artefacts import ArtefactStore  # avoid circular at module level

    fts_available = isinstance(store, ArtefactStore)

    # Phase 1: FTS candidate retrieval (gracefully skipped if unavailable)
    candidate_keys: set[str] = set()
    if fts_available:
        fts_query = _build_fts_query(prompt)
        if fts_query:
            try:
                candidate_keys = set(await store.select_artefacts_fts(fts_query, limit=50))  # type: ignore[union-attr]
            except Exception:
                candidate_keys = set()

    # Phase 2: load candidates; fall back to full list on FTS miss
    if fts_available:
        all_artefacts: list[Artefact] = await store.list(limit=2000)  # type: ignore[union-attr]
    else:
        return []

    if candidate_keys:
        candidates = [a for a in all_artefacts if a.key in candidate_keys]
        # Always include preferred items even if not in FTS results
        preferred = preferred_keys or set()
        preferred_paths_set = preferred_paths or set()
        for a in all_artefacts:
            if a.key not in candidate_keys and (a.key in preferred or a.source_path in preferred_paths_set):
                candidates.append(a)
    else:
        candidates = all_artefacts

    return select_artefacts(
        prompt,
        candidates,
        top_n=top_n,
        preferred_paths=preferred_paths,
        preferred_keys=preferred_keys,
        scorer=scorer,
        exclude_private=exclude_private,
        path_bonuses=path_bonuses,
        path_excludes=path_excludes,
    )


def select_artefacts(
    prompt: str,
    artefacts: list[Artefact],
    top_n: int = 8,
    preferred_paths: set[str] | None = None,
    preferred_keys: set[str] | None = None,
    scorer: Callable[[str, Artefact], float] | None = None,
    exclude_private: bool = False,
    path_bonuses: list[str] | None = None,
    path_excludes: list[str] | None = None,
) -> list[Artefact]:
    preferred_paths = preferred_paths or set()
    preferred_keys = preferred_keys or set()
    path_bonuses = path_bonuses or []
    path_excludes = path_excludes or []
    apply_origin_rerank = _is_origin_question(prompt)

    scored_rows: list[tuple[float, Artefact]] = []
    for artefact in artefacts:
        if exclude_private and str(artefact.metadata.get("privacy_zone", "")).lower() == "private_local":
            continue
        if any(fnmatch(artefact.source_path, pattern) for pattern in path_excludes):
            continue
        score = scorer(prompt, artefact) if scorer is not None else score_artefact(prompt, artefact)
        if apply_origin_rerank:
            score += _origin_bonus(prompt, artefact.content)
        if artefact.source_path in preferred_paths:
            score += 0.8
        if artefact.key in preferred_keys:
            score += 0.8
        if any(fnmatch(artefact.source_path, pattern) for pattern in path_bonuses):
            score += 0.3
        scored_rows.append((score, artefact))

    ranked = sorted(scored_rows, key=lambda item: item[0], reverse=True)
    selected: list[Artefact] = []
    seen_corpora: set[str] = set()
    min_competitive_score = ranked[0][0] * 0.45 if ranked else 0.0
    for score, artefact in ranked:
        if score < min_competitive_score:
            continue
        corpus_id = str(artefact.metadata.get("corpus_id", "default"))
        if selected and corpus_id not in seen_corpora:
            selected.append(artefact)
            seen_corpora.add(corpus_id)
        elif len(selected) < top_n:
            selected.append(artefact)
            seen_corpora.add(corpus_id)
        if len(selected) >= top_n:
            break
    return selected[:top_n]
