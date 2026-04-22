# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import subprocess
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from vaner.broker.assembler import assemble_context_package
from vaner.broker.selector import select_artefacts, select_artefacts_fts
from vaner.cli.commands.config import load_config
from vaner.daemon.runner import VanerDaemon
from vaner.daemon.signals.git_reader import read_git_state
from vaner.intent.adapter import CodeRepoAdapter, ContextSource, CorpusAdapter, RelationshipEdge, SignalSource
from vaner.intent.arcs import ArcObservation, ConversationArcModel, classify_query_category, derive_prompt_macro
from vaner.intent.cache import TieredPredictionCache
from vaner.intent.features import extract_hybrid_features
from vaner.intent.frontier import ExplorationFrontier, ExplorationScenario, file_set_fingerprint
from vaner.intent.governor import PredictionGovernor
from vaner.intent.graph import RelationshipGraph
from vaner.intent.maturity import MaturityTracker
from vaner.intent.reasoner import CorpusReasoner, PredictionScenario
from vaner.intent.scorer import IntentScorer
from vaner.intent.scoring_policy import ScoringPolicy
from vaner.intent.trainer import IntentTrainer
from vaner.intent.transfer import bootstrap_transfer_priors
from vaner.learning.reward import RewardInput, compute_reward
from vaner.models.artefact import Artefact, ArtefactKind
from vaner.models.config import ComputeConfig, ExplorationConfig, VanerConfig
from vaner.models.context import ContextPackage
from vaner.models.decision import DecisionRecord, PredictionLink, ScoreFactor
from vaner.models.signal import SignalEvent
from vaner.store.artefacts import ArtefactStore

LLMCallable = Callable[[str], Awaitable[str]]
EmbedCallable = Callable[[list[str]], Awaitable[list[list[float]]]]


@dataclass(slots=True)
class IntentPrediction:
    key: str
    score: float
    reason: str


@dataclass(slots=True)
class ExploredScenario:
    source: str
    anchor: str
    reason: str
    priority: float
    depth: int
    unit_ids: list[str]
    cached: bool


