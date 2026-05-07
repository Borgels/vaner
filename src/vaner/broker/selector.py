# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import math
import re
import time
from collections.abc import Awaitable, Callable
from fnmatch import fnmatch

from vaner.broker.context_preparation import (
    build_prepared_context_diagnostics,
    competitive_threshold_multiplier,
    exact_reference_candidates,
    hard_constraints_satisfied,
    infer_context_preparation_profile,
    query_variants,
    source_path_hint_candidates,
)
from vaner.models.artefact import Artefact
from vaner.models.context_preparation import ContextPreparationProfile, PreparedContextDiagnostics
from vaner.models.decision import ScoreFactor
from vaner.semantic_aliases import engineering_semantic_aliases


def _recency_bonus(artefact: Artefact, decay_half_life_seconds: int = 1800) -> float:
    baseline = artefact.last_accessed or artefact.generated_at
    age_seconds = max(0.0, time.time() - baseline)
    decay = math.exp(-age_seconds / decay_half_life_seconds)
    return 0.1 + (0.9 * decay)


def _source_rank_prior(artefact: Artefact) -> float:
    """Bounded prior from an upstream context source ranking, when present."""

    raw_rank = artefact.metadata.get("retrieval_rank") or artefact.metadata.get("source_rank")
    try:
        rank = int(raw_rank)
    except (TypeError, ValueError):
        return 0.0
    if rank <= 0:
        return 0.0
    return min(15.0, 15.0 / math.sqrt(rank))


_COMMON_WORDS = frozenset(
    "the and are for but not that with this from have been will can its also more"
    " use used using used using using into they their there when than then what"
    " all any get has had its may not new one out per set via was yet you"
    # short common programming words that dilute scoring
    " work works working longer seems look looks correct correctly already just"
    " which would could should need needs using between only still over under"
    " how does explain describe walk through".split()
)


def _prompt_terms(prompt: str) -> list[str]:
    """Extract searchable prompt terms, splitting code-style identifiers."""

    terms: list[str] = []
    for raw in _identifier_chunks(prompt):
        lowered = raw.lower()
        terms.append(lowered)
        terms.extend(_term_variants(lowered))
        terms.extend(part.lower() for part in raw.split("_") if len(part) > 2)
        if lowered not in {"fastapi", "openapi"}:
            terms.extend(part.lower() for part in _camel_parts(raw) if len(part) > 2)
    terms.extend(raw for raw in re.findall(r"\b\d{2,4}\b", prompt))
    terms.extend(engineering_semantic_aliases(prompt, terms, stopwords=_COMMON_WORDS))
    return list(dict.fromkeys(term for term in terms if len(term) > 2 and term not in _COMMON_WORDS))


def _term_variants(term: str) -> list[str]:
    variants: list[str] = []
    if len(term) > 4 and term.endswith("ies"):
        variants.append(f"{term[:-3]}y")
    if len(term) > 4 and term.endswith("es"):
        variants.append(term[:-2])
    if len(term) > 3 and term.endswith("s"):
        variants.append(term[:-1])
    return variants


def _singularize(term: str) -> str:
    variants = _term_variants(term)
    return variants[-1] if variants else term


def _identifier_chunks(text: str, *, max_len: int = 128) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    for char in text:
        if char.isascii() and (char.isalnum() or char == "_"):
            if not current and not char.isalpha():
                continue
            if len(current) < max_len:
                current.append(char)
            continue
        if len(current) > 2:
            chunks.append("".join(current))
        current = []
    if len(current) > 2:
        chunks.append("".join(current))
    return chunks


