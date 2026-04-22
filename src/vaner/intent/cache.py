# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import ValidationError

from vaner.intent.scoring_policy import ScoringPolicy
from vaner.models.context import ContextPackage
from vaner.store.artefacts import ArtefactStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CacheMatchResult:
    tier: str
    similarity: float
    package: ContextPackage | None
    enrichment: dict[str, object]
    cache_key: str | None = None


def _tokenize(text: str) -> set[str]:
    return {token for token in text.lower().split() if token}


def _token_similarity(a: str, b: str) -> float:
    left = _tokenize(a)
    right = _tokenize(b)
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    union = len(left | right)
    return overlap / union


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(left * right for left, right in zip(a, b, strict=False))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


class TieredPredictionCache:
    def __init__(
        self,
        store: ArtefactStore,
        *,
        embed: Callable[[list[str]], Awaitable[list[list[float]]]] | None = None,
        scoring_policy: ScoringPolicy | None = None,
    ) -> None:
        self.store = store
        self.embed = embed
        self.scoring_policy = scoring_policy

    async def _embed_prompt(self, prompt: str) -> list[float] | None:
        if self.embed is None:
            return None
        try:
            vectors = await self.embed([prompt])
        except Exception as exc:
            logger.warning("Vaner cache embeddings unavailable (%s); falling back to lexical matching.", exc)
            self.embed = None
            return None
        if not vectors:
            return None
        vector = vectors[0]
        if not isinstance(vector, list):
            return None
        return [float(value) for value in vector]

    @staticmethod
    def _unit_overlap_score(enrichment: object, relevant_paths: set[str]) -> float:
        """Compute Jaccard-like overlap between query paths and cached entry units.

        Returns the fraction of query-relevant units covered by the cache entry:
        ``|entry ∩ relevant| / |relevant|``.

        This is the primary cache-match signal: if we pre-built a package
        covering >= 70% of the units this query needs, it's a full hit.

        Reads both the new canonical keys (``source_units``, ``anchor_units``)
        and the legacy keys (``source_paths``, ``anchor_files``) for backward
        compatibility with cache entries written by older engine versions.
        """
        if not relevant_paths or not isinstance(enrichment, dict):
            return 0.0
        entry_units: set[str] = set()

        def _collect_units(canonical_key: str, legacy_key: str) -> set[str]:
            canonical = enrichment.get(canonical_key, [])
            if isinstance(canonical, list) and canonical:
                return {p for p in canonical if isinstance(p, str) and p}
            legacy = enrichment.get(legacy_key, [])
            if isinstance(legacy, list):
                return {p for p in legacy if isinstance(p, str) and p}
            return set()

        entry_units |= _collect_units("anchor_units", "anchor_files")
        entry_units |= _collect_units("source_units", "source_paths")
        if not entry_units:
            return 0.0
        covered = len(relevant_paths & entry_units)
        return covered / len(relevant_paths)

    # Backward-compat alias
    _path_overlap_score = _unit_overlap_score

    async def match(self, prompt: str, *, relevant_paths: set[str] | None = None) -> CacheMatchResult:
        records = await self.store.list_prediction_cache(limit=512)
        prompt_vector = await self._embed_prompt(prompt)
        best_record = None
        best_similarity = 0.0
        for record in records:
            enrichment = record.get("enrichment", {})

            # --- Semantic signal (embedding cosine or token Jaccard) ------
            prompt_embedding = enrichment.get("_prompt_embedding") if isinstance(enrichment, dict) else None
            if (
                prompt_vector is not None
                and isinstance(prompt_embedding, list)
                and all(isinstance(value, (float, int)) for value in prompt_embedding)
            ):
                # True semantic similarity via dense embeddings
                embedding_sim = _cosine_similarity(prompt_vector, [float(value) for value in prompt_embedding])
            else:
                # Fall back to token-Jaccard.  Also check the richer
                # semantic_intent field if present (set during LLM exploration).
                hint = str(record.get("prompt_hint", ""))
                semantic_intent = enrichment.get("semantic_intent", "") if isinstance(enrichment, dict) else ""
                combined_hint = f"{hint} {semantic_intent}".strip() if semantic_intent else hint
                embedding_sim = _token_similarity(prompt, combined_hint)

            # --- Structural signal: unit-set path overlap -----------------
            path_score = self._unit_overlap_score(enrichment, relevant_paths or set())

            # --- Two-pass combination -------------------------------------
            # Either strong path overlap OR strong semantic similarity
            # can independently trigger a hit.  We take the max so that
            # a conceptual query ("how does auth work?") that has no path
            # overlap can still match a cache entry built from auth files.
            if path_score > 0.0:
                # Path overlap present: blend with a small semantic bonus
                # to prefer semantically closer entries when path scores tie.
                similarity = path_score + embedding_sim * 0.05
            else:
                # No path overlap: fall back purely to semantic similarity,
                # discounted slightly so heuristic-path hits still rank higher.
                similarity = embedding_sim * 0.85

            if similarity > best_similarity:
                best_similarity = similarity
                best_record = record

        if best_record is None:
            return CacheMatchResult(tier="cold_miss", similarity=0.0, package=None, enrichment={})

        package_json = best_record["package_json"]
        package = None
        if isinstance(package_json, str) and package_json:
            try:
                package = ContextPackage.model_validate_json(package_json)
            except (ValidationError, ValueError):
                package = None

        # Tier thresholds: path-overlap is primary when relevant_paths are
        # available; semantic similarity is the fallback for callers that don't
        # supply file context (e.g. plain text queries without FTS pre-filter).
        enrichment_dict = best_record.get("enrichment", {})
        path_score_for_tier = self._unit_overlap_score(enrichment_dict, relevant_paths or set())

        # Re-compute semantic similarity for tiering using the same logic as
        # the selection loop above (don't reuse best_similarity since that
        # may include path_score components).
        prompt_emb_for_tier = enrichment_dict.get("_prompt_embedding") if isinstance(enrichment_dict, dict) else None
        if (
            prompt_vector is not None
            and isinstance(prompt_emb_for_tier, list)
            and all(isinstance(v, (float, int)) for v in prompt_emb_for_tier)
        ):
            semantic_sim_for_tier = _cosine_similarity(prompt_vector, [float(v) for v in prompt_emb_for_tier])
        else:
            hint_for_tier = str(best_record.get("prompt_hint", ""))
            semantic_intent_for_tier = enrichment_dict.get("semantic_intent", "") if isinstance(enrichment_dict, dict) else ""
            combined = f"{hint_for_tier} {semantic_intent_for_tier}".strip() if semantic_intent_for_tier else hint_for_tier
            semantic_sim_for_tier = _token_similarity(prompt, combined)

        full_path_threshold = self.scoring_policy.cache_full_hit_path_threshold if self.scoring_policy is not None else 0.70
        partial_path_threshold = self.scoring_policy.cache_partial_hit_path_threshold if self.scoring_policy is not None else 0.30
        full_similarity_threshold = self.scoring_policy.cache_full_hit_similarity_threshold if self.scoring_policy is not None else 0.70
        partial_similarity_threshold = (
            self.scoring_policy.cache_partial_hit_similarity_threshold if self.scoring_policy is not None else 0.30
        )
        warm_similarity_threshold = self.scoring_policy.cache_warm_similarity_threshold if self.scoring_policy is not None else 0.10
        if path_score_for_tier >= full_path_threshold and package is not None:
            tier = "full_hit"
        elif path_score_for_tier >= partial_path_threshold:
            tier = "partial_hit"
        elif semantic_sim_for_tier >= full_similarity_threshold and package is not None:
            # Semantic-similarity full-hit (no file context overlap but
            # embeddings / token similarity strongly matched the cache entry)
            tier = "full_hit"
        elif semantic_sim_for_tier >= partial_similarity_threshold:
            tier = "partial_hit"
        elif semantic_sim_for_tier >= warm_similarity_threshold:
            tier = "warm_start"
        else:
            tier = "cold_miss" if package is None else "warm_start"
        cache_key = str(best_record.get("cache_key") or "")
        # Bump access_count + last_accessed_at for anything that counts as a
        # real consumption (warm_start or better). A pure cold_miss on a
        # non-matching candidate shouldn't revive it; only entries Vaner
        # actually returned to the caller should be protected from the
        # unused-decay prune.
        if cache_key and tier != "cold_miss":
            try:
                await self.store.touch_prediction_cache(cache_key)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Vaner cache touch failed (%s); skipping access bump.", exc)
        return CacheMatchResult(
            tier=tier,
            similarity=best_similarity,
            package=package,
            enrichment=dict(best_record.get("enrichment") or {}),
            cache_key=cache_key,
        )

    async def store_entry(
        self,
        *,
        prompt_hint: str,
        package: ContextPackage | None,
        enrichment: dict[str, object],
        ttl_seconds: int = 600,
    ) -> str:
        payload = dict(enrichment)
        # Prefer canonical keys when present, then fall back to legacy ones.
        anchor_units: list[str] = []
        source_units: list[str] = []
        _seen: set[str] = set()
        raw_anchor = payload.get("anchor_units", payload.get("anchor_files", []))
        for p in raw_anchor if isinstance(raw_anchor, list) else []:
            if isinstance(p, str) and p and p not in _seen:
                anchor_units.append(p)
                _seen.add(p)
        raw_source = payload.get("source_units", payload.get("source_paths", []))
        for p in raw_source if isinstance(raw_source, list) else []:
            if isinstance(p, str) and p:
                source_units.append(p)
        if package is not None:
            for sel in package.selections:
                if sel.source_path and sel.source_path not in _seen:
                    anchor_units.append(sel.source_path)
                    _seen.add(sel.source_path)
        if anchor_units:
            # Write both canonical and legacy keys for forward/backward compat
            payload["anchor_units"] = anchor_units
            payload["anchor_files"] = anchor_units
        if source_units:
            payload["source_units"] = source_units
            payload["source_paths"] = source_units

        prompt_vector = await self._embed_prompt(prompt_hint)
        if prompt_vector is not None:
            payload["_prompt_embedding"] = prompt_vector

        # Use a content-addressable key based on the file set when available,
        # so graph-walk scenarios with the same neighborhood reuse the same slot.
        if anchor_units:
            file_fingerprint = "|".join(sorted(anchor_units))
            cache_key = hashlib.sha1(file_fingerprint.encode("utf-8")).hexdigest()  # noqa: S324
        else:
            cache_key = hashlib.sha1(prompt_hint.encode("utf-8")).hexdigest()  # noqa: S324
        await self.store.upsert_prediction_cache(
            cache_key=cache_key,
            prompt_hint=prompt_hint,
            package_json=package.model_dump_json() if package is not None else None,
            enrichment=payload,
            ttl_seconds=ttl_seconds,
        )
        return cache_key