class VanerEngine:
    """Public middleware engine that hosts can embed directly.

    Parameters
    ----------
    adapter:
        Legacy unified corpus adapter.  Kept for backward compatibility.
    signals:
        Optional list of ``SignalSource`` implementations.  These are queried
        during each precompute cycle to collect fresh context signals.
    sources:
        Optional list of ``ContextSource`` implementations.  Items from all
        sources are merged into the available-paths pool for prediction.
    config, llm, embed:
        As before.
    """

    def __init__(
        self,
        *,
        adapter: CorpusAdapter,
        signals: list[SignalSource] | None = None,
        sources: list[ContextSource] | None = None,
        config: VanerConfig | None = None,
        llm: LLMCallable | str | None = None,
        embed: EmbedCallable | None = None,
    ) -> None:
        self.adapter = adapter
        self._extra_signal_sources: list[SignalSource] = list(signals or [])
        self._extra_context_sources: list[ContextSource] = list(sources or [])
        repo_root = getattr(adapter, "repo_root", Path.cwd())
        self.config = config if config is not None else load_config(Path(repo_root))
        self.store = ArtefactStore(self.config.store_path)
        self.llm = self._resolve_llm(llm)
        self.embed = embed
        self._background_task: asyncio.Task[None] | None = None
        self._running = False
        self._reasoner = CorpusReasoner()
        self._arc_model = ConversationArcModel()
        self._intent_scorer = IntentScorer()
        self._maturity = MaturityTracker()
        self._trainer = IntentTrainer(self.store, self._intent_scorer)
        self._scoring_policy = ScoringPolicy()
        self._cache = TieredPredictionCache(self.store, embed=self.embed, scoring_policy=self._scoring_policy)
        self._scorer_model_path = self.store.db_path.parent / "intent_scorer.txt"
        self._active_session_id = self._make_session_id()
        self._arc_loaded = False
        self._precompute_cycles = 0
        self._background_governor: PredictionGovernor | None = None
        self._feedback_cursor_ts = 0.0
        self._corpus_prepared = False
        self._extra_sources_synced = False
        self._extra_sources_dirty = False
        self._pinned_facts_synced = False
        self._pinned_facts_dirty = False
        self._pinned_focus_paths: list[str] = []
        self._pinned_avoid_paths: list[str] = []
        self._trusted_follow_up_patterns: list[str] = []
        self._applied_prefer_source_deltas: dict[str, float] = {}
        self._last_heuristic_paths: set[str] = set()
        self._last_explored_scenarios: list[ExploredScenario] = []
        # Working set: maps source_path -> last interaction timestamp.
        # Seeded by every query() call; used as graph-walk anchor.
        self._working_set: dict[str, float] = {}
        # In-memory cache of the relationship graph (invalidated on corpus reload).
        self._graph: RelationshipGraph | None = None
        # Cold-miss recovery: paths from recent cold-miss queries that should be
        # seeded back into the exploration frontier on the next precompute cycle.
        self._miss_recovery_paths: list[list[str]] = []
        # Per-source priority multipliers that persist across exploration cycles.
        self._policy_state_dirty = False
        self._last_policy_persist_at = 0.0
        self._policy_persist_interval_seconds = 2.0
        self._learning_state_loaded = False
        self._last_decision_record: DecisionRecord | None = None
        self._concurrency_banner_emitted = False

    async def initialize(self) -> None:
        await self.store.initialize()
        await self._load_learning_state()
        self._try_load_trained_scorer()
        if not self._arc_loaded:
            history = await self.store.list_query_history(limit=5000)
            ordered_queries = [str(entry["query_text"]) for entry in reversed(history)]
            self._arc_model.rebuild_from_history(ordered_queries)
            await self._refresh_behavioral_memory_from_model()
            self._arc_loaded = True
        await self._sync_extra_context_sources()
        await self._sync_pinned_facts()

    def invalidate_extra_sources(self) -> None:
        """Mark external context sources dirty so they are re-synced."""
        self._extra_sources_dirty = True
        self._extra_sources_synced = False

    def invalidate_pinned_facts(self) -> None:
        """Mark pinned profile facts dirty so they are reloaded on initialize()."""
        self._pinned_facts_dirty = True
        self._pinned_facts_synced = False

    async def observe(self, event: SignalEvent) -> None:
        await self.initialize()
        event.payload.setdefault("corpus_id", getattr(self.adapter, "corpus_id", "default"))
        event.payload.setdefault("privacy_zone", getattr(self.adapter, "privacy_zone", "local"))
        await self.store.insert_signal_event(event)

    async def prepare(self, changed_files: list[Path] | None = None) -> int:
        await self.initialize()
        daemon = VanerDaemon(self.config)
        return await daemon.run_once(changed_files=changed_files)

    async def prepare_corpus(self) -> None:
        """Run the full corpus preparation: prepare() + relationships + quality.

        Call this ONCE before cloning the store for parallel benchmark positions.
        Engines that receive a pre-populated store clone should set
        ``_corpus_prepared = True`` to skip re-running these steps inside
        ``precompute_cycle()``.
        """
        await self.prepare()
        await self.store.replace_relationship_edges(await self._collect_relationship_edges())
        issues = await self.adapter.check_quality()
        await self.store.replace_quality_issues(
            [{"key": issue.key, "severity": issue.severity, "message": issue.message, "metadata": issue.metadata} for issue in issues]
        )
        self._corpus_prepared = True

    async def predict(self, top_k: int = 5) -> list[IntentPrediction]:
        await self.initialize()
        artefacts = await self.store.list(limit=100)
        history = await self.store.list_query_history(limit=32)
        recent_queries = [str(entry["query_text"]) for entry in reversed(history)]
        phase_summary = self._arc_model.summarize_workflow_phase(recent_queries)
        current_category = classify_query_category(recent_queries[-1]) if recent_queries else phase_summary.dominant_category
        predicted_categories = dict(self._arc_model.predict_next(current_category, top_k=3, recent_queries=recent_queries))
        recent_macro = derive_prompt_macro(recent_queries[-1]) if recent_queries else phase_summary.recent_macro
        behavior_profile = await self._behavioral_prediction_profile(
            current_category=current_category,
            recent_macro=recent_macro,
            predicted_categories=predicted_categories,
        )
        predictions: list[IntentPrediction] = []
        seen_keys: set[str] = set()

        patterns = await self.store.list_validated_patterns(trigger_category=current_category, limit=3)
        for pattern in patterns:
            confirmation_count = int(pattern.get("confirmation_count", 0))
            if confirmation_count < 3:
                continue
            predicted_keys = [str(item) for item in pattern.get("predicted_keys", [])]
            if not predicted_keys:
                continue
            score = 2.0 + math.log(float(max(1, confirmation_count)))
            for predicted_key in predicted_keys:
                if predicted_key in seen_keys:
                    continue
                predictions.append(
                    IntentPrediction(
                        key=predicted_key,
                        score=score,
                        reason="validated_pattern_match",
                    )
                )
                seen_keys.add(predicted_key)
                if len(predictions) >= top_k:
                    return predictions[:top_k]

        fts_boost_by_path: dict[str, float] = {}
        recent_context = recent_queries[-1] if recent_queries else current_category
        fts_query = self._fts_query(recent_context)
        if fts_query:
            try:
                similar_queries = await self.store.search_query_history(fts_query, limit=10)
            except Exception:
                similar_queries = []
            for match in similar_queries:
                for selected_path in match.get("selected_paths", []):
                    path = str(selected_path)
                    fts_boost_by_path[path] = fts_boost_by_path.get(path, 0.0) + 0.15

        for artefact in artefacts:
            if artefact.key in seen_keys:
                continue
            recency = max(0.0, time.time() - artefact.generated_at)
            score = (1.0 / (1.0 + recency / 300.0)) + min(2.0, artefact.access_count * 0.1)
            predicted_category = self._category_for_artefact(artefact.source_path, artefact.content)
            score += predicted_categories.get(predicted_category, 0.0) * 0.75
            score += min(1.0, fts_boost_by_path.get(artefact.source_path, 0.0))
            behavior_boost, behavior_reasons = self._behavioral_boost_for_artefact(
                artefact.source_path,
                predicted_category=predicted_category,
                recent_macro=recent_macro,
                behavior_profile=behavior_profile,
            )
            score += behavior_boost
            reason = "recent_and_frequent_access_with_arc_boost"
            if behavior_reasons:
                reason += "+behavior:" + ",".join(behavior_reasons)
            predictions.append(
                IntentPrediction(
                    key=artefact.key,
                    score=score,
                    reason=reason,
                )
            )
            seen_keys.add(artefact.key)
        predictions.sort(key=lambda item: item.score, reverse=True)
        return predictions[:top_k]

    async def query(self, prompt: str, *, max_tokens: int | None = None, top_n: int = 8) -> ContextPackage:
        await self.initialize()
        started_at = time.time()
        self._notify_user_request_start()
        try:
            _quick_artefacts = await self.store.list(limit=2000)
            _quick_paths = {
                artefact.source_path
                for artefact in select_artefacts(
                    prompt,
                    _quick_artefacts,
                    top_n=8,
                    exclude_private=self.config.privacy.exclude_private,
                    path_bonuses=self._pinned_focus_paths,
                    path_excludes=self._pinned_avoid_paths,
                )
                if artefact.source_path
            }
            cache_result = await self._cache.match(prompt, relevant_paths=_quick_paths)
            observation = self._arc_model.observe_detail(prompt)
            category = observation.category
            await self._persist_behavioral_observation(observation)
            if cache_result.tier == "full_hit" and cache_result.package is not None:
                self._update_working_set([sel.source_path for sel in cache_result.package.selections])
                exploration_source = str(cache_result.enrichment.get("exploration_source", "")) if cache_result.enrichment else ""
                source_key = cache_result.package.selections[0].artefact_key if cache_result.package.selections else None
                features = await extract_hybrid_features(self.store, prompt=prompt, source_key=source_key)
                features = self._augment_feature_snapshot(
                    features,
                    selected_paths=[selection.source_path for selection in cache_result.package.selections],
                    exploration_source=exploration_source,
                    cache_tier=cache_result.tier,
                    freshness_hint=0.8,
                )
                query_id = await self.store.insert_query_history(
                    session_id=self._session_id(),
                    query_text=prompt,
                    selected_paths=[selection.source_path for selection in cache_result.package.selections],
                    hit_precomputed=True,
                    token_used=cache_result.package.token_used,
                    corpus_id=getattr(self.adapter, "corpus_id", "default"),
                )
                quality_lift = 0.3
                latency_ms = (time.time() - started_at) * 1000.0
                judge_score_raw = cache_result.enrichment.get("judge_score") if cache_result.enrichment else None
                host_outcome_raw = cache_result.enrichment.get("host_outcome") if cache_result.enrichment else None
                reward = compute_reward(
                    RewardInput(
                        cache_tier=cache_result.tier,
                        similarity=cache_result.similarity,
                        quality_lift=quality_lift,
                        host_outcome=float(host_outcome_raw) if isinstance(host_outcome_raw, (int, float)) else None,
                        judge_score=float(judge_score_raw) if isinstance(judge_score_raw, (int, float)) else None,
                        latency_ms=latency_ms,
                    )
                )
                self._scoring_policy.adapt_cache_thresholds(
                    reward_total=reward.reward_total,
                    phase=self._maturity.phase_for_query_count(await self.store.count_query_history()).value,
                )
                self._adapt_policy_from_feedback(
                    reward_total=reward.reward_total,
                    feature_snapshot=features,
                    source=exploration_source,
                )
                self._mark_policy_state_dirty()
                await self.store.insert_feedback_event(
                    query_id=query_id,
                    cache_tier=cache_result.tier,
                    similarity=cache_result.similarity,
                    quality_lift=quality_lift,
                    latency_ms=latency_ms,
                    metadata={
                        "source": "cache",
                        "exploration_source": exploration_source,
                        "host_outcome": host_outcome_raw if isinstance(host_outcome_raw, (int, float)) else None,
                        "judge_score": judge_score_raw if isinstance(judge_score_raw, (int, float)) else None,
                        "reward_total": reward.reward_total,
                        "reward_components": reward.reward_components,
                    },
                )
                await self.store.update_query_feedback(query_id, quality_lift)
                await self._trainer.online_update(
                    prompt=prompt,
                    tier=cache_result.tier,
                    similarity=cache_result.similarity,
                    quality_lift=quality_lift,
                    host_outcome=float(host_outcome_raw) if isinstance(host_outcome_raw, (int, float)) else None,
                    judge_score=float(judge_score_raw) if isinstance(judge_score_raw, (int, float)) else None,
                    feature_snapshot=features,
                    access_count=0.0,
                    artefact_age_seconds=0.0,
                    latency_ms=latency_ms,
                )
                await self._update_validated_patterns(
                    prompt=prompt,
                    selected_keys=[selection.artefact_key for selection in cache_result.package.selections],
                    category=category,
                )
                await self._persist_learning_state()
                cache_result.package.cache_tier = "full_hit"
                cache_result.package.partial_similarity = cache_result.similarity
                self._last_decision_record = self._build_decision_record_from_package(
                    prompt,
                    cache_result.package,
                    cache_tier="full_hit",
                    partial_similarity=cache_result.similarity,
                    enrichment=cache_result.enrichment,
                )
                return cache_result.package

            if cache_result.tier == "partial_hit" and cache_result.package is not None:
                # Partial hit: record a weaker positive signal for the source
                exploration_source = str(cache_result.enrichment.get("exploration_source", "")) if cache_result.enrichment else ""
                # Use the LLM-curated files from the precomputed package as primary selection,
                # then fill remaining top_n slots from the heuristic selector.
                cache_keys = {s.artefact_key for s in cache_result.package.selections}
                all_artefacts = await self.store.list(limit=2000)
                artefacts_by_key = {a.key: a for a in all_artefacts}
                selected = [artefacts_by_key[k] for k in cache_keys if k in artefacts_by_key]
                factor_map: dict[str, list[ScoreFactor]] = {}
                drop_reasons: dict[str, str] = {}
                if len(selected) < top_n:
                    heuristic_picks = select_artefacts(
                        prompt,
                        all_artefacts,
                        top_n=top_n,
                        exclude_private=self.config.privacy.exclude_private,
                        path_bonuses=self._pinned_focus_paths,
                        path_excludes=self._pinned_avoid_paths,
                        capture_factors=factor_map,
                        capture_drop_reasons=drop_reasons,
                    )
                    seen = {a.key for a in selected}
                    for pick in heuristic_picks:
                        if pick.key not in seen:
                            selected.append(pick)
                            seen.add(pick.key)
                            if len(selected) >= top_n:
                                break
                source_key = selected[0].key if selected else None
                features = await extract_hybrid_features(self.store, prompt=prompt, source_key=source_key)
                features = self._augment_feature_snapshot(
                    features,
                    selected_paths=[item.source_path for item in selected],
                    exploration_source=exploration_source,
                    cache_tier=cache_result.tier,
                )
                score_map = {a.key: self._intent_scorer.score(prompt, a, features=features) for a in selected}
                package, decision_record = assemble_context_package(
                    prompt,
                    selected,
                    max_tokens if max_tokens is not None else self.config.max_context_tokens,
                    repo_root=self.config.repo_root,
                    max_age_seconds=self.config.max_age_seconds,
                    score_map=score_map,
                    factor_map=factor_map,
                    drop_reasons=drop_reasons,
                    return_decision=True,
                )
            else:
                if cache_result.tier == "cold_miss":
                    # On a cold miss the frontier failed to predict this query.
                    # We can't know which source was responsible, so we only
                    # apply a mild penalty to the graph source (which is always
                    # active and should have caught structural neighbors) and
                    # leave arc/pattern/llm_branch multipliers unchanged to
                    # avoid penalising sources that never had a chance to fire.
                    self._scoring_policy.record_source_feedback("graph", hit=False)
                    self._mark_policy_state_dirty()
                    # Record miss paths so the next precompute cycle can seed them
                    # back into the frontier at high priority (MCTS recovery).
                    if _quick_paths:
                        self._miss_recovery_paths.append(list(_quick_paths))
                        # Keep at most 10 recent miss contexts to avoid stale data
                        self._miss_recovery_paths = self._miss_recovery_paths[-10:]
                preferred_keys: set[str] = set()
                working_set = await self.store.get_latest_working_set()
                if working_set is not None:
                    preferred_keys |= set(working_set.artefact_keys)
                if cache_result.tier == "warm_start":
                    preferred_keys |= set(str(key) for key in cache_result.enrichment.get("relevant_keys", []))
                package, selected, decision_record = await self._build_package_for_prompt(
                    prompt,
                    max_tokens=max_tokens,
                    top_n=top_n,
                    preferred_keys=preferred_keys,
                )
            for artefact in selected:
                await self.store.mark_accessed(artefact.key)
            self._update_working_set([a.source_path for a in selected])

            query_id = await self.store.insert_query_history(
                session_id=self._session_id(),
                query_text=prompt,
                selected_paths=[artefact.source_path for artefact in selected],
                hit_precomputed=cache_result.tier in {"partial_hit", "warm_start"},
                token_used=package.token_used,
                corpus_id=getattr(self.adapter, "corpus_id", "default"),
            )
            await self.store.insert_signal_event(
                SignalEvent(
                    id=str(uuid.uuid4()),
                    source="query",
                    kind="query_issued",
                    timestamp=time.time(),
                    payload={
                        "category": category,
                        "corpus_id": getattr(self.adapter, "corpus_id", "default"),
                        "privacy_zone": getattr(self.adapter, "privacy_zone", "local"),
                    },
                )
            )
            await self._update_validated_patterns(
                prompt=prompt,
                selected_keys=[artefact.key for artefact in selected],
                category=category,
            )
            hypotheses = await self.store.list_hypotheses(limit=5)
            await self._cache.store_entry(
                prompt_hint=prompt,
                package=package,
                enrichment={
                    "relevant_keys": [artefact.key for artefact in selected[:5]],
                    "hypotheses": [hypothesis["question"] for hypothesis in hypotheses],
                },
            )
            quality_lift = 0.2 if cache_result.tier == "partial_hit" else (0.1 if cache_result.tier == "warm_start" else 0.0)
            latency_ms = (time.time() - started_at) * 1000.0
            exploration_source = str(cache_result.enrichment.get("exploration_source", "")) if cache_result.enrichment else ""
            judge_score_raw = cache_result.enrichment.get("judge_score") if cache_result.enrichment else None
            host_outcome_raw = cache_result.enrichment.get("host_outcome") if cache_result.enrichment else None
            reward = compute_reward(
                RewardInput(
                    cache_tier=cache_result.tier,
                    similarity=cache_result.similarity,
                    quality_lift=quality_lift,
                    host_outcome=float(host_outcome_raw) if isinstance(host_outcome_raw, (int, float)) else None,
                    judge_score=float(judge_score_raw) if isinstance(judge_score_raw, (int, float)) else None,
                    latency_ms=latency_ms,
                )
            )
            await self.store.insert_feedback_event(
                query_id=query_id,
                cache_tier=cache_result.tier,
                similarity=cache_result.similarity,
                quality_lift=quality_lift,
                latency_ms=latency_ms,
                metadata={
                    "selected_count": len(selected),
                    "exploration_source": exploration_source,
                    "host_outcome": host_outcome_raw if isinstance(host_outcome_raw, (int, float)) else None,
                    "judge_score": judge_score_raw if isinstance(judge_score_raw, (int, float)) else None,
                    "reward_total": reward.reward_total,
                    "reward_components": reward.reward_components,
                },
            )
            await self.store.update_query_feedback(query_id, quality_lift)
            source_key = selected[0].key if selected else None
            features = await extract_hybrid_features(self.store, prompt=prompt, source_key=source_key)
            average_age = sum(max(0.0, time.time() - item.generated_at) for item in selected) / len(selected) if selected else 0.0
            freshness_hint = max(0.1, min(1.0, 1.0 - (average_age / max(1.0, self._scoring_policy.freshness_half_life * 4.0))))
            features = self._augment_feature_snapshot(
                features,
                selected_paths=[item.source_path for item in selected],
                exploration_source=exploration_source,
                cache_tier=cache_result.tier,
                freshness_hint=freshness_hint,
            )
            await self._trainer.online_update(
                prompt=prompt,
                tier=cache_result.tier,
                similarity=cache_result.similarity,
                quality_lift=quality_lift,
                host_outcome=float(host_outcome_raw) if isinstance(host_outcome_raw, (int, float)) else None,
                judge_score=float(judge_score_raw) if isinstance(judge_score_raw, (int, float)) else None,
                feature_snapshot=features,
                access_count=float(selected[0].access_count) if selected else 0.0,
                artefact_age_seconds=float(max(0.0, time.time() - selected[0].generated_at)) if selected else 0.0,
                latency_ms=latency_ms,
            )
            phase = self._maturity.phase_for_query_count(await self.store.count_query_history())
            self._scoring_policy.adapt_cache_thresholds(
                reward_total=reward.reward_total,
                phase=phase.value,
            )
            self._adapt_policy_from_feedback(
                reward_total=reward.reward_total,
                feature_snapshot=features,
                source=exploration_source,
            )
            self._mark_policy_state_dirty()
            query_count = await self.store.count_query_history()
            phase = self._maturity.phase_for_query_count(query_count)
            transfer_priors = bootstrap_transfer_priors(corpus_type=self.adapter.corpus_type, query_count=query_count)
            await self.store.insert_signal_event(
                SignalEvent(
                    id=str(uuid.uuid4()),
                    source="engine",
                    kind="online_update",
                    timestamp=time.time(),
                    payload={
                        "phase": phase.value,
                        "priors": ",".join(sorted(transfer_priors.keys())),
                        "corpus_id": getattr(self.adapter, "corpus_id", "default"),
                        "privacy_zone": getattr(self.adapter, "privacy_zone", "local"),
                    },
                )
            )
            await self._persist_learning_state()
            package.cache_tier = cache_result.tier if cache_result.tier != "full_hit" else "miss"
            package.partial_similarity = cache_result.similarity if cache_result.tier == "partial_hit" else 0.0
            decision_record.cache_tier = package.cache_tier
            decision_record.partial_similarity = package.partial_similarity
            self._attach_prediction_links(decision_record, cache_result.enrichment)
            self._last_decision_record = decision_record
            return package
        finally:
            self._notify_user_request_end()

    async def inject_history(self, queries: list[str], *, session_id: str = "external") -> int:
        await self.initialize()
        injected = 0
        for query_text in queries:
            if not query_text.strip():
                continue
            observation = self._arc_model.observe_detail(query_text)
            category = observation.category
            await self._persist_behavioral_observation(observation, session_id=session_id)
            await self.store.insert_query_history(
                session_id=session_id,
                query_text=query_text,
                selected_paths=[],
                hit_precomputed=False,
                token_used=0,
                corpus_id=getattr(self.adapter, "corpus_id", "default"),
            )
            await self.store.insert_signal_event(
                SignalEvent(
                    id=str(uuid.uuid4()),
                    source="query",
                    kind="query_issued",
                    timestamp=time.time(),
                    payload={
                        "category": category,
                        "injected": "true",
                        "corpus_id": getattr(self.adapter, "corpus_id", "default"),
                        "privacy_zone": getattr(self.adapter, "privacy_zone", "local"),
                    },
                )
            )
            injected += 1
        return injected

    async def precompute_cycle(self, governor: PredictionGovernor | None = None) -> int:
        """Run one precompute cycle as a continuous best-first exploration loop.

        The frontier unifies all scenario sources (graph, arc, validated patterns,
        and LLM follow-on branches) into a single priority queue.  The admission
        gate handles deduplication, depth caps, and marginal-utility filtering.

        Trivial scenarios (depth-0 graph-walk) are cached directly without an LLM
        call.  Non-trivial scenarios are sent to the LLM for enrichment; the LLM
        response may propose follow-on branches which re-enter the frontier through
        the same admission gate.

        The loop runs until the governor says to stop OR the frontier is saturated
        (everything reachable has been explored or coverage exceeds the configured
        threshold).
        """
        cycle_started = time.monotonic()
        await self.initialize()
        compute = self.config.compute
        # Keep the binary idle-only gate as an escape hatch: if the load is
        # genuinely saturating, skip the cycle entirely. Between idle and
        # saturation, _compute_effective_concurrency (invoked below) scales
        # parallelism smoothly so we still make progress under light load.
        if compute.idle_only:
            cpu_load = _cpu_load_fraction()
            gpu_load = _gpu_load_fraction()
            if cpu_load > compute.idle_cpu_threshold or gpu_load > compute.idle_gpu_threshold:
                self._last_explored_scenarios = []
                return 0
        cycle_deadline: float | None = None
        if compute.max_cycle_seconds and compute.max_cycle_seconds > 0:
            cycle_deadline = cycle_started + float(compute.max_cycle_seconds)
        governor = governor or PredictionGovernor()
        governor.reset()
        self._precompute_cycles += 1
        self._last_explored_scenarios = []
        full_packages = 0

        if not self._corpus_prepared:
            await self.store.replace_relationship_edges(await self._collect_relationship_edges())
            issues = await self.adapter.check_quality()
            await self.store.replace_quality_issues(
                [
                    {
                        "key": issue.key,
                        "severity": issue.severity,
                        "message": issue.message,
                        "metadata": issue.metadata,
                    }
                    for issue in issues
                ]
            )
            if not await self.store.list(limit=1):
                await self.prepare()
            self._corpus_prepared = True
            self._graph = None

        available_paths = await self._available_file_paths()
        ecfg = self.config.exploration

        # Build the frontier for this cycle and restore learned multipliers
        frontier = ExplorationFrontier(
            max_depth=ecfg.max_exploration_depth,
            max_size=ecfg.frontier_max_size,
            min_priority=ecfg.min_priority,
            dedup_threshold=ecfg.dedup_threshold,
            saturation_coverage=ecfg.saturation_coverage,
            scoring_policy=self._scoring_policy,
        )

        # Seed the frontier from all sources
        graph = await self._load_graph()
        anchor_working_set = dict(self._working_set)

        # Cold-start: if working set is empty, seed from recent graph edges
        if not anchor_working_set:
            rows = await self.store.list_relationship_edges(limit=200)
            now = time.time()
            for row in rows[:20]:
                if row[0].startswith("file:"):
                    anchor_working_set[row[0].split(":", 1)[1]] = now

        coverage = await self._coverage_map()
        covered_paths: set[str] = set()
        c = coverage.get("covered_paths")
        if isinstance(c, set):
            covered_paths = c

        frontier.seed_from_graph(anchor_working_set, graph, available_paths, covered_paths)

        recent_queries = await self.store.list_query_history(limit=10)
        recent_query_text = [str(entry["query_text"]) for entry in recent_queries]
        frontier.seed_from_workflow_phase(self._arc_model, recent_query_text, available_paths)
        frontier.seed_from_arc(self._arc_model, recent_query_text, available_paths)

        prompt_macros = await self.store.list_prompt_macros(limit=25)
        frontier.seed_from_prompt_macros(prompt_macros, available_paths)

        patterns = await self.store.list_validated_patterns(limit=50)
        frontier.seed_from_patterns(patterns)

        # Seed recovery scenarios for recent cold-miss queries.  These paths
        # clearly weren't precomputed and should be prioritized in this cycle.
        for miss_paths in self._miss_recovery_paths:
            frontier.seed_from_miss(miss_paths, available_paths)
        self._miss_recovery_paths.clear()

        # Collect artefacts once for package building (avoid repeated DB reads)
        artefacts_by_key = {a.key: a for a in await self.store.list(limit=2000)}

        # Heuristic paths for diversity bonus
        heuristic_paths: set[str] = set()
        for q in recent_query_text[-3:]:
            for a in select_artefacts(
                q,
                list(artefacts_by_key.values()),
                top_n=8,
                exclude_private=self.config.privacy.exclude_private,
                path_bonuses=self._pinned_focus_paths,
                path_excludes=self._pinned_avoid_paths,
            ):
                if a.source_path:
                    heuristic_paths.add(a.source_path)
        self._last_heuristic_paths = heuristic_paths

        # ── Adaptive depth budget (MCTS-lite two-phase strategy) ─────────────
        # Phase 1 — breadth-first: explore shallow scenarios first (depth <= 1)
        # to build wide coverage quickly.  This mirrors a chess engine starting
        # with a fast "horizon" search before committing to deeper lines.
        # Phase 2 — depth: once shallow coverage is adequate (>= 40%), open the
        # frontier to full depth so high-value LLM branches can be explored.
        _BREADTH_COVERAGE_THRESHOLD = 0.40
        phase_breadth_done = [frontier.coverage_ratio >= _BREADTH_COVERAGE_THRESHOLD]
        frontier.set_effective_max_depth(ecfg.max_exploration_depth if phase_breadth_done[0] else min(1, ecfg.max_exploration_depth))

        # ── Exploration loop (bounded-parallel) ──────────────────────────────
        # Pre-#135 this loop was strictly serial: pop → await LLM → push follow-ons.
        # The `exploration_concurrency` config field (default 4) was defined but
        # never consulted. Now we fan out LLM calls up to that bound via a
        # semaphore, with an asyncio.Lock guarding the shared state the loop
        # mutates (frontier, covered_paths, governor, and the explored-scenarios
        # accumulator). The LLM call itself runs outside the lock — that's the
        # point: concurrent LLM requests go to concurrent endpoint workers.
        #
        # P3 (idle-aware ramp): effective concurrency scales with current load.
        # At idle it runs at the full config value; under load it clamps down to
        # 1 rather than skipping the cycle entirely (which was the pre-P3 behavior
        # for idle_only=true).
        effective_concurrency = self._compute_effective_concurrency(ecfg, compute)

        # One-time warning: raising `exploration_concurrency` without matching
        # server-side concurrency (e.g. OLLAMA_NUM_PARALLEL) actively hurts
        # throughput. Live benchmarks on default ollama confirmed that 8
        # parallel generate requests took ~3x the wall time of the same 8
        # serial requests. Warn the operator exactly once per engine instance
        # so the footgun is surfaced in the daemon log.
        if compute.exploration_concurrency > 1 and not self._concurrency_banner_emitted:
            import logging as _logging

            _log = _logging.getLogger(__name__)
            _log.warning(
                "Vaner: compute.exploration_concurrency=%d. The exploration LLM "
                "endpoint must serve concurrent requests or throughput will *degrade* "
                "below serial. For ollama, set OLLAMA_NUM_PARALLEL=%d on the server. "
                "vLLM / OpenAI-compatible servers typically serve concurrency natively. "
                "See docs/performance.md for the full tuning ladder.",
                compute.exploration_concurrency,
                compute.exploration_concurrency,
            )
            self._concurrency_banner_emitted = True
        cycle_sem = asyncio.Semaphore(effective_concurrency)
        state_lock = asyncio.Lock()
        in_flight: list[asyncio.Task[None]] = []
        full_packages_box = [0]

        async def _process_scenario(scenario: ExplorationScenario) -> None:
            """Explore one scenario; mutate shared state under state_lock."""
            async with cycle_sem:
                if cycle_deadline is not None and time.monotonic() >= cycle_deadline:
                    return

                use_llm = self._should_use_llm(scenario, ecfg)
                # High-priority scenarios (and anything already carrying a
                # depth bonus from a high-priority ancestor) get wider LLM
                # fan-out and softer per-hop decay so Vaner can invest more
                # cycles on predictions it already scored as likely.
                is_high_priority = scenario.priority >= ecfg.deep_drill_priority_threshold or scenario.depth_bonus > 0
                llm_semantic_intent = ""
                follow_on: list[dict[str, object]] = []
                llm_confidence = 0.0
                effective_paths: list[str] = list(scenario.file_paths)

                if use_llm and callable(self.llm):
                    try:
                        ranked_files, follow_on, llm_semantic_intent, llm_confidence = await self._explore_scenario_with_llm(
                            scenario=scenario,
                            available_paths=available_paths,
                            recent_queries=recent_query_text,
                            covered_paths=covered_paths,
                            artefacts_by_key=artefacts_by_key,
                            high_priority=is_high_priority,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        # Individual scenario failure must not kill the cycle.
                        ranked_files, follow_on, llm_semantic_intent, llm_confidence = [], [], "", 0.0
                    if ranked_files:
                        effective_paths = list(ranked_files)

                pred_scenario = PredictionScenario(
                    question=f"context:{scenario.anchor}",
                    unit_ids=effective_paths,
                    confidence=scenario.priority,
                    rationale=scenario.reason,
                )
                combined_intent = " ".join(part for part in [scenario.reason, llm_semantic_intent] if part)
                try:
                    cached = await self._cache_context_for_scenario(
                        pred_scenario,
                        exploration_source=scenario.source,
                        semantic_intent=combined_intent,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    cached = None

                async with state_lock:
                    # Push follow-on branches (LLM confidence gates priority).
                    # High-priority lineages use a softer decay and carry a
                    # decrementing depth bonus so the deep line keeps ranking
                    # near the top of the frontier instead of being crowded
                    # out by fresh shallow seeds.
                    if is_high_priority:
                        branch_decay = ecfg.deep_drill_branch_decay
                    else:
                        branch_decay = self._scoring_policy.branch_priority_decay
                    if scenario.priority >= ecfg.deep_drill_priority_threshold:
                        # Fresh high-priority lineage: grant full bonus budget.
                        child_depth_bonus = max(scenario.depth_bonus, ecfg.deep_drill_depth_bonus)
                    else:
                        # Inherited lineage: decrement the bonus each hop.
                        child_depth_bonus = max(0, scenario.depth_bonus - 1)
                    for branch in follow_on:
                        branch_files_raw = branch.get("files", [])
                        if not isinstance(branch_files_raw, list):
                            continue
                        branch_files: list[str] = [str(f) for f in branch_files_raw if f]
                        if not branch_files:
                            continue
                        branch_conf = float(branch.get("confidence", llm_confidence or 0.5))
                        branch_conf = max(0.1, min(1.0, branch_conf))
                        branch_priority = scenario.priority * branch_decay * branch_conf
                        branch_scenario = ExplorationScenario(
                            id=file_set_fingerprint(branch_files),
                            file_paths=branch_files,
                            anchor=scenario.id,
                            source="llm_branch",
                            priority=branch_priority,
                            depth=scenario.depth + 1,
                            parent_id=scenario.id,
                            reason=str(branch.get("reason", "")),
                            layer="operational",
                            depth_bonus=child_depth_bonus,
                        )
                        frontier.push(branch_scenario)

                    self._last_explored_scenarios.append(
                        ExploredScenario(
                            source=scenario.source,
                            anchor=scenario.anchor,
                            reason=scenario.reason,
                            priority=scenario.priority,
                            depth=scenario.depth,
                            unit_ids=list(effective_paths),
                            cached=bool(cached),
                        )
                    )
                    if cached:
                        full_packages_box[0] += 1
                        covered_paths.update(effective_paths)

                    frontier.mark_explored(scenario.id, covered_files=effective_paths)
                    governor.iteration_done(1)

                    if not phase_breadth_done[0] and frontier.coverage_ratio >= _BREADTH_COVERAGE_THRESHOLD:
                        phase_breadth_done[0] = True
                        frontier.set_effective_max_depth(ecfg.max_exploration_depth)

        # Bounded-parallel dispatcher. We pop one scenario at a time under the
        # state lock (cheap), spawn a task for it, and maintain backpressure so
        # the pending-task pool never balloons past 2× concurrency. When the
        # frontier empties we still drain in-flight tasks in case LLM branches
        # push new work back onto the queue.
        max_inflight = max(2, effective_concurrency * 2)
        while governor.should_continue() and not frontier.is_saturated():
            if cycle_deadline is not None and time.monotonic() >= cycle_deadline:
                break

            async with state_lock:
                scenario = frontier.pop()

            if scenario is None:
                # No pending work right now. If tasks are in flight they may
                # push follow-ons on completion, so wait for one to finish and
                # check again. Otherwise we're done.
                if not in_flight:
                    break
                done, pending = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
                in_flight = [t for t in pending]
                # Surface the earliest exception (tasks swallow their own).
                for task in done:
                    exc = task.exception()
                    if exc is not None and not isinstance(exc, asyncio.CancelledError):
                        raise exc
                continue

            task = asyncio.create_task(_process_scenario(scenario))
            in_flight.append(task)

            if len(in_flight) >= max_inflight:
                done, pending = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
                in_flight = [t for t in pending]
                for task in done:
                    exc = task.exception()
                    if exc is not None and not isinstance(exc, asyncio.CancelledError):
                        raise exc

            # Short adaptive sleep when both frontier and in-flight are empty.
            if frontier.pending_count == 0 and not in_flight and governor.mode != PredictionGovernor.Mode.BUDGET:
                await asyncio.sleep(governor.inter_iteration_delay)

        # Drain any remaining in-flight tasks so their state mutations finish
        # before we tear down the cycle. Cancelled tasks surface cleanly.
        if in_flight:
            results = await asyncio.gather(*in_flight, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                    raise result

        full_packages = full_packages_box[0]

        retention_seconds = max(3600, int(self.config.max_age_seconds))
        await self.store.purge_old_signal_events(max_age_seconds=retention_seconds)
        await self.store.purge_old_replay_entries(max_age_seconds=retention_seconds)
        await self.store.purge_old_query_history(max_age_seconds=retention_seconds)
        await self.store.purge_expired_prediction_cache()
        await self.store.purge_stale_patterns(max_age_seconds=retention_seconds)
        await self._persist_learning_state()
        _record_idle_usage_seconds(self.config, time.monotonic() - cycle_started)
        return full_packages

    def get_explored_scenarios(self) -> list[ExploredScenario]:
        """Return scenarios explored in the most recent precompute cycle."""
        return list(self._last_explored_scenarios)

    def get_last_decision_record(self) -> DecisionRecord | None:
        return self._last_decision_record

    @staticmethod
    def _compute_effective_concurrency(ecfg: ExplorationConfig, compute: ComputeConfig) -> int:
        """Decide how many exploration LLM calls to run in parallel this cycle.

        Returns a value in ``[1, compute.exploration_concurrency]``. When
        ``idle_only=False`` (always-on compute) returns the configured value.
        When ``idle_only=True`` (the default) scales inversely with current
        load so the daemon shares the machine with foreground work.

        At load 0.0 returns the full configured concurrency; at load 0.9 returns
        approximately 10% of configured (floor 1). The binary `idle_only` cutoff
        already ran above this — so by the time we get here we're below the
        threshold and can always make at least serial progress.

        The ``ecfg`` argument is accepted but currently unused; kept in the
        signature for symmetry with other helpers that take both configs and to
        leave room for an exploration-side override (e.g., per-endpoint parallel
        budgets once multi-endpoint routing lands).
        """
        del ecfg  # reserved for future per-exploration overrides
        base = max(1, int(compute.exploration_concurrency))
        if not compute.idle_only:
            return base
        cpu_load = _cpu_load_fraction()
        gpu_load = _gpu_load_fraction()
        load = max(cpu_load, gpu_load)
        # Clamp load to [0, 1] before scaling.
        load = max(0.0, min(1.0, load))
        scaled = int(round(base * (1.0 - load)))
        return max(1, min(base, scaled))

    @staticmethod
    def _should_use_llm(scenario: ExplorationScenario, ecfg: ExplorationConfig) -> bool:
        """Decide whether this scenario should be sent to the LLM."""
        if ecfg.llm_gate == "none":
            return False
        if ecfg.llm_gate == "all":
            return True
        # "non_trivial": skip LLM for trivial depth-0 graph scenarios
        return not scenario.is_trivial()

    async def _explore_scenario_with_llm(
        self,
        *,
        scenario: ExplorationScenario,
        available_paths: list[str],
        recent_queries: list[str],
        covered_paths: set[str],
        artefacts_by_key: dict[str, object],
        high_priority: bool = False,
    ) -> tuple[list[str], list[dict[str, object]], str, float]:
        """Send a scenario to the LLM for enrichment and branch proposals.

        The LLM is given:
          - the scenario's file set and its artefact summaries
          - recent developer queries and working-set paths
          - what is already covered (to steer toward novel territory)

        It returns a 4-tuple:
          - ranked_files: the scenario's files re-ranked by likely relevance
          - follow_on: 0-3 adjacent scenarios worth exploring next
          - semantic_intent: natural-language description of the exploration intent
            (used to enrich cache entries for conceptual query matching)
          - confidence: LLM's estimated value of this exploration (0.0-1.0)

        Returns ([], [], "", 0.0) if the LLM is unavailable or produces unusable output.
        """
        from vaner.intent.reasoner import _as_str_list

        if not callable(self.llm):
            return [], [], "", 0.0

        # Build brief artefact summaries for the scenario's files
        file_summaries: list[str] = []
        for path in scenario.file_paths[:8]:
            key = f"file_summary:{path}"
            artefact = artefacts_by_key.get(key)  # type: ignore[call-overload]
            if artefact is not None:
                content = getattr(artefact, "content", "")
                snippet = content[:300].replace("\n", " ") if content else "(no summary)"
                file_summaries.append(f"  {path}: {snippet}")
            else:
                file_summaries.append(f"  {path}: (no artefact)")
        summaries_text = "\n".join(file_summaries) or "  (none)"

        working_set_hint = "\n".join(sorted(self._working_set.keys())[:15]) or "none"
        recent_hint = "\n".join(reversed(recent_queries[-8:])) or "none"
        covered_hint = "\n".join(sorted(covered_paths)[:20]) or "none"
        uncovered = [p for p in available_paths if p not in covered_paths]
        available_hint = "\n".join(uncovered[:50]) or "none"

        ecfg = self.config.exploration
        if high_priority:
            max_follow_on = max(3, int(ecfg.deep_drill_max_followons))
            follow_on_guidance = (
                f"2. THIS IS A HIGH-CONFIDENCE NEXT-PROMPT PREDICTION. Invest effort and\n"
                f"   suggest 0-{max_follow_on} ADJACENT scenarios worth exploring. Cover the\n"
                "   second-order context the developer will likely need if this prediction\n"
                "   lands (direct callers, callees, tests, configuration, cross-cutting\n"
                "   concerns). Prefer higher-confidence branches over breadth-for-its-own-sake.\n"
            )
            priority_tag = " [HIGH-PRIORITY]"
        else:
            max_follow_on = 3
            follow_on_guidance = (
                "2. Suggest 0-3 ADJACENT scenarios worth exploring: file sets NOT in this\n"
                "   scenario that the developer might need as a consequence. Only suggest\n"
                "   scenarios you are confident have high value.\n"
            )
            priority_tag = ""

        prompt = (
            f"You are a code-context exploration engine.{priority_tag} Evaluate this scenario and decide "
            "which files are most relevant and what adjacent scenarios are worth exploring next.\n\n"
            f"Developer context:\n"
            f"- Recent queries:\n{recent_hint}\n"
            f"- Working set (recently touched):\n{working_set_hint}\n\n"
            f"Scenario to evaluate (proposed by: {scenario.source}):\n"
            f"Files: {', '.join(scenario.file_paths[:8])}\n"
            f"Reason: {scenario.reason}\n\n"
            f"File summaries:\n{summaries_text}\n\n"
            f"Already covered (do NOT repeat):\n{covered_hint}\n\n"
            f"Available uncovered paths (candidates for follow-on):\n{available_hint}\n\n"
            "Tasks:\n"
            "1. Rank these files by likely relevance to the developer's next interaction.\n"
            "   Drop any that seem irrelevant given the developer's trajectory.\n"
            f"{follow_on_guidance}"
            "3. Write a short semantic_intent (1-2 sentences) describing what developer\n"
            "   need this scenario addresses (e.g. 'authentication middleware, JWT validation').\n"
            "   This is used for matching future queries to this cached context.\n"
            "4. Set confidence (0.0-1.0): how likely is this area to be needed next?\n\n"
            "Return JSON only (no markdown fences):\n"
            "{\n"
            '  "ranked_files": ["path/a.py", "path/b.py"],\n'
            '  "semantic_intent": "...",\n'
            '  "confidence": 0.0,\n'
            '  "follow_on": [\n'
            '    {"files": ["path/x.py"], "reason": "...", "confidence": 0.0}\n'
            "  ]\n"
            "}"
        )

        try:
            llm_output = await self.llm(prompt)  # type: ignore[misc]
        except Exception:
            return [], [], "", 0.0

        # Parse the JSON response
        import json as _json

        text = llm_output.strip()
        # Strip optional markdown fences
        if text.startswith("```"):
            lines = text.splitlines()
            lines = lines[1:] if lines and lines[0].startswith("```") else lines
            lines = lines[:-1] if lines and lines[-1].strip().startswith("```") else lines
            text = "\n".join(lines).strip()
        # Find JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return [], [], "", 0.0
        try:
            obj = _json.loads(text[start : end + 1])
        except _json.JSONDecodeError:
            return [], [], "", 0.0

        available_set = set(available_paths)
        ranked_raw = _as_str_list(obj.get("ranked_files", []))
        ranked_files = [p for p in ranked_raw if p in available_set][:8]

        # Parse semantic_intent (richer description for cache matching)
        semantic_intent = str(obj.get("semantic_intent", "")).strip()

        # Parse top-level confidence
        raw_conf = obj.get("confidence", 0.0)
        try:
            confidence = float(raw_conf)
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.0

        follow_on: list[dict[str, object]] = []
        raw_follow_on = obj.get("follow_on", [])
        if not isinstance(raw_follow_on, list):
            raw_follow_on = []
        for item in raw_follow_on[:max_follow_on]:
            if not isinstance(item, dict):
                continue
            files = [p for p in _as_str_list(item.get("files", [])) if p in available_set]
            if files:
                item_conf = 0.0
                try:
                    item_conf = max(0.0, min(1.0, float(item.get("confidence", 0.0))))
                except (TypeError, ValueError):
                    pass
                follow_on.append(
                    {
                        "files": files,
                        "reason": str(item.get("reason", "")),
                        "confidence": item_conf,
                    }
                )

        return ranked_files, follow_on, semantic_intent, confidence

    async def propagate_related_keys(self, source_key: str, depth: int = 2) -> list[str]:
        await self.initialize()
        graph = await self._load_graph()
        return graph.propagate(source_key, depth=depth)

    async def run_background(self, interval_seconds: int = 15, governor: PredictionGovernor | None = None) -> None:
        self._running = True
        self._background_governor = governor or PredictionGovernor(mode=PredictionGovernor.Mode.BACKGROUND)
        while self._running:
            await self.precompute_cycle(governor=self._background_governor)
            if self._background_governor.mode == PredictionGovernor.Mode.BUDGET:
                await asyncio.sleep(interval_seconds)
            else:
                await asyncio.sleep(self._background_governor.inter_iteration_delay)

    def start_background(
        self,
        interval_seconds: int = 15,
        governor: PredictionGovernor | None = None,
    ) -> asyncio.Task[None]:
        if self._background_task is not None and not self._background_task.done():
            return self._background_task
        self._background_task = asyncio.create_task(self.run_background(interval_seconds=interval_seconds, governor=governor))
        return self._background_task

    def stop_background(self) -> None:
        self._running = False
        if self._background_governor is not None:
            self._background_governor.stop()
        if self._background_task is not None:
            self._background_task.cancel()

    def _session_id(self) -> str:
        return self._active_session_id

    def reset_session(self) -> None:
        self._active_session_id = self._make_session_id()

    def _make_session_id(self) -> str:
        digest = hashlib.sha1(str(self.config.repo_root).encode("utf-8")).hexdigest()[:10]  # noqa: S324
        branch = read_git_state(self.config.repo_root).get("branch", "default") or "default"
        return f"{digest}:{branch}"

    def _notify_user_request_start(self) -> None:
        if self._background_governor is not None:
            self._background_governor.notify_user_request_start()

    def _notify_user_request_end(self) -> None:
        if self._background_governor is not None:
            self._background_governor.notify_user_request_end()

    def _max_reasoner_iterations(self, governor: PredictionGovernor) -> int:
        if governor.mode == PredictionGovernor.Mode.BUDGET:
            return max(1, governor.remaining)
        return max(1, min(50, self.config.generation.max_generations_per_cycle))

    _WORKING_SET_MAX = 100  # paths retained in memory

    def _anchor_paths(self) -> list[str]:
        """Return working-set paths sorted by most recently touched first."""
        if not self._working_set:
            return []
        return [p for p, _ in sorted(self._working_set.items(), key=lambda x: x[1], reverse=True)]

    def _update_working_set(self, paths: list[str]) -> None:
        """Add paths to the working set, evicting the oldest entries when full."""
        now = time.time()
        for path in paths:
            if path:
                self._working_set[path] = now
        if len(self._working_set) > self._WORKING_SET_MAX:
            # Keep only the most recently touched entries
            trimmed = sorted(self._working_set.items(), key=lambda x: x[1], reverse=True)
            self._working_set = dict(trimmed[: self._WORKING_SET_MAX])

    async def _load_graph(self) -> RelationshipGraph:
        """Load the relationship graph from the store (cached in memory)."""
        if self._graph is not None:
            return self._graph
        rows = await self.store.list_relationship_edges(limit=10000)
        self._graph = RelationshipGraph([RelationshipEdge(source_key=row[0], target_key=row[1], kind=row[2]) for row in rows])
        return self._graph

    _CATEGORY_KEYWORDS: dict[str, list[str]] = {
        "review": ["review", "audit", "comment", "feedback", "lint"],
        "planning": ["plan", "roadmap", "design", "architecture", "proposal"],
        "testing": ["test", "spec", "conftest", "_test", "test_", "ci"],
        "debugging": ["error", "exception", "log", "debug", "trace", "fix"],
        "implementation": ["impl", "core", "engine", "handler", "service"],
        "cleanup": ["util", "helper", "common", "base", "mixin", "refactor"],
        "understanding": ["readme", "doc", "api", "interface", "schema"],
    }

    async def _graph_walk_scenarios(
        self,
        anchor_paths: list[str],
        n: int,
        available_paths: list[str],
    ) -> list[PredictionScenario]:
        """Generate scenarios by walking the dependency graph from anchor paths.

        For each anchor, we collect its structural neighborhood (imports,
        dependents, related tests) and package them as a high-confidence
        prediction scenario.  No LLM needed — these are purely structural.
        """
        if not anchor_paths:
            return []
        graph = await self._load_graph()
        available_set = set(available_paths)
        scenarios: list[PredictionScenario] = []
        seen_file_sets: list[frozenset[str]] = []
        # Scan enough anchors to fill the budget; the dedup filter means we
        # need more candidates than budget slots.
        scan_limit = max(n * 3, 20)
        for path in anchor_paths[:scan_limit]:
            file_key = f"file:{path}"
            neighbors = graph.propagate(file_key, depth=2)
            neighbor_paths = [
                key.split(":", 1)[1] for key in neighbors if key.startswith("file:") and key.split(":", 1)[1] in available_set
            ]
            # Include isolated anchors (no graph neighbors) as single-file scenarios
            file_set = frozenset([path] + neighbor_paths[:7])
            # Skip very similar file sets to avoid redundant cache entries
            if any(len(file_set & prev) / max(1, len(file_set | prev)) > 0.8 for prev in seen_file_sets):
                continue
            seen_file_sets.append(file_set)
            scenarios.append(
                PredictionScenario(
                    question=f"context:{path}",
                    unit_ids=[path] + neighbor_paths[:7],
                    confidence=0.85,
                    rationale=f"dependency neighborhood of {path}",
                )
            )
            if len(scenarios) >= n:
                break
        return scenarios

    async def _arc_scenarios(
        self,
        n: int,
        available_paths: list[str],
    ) -> list[PredictionScenario]:
        """Generate scenarios based on ConversationArcModel predictions.

        Uses the arc model to predict the next interaction category (debugging,
        testing, implementation, …) and selects paths that match that category.

        Cold-start fallback: when the arc model has no transition history yet
        (e.g. very start of a session), we emit one scenario per known category
        ordered by how many matching paths exist in the repo — giving the graph-
        walk 20% budget something useful to do even before the first query.
        """
        recent_queries = await self.store.list_query_history(limit=10)
        recent_texts = [str(entry["query_text"]) for entry in recent_queries]

        if recent_texts:
            phase_summary = self._arc_model.summarize_workflow_phase(recent_texts)
            current_phase = phase_summary.phase
            next_categories = self._arc_model.predict_next(
                phase_summary.dominant_category,
                top_k=min(n, 3),
                recent_queries=recent_texts,
            )
        else:
            current_phase = "exploring"
            next_categories = []

        # Cold-start fallback: rank categories by path-count match
        if not next_categories:
            ranked: list[tuple[str, float]] = []
            for cat, kws in self._CATEGORY_KEYWORDS.items():
                count = sum(1 for p in available_paths if any(kw in p.lower() for kw in kws))
                if count:
                    ranked.append((cat, count / max(1, len(available_paths))))
            ranked.sort(key=lambda x: x[1], reverse=True)
            next_categories = ranked[: min(n, 3)]

        scenarios: list[PredictionScenario] = []
        seen_file_sets: list[frozenset[str]] = []
        for category, confidence in next_categories[:n]:
            keywords = self._CATEGORY_KEYWORDS.get(category, [])
            matched = [p for p in available_paths if any(kw in p.lower() for kw in keywords)][:6]
            if not matched:
                matched = available_paths[:3]
            file_set = frozenset(matched)
            # Deduplicate across categories
            if any(len(file_set & prev) / max(1, len(file_set | prev)) > 0.7 for prev in seen_file_sets):
                continue
            seen_file_sets.append(file_set)
            scenarios.append(
                PredictionScenario(
                    question=f"arc:{category}",
                    unit_ids=matched,
                    confidence=confidence * 0.7,
                    rationale=f"arc model: {current_phase} → {category}",
                )
            )
        return scenarios

    async def _sync_extra_context_sources(self) -> None:
        if not self._extra_context_sources:
            return
        if self._extra_sources_synced and not self._extra_sources_dirty:
            return
        synced_any = False
        attempted_sync = False
        for src in self._extra_context_sources:
            try:
                items = await src.list_items(limit=500)
            except Exception:
                continue
            attempted_sync = True
            for item in items:
                source_path = str(item.metadata.get("path", item.key))
                artefact = Artefact(
                    key=f"file_summary:{item.key}",
                    kind=ArtefactKind.FILE_SUMMARY,
                    source_path=source_path,
                    source_mtime=float(item.updated_at),
                    generated_at=time.time(),
                    model=f"context_source:{getattr(src, 'source_type', 'external')}",
                    content=item.content,
                    metadata={
                        **item.metadata,
                        "corpus_id": getattr(item, "corpus_id", getattr(src, "corpus_id", "default")),
                        "privacy_zone": getattr(item, "privacy_zone", getattr(src, "privacy_zone", "local")),
                        "source_type": getattr(src, "source_type", "external"),
                    },
                )
                await self.store.upsert(artefact)
                synced_any = True
        if synced_any or attempted_sync:
            self._extra_sources_synced = True
            self._extra_sources_dirty = False

    async def _sync_pinned_facts(self) -> None:
        if self._pinned_facts_synced and not self._pinned_facts_dirty:
            return
        rows = await self.store.list_pinned_facts()
        self._pinned_focus_paths = [str(row.get("value", "")) for row in rows if str(row.get("key", "")) == "focus_paths"]
        self._pinned_avoid_paths = [str(row.get("value", "")) for row in rows if str(row.get("key", "")) == "avoid_paths"]
        adapter_corpus_id = getattr(self.adapter, "corpus_id", "default")
        trusted_follow_ups: list[str] = []
        for row in rows:
            key = str(row.get("key", ""))
            if not key.startswith("follow_up_pattern:"):
                continue
            scoring_hint = row.get("scoring_hint")
            if not isinstance(scoring_hint, dict):
                continue
            if str(scoring_hint.get("state", "candidate")) != "trusted":
                continue
            scoped_corpus = str(scoring_hint.get("corpus_id", adapter_corpus_id))
            if scoped_corpus != adapter_corpus_id:
                continue
            value = str(row.get("value", "")).strip()
            if value:
                trusted_follow_ups.append(value)
        self._trusted_follow_up_patterns = trusted_follow_ups[:5]

        # Remove previously applied prefer-source boosts before re-applying.
        for source, delta in self._applied_prefer_source_deltas.items():
            current = float(self._scoring_policy.source_multipliers.get(source, 1.0))
            self._scoring_policy.source_multipliers[source] = max(0.3, current - delta)
        self._applied_prefer_source_deltas = {}

        prefer_source_targets = [str(row.get("value", "")).strip() for row in rows if str(row.get("key", "")) == "prefer_source"]
        for source in prefer_source_targets:
            if not source:
                continue
            current = float(self._scoring_policy.source_multipliers.get(source, 1.0))
            updated = min(2.0, current + 0.10)
            applied_delta = updated - current
            self._scoring_policy.source_multipliers[source] = updated
            self._applied_prefer_source_deltas[source] = self._applied_prefer_source_deltas.get(source, 0.0) + applied_delta

        self._pinned_facts_synced = True
        self._pinned_facts_dirty = False
        self._mark_policy_state_dirty()

    async def _collect_relationship_edges(self) -> list[tuple[str, str, str, str]]:
        adapter_corpus_id = getattr(self.adapter, "corpus_id", "default")
        edges = [(edge.source_key, edge.target_key, edge.kind, adapter_corpus_id) for edge in await self.adapter.extract_relationships()]
        for src in self._extra_context_sources:
            try:
                extra_edges = await src.extract_relationships()
            except Exception:
                continue
            edges.extend(
                (
                    edge.source_key,
                    edge.target_key,
                    edge.kind,
                    str(getattr(edge, "corpus_id", "") or getattr(src, "corpus_id", "default")),
                )
                for edge in extra_edges
            )
        return edges

    async def _available_file_paths(self) -> list[str]:
        items = await self.adapter.list_items()
        # Merge paths from extra context sources
        extra_items = []
        for src in self._extra_context_sources:
            try:
                extra_items.extend(await src.list_items())
            except Exception:
                pass
        paths: list[str] = []
        for item in list(items) + extra_items:
            path = str(item.metadata.get("path", "")).strip()
            if path:
                paths.append(path)
        return sorted(set(paths))

    async def _coverage_map(self) -> dict[str, object]:
        cache_rows = await self.store.list_prediction_cache(limit=500)
        covered_paths: set[str] = set()
        for row in cache_rows:
            enrichment = row.get("enrichment", {})
            if not isinstance(enrichment, dict):
                continue
            # Read from both canonical keys so graph-walk entries (anchor_files)
            # and legacy LLM entries (source_paths) are both accounted for.
            for key in ("anchor_files", "source_paths"):
                for rel_path in enrichment.get(key, []):
                    if isinstance(rel_path, str) and rel_path:
                        covered_paths.add(rel_path)
        return {
            "covered_paths": covered_paths,
            "cache_entries": len(cache_rows),
        }

    async def _feedback_digest(self, *, limit: int = 30) -> str:
        events = await self.store.list_feedback_events(limit=limit)
        if not events:
            return "no feedback yet"
        incremental = [event for event in events if float(event.get("timestamp", 0.0)) > self._feedback_cursor_ts]
        effective_events = incremental or events
        self._feedback_cursor_ts = max(float(event.get("timestamp", 0.0)) for event in effective_events)
        tier_counts: dict[str, int] = {}
        similarity_values: list[float] = []
        quality_values: list[float] = []
        for event in effective_events:
            tier = str(event.get("cache_tier", "unknown"))
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            similarity_values.append(float(event.get("similarity", 0.0)))
            quality = event.get("quality_lift")
            if quality is not None:
                quality_values.append(float(quality))
        avg_similarity = sum(similarity_values) / max(1, len(similarity_values))
        avg_quality = sum(quality_values) / max(1, len(quality_values)) if quality_values else 0.0
        tier_summary = ", ".join(f"{key}:{value}" for key, value in sorted(tier_counts.items()))
        return f"tiers={tier_summary}; avg_similarity={avg_similarity:.3f}; avg_quality_lift={avg_quality:.3f}"

    async def _run_reasoner_loop_iteration(
        self,
        *,
        available_paths: list[str],
        coverage: dict[str, object],
    ) -> list[PredictionScenario]:
        covered_paths = {str(path) for path in coverage.get("covered_paths", set())}
        uncovered = [path for path in available_paths if path not in covered_paths]
        if not uncovered:
            uncovered = available_paths[:]
        recent_queries = await self.store.list_query_history(limit=10)
        recent_query_text = [str(entry["query_text"]) for entry in recent_queries]
        context = await self.adapter.get_context_for_reasoning()
        feedback_summary = await self._feedback_digest(limit=40)
        llm_output: str | None = None
        if callable(self.llm):
            uncovered_hint = "\n".join(uncovered[:25])
            recent_hint = "\n".join(reversed(recent_query_text[:8]))

            # Compute what the heuristic selector would already pick for recent
            # queries so the LLM can focus on genuinely complementary files.
            artefacts_for_heuristic = await self.store.list(limit=2000)
            heuristic_paths: set[str] = set()
            for q in recent_query_text[-3:]:
                for a in select_artefacts(
                    q,
                    artefacts_for_heuristic,
                    top_n=8,
                    exclude_private=self.config.privacy.exclude_private,
                    path_bonuses=self._pinned_focus_paths,
                    path_excludes=self._pinned_avoid_paths,
                ):
                    if a.source_path:
                        heuristic_paths.add(a.source_path)
            self._last_heuristic_paths = heuristic_paths
            heuristic_hint = "\n".join(sorted(heuristic_paths)[:15])

            # Summarise what graph-walk and arc generators already cover so the
            # LLM focuses purely on the long-tail predictions they can't make.
            graph_covered = "\n".join(sorted(covered_paths)[:20]) or "none"
            prompt = (
                "You are Vaner's speculative prediction engine — the long-tail layer.\n"
                "The system has ALREADY pre-built context packages for:\n"
                "  • Dependency-graph neighborhoods (structural, high-confidence)\n"
                "  • Conversation-arc predictions (behavioral, category-based)\n"
                "Your job is to predict interactions that NEITHER of those layers would cover:\n"
                "  – Cross-cutting refactors involving unrelated modules\n"
                "  – Config or infra files triggered by a specific code change\n"
                "  – Third-party API surfaces the developer will need to read\n"
                "  – Novel debugging paths not implied by recent errors\n\n"
                "Return a JSON array with fields: question, file_paths, confidence, rationale.\n"
                "Limit to 3-5 predictions.\n\n"
                f"Recent queries:\n{recent_hint or 'none'}\n\n"
                f"Feedback summary:\n{feedback_summary}\n\n"
                f"Context summary:\n{context.summary}\n\n"
                "Already covered by graph-walk + arc generators (do NOT repeat unless truly essential):\n"
                f"{graph_covered}\n\n"
                "Heuristic selector would also pick:\n"
                f"{heuristic_hint or 'none'}\n\n"
                "Uncovered repo paths (good candidates):\n"
                f"{uncovered_hint or 'none'}\n"
            )
            llm_output = await self.llm(prompt)
        scenarios = await self._reasoner.generate_scenarios(
            llm_output=llm_output,
            available_paths=available_paths,
            covered_paths=covered_paths,
            limit=5,
        )
        for scenario in scenarios:
            scenario.unit_ids = self._augment_paths_for_question(
                scenario.question,
                scenario.unit_ids,
                available_paths,
                max_paths=4,
            )
        return scenarios

    def _augment_paths_for_question(
        self,
        question: str,
        current_paths: list[str],
        available_paths: list[str],
        *,
        max_paths: int,
    ) -> list[str]:
        selected = [path for path in current_paths if path]
        selected_set = set(selected)
        question_tokens = self._tokenize(question)
        if not question_tokens:
            return selected[:max_paths]
        scored: list[tuple[float, str]] = []
        for path in available_paths:
            if path in selected_set:
                continue
            path_tokens = self._tokenize(path.replace("/", " ").replace(".", " "))
            overlap = self._token_overlap(question_tokens, path_tokens)
            if overlap <= 0.0:
                continue
            scored.append((overlap, path))
        scored.sort(reverse=True)
        for _, path in scored:
            selected.append(path)
            if len(selected) >= max_paths:
                break
        return selected[:max_paths]

    async def _cache_context_for_scenario(
        self,
        scenario: PredictionScenario,
        *,
        exploration_source: str = "",
        semantic_intent: str = "",
    ) -> bool:
        package, keys = await self._build_package_for_paths(
            scenario.question, scenario.file_paths, heuristic_paths=self._last_heuristic_paths
        )
        await self.store.insert_hypothesis(
            question=scenario.question,
            confidence=scenario.confidence,
            evidence=[scenario.rationale] if scenario.rationale else [],
            relevant_keys=keys,
            category=classify_query_category(scenario.question),
            response_format="explanation",
            follow_ups=[],
        )
        enrichment: dict[str, object] = {
            "relevant_keys": keys,
            # Both keys used by TieredPredictionCache._path_overlap_score
            "source_paths": scenario.file_paths,
            "anchor_files": scenario.file_paths,
            "scenario_question": scenario.question,
            "confidence": scenario.confidence,
            "rationale": scenario.rationale,
            "exploration_source": exploration_source,
        }
        # Store the LLM's semantic reasoning about this scenario so the cache
        # can match conceptual queries ("how does X work?") even without path overlap.
        if semantic_intent:
            enrichment["semantic_intent"] = semantic_intent
        await self._cache.store_entry(
            prompt_hint=scenario.question,
            package=package,
            enrichment=enrichment,
        )
        return package is not None and bool(package.selections)

    async def _build_package_for_paths(
        self,
        question: str,
        file_paths: list[str],
        *,
        heuristic_paths: set[str] | None = None,
    ) -> tuple[ContextPackage | None, list[str]]:
        if not file_paths:
            return None, []
        artefacts_by_key = {artefact.key: artefact for artefact in await self.store.list(limit=2000)}
        selected = []
        selected_keys: list[str] = []
        for path in file_paths:
            key = f"file_summary:{path}"
            artefact = artefacts_by_key.get(key)
            if artefact is None:
                continue
            selected.append(artefact)
            selected_keys.append(key)
        if not selected:
            return None, []
        # Give a diversity bonus to files the heuristic would NOT already
        # select. This promotes genuinely complementary LLM predictions
        # without penalising files that happen to overlap with heuristic picks.
        _heuristic = heuristic_paths or set()
        score_map = {artefact.key: (1.0 if artefact.source_path in _heuristic else 1.3) for artefact in selected}
        package, _decision_record = assemble_context_package(
            question,
            selected[:8],
            self.config.max_context_tokens,
            repo_root=self.config.repo_root,
            max_age_seconds=self.config.max_age_seconds,
            score_map=score_map,
            return_decision=True,
        )
        return package, selected_keys

    def _prediction_prompt(self, prediction_key: str) -> str:
        if ":" in prediction_key:
            tail = prediction_key.split(":", 1)[1]
            return f"explain {tail}"
        return f"explain {prediction_key}"

    def _category_for_artefact(self, source_path: str, content: str) -> str:
        candidate = f"{source_path}\n{content[:400]}"
        return classify_query_category(candidate)

    async def train_batch(self, output_dir: Path | None = None) -> Path | None:
        await self.initialize()
        model_path = await self._trainer.train_batch(output_dir or self.store.db_path.parent)
        if model_path is not None:
            self._intent_scorer = IntentScorer(model_path=model_path)
            self._trainer.scorer = self._intent_scorer
            self._mark_policy_state_dirty()
            await self._persist_learning_state()
        return model_path

    async def load_bundle(self, bundle_dir: Path | str) -> bool:
        """Apply a pre-trained bundle to this engine's store.

        A bundle is a directory produced by ``eval/train_policy.py`` containing:
        - ``intent_scorer.txt`` (or ``.json``/``.cbm``) — LightGBM/XGBoost/CatBoost model
        - ``intent_scorer_metadata.json`` — scorer metadata (model_influence etc.)
        - ``scoring_policy.json`` — serialised ScoringPolicy

        Calling this promotes the bundle: the model and policy are loaded into
        the engine and persisted to the store so future ``initialize()`` calls
        load them automatically.

        Returns True if the bundle was applied successfully, False on failure.
        """
        import json as _json

        await self.initialize()
        bundle_path = Path(bundle_dir)
        if not bundle_path.exists():
            return False

        # Load scoring policy
        policy_file = bundle_path / "scoring_policy.json"
        if policy_file.exists():
            try:
                self._scoring_policy = ScoringPolicy.deserialize(policy_file.read_text(encoding="utf-8"))
                self._cache.scoring_policy = self._scoring_policy
            except Exception:
                pass

        # Load scorer model
        meta_file = bundle_path / "intent_scorer_metadata.json"
        model_loaded = False
        if meta_file.exists():
            meta = _json.loads(meta_file.read_text(encoding="utf-8"))
            model_path_str = meta.get("model_path", "")
            if model_path_str:
                model_path = Path(model_path_str)
                if not model_path.is_absolute():
                    model_path = bundle_path / model_path_str
                if model_path.exists():
                    loaded = self._intent_scorer.load_model(
                        model_path,
                        backend=str(meta.get("backend")) if isinstance(meta.get("backend"), str) else None,
                    )
                    if loaded:
                        self._trainer.scorer = self._intent_scorer
                        model_loaded = True
            else:
                # Try conventional filenames
                for candidate in ["intent_scorer.txt", "intent_scorer.json", "intent_scorer.cbm"]:
                    maybe = bundle_path / candidate
                    if maybe.exists():
                        if self._intent_scorer.load_model(maybe):
                            self._trainer.scorer = self._intent_scorer
                            model_loaded = True
                            break

            # Set model_influence from metadata (validation-based)
            influence = meta.get("model_influence")
            if isinstance(influence, (int, float)) and model_loaded:
                self._intent_scorer.set_model_influence(float(influence))

        self._mark_policy_state_dirty()
        await self._persist_learning_state(force=True)
        return model_loaded

    def _try_load_trained_scorer(self) -> None:
        if self._intent_scorer.model_path is not None:
            return
        if not self._scorer_model_path.exists():
            return
        scorer = IntentScorer(model_path=self._scorer_model_path)
        if scorer.model_path is not None:
            self._intent_scorer = scorer
            self._trainer.scorer = scorer

    async def _load_learning_state(self) -> None:
        if self._learning_state_loaded:
            return
        policy_row = await self.store.get_learning_state("scoring_policy")
        if policy_row:
            policy_json = policy_row.get("policy_json")
            if isinstance(policy_json, str):
                self._scoring_policy = ScoringPolicy.deserialize(policy_json)
                self._cache.scoring_policy = self._scoring_policy

        scorer_row = await self.store.get_learning_state("intent_scorer")
        if scorer_row:
            model_path = scorer_row.get("model_path")
            backend = scorer_row.get("backend")
            if isinstance(model_path, str) and model_path.strip():
                maybe_path = Path(model_path)
                if maybe_path.exists():
                    self._intent_scorer.load_model(maybe_path, backend=str(backend) if isinstance(backend, str) else None)
                    self._trainer.scorer = self._intent_scorer
            influence = scorer_row.get("model_influence")
            if isinstance(influence, (int, float)):
                self._intent_scorer.set_model_influence(float(influence))
        self._learning_state_loaded = True

    def _mark_policy_state_dirty(self) -> None:
        self._policy_state_dirty = True

    async def _persist_learning_state(self, *, force: bool = False) -> None:
        if not self._policy_state_dirty and not force:
            return
        now = time.time()
        if not force and (now - self._last_policy_persist_at) < self._policy_persist_interval_seconds:
            return
        await self.store.upsert_learning_state(
            key="scoring_policy",
            value={
                "policy_json": self._scoring_policy.serialize(),
                "compatibility_version": 1,
                "trained_at": now,
            },
        )
        scorer_state = self._intent_scorer.export_metadata()
        scorer_state["compatibility_version"] = 1
        scorer_state["trained_at"] = now
        await self.store.upsert_learning_state(key="intent_scorer", value=scorer_state)
        self._last_policy_persist_at = now
        self._policy_state_dirty = False

    async def _build_package_for_prompt(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        top_n: int = 8,
        source_key: str | None = None,
        preferred_keys: set[str] | None = None,
    ) -> tuple[ContextPackage, list, DecisionRecord]:
        artefacts = await self.store.list(limit=2000)
        if not artefacts:
            await self.prepare()
            artefacts = await self.store.list(limit=2000)

        repo_root = self.config.repo_root
        git_state = read_git_state(repo_root)
        preferred_paths = {
            line.strip() for line in (git_state.get("recent_diff", "") + "\n" + git_state.get("staged", "")).splitlines() if line.strip()
        }
        merged_preferred_keys = set(preferred_keys or set())
        working_set = await self.store.get_latest_working_set()
        if working_set is not None:
            merged_preferred_keys |= set(working_set.artefact_keys)

        features = await extract_hybrid_features(self.store, prompt=prompt, source_key=source_key)

        def _score_with_model(question: str, artefact) -> float:
            return self._intent_scorer.score(question, artefact, features=features)

        factor_map: dict[str, list[ScoreFactor]] = {}
        drop_reasons: dict[str, str] = {}
        selected = await select_artefacts_fts(
            prompt,
            self.store,
            top_n=top_n,
            preferred_paths=preferred_paths,
            preferred_keys=merged_preferred_keys,
            scorer=_score_with_model,
            exclude_private=self.config.privacy.exclude_private,
            path_bonuses=self._pinned_focus_paths,
            path_excludes=self._pinned_avoid_paths,
            capture_factors=factor_map,
            capture_drop_reasons=drop_reasons,
        )
        if source_key is None and selected:
            source_key = selected[0].key
            features = await extract_hybrid_features(self.store, prompt=prompt, source_key=source_key)

            def _score_with_source(question: str, artefact) -> float:
                return self._intent_scorer.score(question, artefact, features=features)

            selected = await select_artefacts_fts(
                prompt,
                self.store,
                top_n=top_n,
                preferred_paths=preferred_paths,
                preferred_keys=merged_preferred_keys,
                scorer=_score_with_source,
                exclude_private=self.config.privacy.exclude_private,
                path_bonuses=self._pinned_focus_paths,
                path_excludes=self._pinned_avoid_paths,
                capture_factors=factor_map,
                capture_drop_reasons=drop_reasons,
            )
        score_map = {artefact.key: self._intent_scorer.score(prompt, artefact, features=features) for artefact in selected}
        package, decision_record = assemble_context_package(
            prompt,
            selected,
            max_tokens if max_tokens is not None else self.config.max_context_tokens,
            repo_root=self.config.repo_root,
            max_age_seconds=self.config.max_age_seconds,
            score_map=score_map,
            factor_map=factor_map,
            drop_reasons=drop_reasons,
            return_decision=True,
        )
        return package, selected, decision_record

    def _build_decision_record_from_package(
        self,
        prompt: str,
        package: ContextPackage,
        *,
        cache_tier: str,
        partial_similarity: float,
        enrichment: dict[str, object] | None,
    ) -> DecisionRecord:
        decision_record = DecisionRecord(
            id=package.id,
            prompt=prompt,
            prompt_hash=package.prompt_hash,
            assembled_at=package.assembled_at,
            cache_tier=cache_tier,
            partial_similarity=partial_similarity,
            token_budget=package.token_budget,
            token_used=package.token_used,
            selections=[
                {
                    "artefact_key": selection.artefact_key,
                    "source_path": selection.source_path,
                    "final_score": selection.score,
                    "token_count": selection.token_count,
                    "stale": selection.stale,
                    "kept": True,
                    "drop_reason": None,
                    "rationale": selection.rationale,
                    "factors": [],
                }
                for selection in package.selections
            ],
        )
        self._attach_prediction_links(decision_record, enrichment)
        return decision_record

    def _attach_prediction_links(self, decision_record: DecisionRecord, enrichment: dict[str, object] | None) -> None:
        if not enrichment:
            return
        source = str(enrichment.get("exploration_source", "")).strip()
        if not source:
            return
        scenario_question_raw = enrichment.get("scenario_question") or enrichment.get("prompt_hint")
        scenario_question = str(scenario_question_raw) if scenario_question_raw else None
        rationale_raw = enrichment.get("rationale")
        scenario_rationale = str(rationale_raw) if rationale_raw else None
        confidence_value = enrichment.get("confidence")
        confidence = float(confidence_value) if isinstance(confidence_value, (int, float)) else None
        for selection in decision_record.selections:
            decision_record.prediction_links[selection.artefact_key] = PredictionLink(
                source=source,
                scenario_question=scenario_question,
                scenario_rationale=scenario_rationale,
                confidence=confidence,
            )

    def _resolve_llm(self, llm: LLMCallable | str | None) -> LLMCallable | None:
        if llm is None or callable(llm):
            return llm
        if llm.startswith("openai:"):
            from vaner.clients.openai import openai_llm

            model = llm.split(":", 1)[1] or self.config.backend.model
            api_key = os.environ.get(self.config.backend.api_key_env, "")
            if not api_key:
                return None
            return openai_llm(
                model=model,
                api_key=api_key,
                base_url=self.config.backend.base_url,
                timeout=float(self.config.backend.request_timeout_seconds),
            )
        if llm.startswith("ollama:"):
            from vaner.clients.ollama import ollama_llm

            model = llm.split(":", 1)[1]
            if not model:
                return None
            return ollama_llm(model=model, timeout=float(self.config.backend.request_timeout_seconds))
        if llm.startswith("vllm:"):
            # vllm:<model>  or  vllm:<model>@<host>:<port>
            from vaner.clients.openai import openai_llm

            rest = llm[len("vllm:") :]
            if "@" in rest:
                model, hostport = rest.rsplit("@", 1)
                base_url = f"http://{hostport}/v1"
            else:
                model = rest
                base_url = "http://127.0.0.1:8000/v1"
            api_key = os.environ.get("VANER_EXPLORATION_API_KEY", "EMPTY")
            return openai_llm(model=model, api_key=api_key, base_url=base_url, timeout=float(self.config.backend.request_timeout_seconds))
        return None

    async def _refresh_follow_up_pattern_memory(self) -> None:
        adapter_corpus_id = getattr(self.adapter, "corpus_id", "default")
        now = time.time()
        window_seconds = 6 * 3600.0
        accepted_threshold = 3

        events = await self.store.list_signal_events(corpus_id=adapter_corpus_id, limit=400)
        recent_events = [event for event in events if now - float(event.timestamp) <= window_seconds]

        detected: dict[tuple[str, str, str], int] = {}
        accepted: dict[tuple[str, str, str], int] = {}
        last_action: dict[tuple[str, str, str], str] = {}
        last_seen: dict[tuple[str, str, str], float] = {}
        for event in recent_events:
            if event.kind not in {"follow_up_offer_detected", "follow_up_offer_accepted"}:
                continue
            payload = event.payload
            pattern_id = str(payload.get("phrase_pattern_id", "")).strip()
            if not pattern_id:
                continue
            prompt_macro = str(payload.get("prompt_macro", "")).strip() or "general"
            phrase_family = str(payload.get("phrase_family", "")).strip() or "offer"
            key = (pattern_id, prompt_macro, phrase_family)
            if event.kind == "follow_up_offer_detected":
                detected[key] = detected.get(key, 0) + 1
            else:
                accepted[key] = accepted.get(key, 0) + 1
            action = str(payload.get("action", "")).strip()
            if action:
                last_action[key] = action
            last_seen[key] = max(last_seen.get(key, 0.0), float(event.timestamp))

        trusted_by_scope: dict[tuple[str, str], tuple[str, float, int, int]] = {}
        for key, detected_count in detected.items():
            pattern_id, prompt_macro, phrase_family = key
            accepted_count = accepted.get(key, 0)
            if accepted_count < accepted_threshold:
                continue
            acceptance_rate = accepted_count / max(1, detected_count)
            scope = (prompt_macro, phrase_family)
            existing = trusted_by_scope.get(scope)
            if existing is None or acceptance_rate > existing[1]:
                trusted_by_scope[scope] = (pattern_id, acceptance_rate, detected_count, accepted_count)

        workflow_facts = [
            row for row in await self.store.list_pinned_facts(scope="workflow") if str(row.get("key", "")).startswith("follow_up_pattern:")
        ]
        active_keys: set[str] = set()
        for (prompt_macro, phrase_family), (pattern_id, acceptance_rate, detected_count, accepted_count) in trusted_by_scope.items():
            key = f"follow_up_pattern:{pattern_id}:{prompt_macro}"
            active_keys.add(key)
            fact_value = last_action.get((pattern_id, prompt_macro, phrase_family), "")
            if not fact_value:
                continue
            await self.store.upsert_pinned_fact(
                scope="workflow",
                key=key,
                value=fact_value,
                scoring_hint={
                    "state": "trusted",
                    "phrase_pattern_id": pattern_id,
                    "phrase_family": phrase_family,
                    "prompt_macro": prompt_macro,
                    "acceptance_rate": acceptance_rate,
                    "detected_count": detected_count,
                    "accepted_count": accepted_count,
                    "last_seen": last_seen.get((pattern_id, prompt_macro, phrase_family), now),
                    "corpus_id": adapter_corpus_id,
                },
            )

        for row in workflow_facts:
            key = str(row.get("key", ""))
            if key in active_keys:
                continue
            scoring_hint = row.get("scoring_hint")
            if not isinstance(scoring_hint, dict):
                scoring_hint = {}
            if str(scoring_hint.get("state", "")) == "stale":
                continue
            stale_hint = dict(scoring_hint)
            stale_hint["state"] = "stale"
            stale_hint["stale_reason"] = "insufficient_signal_or_superseded"
            stale_hint["stale_at"] = now
            stale_hint.setdefault("corpus_id", adapter_corpus_id)
            await self.store.upsert_pinned_fact(
                scope="workflow",
                key=key,
                value=str(row.get("value", "")),
                scoring_hint=stale_hint,
            )

        self._pinned_facts_dirty = True
        await self._sync_pinned_facts()

    async def _run_reasoner(self) -> None:
        target_hypotheses = 5
        await self._refresh_follow_up_pattern_memory()
        artefacts = await self.store.list(limit=1000)
        valid_keys = {artefact.key for artefact in artefacts}
        await self.store.invalidate_stale_hypotheses(valid_keys)
        existing = await self.store.list_hypotheses(limit=target_hypotheses)
        if len(existing) >= target_hypotheses:
            return
        existing_questions = [str(item.get("question", "")) for item in existing if str(item.get("question", "")).strip()]
        missing = target_hypotheses - len(existing)
        if missing <= 0:
            return
        context = await self.adapter.get_context_for_reasoning()
        recent_queries = await self.store.list_query_history(limit=5)
        session_phase = self._arc_model.detect_session_phase([str(entry["query_text"]) for entry in reversed(recent_queries)])
        llm_output: str | None = None
        if callable(self.llm):
            prompt = (
                "Predict likely next user questions as a JSON list. "
                "Each item must contain question, confidence, evidence, relevant_keys, category, response_format, follow_ups.\n\n"
                f"Context:\n{context.summary}\nSession phase: {session_phase}\n"
                f"Already covered questions (avoid duplicates): {existing_questions[:8]}"
            )
            llm_output = await self.llm(prompt)
        hypotheses = await self._reasoner.generate(
            context=context,
            llm_output=llm_output,
            fallback_items=[item.key for item in (await self.adapter.list_items())[:10]],
            existing_questions=existing_questions,
            limit=missing,
            preferred_follow_ups=self._trusted_follow_up_patterns,
        )
        for hypothesis in hypotheses:
            await self.store.insert_hypothesis(
                question=hypothesis.question,
                confidence=hypothesis.confidence,
                evidence=hypothesis.evidence,
                relevant_keys=hypothesis.relevant_keys,
                category=hypothesis.category,
                response_format=hypothesis.response_format,
                follow_ups=hypothesis.follow_ups,
            )

    async def _behavioral_prediction_profile(
        self,
        *,
        current_category: str,
        recent_macro: str,
        predicted_categories: dict[str, float],
    ) -> dict[str, object]:
        exact_transitions = await self.store.list_habit_transitions(
            previous_category=current_category,
            previous_macro=recent_macro,
            limit=12,
        )
        if not exact_transitions:
            exact_transitions = await self.store.list_habit_transitions(
                previous_category=current_category,
                limit=12,
            )
        macros = await self.store.list_prompt_macros(limit=20)
        macro_map = {
            str(row.get("macro_key", "")): {
                "use_count": int(row.get("use_count", 0)),
                "confidence": float(row.get("confidence", 0.0)),
                "category": str(row.get("category", "understanding")),
            }
            for row in macros
        }
        transition_scores: dict[str, float] = {}
        transition_macro_counts: dict[str, int] = {}
        total = sum(int(row.get("transition_count", 0)) for row in exact_transitions)
        for row in exact_transitions:
            category = str(row.get("category", "understanding"))
            weight = int(row.get("transition_count", 0)) / max(1, total)
            transition_scores[category] = transition_scores.get(category, 0.0) + weight
            next_macro = str(row.get("prompt_macro", "")).strip()
            if next_macro:
                transition_macro_counts[next_macro] = max(
                    transition_macro_counts.get(next_macro, 0),
                    int(row.get("transition_count", 0)),
                )
        for category, score in predicted_categories.items():
            transition_scores[category] = transition_scores.get(category, 0.0) + (score * 0.35)
        return {
            "transition_scores": transition_scores,
            "transition_macro_counts": transition_macro_counts,
            "macro_map": macro_map,
        }

    def _behavioral_boost_for_artefact(
        self,
        source_path: str,
        *,
        predicted_category: str,
        recent_macro: str,
        behavior_profile: dict[str, object],
    ) -> tuple[float, list[str]]:
        stop_tokens = {"test", "api", "code", "file", "repo"}

        def _macro_tokens(macro_key: str) -> list[str]:
            return [token for token in str(macro_key).split() if len(token) > 2 and token.lower() not in stop_tokens]

        boost = 0.0
        reasons: list[str] = []
        path_lower = source_path.lower()

        transition_scores = behavior_profile.get("transition_scores", {})
        if isinstance(transition_scores, dict):
            transition_score = float(transition_scores.get(predicted_category, 0.0))
            if transition_score > 0:
                boost += min(0.15, transition_score * 0.25)
                reasons.append(f"transition:{predicted_category}")

        macro_map = behavior_profile.get("macro_map", {})
        if isinstance(macro_map, dict):
            best_macro_key = ""
            best_macro_boost = 0.0
            for macro_key, stats in macro_map.items():
                if not macro_key or macro_key == recent_macro or not isinstance(stats, dict):
                    continue
                tokens = _macro_tokens(str(macro_key))
                if len(tokens) < 2 or not all(token in path_lower for token in tokens[:2]):
                    continue
                use_count = int(stats.get("use_count", 0))
                confidence = float(stats.get("confidence", 0.0))
                macro_boost = min(0.12, (use_count / 10.0) * max(0.4, confidence) * 0.2)
                if macro_boost > best_macro_boost:
                    best_macro_boost = macro_boost
                    best_macro_key = str(macro_key)
            if best_macro_boost > 0:
                boost += best_macro_boost
                reasons.append(f"macro:{best_macro_key}")

        transition_macro_counts = behavior_profile.get("transition_macro_counts", {})
        if isinstance(transition_macro_counts, dict):
            best_next_macro_key = ""
            best_next_macro_boost = 0.0
            for macro_key, count in transition_macro_counts.items():
                tokens = _macro_tokens(str(macro_key))
                if len(tokens) < 2 or not all(token in path_lower for token in tokens[:2]):
                    continue
                macro_boost = min(0.1, int(count) / 20.0)
                if macro_boost > best_next_macro_boost:
                    best_next_macro_boost = macro_boost
                    best_next_macro_key = str(macro_key)
            if best_next_macro_boost > 0:
                boost += best_next_macro_boost
                reasons.append(f"next_macro:{best_next_macro_key}")

        uncapped_boost = boost
        boost = min(0.3, boost)
        if boost < uncapped_boost:
            reasons.append("behavior_cap:0.3")

        return boost, reasons

    async def _persist_behavioral_observation(
        self,
        observation: ArcObservation,
        *,
        session_id: str | None = None,
    ) -> None:
        transition = self._arc_model.last_transition
        if transition is not None:
            await self.store.record_habit_transition(
                previous_category=transition.previous_category,
                category=transition.category,
                previous_macro=transition.previous_macro,
                prompt_macro=transition.prompt_macro,
            )
        await self.store.bump_prompt_macro(
            macro_key=observation.prompt_macro,
            example_query=observation.query_text,
            category=observation.category,
        )
        await self.store.upsert_workflow_phase_summary(
            session_id=session_id or self._session_id(),
            phase=observation.workflow_phase,
            dominant_category=observation.dominant_category,
            recent_categories=list(observation.recent_categories),
            recent_macro=observation.prompt_macro,
        )

    async def _refresh_behavioral_memory_from_model(self) -> None:
        await self.store.replace_habit_transitions(self._arc_model.export_habit_transitions())
        await self.store.replace_prompt_macros(self._arc_model.mine_prompt_macros())
        summary = self._arc_model.summarize_workflow_phase([])
        await self.store.upsert_workflow_phase_summary(
            session_id=self._session_id(),
            phase=summary.phase,
            dominant_category=summary.dominant_category,
            recent_categories=list(summary.recent_categories),
            recent_macro=summary.recent_macro,
        )

    async def _update_validated_patterns(self, *, prompt: str, selected_keys: list[str], category: str) -> None:
        if not selected_keys:
            return
        hypotheses = await self.store.list_hypotheses(limit=20)
        prompt_tokens = self._tokenize(prompt)
        matching_hypothesis: dict[str, object] | None = None
        for hypothesis in hypotheses:
            question = str(hypothesis.get("question", ""))
            if self._token_overlap(prompt_tokens, self._tokenize(question)) < 0.7:
                continue
            relevant_keys = {str(item) for item in hypothesis.get("relevant_keys", [])}
            overlap = relevant_keys & set(selected_keys) if relevant_keys else set(selected_keys)
            if overlap:
                matching_hypothesis = hypothesis
                break
        if matching_hypothesis is None:
            return

        keywords = " ".join(sorted(self._tokenize(prompt)))
        predicted_keys = [str(item) for item in matching_hypothesis.get("relevant_keys", [])] or selected_keys
        patterns = await self.store.list_validated_patterns(trigger_category=category, limit=100)
        for pattern in patterns:
            same_keywords = str(pattern.get("trigger_keywords", "")) == keywords
            existing_keys = sorted(str(item) for item in pattern.get("predicted_keys", []))
            if same_keywords and existing_keys == sorted(predicted_keys):
                await self.store.increment_pattern_confirmation(str(pattern["id"]))
                return
        await self.store.insert_validated_pattern(
            trigger_category=category,
            trigger_keywords=keywords,
            predicted_keys=predicted_keys,
        )

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token for token in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if token}

    @staticmethod
    def _token_overlap(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        union = left | right
        if not union:
            return 0.0
        return len(left & right) / len(union)

    def _fts_query(self, text: str) -> str | None:
        tokens = [token for token in self._tokenize(text) if len(token) > 2]
        if not tokens:
            return None
        return " OR ".join(tokens[:6])

    def _augment_feature_snapshot(
        self,
        snapshot: dict[str, float],
        *,
        selected_paths: list[str],
        exploration_source: str,
        cache_tier: str = "",
        freshness_hint: float = 0.5,
    ) -> dict[str, float]:
        augmented = dict(snapshot)
        focus_match = 0.0
        avoid_match = 0.0
        for path in selected_paths:
            if any(fnmatch(path, pattern) for pattern in self._pinned_focus_paths):
                focus_match = 1.0
            if any(fnmatch(path, pattern) for pattern in self._pinned_avoid_paths):
                avoid_match = 1.0
        source = exploration_source.strip().lower()
        normalized_tier = cache_tier.strip().lower()
        augmented["pin_focus_match"] = focus_match
        augmented["pin_avoid_match"] = avoid_match
        augmented["frontier_source_graph"] = 1.0 if source == "graph" else 0.0
        augmented["frontier_source_arc"] = 1.0 if source == "arc" else 0.0
        augmented["frontier_source_pattern"] = 1.0 if source == "pattern" else 0.0
        augmented["frontier_source_llm_branch"] = 1.0 if source == "llm_branch" else 0.0
        augmented["policy_signal_graph"] = 1.0 if source == "graph" else 0.0
        augmented["policy_signal_arc"] = 1.0 if source == "arc" else 0.0
        augmented["policy_signal_coverage_gap"] = 1.0 if normalized_tier in {"cold_miss", "warm_start"} else 0.0
        augmented["policy_signal_pattern"] = 1.0 if source in {"pattern", "llm_branch"} else 0.0
        augmented["policy_signal_freshness"] = max(0.0, min(1.0, freshness_hint))
        return augmented

    def _adapt_policy_from_feedback(
        self,
        *,
        reward_total: float,
        feature_snapshot: dict[str, float],
        source: str,
    ) -> None:
        hit = reward_total >= 0.0
        if source:
            self._scoring_policy.record_source_feedback(source, hit=hit)
        active_signals = [
            feature_snapshot.get("policy_signal_graph", 0.0) > 0.5,
            feature_snapshot.get("policy_signal_arc", 0.0) > 0.5,
            feature_snapshot.get("policy_signal_coverage_gap", 0.0) > 0.5,
            feature_snapshot.get("policy_signal_pattern", 0.0) > 0.5,
            feature_snapshot.get("policy_signal_freshness", 0.0) > 0.5,
        ]
        query_count = feature_snapshot.get("query_count_total", 0.0)
        phase = self._maturity.phase_for_query_count(int(query_count)).value
        self._scoring_policy.adapt_weights(active_signals=active_signals, hit=hit, phase=phase)
        self._scoring_policy.adapt_depth_decay(
            deep_hit=hit and feature_snapshot.get("frontier_source_llm_branch", 0.0) > 0.5,
            phase=phase,
        )
        self._scoring_policy.adapt_freshness(
            stale_hit=hit and feature_snapshot.get("policy_signal_freshness", 0.0) < 0.35,
            phase=phase,
        )


def _probe_openai_endpoint(base_url: str, timeout: float = 2.0) -> tuple[bool, list[str]]:
    """Check if an OpenAI-compatible endpoint is reachable and return available model IDs."""
    import json as _json
    import urllib.error
    import urllib.request

    url = base_url.rstrip("/") + "/models"
    try:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer EMPTY"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read())
            models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
            return True, models
    except Exception:
        return False, []


def _probe_ollama_endpoint(base_url: str, timeout: float = 2.0) -> tuple[bool, list[str]]:
    """Check if an Ollama endpoint is reachable and return available model tags."""
    import json as _json
    import urllib.error
    import urllib.request

    url = base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = _json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
            return True, models
    except Exception:
        return False, []


def _build_exploration_llm(ecfg: ExplorationConfig) -> LLMCallable | None:
    """Resolve the exploration LLM from ExplorationConfig, probing endpoints as needed."""
    import logging as _logging

    _log = _logging.getLogger(__name__)

    # Multi-endpoint pool path (PR #135 P5). When the config provides a list
    # of endpoints, build an ExplorationEndpointPool that round-robins across
    # them with per-endpoint health tracking. The pool implements the same
    # LLMCallable protocol the engine already consumes — the rest of the
    # pipeline is unchanged.
    if ecfg.endpoints:
        try:
            from vaner.clients.endpoint_pool import ExplorationEndpointPool

            pool = ExplorationEndpointPool.from_endpoints(list(ecfg.endpoints))
            _log.info(
                "Vaner exploration LLM: multi-endpoint pool size=%d entries=%s",
                len(ecfg.endpoints),
                [f"{e.url} ({e.model}, w={e.weight})" for e in ecfg.endpoints],
            )
            return pool
        except ValueError as exc:
            _log.warning(
                "Vaner: failed to build exploration endpoint pool (%s); falling back to single-endpoint config.",
                exc,
            )

    backend = ecfg.exploration_backend  # "auto", "ollama", "openai"
    endpoint = ecfg.exploration_endpoint.strip()
    model = ecfg.exploration_model.strip()
    api_key = ecfg.exploration_api_key.strip() or os.environ.get("VANER_EXPLORATION_API_KEY", "")

    def _make_openai(base_url: str, m: str) -> LLMCallable:
        from vaner.clients.openai import openai_llm

        key = api_key or "EMPTY"
        _log.info("Vaner exploration LLM: OpenAI-compatible at %s model=%s", base_url, m)
        return openai_llm(model=m, api_key=key, base_url=base_url)

    def _make_ollama(base_url: str, m: str) -> LLMCallable:
        from vaner.clients.ollama import ollama_llm

        _log.info("Vaner exploration LLM: Ollama at %s model=%s", base_url, m)
        return ollama_llm(model=m, base_url=base_url)

    # ------------------------------------------------------------------
    # Explicit endpoint given: probe or trust based on backend hint
    # ------------------------------------------------------------------
    if endpoint:
        if backend == "openai" or (backend == "auto" and "/v1" in endpoint):
            ok, models = _probe_openai_endpoint(endpoint)
            if ok:
                m = model or (models[0] if models else "")
                if m:
                    return _make_openai(endpoint, m)
            # Fallthrough if probe fails
        if backend == "ollama" or backend == "auto":
            ok, models = _probe_ollama_endpoint(endpoint)
            if ok:
                m = model or (models[0] if models else "")
                if m:
                    return _make_ollama(endpoint, m)
        if backend == "auto" and "/v1" not in endpoint:
            ok, models = _probe_openai_endpoint(endpoint)
            if ok:
                m = model or (models[0] if models else "")
                if m:
                    return _make_openai(endpoint, m)
        _log.warning(
            "Vaner: exploration_endpoint=%r is unreachable or has no models; falling back to heuristic-only exploration.",
            endpoint,
        )
        return None

    # ------------------------------------------------------------------
    # Auto-detect on localhost: try vLLM port 8000, then Ollama port 11434
    # ------------------------------------------------------------------
    vllm_url = "http://127.0.0.1:8000/v1"
    ollama_url = "http://127.0.0.1:11434"

    ok, models = _probe_openai_endpoint(vllm_url)
    if ok:
        m = model or (models[0] if models else "")
        if m:
            return _make_openai(vllm_url, m)

    ok, models = _probe_ollama_endpoint(ollama_url)
    if ok:
        m = model or (models[0] if models else "")
        if m:
            return _make_ollama(ollama_url, m)

    _log.debug(
        "Vaner: no exploration LLM detected on localhost (tried vLLM :8000 and Ollama :11434). "
        "Set exploration_endpoint in config to enable LLM-powered exploration."
    )
    return None


def _build_embed_callable(ecfg: ExplorationConfig) -> EmbedCallable | None:
    """Build an embedding callable from ExplorationConfig. Returns None if disabled."""
    import logging as _logging

    _log = _logging.getLogger(__name__)

    if not ecfg.embedding_model:
        return None
    try:
        from vaner.clients.embeddings import sentence_transformer_embed

        _log.info(
            "Vaner embeddings: sentence-transformers model=%s device=%s",
            ecfg.embedding_model,
            ecfg.embedding_device,
        )
        return sentence_transformer_embed(
            model=ecfg.embedding_model,
            device=ecfg.embedding_device,
        )
    except Exception as exc:
        _log.warning(
            "Vaner: could not load embedding model %r (%s). Semantic cache matching disabled; install sentence-transformers to enable.",
            ecfg.embedding_model,
            exc,
        )
        return None


def _apply_compute_settings(compute: ComputeConfig) -> None:
    cpu_fraction = max(0.01, min(compute.cpu_fraction, 1.0))
    cpu_count = os.cpu_count() or 1
    capped_threads = max(1, int(round(cpu_count * cpu_fraction)))
    os.environ["OMP_NUM_THREADS"] = str(capped_threads)
    os.environ["MKL_NUM_THREADS"] = str(capped_threads)
    os.environ["TOKENIZERS_PARALLELISM"] = "false"

    if not compute.device.startswith("cuda"):
        return

    try:  # pragma: no cover - torch/cuda may be unavailable in test env
        import torch

        if not torch.cuda.is_available():
            return
        fraction = max(0.05, min(compute.gpu_memory_fraction, 1.0))
        if ":" in compute.device:
            device_index = int(compute.device.split(":", 1)[1])
        else:
            device_index = torch.cuda.current_device()
        torch.cuda.set_per_process_memory_fraction(fraction, device=device_index)
    except Exception:
        return


def _embedding_device_from_compute(config: VanerConfig) -> str:
    compute_device = (config.compute.embedding_device or config.compute.device).strip().lower()
    if not compute_device or compute_device == "auto":
        return config.exploration.embedding_device
    if compute_device.startswith("cuda"):
        return "cuda"
    if compute_device in {"cpu", "mps"}:
        return compute_device
    return config.exploration.embedding_device


def _cpu_load_fraction() -> float:
    try:
        cpu_count = os.cpu_count() or 1
        one_min, _, _ = os.getloadavg()
        return max(0.0, one_min / cpu_count)
    except Exception:
        return 0.0


def _gpu_load_fraction() -> float:
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.0,
        ).strip()
        if not output:
            return 0.0
        values = [float(item.strip()) / 100.0 for item in output.splitlines() if item.strip()]
        return max(values) if values else 0.0
    except Exception:
        return 0.0


def _record_idle_usage_seconds(config: VanerConfig, seconds: float) -> None:
    runtime_dir = config.repo_root / ".vaner" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_dir / "idle_usage.json"
    payload = {"idle_seconds_used": 0.0}
    if path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                payload["idle_seconds_used"] = float(parsed.get("idle_seconds_used", 0.0))
        except Exception:
            pass
    payload["idle_seconds_used"] = round(payload["idle_seconds_used"] + max(0.0, seconds), 3)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_default_engine(repo: Path | str | None = None, config: VanerConfig | None = None) -> VanerEngine:
    """Build a VanerEngine with auto-detected exploration LLM and embeddings.

    Auto-detection order (when ``exploration_endpoint`` is empty):
    1. vLLM / OpenAI-compatible on ``http://127.0.0.1:8000/v1``
    2. Ollama on ``http://127.0.0.1:11434``
    3. Heuristic-only (no LLM) — graceful fallback

    Set ``config.exploration.exploration_endpoint`` to point at a remote
    OpenAI-compatible endpoint (vLLM, LM Studio, remote Ollama, etc.).
    """
    root = Path(repo).resolve() if repo is not None else Path.cwd().resolve()
    adapter = CodeRepoAdapter(root)
    resolved = config if config is not None else load_config(root)
    _apply_compute_settings(resolved.compute)

    resolved.exploration.embedding_device = _embedding_device_from_compute(resolved)

    exploration_llm = _build_exploration_llm(resolved.exploration)
    embed = _build_embed_callable(resolved.exploration)

    return VanerEngine(adapter=adapter, config=resolved, llm=exploration_llm, embed=embed)