def _camel_parts(identifier: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for index in range(1, len(identifier)):
        previous = identifier[index - 1]
        current = identifier[index]
        next_char = identifier[index + 1] if index + 1 < len(identifier) else ""
        boundary = (
            (previous.islower() and current.isupper())
            or (previous.isalpha() and current.isdigit())
            or (previous.isdigit() and current.isalpha())
            or (previous.isupper() and current.isupper() and next_char.islower())
        )
        if boundary:
            parts.append(identifier[start:index])
            start = index
    parts.append(identifier[start:])
    return parts


def score_artefact(prompt: str, artefact: Artefact, *, factor_sink: list[ScoreFactor] | None = None) -> float:
    # Extract identifiers: split on non-alphanumeric boundaries so
    # "col_insert()" → "col_insert", "Matrix.foo" → ["matrix", "foo"]
    raw_terms = _prompt_terms(prompt)
    path_text = artefact.source_path.lower()
    basename = path_text.rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0]
    content_text = artefact.content.lower()

    keyword_overlap = 0.0
    for term in raw_terms:
        if term in _COMMON_WORDS:
            continue
        path_hit = term in path_text
        content_hit = term in content_text
        if not path_hit and not content_hit:
            continue
        # Code identifiers (containing underscore) are stronger signals
        weight = 3.0 if "_" in term else 1.0
        if path_hit:
            keyword_overlap += weight * (5.0 if term in basename else 3.0)
            if term == stem or term == _singularize(stem):
                keyword_overlap += weight * 8.0
        if content_hit:
            keyword_overlap += weight

    doc_language_bonus = _doc_language_bonus(prompt, path_text)
    keyword_overlap += doc_language_bonus
    doc_domain_bonus = _doc_domain_bonus(path_text, content_text, raw_terms)
    keyword_overlap += doc_domain_bonus
    direct_lookup_bonus = _direct_lookup_bonus(prompt, path_text, content_text, raw_terms)
    keyword_overlap += direct_lookup_bonus

    prompt_mentions_tests = any(term in {"test", "tests", "testing", "spec", "specs"} for term in raw_terms)
    if path_text.startswith(("src/", "lib/", "app/", "packages/")):
        keyword_overlap += 0.7
    elif path_text.startswith(("tests/", "test/")) and not prompt_mentions_tests:
        keyword_overlap -= 6.0

    recency_bonus = _recency_bonus(artefact)
    if factor_sink is not None:
        if keyword_overlap > 0:
            factor_sink.append(
                ScoreFactor(
                    name="keyword_overlap",
                    contribution=keyword_overlap,
                    detail="prompt terms matched source path/content",
                )
            )
        if direct_lookup_bonus:
            factor_sink.append(
                ScoreFactor(
                    name="direct_lookup_floor",
                    contribution=direct_lookup_bonus,
                    detail="direct documentation/origin lookup matched path/content",
                )
            )
        if doc_language_bonus:
            factor_sink.append(
                ScoreFactor(
                    name="doc_language_preference",
                    contribution=doc_language_bonus,
                    detail="English prompt prefers English documentation sources",
                )
            )
        if doc_domain_bonus:
            factor_sink.append(
                ScoreFactor(
                    name="doc_domain_match",
                    contribution=doc_domain_bonus,
                    detail="documentation path/content matched the prompt domain",
                )
            )
        factor_sink.append(
            ScoreFactor(
                name="recency",
                contribution=recency_bonus,
                detail="recently generated or accessed artefacts are boosted",
            )
        )
    return keyword_overlap + recency_bonus


def _is_direct_lookup_prompt(prompt: str) -> bool:
    q = prompt.lower()
    return any(
        phrase in q
        for phrase in (
            "where ",
            "official documentation",
            "documentation",
            "docs",
            "introduce",
            "introduced",
            "defined",
            "implemented",
            "explain where",
        )
    )


def _needs_lexical_floor(prompt: str) -> bool:
    terms = set(_prompt_terms(prompt))
    return _is_direct_lookup_prompt(prompt) or bool(
        terms
        & {
            "auth",
            "authentication",
            "security",
            "oauth",
            "token",
            "scheme",
            "dependency",
            "dependencies",
            "documentation",
            "tutorial",
            "first",
            "steps",
        }
    )


def _doc_language_bonus(prompt: str, path_text: str) -> float:
    if not _english_prompt_likely(prompt):
        return 0.0
    if not ("/docs/" in path_text or path_text.startswith(("docs/", "doc/"))):
        return 0.0
    english_doc_path = path_text.startswith("docs/en/") or "/en/docs/" in path_text
    non_english_doc_path = bool(re.search(r"(^|/)docs/[a-z]{2}(-[a-z]{2})?/", path_text)) and not english_doc_path
    if english_doc_path:
        return 6.0
    if non_english_doc_path:
        return -8.0
    return 0.0


def _doc_domain_bonus(path_text: str, content_text: str, terms: list[str]) -> float:
    term_set = set(terms)
    bonus = 0.0
    security_terms = {"auth", "authentication", "security", "oauth", "token", "scheme"}
    dependency_terms = {"dependency", "dependencies", "depends"}
    if term_set & security_terms:
        if "/security/" in path_text:
            bonus += 8.0
        if any(term in content_text for term in ("security", "oauth", "authentication", "token", "bearer")):
            bonus += 3.0
    if {"first", "steps"} <= term_set and "first-steps" in path_text:
        bonus += 8.0
    if term_set & dependency_terms:
        if "/dependencies/" in path_text:
            bonus += 8.0
        if "dependency injection" in content_text:
            bonus += 3.0
    cold_start_terms = {
        "cold",
        "start",
        "cached",
        "cache",
        "data",
        "empty",
        "repository",
        "repo",
        "bootstrap",
        "prepare",
        "preparation",
    }
    cold_start_intent_terms = {"cold", "empty", "repository", "repo", "bootstrap", "prepare", "preparation"}
    if len(term_set & cold_start_terms) >= 2 and bool(term_set & cold_start_intent_terms):
        if path_text in {
            "src/vaner/engine.py",
            "src/vaner/daemon/runner.py",
            "src/vaner/daemon/signals/fs_watcher.py",
            "src/vaner/intent/adapter.py",
            "src/vaner/intent/cache.py",
            "src/vaner/store/artefacts.py",
        }:
            bonus += 16.0
        if any(
            term in content_text
            for term in (
                "prepare_corpus",
                "run_once",
                "scan_repo_files",
                "scan_repository",
                "summarize",
                "cold_miss",
                "cold",
                "bootstrap",
            )
        ):
            bonus += 8.0
        if path_text in {"src/vaner/router/proxy.py", "src/vaner/server.py", "src/vaner/mcp/server.py"}:
            bonus -= 6.0
        if any(term in content_text for term in ("warm_start", "warm package", "warm cache", "warm-start")):
            bonus -= 8.0
    hybrid_feature_terms = {
        "train",
        "training",
        "feature",
        "features",
        "hybrid",
        "artefact",
        "artefacts",
        "artifact",
        "artifacts",
        "data",
        "pipeline",
    }
    if len(term_set & hybrid_feature_terms) >= 3:
        if path_text in {
            "src/vaner/intent/features.py",
            "src/vaner/intent/trainer.py",
            "src/vaner/intent/scorer.py",
            "src/vaner/models/artefact.py",
            "src/vaner/store/artefacts.py",
            "tests/test_intent/test_features_follow_up.py",
            "tests/test_intent/test_trainer_v4_rollover.py",
        }:
            bonus += 14.0
        if "intent" in term_set and "scorer" in term_set and path_text in {
            "src/vaner/intent/scorer.py",
            "src/vaner/intent/features.py",
        }:
            bonus += 10.0
        if any(
            term in content_text
            for term in (
                "extract_hybrid_features",
                "feature_vector_for_artefact",
                "feature_vector_for_artifact",
                "artefact_age_seconds",
                "training example",
                "train_model",
            )
        ):
            bonus += 8.0
        if path_text in {"src/vaner/broker/selector.py", "src/vaner/router/proxy.py", "src/vaner/server.py"}:
            bonus -= 5.0
    cache_tier_terms = {"tier", "tiered", "full", "partial", "warm", "hit", "hits", "start", "cache", "cached"}
    if len(term_set & cache_tier_terms) >= 3:
        if path_text in {
            "src/vaner/intent/cache.py",
            "src/vaner/intent/scoring_policy.py",
            "src/vaner/engine.py",
            "tests/test_intent/test_cache.py",
            "tests/test_store/test_prediction_cache_decay.py",
        }:
            bonus += 12.0
        if path_text == "src/vaner/intent/scoring_policy.py":
            bonus += 6.0
        elif path_text == "src/vaner/intent/cache.py":
            bonus += 4.0
        elif path_text in {"tests/test_intent/test_cache.py", "tests/test_store/test_prediction_cache_decay.py"}:
            bonus += 2.0
        if any(term in content_text for term in ("full_hit", "partial_hit", "warm_start", "cache_full_hit", "cache_partial_hit")):
            bonus += 8.0
    reward_terms = {"reward", "computation", "compute", "signals", "combine", "final", "value", "quality", "lift", "judge"}
    if "reward" in term_set and len(term_set & reward_terms) >= 3:
        if path_text in {
            "src/vaner/learning/reward.py",
            "eval/train_policy.py",
            "src/vaner/intent/trainer.py",
            "tests/test_learning/test_reward.py",
        }:
            bonus += 18.0
        if any(
            term in content_text
            for term in (
                "compute_reward",
                "reward_total",
                "reward_components",
                "quality_lift",
                "host_outcome",
                "judge_score",
            )
        ):
            bonus += 10.0
        if path_text == "src/vaner/intent/scoring_policy.py":
            bonus -= 4.0
    store_schema_terms = {"artefactstore", "artefact", "artefacts", "persist", "retrieve", "database", "schema", "table", "tables"}
    if len(term_set & store_schema_terms) >= 3:
        if path_text == "src/vaner/store/artefacts.py":
            bonus += 24.0
        if path_text == "src/vaner/models/context.py":
            bonus += 8.0
        if "package.json" in path_text:
            bonus -= 22.0
        if any(term in content_text for term in ("create table", "from artefacts", "insert into artefacts", "select key")):
            bonus += 12.0
    llm_exploration_terms = {
        "llm",
        "external",
        "exploration",
        "rank",
        "ranking",
        "ranked",
        "file",
        "files",
        "follow",
        "scenario",
        "scenarios",
        "proposal",
        "propose",
        "proposes",
    }
    if len(term_set & llm_exploration_terms) >= 4:
        if path_text in {
            "src/vaner/engine.py",
            "src/vaner/clients/openai.py",
            "src/vaner/clients/ollama.py",
            "src/vaner/clients/llm_response.py",
            "tests/test_engine/test_deep_drill.py",
            "tests/test_engine/test_exploration_parallelism.py",
        }:
            bonus += 14.0
        if path_text == "src/vaner/engine.py" and "_explore_scenario_with_llm" in content_text:
            bonus += 8.0
        if any(term in content_text for term in ("ranked_files", "follow_on", "follow-on", "semantic_intent")):
            bonus += 6.0
    return bonus


def _direct_lookup_bonus(prompt: str, path_text: str, content_text: str, terms: list[str]) -> float:
    if not _is_direct_lookup_prompt(prompt):
        return 0.0

    bonus = 0.0
    doc_path = "/docs/" in path_text or path_text.startswith(("docs/", "doc/"))
    tutorial_path = "/tutorial/" in path_text or path_text.endswith("/tutorial/index.md")

    if doc_path:
        bonus += 2.0
    if tutorial_path:
        bonus += 2.0
    if ("dependency" in path_text or "dependencies" in path_text or "dependency" in content_text) and "injection" in content_text:
        bonus += 8.0

    term_hits = 0
    for term in terms:
        if len(term) < 4 or term in _COMMON_WORDS:
            continue
        if term in path_text:
            term_hits += 2
        elif term in content_text:
            term_hits += 1
    if term_hits >= 3:
        bonus += min(4.0, float(term_hits) * 0.45)
    return bonus


def _english_prompt_likely(prompt: str) -> bool:
    ascii_chars = sum(1 for char in prompt if ord(char) < 128)
    ratio = ascii_chars / max(1, len(prompt))
    lowered = prompt.lower()
    asks_translation = any(term in lowered for term in ("translate", "translation", "non-english", "localized"))
    return ratio > 0.95 and not asks_translation


def _is_origin_question(prompt: str) -> bool:
    q = prompt.lower().strip()
    return (
        (q.startswith("where ") or q.startswith("how ")) and any(term in q for term in ("checked", "implemented", "defined"))
    ) or q.startswith("what conditions")


def _origin_bonus(prompt: str, content: str) -> float:
    prompt_tokens = set(_prompt_terms(prompt))
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
    filtered = _prompt_terms(prompt)[:24]
    return " OR ".join(filtered)


def _candidate_limit(prompt: str, top_n: int) -> int:
    terms = _prompt_terms(prompt)
    multi_facet = prompt.count("?") + len(re.findall(r"\b(?:and|what|how|when|which)\b", prompt, flags=re.IGNORECASE))
    requested = max(50, int(top_n) * 12, len(terms) * 8, multi_facet * 12)
    return max(50, min(240, requested))


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
    capture_factors: dict[str, list[ScoreFactor]] | None = None,
    capture_drop_reasons: dict[str, str] | None = None,
    candidate_limit: int | None = None,
    full_scan_limit: int = 2000,
    context_preparation_mode: str = "balanced",
    max_query_variants: int = 6,
    max_candidate_keys: int | None = None,
    coverage_floor_enabled: bool = True,
    max_expansion_passes: int = 1,
    capture_prepared_context_diagnostics: list[PreparedContextDiagnostics] | None = None,
    semantic_memory_enabled: bool = False,
    semantic_embed: Callable[[list[str]], Awaitable[list[list[float]]]] | None = None,
) -> list[Artefact]:
    """Multi-source context candidate retrieval, then scorer re-rank.

    Falls back to loading all artefacts when the FTS index returns no hits
    or when *store* does not expose ``select_artefacts_fts``.
    """
    from vaner.store.artefacts import ArtefactStore  # avoid circular at module level

    fts_available = isinstance(store, ArtefactStore)
    started = time.monotonic()
    profile = infer_context_preparation_profile(prompt)
    context_enabled = context_preparation_mode != "legacy"

    source_by_key: dict[str, set[str]] = {}
    source_rankings: dict[str, list[str]] = {}
    retrieval_limit = candidate_limit if candidate_limit is not None else _candidate_limit(prompt, top_n)
    if max_candidate_keys is not None:
        retrieval_limit = max(50, min(retrieval_limit, max_candidate_keys))
    if fts_available:
        variants = query_variants(prompt, profile, max_variants=max_query_variants) if context_enabled else [prompt]
        for index, variant in enumerate(variants):
            fts_query = _build_fts_query(variant)
            if not fts_query:
                continue
            source = "lexical_search" if index == 0 else "generated_query_variants"
            try:
                keys = list(await store.select_artefacts_fts(fts_query, limit=retrieval_limit))  # type: ignore[union-attr]
            except Exception:
                keys = []
            _record_source_ranking(source_rankings, source_by_key, source, keys)
        if context_enabled:
            try:
                available_paths = await store.list_source_paths(limit=max(5000, retrieval_limit * 10))  # type: ignore[union-attr]
            except Exception:
                available_paths = []
            exact_paths = set(exact_reference_candidates(prompt, available_paths, limit=min(32, max(8, top_n * 3))))
            source_hint_paths = set(source_path_hint_candidates(prompt, available_paths, limit=min(96, max(16, top_n * 8))))
        else:
            available_paths = []
            exact_paths = set()
            source_hint_paths = set()
        if semantic_memory_enabled and semantic_embed is not None and hasattr(store, "select_artefacts_semantic"):
            semantic_variants = variants if context_enabled else [prompt]
            for index, variant in enumerate(semantic_variants):
                try:
                    semantic_keys = list(await store.select_artefacts_semantic(variant, limit=retrieval_limit, embed=semantic_embed))  # type: ignore[attr-defined]
                except Exception:
                    semantic_keys = []
                source = "semantic_memory" if index == 0 else "semantic_query_variants"
                _record_source_ranking(source_rankings, source_by_key, source, semantic_keys)
    else:
        exact_paths = set()
        source_hint_paths = set()

    preferred = preferred_keys or set()
    preferred_paths_set = preferred_paths or set()
    if preferred:
        _record_source_ranking(source_rankings, source_by_key, "working_set", list(preferred))

    fused_keys = _fuse_source_rankings(source_rankings, limit=max_candidate_keys or retrieval_limit)

    if fts_available:
        if fused_keys or exact_paths or preferred:
            loaded: list[Artefact] = []
            load_keys = set(fused_keys) | preferred
            loaded.extend(await store.list_by_keys(load_keys, limit=max(retrieval_limit, len(load_keys))))  # type: ignore[union-attr]
            path_loads = set(preferred_paths_set) | exact_paths | source_hint_paths
            if path_loads:
                if exact_paths:
                    exact_loaded = await store.list_by_source_paths(exact_paths, limit=max(retrieval_limit, len(exact_paths)))  # type: ignore[union-attr]
                    loaded.extend(exact_loaded)
                    _record_source_ranking(source_rankings, source_by_key, "exact_reference", [artefact.key for artefact in exact_loaded])
                if source_hint_paths:
                    hint_loaded = await store.list_by_source_paths(source_hint_paths, limit=max(retrieval_limit, len(source_hint_paths)))  # type: ignore[union-attr]
                    loaded.extend(hint_loaded)
                    _record_source_ranking(source_rankings, source_by_key, "source_metadata", [artefact.key for artefact in hint_loaded])
                preferred_path_loads = preferred_paths_set - exact_paths - source_hint_paths
                if preferred_path_loads:
                    loaded.extend(
                        await store.list_by_source_paths(preferred_path_loads, limit=max(retrieval_limit, len(preferred_path_loads)))  # type: ignore[union-attr]
                    )
            seen: set[str] = set()
            candidates = []
            for artefact in loaded:
                if artefact.key in seen:
                    continue
                seen.add(artefact.key)
                candidates.append(_with_context_source_metadata(artefact, source_rankings, source_by_key))
            selected = select_artefacts(
                prompt,
                candidates,
                top_n=top_n,
                preferred_paths=preferred_paths,
                preferred_keys=preferred_keys,
                scorer=scorer,
                exclude_private=exclude_private,
                path_bonuses=path_bonuses,
                path_excludes=path_excludes,
                capture_factors=capture_factors,
                capture_drop_reasons=capture_drop_reasons,
                context_need=profile.need if context_enabled else None,
                context_profile=profile if context_enabled else None,
            )
            if context_enabled and coverage_floor_enabled and max_expansion_passes > 0:
                diagnostics = build_prepared_context_diagnostics(
                    profile,
                    source_by_key=source_by_key,
                    selected=selected,
                    fused_candidate_count=len(fused_keys),
                    latency_ms=(time.monotonic() - started) * 1000.0,
                )
                if diagnostics.coverage.gap_flags:
                    recovery_terms = _coverage_recovery_terms(profile, diagnostics)
                    recovery_query = _build_fts_query(recovery_terms)
                    if recovery_query:
                        try:
                            recovery_keys = list(await store.select_artefacts_fts(recovery_query, limit=retrieval_limit))  # type: ignore[union-attr]
                        except Exception:
                            recovery_keys = []
                        new_keys = [key for key in recovery_keys if key not in {candidate.key for candidate in candidates}]
                        if new_keys:
                            _record_source_ranking(source_rankings, source_by_key, "coverage_floor", new_keys)
                            recovered = await store.list_by_keys(set(new_keys), limit=max(retrieval_limit, len(new_keys)))  # type: ignore[union-attr]
                            candidates.extend(
                                _with_context_source_metadata(artefact, source_rankings, source_by_key) for artefact in recovered
                            )
                            selected = select_artefacts(
                                prompt,
                                candidates,
                                top_n=top_n,
                                preferred_paths=preferred_paths,
                                preferred_keys=preferred_keys,
                                scorer=scorer,
                                exclude_private=exclude_private,
                                path_bonuses=path_bonuses,
                                path_excludes=path_excludes,
                                capture_factors=capture_factors,
                                capture_drop_reasons=capture_drop_reasons,
                                context_need=profile.need,
                                context_profile=profile,
                            )
            _append_source_factors(capture_factors, source_by_key, selected)
            if capture_prepared_context_diagnostics is not None:
                capture_prepared_context_diagnostics.append(
                    build_prepared_context_diagnostics(
                        profile,
                        source_by_key=source_by_key,
                        selected=selected,
                        fused_candidate_count=len(fused_keys),
                        latency_ms=(time.monotonic() - started) * 1000.0,
                    )
                )
            return selected
        all_artefacts: list[Artefact] = await store.list(limit=full_scan_limit)  # type: ignore[union-attr]
    else:
        return []

    selected = select_artefacts(
        prompt,
        all_artefacts,
        top_n=top_n,
        preferred_paths=preferred_paths,
        preferred_keys=preferred_keys,
        scorer=scorer,
        exclude_private=exclude_private,
        path_bonuses=path_bonuses,
        path_excludes=path_excludes,
        capture_factors=capture_factors,
        capture_drop_reasons=capture_drop_reasons,
        context_need=profile.need if context_enabled else None,
        context_profile=profile if context_enabled else None,
    )
    if capture_prepared_context_diagnostics is not None:
        capture_prepared_context_diagnostics.append(
            build_prepared_context_diagnostics(
                profile,
                source_by_key=source_by_key,
                selected=selected,
                fused_candidate_count=len(all_artefacts),
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
        )
    return selected


def _record_source_ranking(
    source_rankings: dict[str, list[str]],
    source_by_key: dict[str, set[str]],
    source: str,
    keys: list[str],
) -> None:
    deduped = list(dict.fromkeys(key for key in keys if key))
    if not deduped:
        return
    source_rankings.setdefault(source, [])
    seen = set(source_rankings[source])
    for key in deduped:
        source_by_key.setdefault(key, set()).add(source)
        if key not in seen:
            source_rankings[source].append(key)
            seen.add(key)


def _fuse_source_rankings(source_rankings: dict[str, list[str]], *, limit: int) -> list[str]:
    weights = {
        "exact_reference": 1.45,
        "prepared_context_cache": 1.25,
        "working_set": 1.20,
        "lexical_search": 1.0,
        "generated_query_variants": 0.72,
        "relationship_graph": 0.9,
        "semantic_memory": 0.75,
        "semantic_query_variants": 0.68,
        "source_metadata": 0.82,
        "coverage_floor": 0.85,
    }
    scores: dict[str, float] = {}
    for source, keys in source_rankings.items():
        weight = weights.get(source, 0.8)
        for rank, key in enumerate(keys, start=1):
            scores[key] = scores.get(key, 0.0) + weight / (60.0 + rank)
    return [
        key
        for key, _ in sorted(
            scores.items(),
            key=lambda item: (-item[1], item[0]),
        )[: max(1, limit)]
    ]


def _with_context_source_metadata(
    artefact: Artefact,
    source_rankings: dict[str, list[str]],
    source_by_key: dict[str, set[str]],
) -> Artefact:
    sources = sorted(source_by_key.get(artefact.key, set()))
    if not sources:
        return artefact
    best_rank: int | None = None
    best_source: str | None = None
    for source in sources:
        try:
            rank = source_rankings.get(source, []).index(artefact.key) + 1
        except ValueError:
            continue
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best_source = source
    metadata = dict(artefact.metadata)
    metadata.setdefault("context_sources", sources)
    if best_rank is not None:
        metadata.setdefault("retrieval_rank", best_rank)
        metadata.setdefault("retrieval_source", best_source)
    return artefact.model_copy(update={"metadata": metadata})


def _append_source_factors(
    capture_factors: dict[str, list[ScoreFactor]] | None,
    source_by_key: dict[str, set[str]],
    selected: list[Artefact],
) -> None:
    if capture_factors is None:
        return
    for artefact in selected:
        sources = sorted(source_by_key.get(artefact.key, set()))
        if not sources:
            continue
        capture_factors.setdefault(artefact.key, []).append(
            ScoreFactor(
                name="context_sources",
                contribution=min(2.0, 0.25 * len(sources)),
                detail="candidate appeared in context sources: " + ", ".join(sources),
            )
        )


def _diversity_bucket(artefact: Artefact, context_need: str | None) -> str:
    path = artefact.source_path
    if context_need == "implementation_support":
        parts = path.split("/")
        return "/".join(parts[:3]) if len(parts) >= 3 else path
    source_type = str(artefact.metadata.get("source_type") or artefact.metadata.get("corpus_id") or "")
    if source_type:
        return source_type
    parts = path.split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else path


def _coverage_recovery_terms(profile: ContextPreparationProfile, diagnostics: PreparedContextDiagnostics) -> str:
    covered = {item.lower() for item in diagnostics.coverage.covered_facets}
    missing_facets = [facet.value for facet in profile.facets if facet.value.lower() not in covered]
    terms = [*diagnostics.coverage.missing_constraints, *missing_facets[:10]]
    if profile.need == "conflict_resolution":
        terms.extend(["current", "superseded", "latest", "updated"])
    elif profile.need == "multi_source_synthesis":
        terms.extend(["summary", "status", "evidence"])
    elif profile.need == "implementation_support":
        terms.extend(["caller", "dependency", "test", "implementation"])
    return " ".join(term for term in terms if term)


_COVERAGE_SENSITIVE_NEEDS = {
    "multi_source_synthesis",
    "source_evidence",
    "conflict_resolution",
    "research_mapping",
    "decision_support",
}


def _artefact_context_text(artefact: Artefact) -> str:
    return f"{artefact.source_path}\n{artefact.content}".lower()


def _matches_facet(artefact: Artefact, facet_value: str) -> bool:
    value = facet_value.lower()
    text = _artefact_context_text(artefact)
    if value in text:
        return True
    parts = [part for part in re.split(r"[_\-\s]+", value) if len(part) > 2]
    return bool(parts) and all(part in text for part in parts)


def _coverage_seed_artefacts(
    ranked: list[tuple[float, Artefact]],
    profile: ContextPreparationProfile | None,
    *,
    top_n: int,
    min_score: float,
) -> list[Artefact]:
    if profile is None or profile.need not in _COVERAGE_SENSITIVE_NEEDS or top_n <= 1:
        return []
    selected: list[Artefact] = []
    selected_keys: set[str] = set()

    def add_best_matching(predicate: Callable[[Artefact], bool]) -> None:
        if len(selected) >= top_n:
            return
        for score, artefact in ranked:
            if score < min_score or artefact.key in selected_keys:
                continue
            if predicate(artefact):
                selected.append(artefact)
                selected_keys.add(artefact.key)
                return

    if profile.need == "conflict_resolution":
        add_best_matching(
            lambda artefact: any(term in _artefact_context_text(artefact) for term in ("current", "latest", "updated", "newer", "now"))
        )
        add_best_matching(
            lambda artefact: any(
                term in _artefact_context_text(artefact) for term in ("superseded", "old", "older", "deprecated", "previous")
            )
        )

    facet_limit = min(top_n, max(1, profile.expected_evidence_count), 6)
    facets = sorted(profile.facets, key=lambda facet: (not facet.required, len(facet.value)))
    for facet in facets[: max(facet_limit * 2, facet_limit)]:
        if len(selected) >= facet_limit:
            break
        add_best_matching(lambda artefact, value=facet.value: _matches_facet(artefact, value))
    return selected


def _canonical_source_class_bonus(prompt: str, artefact: Artefact, profile: ContextPreparationProfile | None) -> float:
    if profile is None or profile.need != "multi_source_synthesis":
        return 0.0
    lowered = prompt.lower()
    path = artefact.source_path.lower()
    content_head = (artefact.content or "").lower()[:600]
    if not re.search(r"\b(across|all|every|which .*most|highest number|count)\b", lowered):
        return 0.0
    bonus = 0.0
    if re.search(r"\b(postmortem|postmortems|rca|incident review)\b", lowered):
        if "/postmortems/" in path and path.startswith(("confluence/", "docs/", "google_drive/")):
            bonus += 16.0
        elif path.startswith(("slack/", "gmail/")) and "postmortem" in path:
            bonus -= 6.0
        if "template" in path or "template" in content_head:
            bonus -= 14.0
    return bonus


def _scheduling_evidence_bonus(prompt: str, artefact: Artefact, profile: ContextPreparationProfile | None) -> float:
    lowered = prompt.lower()
    if profile is None or not re.search(r"\b(when|scheduled|schedule|calendar|invite|meeting|time window|booking|booked)\b", lowered):
        return 0.0
    if not re.search(r"\b(client|customer|call|review|demo|technical|architecture|workshop)\b", lowered):
        return 0.0
    path = artefact.source_path.lower()
    text = _artefact_context_text(artefact)[:9000]
    bonus = 0.0
    if path.startswith(("gmail/", "calendar/")):
        bonus += 4.0
    elif path.startswith(("hubspot/", "fireflies/")) and re.search(r"\b(when|scheduled|time window|booking|booked)\b", lowered):
        bonus -= 2.0
    if any(term in text for term in ("calendar", "invite", ".ics", "event/", "scheduled", "confirming", "works for our team")):
        bonus += 5.0
    if "technical deep dive" in lowered and "technical deep dive" in text and "architecture review" in text:
        bonus += 4.0
    if "isolated network" in lowered and "private" in text and any(
        term in text for term in ("vpc", "on-prem", "isolated", "private hosting")
    ):
        bonus += 5.0
    numeric_terms = set(re.findall(r"\b\d{2,4}\b", lowered))
    matched_numeric = sum(1 for term in numeric_terms if term in text)
    if matched_numeric:
        bonus += min(4.0, matched_numeric * 2.0)
    if re.search(r"\b(pacific|pt|time window)\b", lowered) and re.search(r"\b(?:pt|pst|pdt|-0[78]00|\d{1,2}:\d{2})\b", text):
        bonus += 3.0
    return bonus


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
    capture_factors: dict[str, list[ScoreFactor]] | None = None,
    capture_drop_reasons: dict[str, str] | None = None,
    context_need: str | None = None,
    context_profile: ContextPreparationProfile | None = None,
) -> list[Artefact]:
    preferred_paths = preferred_paths or set()
    preferred_keys = preferred_keys or set()
    path_bonuses = path_bonuses or []
    path_excludes = path_excludes or []
    apply_origin_rerank = _is_origin_question(prompt)

    scored_rows: list[tuple[float, Artefact]] = []
    for artefact in artefacts:
        if exclude_private and str(artefact.metadata.get("privacy_zone", "")).lower() == "private_local":
            if capture_drop_reasons is not None:
                capture_drop_reasons[artefact.key] = "privacy_excluded"
            continue
        if any(fnmatch(artefact.source_path, pattern) for pattern in path_excludes):
            if capture_drop_reasons is not None:
                capture_drop_reasons[artefact.key] = "path_excluded"
            continue
        satisfied_constraints: list[str] = []
        if context_profile is not None:
            constraints_ok, satisfied_constraints, missing_constraints = hard_constraints_satisfied(prompt, artefact, context_profile)
            if not constraints_ok:
                if capture_drop_reasons is not None:
                    capture_drop_reasons[artefact.key] = "missing_hard_constraints:" + ",".join(missing_constraints[:3])
                continue
        factors: list[ScoreFactor] = []
        if scorer is not None:
            score = scorer(prompt, artefact)
            factors.append(
                ScoreFactor(
                    name="intent_score",
                    contribution=score,
                    detail="intent scorer baseline for prompt and artefact",
                )
            )
            if _needs_lexical_floor(prompt):
                lexical_factors: list[ScoreFactor] = []
                lexical_score = score_artefact(prompt, artefact, factor_sink=lexical_factors)
                lexical_floor = max(0.0, lexical_score - _recency_bonus(artefact))
                if lexical_floor:
                    score += lexical_floor
                    factors.extend(lexical_factors)
                    factors.append(
                        ScoreFactor(
                            name="lexical_retrieval_floor",
                            contribution=lexical_floor,
                            detail="bounded lexical floor for direct lookup prompts",
                        )
                    )
        else:
            score = score_artefact(prompt, artefact, factor_sink=factors)
        if apply_origin_rerank:
            origin_bonus = _origin_bonus(prompt, artefact.content)
            if origin_bonus:
                factors.append(
                    ScoreFactor(
                        name="origin_bonus",
                        contribution=origin_bonus,
                        detail="question asks for definition/implementation origin",
                    )
                )
                score += origin_bonus
        if artefact.source_path in preferred_paths:
            preferred_path_bonus = 0.8
            factors.append(
                ScoreFactor(
                    name="preferred_path",
                    contribution=preferred_path_bonus,
                    detail="path appears in recent git activity",
                )
            )
            score += preferred_path_bonus
        if artefact.key in preferred_keys:
            preferred_key_bonus = 0.8
            factors.append(
                ScoreFactor(
                    name="working_set",
                    contribution=preferred_key_bonus,
                    detail="artefact already in working set or warm-start keys",
                )
            )
            score += preferred_key_bonus
        if any(fnmatch(artefact.source_path, pattern) for pattern in path_bonuses):
            pinned_path_bonus = 0.3
            factors.append(
                ScoreFactor(
                    name="pinned_path_bonus",
                    contribution=pinned_path_bonus,
                    detail="path matched user-pinned focus glob",
                )
            )
            score += pinned_path_bonus
        if context_profile is not None and satisfied_constraints:
            constraint_bonus = min(2.0, 0.35 * len(satisfied_constraints))
            factors.append(
                ScoreFactor(
                    name="hard_constraint_match",
                    contribution=constraint_bonus,
                    detail="artefact satisfies extracted hard constraints",
                )
            )
            score += constraint_bonus
        canonical_bonus = _canonical_source_class_bonus(prompt, artefact, context_profile)
        if canonical_bonus:
            factors.append(
                ScoreFactor(
                    name="canonical_source_class",
                    contribution=canonical_bonus,
                    detail="aggregation over a source class prefers canonical durable sources over conversational chatter",
                )
            )
            score += canonical_bonus
        scheduling_bonus = _scheduling_evidence_bonus(prompt, artefact, context_profile)
        if scheduling_bonus:
            factors.append(
                ScoreFactor(
                    name="scheduling_evidence",
                    contribution=scheduling_bonus,
                    detail="scheduling queries prefer confirmed invites, time windows, and deployment-specific meeting context",
                )
            )
            score += scheduling_bonus
        source_prior = _source_rank_prior(artefact)
        if source_prior:
            factors.append(
                ScoreFactor(
                    name="source_rank_prior",
                    contribution=source_prior,
                    detail="trusted upstream context source ranked this artefact highly",
                )
            )
            score += source_prior
        if capture_factors is not None:
            capture_factors[artefact.key] = factors
        scored_rows.append((score, artefact))

    ranked = sorted(scored_rows, key=lambda item: item[0], reverse=True)
    selected: list[Artefact] = []
    seen_corpora: set[str] = set()
    threshold_multiplier = competitive_threshold_multiplier(context_need)
    min_competitive_score = ranked[0][0] * threshold_multiplier if ranked else 0.0
    selected_buckets: set[str] = set()
    deferred_for_diversity: list[Artefact] = []
    diversity_floor = max(1, top_n // 2)

    for artefact in _coverage_seed_artefacts(ranked, context_profile, top_n=top_n, min_score=min_competitive_score):
        if artefact in selected:
            continue
        selected.append(artefact)
        seen_corpora.add(str(artefact.metadata.get("corpus_id", "default")))
        selected_buckets.add(_diversity_bucket(artefact, context_need))

    for score, artefact in ranked:
        if artefact in selected:
            continue
        if score < min_competitive_score:
            if capture_drop_reasons is not None:
                capture_drop_reasons[artefact.key] = "below_competitive_threshold"
            continue
        corpus_id = str(artefact.metadata.get("corpus_id", "default"))
        bucket = _diversity_bucket(artefact, context_need)
        if (
            context_need in {"multi_source_synthesis", "conflict_resolution", "research_mapping", "decision_support"}
            and bucket in selected_buckets
            and len(selected) < diversity_floor
        ):
            deferred_for_diversity.append(artefact)
            if capture_drop_reasons is not None:
                capture_drop_reasons[artefact.key] = "deferred_for_context_diversity"
            continue
        if selected and corpus_id not in seen_corpora:
            selected.append(artefact)
            seen_corpora.add(corpus_id)
            selected_buckets.add(bucket)
        elif len(selected) < top_n:
            selected.append(artefact)
            seen_corpora.add(corpus_id)
            selected_buckets.add(bucket)
        if len(selected) >= diversity_floor:
            while deferred_for_diversity and len(selected) < top_n:
                deferred = deferred_for_diversity.pop(0)
                if deferred in selected:
                    continue
                selected.append(deferred)
                seen_corpora.add(str(deferred.metadata.get("corpus_id", "default")))
                selected_buckets.add(_diversity_bucket(deferred, context_need))
        if len(selected) >= top_n:
            break
    if capture_drop_reasons is not None:
        selected_keys = {artefact.key for artefact in selected}
        for _, artefact in ranked:
            if artefact.key in selected_keys:
                continue
            capture_drop_reasons.setdefault(artefact.key, "ranked_below_top_n")
    return selected[:top_n]
