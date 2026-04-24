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
from typing import Any

from vaner.broker.assembler import assemble_context_package
from vaner.broker.selector import select_artefacts, select_artefacts_fts
from vaner.cli.commands.config import load_config
from vaner.clients.llm_response import LLMResponse
from vaner.daemon.runner import VanerDaemon
from vaner.daemon.signals.git_reader import read_content_hashes, read_git_state, read_head_sha
from vaner.defaults.loader import load_defaults_bundle
from vaner.intent.abstain import AbstentionPolicy
from vaner.intent.adapter import CodeRepoAdapter, ContextSource, CorpusAdapter, RelationshipEdge, SignalSource
from vaner.intent.allocator import PortfolioAllocator
from vaner.intent.arcs import ArcObservation, ConversationArcModel, classify_query_category, derive_prompt_macro
from vaner.intent.briefing import BriefingAssembler
from vaner.intent.cache import TieredPredictionCache
from vaner.intent.deep_run import (
    DeepRunFocus,
    DeepRunHorizonBias,
    DeepRunLocality,
    DeepRunPauseReason,
    DeepRunPreset,
    DeepRunSession,
    DeepRunSummary,
)
from vaner.intent.deep_run_gates import (
    NoOpResourceGateProbe,
    ResourceGateConfig,
    ResourceGateProbe,
    cost_gate_spent_usd,
    evaluate_resource_gates,
    reset_cost_gate,
    set_active_session_for_routing,
)
from vaner.intent.deep_run_maturation import (
    RefinementContext,
    mature_one,
    select_maturation_candidates,
)
from vaner.intent.drafter import Drafter
from vaner.intent.ev import jaccard_reuse
from vaner.intent.features import extract_hybrid_features
from vaner.intent.frontier import ExplorationFrontier, ExplorationScenario, file_set_fingerprint
from vaner.intent.governor import PredictionGovernor
from vaner.intent.graph import RelationshipGraph
from vaner.intent.invalidation import (
    build_category_shift_signal,
    build_commit_signal,
    build_file_change_signal,
)
from vaner.intent.maturity import MaturityTracker
from vaner.intent.prediction import (
    HypothesisType,
    PredictedPrompt,
    PredictionSpec,
    Specificity,
    prediction_id,
)
from vaner.intent.prediction_registry import PredictionRegistry
from vaner.intent.profile import UserProfile
from vaner.intent.reasoner import CorpusReasoner, PredictionScenario
from vaner.intent.scorer import IntentScorer
from vaner.intent.scoring_policy import ScoringPolicy
from vaner.intent.taxonomy import EmbeddingTaxonomyClassifier, classify_taxonomy
from vaner.intent.timing import ActivityTimingModel
from vaner.intent.trainer import IntentTrainer
from vaner.intent.transfer import bootstrap_transfer_priors
from vaner.intent.volatility import semantic_volatility_profile
from vaner.learning.counterfactual import CounterfactualAnalyzer
from vaner.learning.reward import RewardInput, compute_reward
from vaner.models.artefact import Artefact, ArtefactKind
from vaner.models.config import ComputeConfig, ExplorationConfig, VanerConfig
from vaner.models.context import ContextPackage
from vaner.models.decision import DecisionRecord, PredictionLink, ScoreFactor
from vaner.models.signal import SignalEvent
from vaner.store import deep_run as deep_run_store
from vaner.store.artefacts import ArtefactStore
from vaner.store.profile_store import UserProfileStore
from vaner.store.scenarios import ScenarioStore
from vaner.telemetry.metrics import MetricsStore

LLMCallable = Callable[[str], Awaitable[str]]
EmbedCallable = Callable[[list[str]], Awaitable[list[list[float]]]]
# Phase 4 / WS2: richer LLM callable that returns a structured
# ``LLMResponse`` (thinking + content + raw). The engine prefers this when
# available so reasoning-model preambles are captured rather than discarded.
StructuredLLMCallable = Callable[[str], Awaitable["LLMResponse"]]

# 0.8.2 WS2 — confidence multiplier per artefact-item state for the
# ``source="artefact_item"`` prediction-spec emitter. Pending items are
# the user's "declared next step" and get highest weight; in_progress
# gets a slight discount to avoid monopolising attention once the work
# has been observed starting; stalled items are demoted but retained as
# possible-branch coverage.
_ITEM_STATE_WEIGHTS: dict[str, float] = {
    "pending": 0.9,
    "in_progress": 0.8,
    "stalled": 0.4,
}


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
        structured_llm: StructuredLLMCallable | None = None,
    ) -> None:
        self.adapter = adapter
        self._extra_signal_sources: list[SignalSource] = list(signals or [])
        self._extra_context_sources: list[ContextSource] = list(sources or [])
        repo_root = getattr(adapter, "repo_root", Path.cwd())
        self.config = config if config is not None else load_config(Path(repo_root))
        self.store = ArtefactStore(self.config.store_path)
        self._metrics_store = MetricsStore(self.config.repo_root / ".vaner" / "metrics.db")
        self.llm = self._resolve_llm(llm)
        # Phase 4 / WS2: optional structured LLM. When provided, the engine
        # prefers it in _explore_scenario_with_llm so reasoning-model
        # thinking traces are captured rather than silently dropped. Legacy
        # ``llm`` callers continue to work unchanged.
        self.structured_llm: StructuredLLMCallable | None = structured_llm
        self.embed = embed
        self._background_task: asyncio.Task[None] | None = None
        self._running = False
        self._reasoner = CorpusReasoner()
        self._defaults_bundle = load_defaults_bundle()
        self._arc_model = ConversationArcModel(
            transition_priors=self._defaults_bundle.behavior.arc_transitions.transitions
            if self._defaults_bundle.behavior.arc_transitions
            else None,
            phase_affinity_priors=self._defaults_bundle.behavior.phase_classifier.phase_affinity
            if self._defaults_bundle.behavior.phase_classifier
            else None,
        )
        self._intent_scorer = IntentScorer()
        self._maturity = MaturityTracker()
        self._trainer = IntentTrainer(self.store, self._intent_scorer)
        policy_defaults = (
            self._defaults_bundle.policy_defaults.model_dump(mode="python") if self._defaults_bundle.policy_defaults is not None else None
        )
        self._scoring_policy = ScoringPolicy.from_policy_defaults(policy_defaults)
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
        # Phase 4 / WS6: prediction registry persists across cycles. First
        # created on the first ``precompute_cycle``; thereafter reused and
        # updated in place via ``merge()`` + ``apply_invalidation_signals()``.
        self._prediction_registry: PredictionRegistry | None = None
        # 0.8.4 WS3: optional drafter injected for background refinement.
        # Default is None — the refinement hook is a no-op. 0.8.5 wires
        # a production drafter; tests inject stubs. Keeping this as an
        # engine-level attribute rather than a constructor param avoids
        # churn on every VanerEngine() call site.
        from vaner.intent.deep_run_maturation import (
            MaturationDrafterCallable as _MaturationDrafterCallable,
        )

        self._refinement_drafter: _MaturationDrafterCallable | None = None
        # WS6: snapshot the most recent git HEAD and category tail so each
        # cycle can diff against them to build invalidation signals. Empty
        # string / list means "no prior observation", which yields no signals.
        self._last_observed_head_sha: str = ""
        self._last_observed_categories: list[str] = []
        # 0.8.3 WS1: cached active Deep-Run session. Loaded from the store
        # by ``initialize()`` (resume-on-restart), updated by
        # ``start_deep_run`` / ``stop_deep_run``. ``None`` means no
        # session is in flight.
        self._active_deep_run_session: DeepRunSession | None = None
        self._deep_run_loaded: bool = False
        # 0.8.3 WS2: resource gate probe + threshold config. The default
        # NoOpResourceGateProbe reports "no constraint" everywhere so
        # tests do not need to mock platform info; production wiring
        # plugs in a psutil-backed probe via ``set_resource_gate_probe``.
        self._resource_gate_probe: ResourceGateProbe = NoOpResourceGateProbe()
        self._resource_gate_config = ResourceGateConfig()
        # WS9: single canonical briefing assembler. Engine holds it so the
        # approximation-warning latch is shared across call sites (draft path
        # + evidence-threshold synthesis). A real tokenizer can be injected
        # later via ``set_briefing_tokenizer`` once the structured LLM client
        # exposes one.
        self._briefing_assembler = BriefingAssembler()
        # WS10: single drafting module. Owns the rewrite + draft LLM
        # templates and the gate arithmetic so arc / pattern / history /
        # future goal-sourced (WS7) predictions all drive through the same
        # path. Engine holds the draft/briefing cache + registry bookkeeping
        # around it.
        self._drafter = Drafter(llm=self.llm, assembler=self._briefing_assembler)
        # WS7: per-cycle cache of active workspace goals. Refreshed at the
        # top of precompute_cycle via ``_refresh_inferred_goals``;
        # consumed by ``_merge_prediction_specs`` to seed goal-anchored
        # predictions.
        self._active_goals_cache: list[dict[str, object]] = []
        # 0.8.2 WS2: per-cycle cache of active intent-artefact items,
        # grouped by artefact id. Refreshed at the top of
        # ``precompute_cycle``; consumed by ``_emit_artefact_item_specs``
        # to emit ``source="artefact_item"`` prediction specs for every
        # pending/in_progress/stalled item under an active artefact-
        # backed goal.
        self._active_artefact_items_cache: dict[str, list[dict[str, object]]] = {}
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
        # Inter-prompt timing model — seeded from query_history on first
        # initialize() and kept up-to-date as new prompts arrive. Used by
        # ``precompute_cycle`` to size the adaptive cycle deadline.
        self._timing_model = ActivityTimingModel()
        self._timing_model_loaded = False
        legacy_profile_json_path = (
            Path.home() / ".vaner" / "user_profile.json"
            if self.config.intent.cross_workspace_profile
            else self.config.repo_root / ".vaner" / "user_profile.json"
        )
        profile_db_path = (
            Path.home() / ".vaner" / "user_profile.db"
            if self.config.intent.cross_workspace_profile
            else self.config.repo_root / ".vaner" / "user_profile.db"
        )
        self._user_profile_json_path = legacy_profile_json_path
        self._user_profile_store = UserProfileStore(profile_db_path)
        # Start with an empty profile; ``initialize()`` hydrates from SQLite
        # (and migrates the legacy JSON on first run).
        self._user_profile = UserProfile()
        self._user_profile_loaded = False
        self._taxonomy_classifier = EmbeddingTaxonomyClassifier()
        self._counterfactual_analyzer = CounterfactualAnalyzer(self.config.repo_root / ".vaner" / "decisions")
        default_gates = self._defaults_bundle.draft_gates
        self._cycle_policy_state: dict[str, float] = {
            "breadth_coverage_threshold": 0.40,
            "deep_drill_priority_threshold": float(self.config.exploration.deep_drill_priority_threshold),
            "entropy_abstain_threshold": 0.95,
            "draft_posterior_threshold": float(default_gates.get("draft_posterior_threshold", 0.55)),
            "draft_evidence_threshold": float(default_gates.get("draft_evidence_threshold", 0.45)),
            "draft_volatility_ceiling": float(default_gates.get("draft_volatility_ceiling", 0.40)),
            "draft_budget_min_ms": float(default_gates.get("draft_budget_min_ms", 2000.0)),
            "exploit_ratio": 0.50,
            "hedge_ratio": 0.20,
            "invest_ratio": 0.10,
            "no_regret_ratio": 0.20,
        }

    async def initialize(self) -> None:
        await self.store.initialize()
        await self._metrics_store.initialize()
        await self._load_learning_state()
        self._try_load_trained_scorer()
        await self._load_user_profile()
        if not self._arc_loaded:
            history = await self.store.list_query_history(limit=5000)
            ordered_queries = [str(entry["query_text"]) for entry in reversed(history)]
            self._arc_model.rebuild_from_history(ordered_queries)
            ordered_timestamps: list[float] = []
            for entry in reversed(history):
                ts = entry.get("timestamp")
                try:
                    ordered_timestamps.append(float(ts))
                except (TypeError, ValueError):
                    continue
            self._timing_model.rebuild_from_history(ordered_timestamps)
            self._timing_model_loaded = True
            await self._refresh_behavioral_memory_from_model()
            # Bootstrap seeds run AFTER the arc-model refresh so they only
            # fill the table when the user has no real history yet. The
            # refresh calls replace_* which clears the table first; seeding
            # before would mean the seeds are immediately overwritten.
            if self._defaults_bundle.behavior.habit_transitions_seed:
                await self.store.bootstrap_habit_transitions(self._defaults_bundle.behavior.habit_transitions_seed)
            if self._defaults_bundle.behavior.prompt_macros_seed:
                await self.store.bootstrap_prompt_macros(self._defaults_bundle.behavior.prompt_macros_seed)
            if self.embed is not None and self._defaults_bundle.behavior.category_centroids:
                _cc = self._defaults_bundle.behavior.category_centroids
                _dc = _cc.get("domain_centroids") or {}
                _mc = _cc.get("mode_centroids") or {}
                _first = next(iter(_dc.values()), None)
                if _first and isinstance(_first, list) and _first and isinstance(_first[0], float):
                    self._taxonomy_classifier = EmbeddingTaxonomyClassifier(
                        domain_centroids={k: [float(x) for x in v] for k, v in _dc.items() if isinstance(v, list)},
                        mode_centroids={k: [float(x) for x in v] for k, v in _mc.items() if isinstance(v, list)},
                    )
            self._arc_loaded = True
        await self._sync_extra_context_sources()
        await self._sync_pinned_facts()
        await self._resume_deep_run_on_restart()

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
        # Fold the freshly-arrived prompt into the timing model so
        # subsequent precompute cycles size their budgets against the
        # up-to-the-second cadence rather than a cached EMA.
        self._timing_model.record_prompt(started_at)
        try:
            history_before = await self.store.list_query_history(limit=32)
            prior_recent_queries = [str(entry["query_text"]) for entry in reversed(history_before)]
            prior_prediction_probs: dict[str, float] = {}
            if prior_recent_queries:
                prior_current_category = classify_query_category(prior_recent_queries[-1])
                prior_predictions = self._arc_model.rank_next(prior_current_category, top_k=3, recent_queries=prior_recent_queries)
                if prior_predictions:
                    total_conf = sum(max(0.0, float(item.confidence)) for item in prior_predictions)
                    if total_conf > 0:
                        prior_prediction_probs = {
                            item.category: max(0.0, float(item.confidence)) / total_conf for item in prior_predictions
                        }
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
            # Let precompute's speculative predictions participate in path-overlap
            # scoring even when the heuristic selector disagrees. Without this
            # union, the bench finds 96% of precompute entries never get consumed
            # because their anchor_units don't overlap with the heuristic's picks.
            _quick_paths = _quick_paths | await self._cache.candidate_anchor_units(prompt, top_k=3)
            cache_result = await self._cache.match(prompt, relevant_paths=_quick_paths)
            observation = self._arc_model.observe_detail(prompt)
            category = observation.category
            if prior_prediction_probs:
                await self._metrics_store.record_next_prompt_prediction(
                    probabilities=prior_prediction_probs,
                    actual_label=category,
                )
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
                        "cache_key": cache_result.cache_key,
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
                generated_at = float(cache_result.enrichment.get("generated_at", 0.0)) if cache_result.enrichment else 0.0
                if generated_at > 0:
                    await self._metrics_store.record_predictive_lead_seconds(max(0.0, time.time() - generated_at))
                if cache_result.enrichment and "predicted_response" in cache_result.enrichment:
                    predicted_prompt = str(
                        cache_result.enrichment.get("predicted_prompt") or cache_result.enrichment.get("prompt_hint") or ""
                    )
                    prompt_tokens = {token for token in prompt.lower().split() if token}
                    predicted_tokens = {token for token in predicted_prompt.lower().split() if token}
                    overlap = len(prompt_tokens & predicted_tokens) / max(1, len(prompt_tokens | predicted_tokens))
                    evidence_paths = set(
                        str(path) for path in cache_result.enrichment.get("source_units", []) if isinstance(path, str)
                    ) or set(str(path) for path in cache_result.enrichment.get("anchor_units", []) if isinstance(path, str))
                    served_path_set = set(_quick_paths)
                    evidence_overlap = len(served_path_set & evidence_paths) / max(1, len(served_path_set | evidence_paths))
                    reuse_ratio, directional = await self._grade_draft_at_serve(
                        prompt=prompt,
                        predicted_prompt=predicted_prompt,
                        draft_referenced_paths=evidence_paths,
                        served_paths=served_path_set,
                    )
                    await self._metrics_store.record_draft_event(
                        status="served",
                        predicted_prompt_similarity=overlap,
                        evidence_overlap=evidence_overlap,
                        answer_reuse_ratio=reuse_ratio,
                        directional_correct=directional,
                        metadata={"tier": cache_result.tier, "source": "cache_full_hit"},
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
                    await self._metrics_store.record_counterfactual_miss(
                        prompt=prompt,
                        miss_type="retrieval",
                        helpful_context=sorted(_quick_paths)[:20],
                        wasted_branches=[],
                        metadata={"stage": "query", "reason": "cold_miss"},
                    )
                    self._counterfactual_analyzer.analyze(
                        prompt=prompt,
                        miss_type="cold_miss",
                        helpful_paths=sorted(_quick_paths)[:20],
                        wasted_paths=[],
                        abstain_was_active=bool(self._cycle_policy_state.get("abstain_mode", 0.0)),
                        metadata={"stage": "query"},
                    )
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
            if self.config.intent.embedding_classifier_enabled:
                taxonomy = await self._taxonomy_classifier.classify(prompt, self.embed if callable(self.embed) else None)
            else:
                taxonomy = classify_taxonomy(prompt)
            self._user_profile.observe(mode=taxonomy.mode, depth=max(0, len(selected) // 2), ts=time.time())
            await self._user_profile_store.save(self._user_profile)
            await self.store.insert_signal_event(
                SignalEvent(
                    id=str(uuid.uuid4()),
                    source="query",
                    kind="query_issued",
                    timestamp=time.time(),
                    payload={
                        "category": category,
                        "domain": taxonomy.domain,
                        "mode": taxonomy.mode,
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
                    "anchor_units": [a.source_path for a in selected if a.source_path],
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
                    "cache_key": cache_result.cache_key,
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
            generated_at = float(cache_result.enrichment.get("generated_at", 0.0)) if cache_result.enrichment else 0.0
            if generated_at > 0:
                await self._metrics_store.record_predictive_lead_seconds(max(0.0, time.time() - generated_at))
            if cache_result.enrichment and "predicted_response" in cache_result.enrichment:
                predicted_prompt = str(cache_result.enrichment.get("predicted_prompt") or cache_result.enrichment.get("prompt_hint") or "")
                prompt_tokens = {token for token in prompt.lower().split() if token}
                predicted_tokens = {token for token in predicted_prompt.lower().split() if token}
                overlap = len(prompt_tokens & predicted_tokens) / max(1, len(prompt_tokens | predicted_tokens))
                evidence_paths = set(str(path) for path in cache_result.enrichment.get("source_units", []) if isinstance(path, str)) or set(
                    str(path) for path in cache_result.enrichment.get("anchor_units", []) if isinstance(path, str)
                )
                served_path_set = set(_quick_paths)
                evidence_overlap = len(served_path_set & evidence_paths) / max(1, len(served_path_set | evidence_paths))
                reuse_ratio, directional = await self._grade_draft_at_serve(
                    prompt=prompt,
                    predicted_prompt=predicted_prompt,
                    draft_referenced_paths=evidence_paths,
                    served_paths=served_path_set,
                )
                await self._metrics_store.record_draft_event(
                    status="served",
                    predicted_prompt_similarity=overlap,
                    evidence_overlap=evidence_overlap,
                    answer_reuse_ratio=reuse_ratio,
                    directional_correct=directional,
                    metadata={"tier": cache_result.tier, "source": "cache_partial_or_warm"},
                )
            return package
        finally:
            self._notify_user_request_end()

    async def inject_history(
        self,
        queries: list[str] | list[tuple[str, float]],
        *,
        session_id: str = "external",
    ) -> int:
        """Inject prior queries into history.

        Accepts either ``list[str]`` (legacy — all queries are stamped with
        ``time.time()`` on insertion) or ``list[tuple[str, float]]`` where the
        second element is the unix timestamp for the query. When timestamps
        are provided they flow through to ``query_history``, the emitted
        ``SignalEvent``, and the ``ActivityTimingModel`` so the inter-prompt
        EMA reflects real session pacing rather than a compressed burst.
        """
        await self.initialize()
        injected = 0
        for item in queries:
            if isinstance(item, tuple):
                query_text, ts_val = item
                ts: float | None = float(ts_val)
            else:
                query_text = item
                ts = None
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
                timestamp=ts,
            )
            await self.store.insert_signal_event(
                SignalEvent(
                    id=str(uuid.uuid4()),
                    source="query",
                    kind="query_issued",
                    timestamp=ts if ts is not None else time.time(),
                    payload={
                        "category": category,
                        "injected": "true",
                        "corpus_id": getattr(self.adapter, "corpus_id", "default"),
                        "privacy_zone": getattr(self.adapter, "privacy_zone", "local"),
                    },
                )
            )
            if ts is not None:
                self._timing_model.record_prompt(ts)
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
        # Adaptive cycle budget: shrink the deadline when the user's
        # typical inter-prompt gap is short, so Vaner finishes an
        # exploration phase *before* the next prompt arrives instead of
        # getting interrupted mid-drill with half-written artefacts. The
        # static ``max_cycle_seconds`` cap remains the hard upper bound —
        # the adaptive model only ever shortens the cycle.
        if compute.adaptive_cycle_budget and compute.max_cycle_seconds and compute.max_cycle_seconds > 0:
            adaptive_budget = self._timing_model.budget_seconds_for_cycle(
                hard_cap_seconds=float(compute.max_cycle_seconds),
                soft_min_seconds=float(compute.adaptive_cycle_min_seconds),
                utilisation_fraction=float(compute.adaptive_cycle_utilisation),
            )
            adaptive_deadline = cycle_started + adaptive_budget
            if cycle_deadline is None or adaptive_deadline < cycle_deadline:
                cycle_deadline = adaptive_deadline
        governor = governor or PredictionGovernor()
        governor.reset()
        self._precompute_cycles += 1
        self._last_explored_scenarios = []
        full_packages = 0
        total_budget_ms = (
            max(0.0, (cycle_deadline - cycle_started) * 1000.0)
            if cycle_deadline is not None
            else max(0.0, float(self.config.compute.max_cycle_seconds) * 1000.0)
        )
        recent_entropy = 0.0
        abstain_mode = False
        if recent_query_text := [str(entry["query_text"]) for entry in await self.store.list_query_history(limit=10)]:
            phase_summary = self._arc_model.summarize_workflow_phase(recent_query_text)
            posterior = self._arc_model.rank_next(phase_summary.dominant_category, top_k=3, recent_queries=recent_query_text)
            if posterior:
                probs = [max(1e-9, float(item.confidence)) for item in posterior]
                total_prob = sum(probs)
                probs = [value / total_prob for value in probs]
                recent_entropy = -sum(value * math.log(value) for value in probs)
                _policy = AbstentionPolicy(
                    entropy_threshold=float(self._cycle_policy_state.get("entropy_abstain_threshold", 0.95)),
                    contradiction_threshold=1.0,
                )
                if _policy.should_abstain({str(i): p for i, p in enumerate(probs)}):
                    await self._metrics_store.increment_counter("abstain_total")
                    abstain_mode = True

        changed_for_volatility: list[str] = []
        volatility_score = 0.0
        volatility_drift_fraction = 0.0
        try:
            git_state_for_volatility = read_git_state(self.config.repo_root)
            changed_for_volatility = [
                line.strip()
                for line in (
                    str(git_state_for_volatility.get("recent_diff", "")) + "\n" + str(git_state_for_volatility.get("staged", ""))
                ).splitlines()
                if line.strip()
            ][:30]
            volatility = semantic_volatility_profile(changed_for_volatility)
            volatility_score = volatility.score
            volatility_drift_fraction = volatility.drift_fraction
        except Exception:
            volatility_score = 0.0
            volatility_drift_fraction = 0.0
        profile_pivot = float(self._user_profile.pivot_rate)
        exploit_ratio = max(0.2, min(0.7, float(self._cycle_policy_state.get("exploit_ratio", 0.5)) - (0.15 * volatility_score)))
        hedge_ratio = max(0.1, min(0.45, float(self._cycle_policy_state.get("hedge_ratio", 0.2)) + (0.10 * recent_entropy)))
        invest_ratio = max(0.05, min(0.35, float(self._cycle_policy_state.get("invest_ratio", 0.1)) + (0.10 * profile_pivot)))
        no_regret_ratio = max(
            0.05,
            min(
                0.45,
                float(self._cycle_policy_state.get("no_regret_ratio", 0.2))
                + (0.10 * volatility_score)
                + (0.10 * max(0.0, recent_entropy - 0.8)),
            ),
        )
        if volatility_drift_fraction > 0.5:
            # High drift means stale confidence; reserve more no-regret budget.
            no_regret_ratio = min(0.5, no_regret_ratio + 0.1)
        if abstain_mode:
            # Flat posterior: broaden exploration, skip committing to hypotheses.
            exploit_ratio = 0.0
            hedge_ratio = 0.0
            invest_ratio = 0.2
            no_regret_ratio = 0.8
        self._cycle_policy_state["exploit_ratio"] = exploit_ratio
        self._cycle_policy_state["hedge_ratio"] = hedge_ratio
        self._cycle_policy_state["invest_ratio"] = invest_ratio
        self._cycle_policy_state["no_regret_ratio"] = no_regret_ratio
        self._cycle_policy_state["recent_entropy"] = recent_entropy
        self._cycle_policy_state["volatility_score"] = volatility_score
        self._cycle_policy_state["volatility_drift_fraction"] = volatility_drift_fraction
        self._cycle_policy_state["abstain_mode"] = 1.0 if abstain_mode else 0.0
        self._mark_policy_state_dirty()
        allocation = PortfolioAllocator(
            exploit_ratio=exploit_ratio,
            hedge_ratio=hedge_ratio,
            invest_ratio=invest_ratio,
            no_regret_ratio=no_regret_ratio,
        ).allocate(total_budget_ms)

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
        prompt_macros = await self.store.list_prompt_macros(limit=25)
        patterns = await self.store.list_validated_patterns(limit=50)

        # WS6 invalidation sweep — run BEFORE merge so staled predictions
        # don't get their state accidentally refreshed. The sweep compares
        # cycle-start git HEAD + category tail + per-prediction file hashes
        # against whatever the registry captured on its last touch. No
        # wall-clock decay: if no signal fires, nothing is invalidated.
        cycle_signals = self._apply_cycle_invalidation(recent_query_text)
        # 0.8.2 WS3 — reconciliation runs on every cycle that saw at
        # least one commit or file_change signal. Best-effort: a
        # reconciliation failure is logged and swallowed so it never
        # breaks the precompute cycle.
        try:
            await self._apply_artefact_reconciliation(cycle_signals)
        except Exception:
            # Best-effort: a reconciliation failure (e.g. transient DB
            # lock, corrupt item row) must not abort the precompute
            # cycle. The next cycle retries automatically.
            pass

        # WS7 (0.8.2 WS2): run unified goal inference — branch name,
        # commit clustering, query clustering, and intent-bearing
        # artefacts all feed the same merge_hints coordinator. Inferred
        # goals are upserted so they participate in prediction seeding
        # alongside user-declared ones. Best-effort: a missing table or
        # legacy DB falls back to an empty list without failing the
        # cycle.
        try:
            await self._refresh_inferred_goals(recent_query_text)
            self._active_goals_cache = await self.store.list_workspace_goals(status="active", limit=20)
        except Exception:
            self._active_goals_cache = []

        # Phase 4 / WS1.d: build the prediction registry BEFORE seeding the
        # frontier so we can tag every admitted scenario with its parent
        # prediction_id. The maps route category/macro-keyed scenarios back
        # to the right PredictedPrompt during the LLM cycle.
        (
            self._prediction_registry,
            _pid_by_category,
            _pid_by_macro,
        ) = self._merge_prediction_specs(
            recent_query_text=recent_query_text,
            prompt_macros=prompt_macros,
            patterns=patterns,
        )

        # 0.8.2 WS2 — configure the artefact_alignment scoring term
        # before seeding. Collect the union of file paths referenced by
        # items under active artefact-backed goals so scenarios that
        # touch them earn the push-time boost. Empty set → no effect.
        aligned_paths: set[str] = set()
        for _item_rows in (self._active_artefact_items_cache or {}).values():
            for _item_row in _item_rows:
                import json as _json

                try:
                    paths = _json.loads(str(_item_row.get("related_files_json") or "[]"))
                    if isinstance(paths, list):
                        aligned_paths.update(str(p) for p in paths if p)
                except Exception:
                    continue
        frontier.set_artefact_aligned_paths(aligned_paths)

        # Order matters: Jaccard-dedup is first-admitted-wins, and
        # seed_from_workflow_phase produces arc-sourced scenarios whose file
        # sets overlap with seed_from_arc's. We seed the pid-tagged ones FIRST
        # so prediction-tagging survives dedup; workflow-phase fills in the
        # non-overlapping remainder.
        frontier.seed_from_arc(
            self._arc_model,
            recent_query_text,
            available_paths,
            prediction_id_for_category=_pid_by_category.get if _pid_by_category else None,
        )
        frontier.seed_from_prompt_macros(
            prompt_macros,
            available_paths,
            prediction_id_for_macro=_pid_by_macro.get if _pid_by_macro else None,
        )
        frontier.seed_from_workflow_phase(self._arc_model, recent_query_text, available_paths)
        frontier.seed_from_patterns(patterns)

        # Seed recovery scenarios for recent cold-miss queries.  These paths
        # clearly weren't precomputed and should be prioritized in this cycle.
        for miss_paths in self._miss_recovery_paths:
            frontier.seed_from_miss(miss_paths, available_paths)
        self._miss_recovery_paths.clear()
        # No-regret context budget: prioritize recently changed files and their
        # immediate neighborhoods regardless of next-prompt posterior.
        try:
            git_state = read_git_state(self.config.repo_root)
            changed_paths = [
                line.strip()
                for line in (str(git_state.get("recent_diff", "")) + "\n" + str(git_state.get("staged", ""))).splitlines()
                if line.strip()
            ][:20]
            no_regret_paths: set[str] = set(changed_paths)
            # Expand the no-regret slice with one-hop graph neighbors.
            for changed in changed_paths:
                no_regret_paths.update(
                    node.split(":", 1)[1]
                    for node in graph.propagate(f"file:{changed}", depth=1)
                    if isinstance(node, str) and node.startswith("file:")
                )
            for changed in sorted(no_regret_paths):
                frontier.seed_from_miss([changed], available_paths)
        except Exception:
            changed_paths = []

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
        _BREADTH_COVERAGE_THRESHOLD = max(0.1, min(0.9, float(self._cycle_policy_state.get("breadth_coverage_threshold", 0.40))))
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
        # WS1.e: counter for registry rebalance cadence. Incremented each time
        # a scenario with a prediction_id completes; when it crosses the
        # threshold we call registry.rebalance() under the registry lock.
        completed_with_pid_box = [0]
        _REBALANCE_EVERY_N = 5
        # Confidence threshold for the grounding→evidence_gathering transition.
        # Below this, a scenario produced a file ranking but not enough signal
        # to claim real evidence. Kept conservative so the registry doesn't
        # over-claim readiness.
        _EVIDENCE_CONFIDENCE_FLOOR = 0.3
        registry = self._prediction_registry
        deep_drill_threshold = float(self._cycle_policy_state.get("deep_drill_priority_threshold", ecfg.deep_drill_priority_threshold))
        if recent_query_text:
            phase_summary = self._arc_model.summarize_workflow_phase(recent_query_text)
            ranked = self._arc_model.rank_next(phase_summary.dominant_category, top_k=2, recent_queries=recent_query_text)
            if len(ranked) >= 2:
                p1 = max(1e-9, float(ranked[0].confidence))
                p2 = max(1e-9, float(ranked[1].confidence))
                ratio = p1 / p2
                deep_drill_threshold = max(0.1, min(0.95, deep_drill_threshold / max(1.0, ratio)))
        if allocation.exploit_ms > 0:
            exploit_share = allocation.exploit_ms / max(1.0, allocation.total_ms)
            deep_drill_threshold = max(0.1, min(0.95, deep_drill_threshold * (1.0 - (exploit_share - 0.5))))

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
                is_high_priority = scenario.priority >= deep_drill_threshold or scenario.depth_bonus > 0
                llm_semantic_intent = ""
                follow_on: list[dict[str, object]] = []
                llm_confidence = 0.0
                effective_paths: list[str] = list(scenario.file_paths)

                # WS1.e: register the scenario against its parent prediction
                # before the optional LLM call. This drives the queued →
                # grounding transition regardless of LLM gate so the registry
                # snapshot reflects admission-side activity even when the
                # scenario is explored structurally (graph-walk, pattern hit).
                pid = scenario.prediction_id
                if pid is not None and registry is not None and pid in registry:
                    async with registry.lock:
                        try:
                            registry.attach_scenario(pid, scenario.id)
                        except (KeyError, ValueError):
                            pass

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

                    # Route LLM outcomes back to the parent prediction. Tokens
                    # approximated by content length (real counts land in WS2
                    # with structured clients). Confidence is the evidence
                    # proxy; above _EVIDENCE_CONFIDENCE_FLOOR we transition to
                    # evidence_gathering.
                    if pid is not None and registry is not None and pid in registry:
                        approx_tokens = max(
                            1,
                            (len(llm_semantic_intent) + sum(len(str(p)) for p in ranked_files)) // 4,
                        )
                        async with registry.lock:
                            try:
                                registry.record_call(pid, tokens_used=approx_tokens)
                                registry.record_evidence(pid, delta_score=float(llm_confidence))
                                prompt = registry.get(pid)
                                if (
                                    prompt is not None
                                    and prompt.run.readiness == "grounding"
                                    and llm_confidence >= _EVIDENCE_CONFIDENCE_FLOOR
                                ):
                                    registry.transition(
                                        pid,
                                        "evidence_gathering",
                                        reason=f"confidence {llm_confidence:.2f} ≥ {_EVIDENCE_CONFIDENCE_FLOOR}",
                                    )
                            except (KeyError, ValueError):
                                pass

                # Mark scenario complete + bump rebalance cadence regardless
                # of LLM path. Graph-walk and pattern-cached scenarios count
                # toward progress too.
                if pid is not None and registry is not None and pid in registry:
                    async with registry.lock:
                        try:
                            registry.complete_scenario(pid, scenario.id)
                        except (KeyError, ValueError):
                            pass
                        completed_with_pid_box[0] += 1
                        if completed_with_pid_box[0] >= _REBALANCE_EVERY_N:
                            registry.rebalance()
                            completed_with_pid_box[0] = 0
                        # WS1.f follow-up: evidence-threshold drafting.
                        # The pattern path (via _precompute_predicted_responses)
                        # only advances pattern-sourced predictions through
                        # drafting→ready. Arc/history/etc. predictions would
                        # otherwise stall at evidence_gathering forever. Here
                        # we synthesise a lightweight briefing from the
                        # completed scenarios' file paths so those predictions
                        # can reach a usable adoption state.
                        try:
                            prompt_obj = registry.get(pid)
                            # WS1.f follow-up: predictions are per-cycle state
                            # (the registry is rebuilt each precompute_cycle),
                            # so we must reach drafting within a single cycle
                            # or the adoption surface is permanently empty.
                            # Trigger the transition as soon as the parent has
                            # at least one scenario complete and cleared the
                            # evidence floor.
                            if (
                                prompt_obj is not None
                                and prompt_obj.run.readiness == "evidence_gathering"
                                and prompt_obj.run.scenarios_complete >= 1
                                and prompt_obj.artifacts.evidence_score >= 0.5
                                and prompt_obj.artifacts.prepared_briefing is None
                            ):
                                # WS9: route through the BriefingAssembler.
                                synthesised = self._briefing_assembler.from_paths(
                                    label=prompt_obj.spec.label,
                                    description=prompt_obj.spec.description,
                                    paths=list(effective_paths),
                                    source=prompt_obj.spec.source,
                                    anchor=prompt_obj.spec.anchor,
                                    confidence=prompt_obj.spec.confidence,
                                    scenarios_complete=prompt_obj.run.scenarios_complete,
                                    evidence_score=prompt_obj.artifacts.evidence_score,
                                ).text
                                registry.transition(
                                    pid,
                                    "drafting",
                                    reason=(
                                        f"evidence-threshold draft "
                                        f"(scenarios={prompt_obj.run.scenarios_complete}, "
                                        f"score={prompt_obj.artifacts.evidence_score:.2f})"
                                    ),
                                )
                                # WS6: capture per-path content hashes so the
                                # next cycle's invalidation sweep can tell when
                                # the briefing's evidence moved on disk.
                                try:
                                    hashes_now = read_content_hashes(self.config.repo_root, list(effective_paths))
                                except Exception:
                                    hashes_now = {}
                                registry.attach_artifact(
                                    pid,
                                    briefing=synthesised,
                                    file_content_hashes=hashes_now or None,
                                )
                                registry.transition(
                                    pid,
                                    "ready",
                                    reason="briefing synthesised; no critical contradictions",
                                )
                        except (KeyError, ValueError):
                            pass

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
                    if scenario.priority >= deep_drill_threshold:
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

        # Predicted-response precompute: if the user's most-validated prompt
        # macro is something like "do a code review of the latest
        # implementation", and the cycle has budget remaining, actually draft
        # the response so the agent can surface it the instant the expected
        # prompt arrives. Opt-in because it amplifies LLM spend.
        # Skip in abstain mode: flat posterior means draft quality is too low.
        if ecfg.predicted_response_enabled and callable(self.llm) and not abstain_mode:
            remaining_deadline = cycle_deadline
            await self._precompute_predicted_responses(
                max_per_cycle=max(0, int(ecfg.predicted_response_max_per_cycle)),
                min_use_count=max(1, int(ecfg.predicted_response_min_macro_use_count)),
                deadline=remaining_deadline,
                available_paths=available_paths,
                recent_queries=recent_query_text,
                prediction_id_for_macro=_pid_by_macro,
            )

        # 0.8.4 WS4 — Flush any pending adoption descriptors from the
        # registry into the adoption-outcome log, and sweep pending
        # outcomes that have aged past the confirm window into
        # ``confirmed``. Both steps run unconditionally (not behind the
        # refinement.enabled flag) so the log accumulates from day one
        # and is ready for 0.8.5 activation of the scoring consumer.
        await self._flush_pending_adoption_outcomes()
        await self._sweep_pending_adoption_outcomes()

        # 0.8.4 WS3 — Background refinement pass. Runs on spare compute
        # post-frontier, post-predicted-response, if (a) the feature flag
        # is on, (b) a drafter callable has been injected, (c) the
        # prediction registry has candidates, and (d) the user is not
        # actively requesting. Generalises the 0.8.3 Deep-Run maturation
        # loop to ordinary idle cycles using the same skeptical-default
        # judge + probation + rollback machinery. Default-off in 0.8.4;
        # 0.8.5 flips the default after the κ bench gate passes.
        if self.config.refinement.enabled and self._refinement_drafter is not None:
            await self._run_background_refinement_pass(
                governor=governor,
                cycle_deadline=cycle_deadline,
            )

        retention_seconds = max(3600, int(self.config.max_age_seconds))
        await self.store.purge_old_signal_events(max_age_seconds=retention_seconds)
        await self.store.purge_old_replay_entries(max_age_seconds=retention_seconds)
        await self.store.purge_old_query_history(max_age_seconds=retention_seconds)
        await self.store.purge_expired_prediction_cache()
        # Decay pass: drop precomputed cache entries the developer never
        # consumed after ``unused_cache_max_age_seconds``. These are
        # predictions that aged out of relevance — keeping them around
        # wastes store space and pollutes future cache matches with stale
        # prompt hints that will never be used.
        unused_max_age = float(self.config.exploration.unused_cache_max_age_seconds)
        if unused_max_age > 0.0:
            await self.store.purge_unused_prediction_cache(
                max_age_seconds_without_access=unused_max_age,
                min_access_count_to_protect=1,
            )
        await self.store.purge_stale_patterns(max_age_seconds=retention_seconds)
        await self._persist_learning_state()
        cycle_elapsed_s = time.monotonic() - cycle_started
        allocated_s = max(0.0, float(cycle_deadline - cycle_started)) if cycle_deadline is not None else cycle_elapsed_s
        total_allocated_ms = allocated_s * 1000.0
        total_used_ms = cycle_elapsed_s * 1000.0
        await self._metrics_store.record_cycle_budget(allocated_ms=total_allocated_ms, used_ms=total_used_ms, bucket="overall")
        allocation_scale = (total_used_ms / max(1.0, allocation.total_ms)) if allocation.total_ms > 0 else 0.0
        await self._metrics_store.record_cycle_budget(
            allocated_ms=allocation.exploit_ms,
            used_ms=allocation.exploit_ms * allocation_scale,
            bucket="exploit",
        )
        await self._metrics_store.record_cycle_budget(
            allocated_ms=allocation.hedge_ms,
            used_ms=allocation.hedge_ms * allocation_scale,
            bucket="hedge",
        )
        await self._metrics_store.record_cycle_budget(
            allocated_ms=allocation.invest_ms,
            used_ms=allocation.invest_ms * allocation_scale,
            bucket="invest",
        )
        await self._metrics_store.record_cycle_budget(
            allocated_ms=allocation.no_regret_ms,
            used_ms=allocation.no_regret_ms * allocation_scale,
            bucket="no_regret",
        )
        _record_idle_usage_seconds(self.config, cycle_elapsed_s)
        return full_packages

    def get_explored_scenarios(self) -> list[ExploredScenario]:
        """Return scenarios explored in the most recent precompute cycle."""
        return list(self._last_explored_scenarios)

    def get_active_predictions(self) -> list[PredictedPrompt]:
        """Return non-terminal PredictedPrompts for the active cycle.

        Empty before the first precompute_cycle runs. Phase C consumes this
        for MCP/HTTP surface; Phase D's desktop pane derives its row list
        from it.
        """
        if self._prediction_registry is None:
            return []
        return self._prediction_registry.active()

    @property
    def prediction_registry(self) -> PredictionRegistry | None:
        """Active prediction registry — None before the first cycle."""
        return self._prediction_registry

    # ------------------------------------------------------------------
    # 0.8.4 WS4 — Adoption-outcome log (flush + sweep)
    # ------------------------------------------------------------------

    async def _flush_pending_adoption_outcomes(self) -> int:
        """Drain the prediction registry's pending-adoption queue and
        persist each descriptor as a ``pending`` outcome row.

        Runs unconditionally at end-of-cycle — the adoption log
        accumulates signal regardless of whether ``refinement.enabled``
        is set. Returns the number of rows written.
        """

        if self._prediction_registry is None:
            return 0
        descriptors = self._prediction_registry.consume_pending_adoption_descriptors()
        if not descriptors:
            return 0
        from vaner.models.prediction_adoption_outcome import (
            PredictionAdoptionOutcome,
        )
        from vaner.store import prediction_adoption_outcomes as _pao_store

        workspace_root = str(self.config.repo_root)
        written = 0
        for d in descriptors:
            try:
                outcome = PredictionAdoptionOutcome.new_pending(
                    prediction_id=str(d["prediction_id"]),
                    label=str(d["label"]),
                    anchor=str(d["anchor"]),
                    revision_at_adoption=int(d["revision_at_adoption"]),  # type: ignore[arg-type]
                    workspace_root=workspace_root,
                    source=str(d["source"]),
                )
                await _pao_store.create_outcome(self.store.db_path, outcome)
                written += 1
            except Exception:  # pragma: no cover - defensive: never crash cycle
                continue
        return written

    async def _sweep_pending_adoption_outcomes(self) -> int:
        """Resolve pending adoption outcomes older than the configured
        confirm window to ``confirmed``. Returns the number updated.

        Runs unconditionally at end-of-cycle (cheap single-table scan
        bounded by ``LIMIT 500``). Resolution is cycle-count-based via
        ``adopted_at`` vs. a synthetic ``adopted_at_cutoff`` derived
        from the current cycle index + an assumed mean cycle time —
        conservative in that it only confirms outcomes whose adoption
        predates the cutoff by a wide margin. Rollback-driven
        ``rejected`` transitions happen via
        :meth:`_reject_pending_adoptions_for_predictions`, invoked by
        the rollback path.
        """

        if self._prediction_registry is None:
            return 0
        cfg = self.config.refinement
        # Use wall-clock as the aging signal — simpler and more honest
        # than synthetic cycle counts (which can diverge between idle
        # and active phases). Assume a nominal 30s cycle; the cutoff
        # is adoption_pending_confirm_cycles × 30s before now.
        nominal_cycle_seconds = 30.0
        cutoff = time.time() - cfg.adoption_pending_confirm_cycles * nominal_cycle_seconds

        from vaner.store import prediction_adoption_outcomes as _pao_store

        pending = await _pao_store.list_pending_outcomes(self.store.db_path, limit=500)
        resolved = 0
        now = time.time()
        for outcome in pending:
            if outcome.adopted_at <= cutoff:
                try:
                    ok = await _pao_store.update_outcome_state(
                        self.store.db_path,
                        outcome.id,
                        outcome="confirmed",
                        resolved_at=now,
                    )
                    if ok:
                        resolved += 1
                except Exception:  # pragma: no cover - defensive
                    continue
        return resolved

    async def _reject_pending_adoptions_for_predictions(self, prediction_ids: list[str], *, reason: str) -> int:
        """Mark any pending adoption outcomes for the named predictions
        as ``rejected``. Called from the ``rollback_kept_maturation()``
        path and from the invalidation sweep when a staled prediction
        had a pending adoption outcome.

        Returns the number of rows updated.
        """

        if not prediction_ids:
            return 0
        from vaner.store import prediction_adoption_outcomes as _pao_store

        return await _pao_store.update_pending_by_prediction_id(
            self.store.db_path,
            prediction_ids,
            outcome="rejected",
            resolved_at=time.time(),
            rollback_reason=reason,
        )

    # ------------------------------------------------------------------
    # 0.8.4 WS3 — Background refinement
    # ------------------------------------------------------------------

    def set_refinement_drafter(self, drafter: object) -> None:
        """Inject the :class:`MaturationDrafterCallable` used by background
        refinement. Default is ``None`` — the refinement pass is a no-op
        until a drafter is set. Typed as ``object`` to avoid forcing
        callers to import the Protocol; the engine treats it as a duck-typed
        async callable matching the Protocol signature."""

        from vaner.intent.deep_run_maturation import MaturationDrafterCallable

        self._refinement_drafter = drafter  # type: ignore[assignment]
        _ = MaturationDrafterCallable  # keep the import alive for type-checkers

    async def _run_background_refinement_pass(
        self,
        *,
        governor: PredictionGovernor | None,
        cycle_deadline: float | None,
    ) -> int:
        """Run one background-refinement pass over the top-K ready predictions.

        Selects candidates via :func:`select_maturation_candidates`, then
        invokes :func:`mature_one` on each (stopping immediately if the
        governor signals a user request or the cycle deadline expires).
        Returns the number of maturation passes actually attempted.

        No audit log is written for background passes — the
        :class:`RefinementContext` carries ``session_id=None`` so the
        ``deep_run_pass_log`` path is bypassed by design.
        """

        if self._prediction_registry is None:
            return 0
        if self._refinement_drafter is None:
            return 0
        refinement_cfg = self.config.refinement
        if not refinement_cfg.enabled:
            return 0

        # Deadline floor — don't start a pass if there's less than the
        # configured minimum headroom; the drafter + judge round-trip
        # alone typically exceeds a few hundred ms.
        if cycle_deadline is not None:
            remaining = cycle_deadline - time.monotonic()
            if remaining < refinement_cfg.min_remaining_deadline_seconds:
                return 0

        active = self._prediction_registry.active()
        if not active:
            return 0

        context = RefinementContext.background_default(cycle_index=self._precompute_cycles)
        candidates = select_maturation_candidates(
            active,
            context=context,
            max_candidates=refinement_cfg.max_candidates_per_cycle,
        )

        attempted = 0
        for candidate in candidates:
            if not candidate.eligible:
                continue
            if governor is not None and not governor.should_continue():
                break
            if cycle_deadline is not None and time.monotonic() >= cycle_deadline:
                break
            pass_id = f"bg-{self._precompute_cycles}-{candidate.prediction.spec.id}"
            try:
                await mature_one(
                    candidate.prediction,
                    context=context,
                    drafter=self._refinement_drafter,  # type: ignore[arg-type]
                    pass_id=pass_id,
                )
            except Exception:  # pragma: no cover — defensive: never crash cycle
                continue
            attempted += 1
        return attempted

    def get_last_decision_record(self) -> DecisionRecord | None:
        return self._last_decision_record

    # ------------------------------------------------------------------
    # WS8: unified resolve_query — single canonical query → Resolution entry
    # ------------------------------------------------------------------

    async def resolve_query(
        self,
        query: str,
        *,
        context: dict | None = None,
        include_briefing: bool = True,
        include_predicted_response: bool = True,
    ) -> Any:
        """Single canonical ``query → Resolution`` path.

        WS8 replaces the two parallel implementations (engine.query
        returning ``ContextPackage`` and MCP ``vaner.resolve`` building
        its own Resolution from scenario-store rows) with one method
        that:

        1. Consults the :class:`PredictionRegistry` for a ready /
           drafting prediction whose label matches the query — if found,
           returns a Resolution built via the shared
           :class:`BriefingAssembler`, with ``predicted_response``
           populated from the prediction's cached draft and
           ``alternatives_considered`` populated from runner-up
           predictions.
        2. Falls back to :meth:`query` (heuristic + tiered cache) when
           no prediction matches, building a Resolution from the
           returned :class:`ContextPackage`.

        The Resolution populated here is honest about provenance
        (``predicted_hit`` / ``cached_result`` / ``fresh_resolution``)
        and reports real token counts via the assembler's tokenizer
        path.

        ``context`` is accepted for symmetry with the MCP surface but
        not consumed here; future goal-aware biasing (WS7 scoring
        integration) can read from it. ``include_briefing`` /
        ``include_predicted_response`` mirror the MCP opt-in flags.
        """
        await self.initialize()
        # Late import keeps the engine module free of pydantic at
        # import time for callers that only need precompute_cycle.
        from vaner.mcp.contracts import (
            Alternative,
            EvidenceItem,
            Provenance,
            Resolution,
        )

        resolution_id = f"resolve-{uuid.uuid4().hex[:12]}"

        # Step 1: prediction-registry match on label similarity.
        matched_prediction: PredictedPrompt | None = None
        alternatives: list[Alternative] = []
        if self._prediction_registry is not None:
            active = self._prediction_registry.active()
            # Only consider predictions that actually have artefacts to
            # return — otherwise the MCP caller would see a "matched"
            # row with empty briefing. The label-match is a cheap
            # contains-check in both directions; a more refined
            # similarity score is a WS8.1 follow-up.
            query_lower = query.lower()
            candidates: list[tuple[float, PredictedPrompt]] = []
            for prompt in active:
                label_lower = prompt.spec.label.lower()
                overlap = 0.0
                if query_lower in label_lower or label_lower in query_lower:
                    overlap = 1.0
                else:
                    # Simple word-overlap heuristic so "add tests for
                    # parser" and "write parser tests" still match.
                    q_tokens = set(w for w in query_lower.split() if len(w) > 2)
                    l_tokens = set(w for w in label_lower.split() if len(w) > 2)
                    if q_tokens and l_tokens:
                        overlap = len(q_tokens & l_tokens) / max(1, len(q_tokens | l_tokens))
                if overlap > 0:
                    candidates.append((overlap, prompt))
            candidates.sort(key=lambda pair: pair[0], reverse=True)
            if candidates and candidates[0][0] >= 0.5:
                matched_prediction = candidates[0][1]
                # Runners-up → Alternative rows for honest provenance.
                for score, prompt in candidates[1:4]:
                    alternatives.append(
                        Alternative(
                            source=prompt.spec.source,
                            reason_rejected=(f"runner-up prediction (overlap={score:.2f}): {prompt.spec.label}"),
                        )
                    )

        if matched_prediction is not None:
            briefing = self._briefing_assembler.from_prediction(matched_prediction)
            predicted_response = matched_prediction.artifacts.draft_answer if include_predicted_response else None
            evidence = [
                EvidenceItem(
                    id=sid,
                    source=matched_prediction.spec.source,
                    kind="record",
                    locator={
                        "prediction_id": matched_prediction.spec.id,
                        "scenario_id": sid,
                    },
                    reason=(f"scenario explored under prediction {matched_prediction.spec.label!r}"),
                )
                for sid in matched_prediction.artifacts.scenario_ids
            ]
            return Resolution(
                intent=matched_prediction.spec.label,
                confidence=float(matched_prediction.spec.confidence),
                summary=matched_prediction.spec.description or matched_prediction.spec.label,
                evidence=evidence,
                alternatives_considered=alternatives,
                provenance=Provenance(
                    mode="predictive_hit",
                    cache="warm",
                    freshness="fresh",
                ),
                resolution_id=resolution_id,
                prepared_briefing=briefing.text if include_briefing else None,
                predicted_response=predicted_response,
                briefing_token_used=briefing.token_count,
                briefing_token_budget=matched_prediction.run.token_budget,
            )

        # Step 2: heuristic fallback via existing query() path.
        package = await self.query(query, top_n=8)
        paths = [sel.source_path for sel in package.selections]
        artefacts_by_key = {a.key: a for a in await self.store.list(limit=2000)}
        artefacts_for_briefing = [artefacts_by_key[sel.artefact_key] for sel in package.selections if sel.artefact_key in artefacts_by_key]
        briefing = self._briefing_assembler.from_artefacts(
            intent=query,
            artefacts=artefacts_for_briefing,
            paths=paths,
        )
        tier = package.cache_tier or "miss"
        provenance_mode = {
            "full_hit": "predictive_hit",
            "partial_hit": "cached_result",
            "warm_start": "fresh_resolution",
            "miss": "retrieval_fallback",
        }.get(tier, "retrieval_fallback")
        cache_label = {
            "full_hit": "hot",
            "partial_hit": "warm",
            "warm_start": "warm",
            "miss": "cold",
        }.get(tier, "cold")
        evidence = [
            EvidenceItem(
                id=sel.artefact_key,
                source=tier,
                kind="file",
                locator={"path": sel.source_path, "artefact_key": sel.artefact_key},
                reason=sel.rationale or f"selected by tier={tier}",
            )
            for sel in package.selections[:8]
        ]
        return Resolution(
            intent=query,
            confidence=0.5 if tier == "miss" else 0.7,
            summary=f"Heuristic context for: {query}",
            evidence=evidence,
            alternatives_considered=alternatives,
            provenance=Provenance(
                mode=provenance_mode,  # type: ignore[arg-type]
                cache=cache_label,  # type: ignore[arg-type]
                freshness="fresh",
            ),
            resolution_id=resolution_id,
            prepared_briefing=briefing.text if include_briefing else None,
            predicted_response=None,
            briefing_token_used=briefing.token_count,
            briefing_token_budget=max(package.token_budget, briefing.token_count),
        )

    # ------------------------------------------------------------------
    # 0.8.2 WS2: unified goal inference
    # ------------------------------------------------------------------

    async def _refresh_inferred_goals(self, recent_query_text: list[str]) -> None:
        """Run the unified goal-inference pipeline and upsert the output.

        Four candidate sources feed :func:`goal_inference.merge_hints`:

        1. Branch name (existing pre-0.8.2 behaviour).
        2. Recent commit subjects (0.8.2 WS2, deferred from 0.8.1).
        3. Recent query history (0.8.2 WS2, deferred from 0.8.1).
        4. Intent-bearing artefacts + their items (0.8.2 WS2).

        User-declared goals are preserved exactly as they are (the
        merge coordinator ranks ``user_declared`` above every inferred
        source). Inferred goals are upserted with their
        artefact_refs / subgoal_of / §6.6 policy-consumer metadata so
        downstream consumers read one canonical representation.

        Best-effort — any source that raises is dropped from the
        candidate stream rather than failing the cycle. The cycle-top
        caller catches exceptions from this method and continues with
        whatever persisted goals already exist.
        """

        import json as _json

        from vaner.daemon.signals.git_reader import (
            read_commit_subjects,
            read_git_state,
        )
        from vaner.intent.branch_parser import parse_branch_name
        from vaner.intent.goal_inference import GoalCandidate, merge_hints
        from vaner.intent.goal_inference_artefacts import hints_from_artefacts
        from vaner.intent.goal_inference_commits import cluster_commit_subjects
        from vaner.intent.goal_inference_queries import cluster_query_history
        from vaner.intent.goals import GoalEvidence

        candidates: list[GoalCandidate] = []

        # Source 1: branch name.
        try:
            git_state = read_git_state(self.config.repo_root)
            branch = str(git_state.get("branch", "") or "").strip()
            hint = parse_branch_name(branch) if branch else None
            if hint is not None:
                candidates.append(
                    GoalCandidate(
                        title=hint.title,
                        source="branch_name",
                        confidence=hint.confidence,
                        description=f"Inferred from branch {branch!r}.",
                        evidence=(GoalEvidence(kind="branch_name", value=branch, weight=1.0),),
                    )
                )
        except Exception:
            # Best-effort per source: any producer that throws is
            # dropped from the candidate stream for this cycle rather
            # than failing the whole inference pass. The other three
            # sources still contribute.
            pass

        # Source 2: commit clustering.
        try:
            subjects = read_commit_subjects(self.config.repo_root, last_n=30)
            candidates.extend(cluster_commit_subjects(subjects))
        except Exception:
            # Best-effort per source — see branch-name comment above.
            pass

        # Source 3: query-history clustering.
        try:
            query_rows = await self.store.list_query_history(limit=50)
            candidates.extend(cluster_query_history(query_rows))
        except Exception:
            # Best-effort per source — see branch-name comment above.
            pass

        # Source 4: intent-bearing artefacts. Also populates the per-
        # cycle item cache that ``_emit_artefact_item_specs`` reads to
        # emit artefact-item-anchored prediction specs.
        self._active_artefact_items_cache = {}
        try:
            artefact_rows = await self.store.list_intent_artefacts(status="active", limit=50)
            from vaner.intent.artefacts import IntentArtefact, IntentArtefactItem

            bundle: list[tuple[IntentArtefact, list[IntentArtefactItem]]] = []
            for row in artefact_rows:
                artefact = IntentArtefact(
                    id=str(row["id"]),
                    source_uri=str(row["source_uri"]),
                    source_tier=str(row["source_tier"]),  # type: ignore[arg-type]
                    connector=str(row["connector"]),
                    kind=str(row["kind"]),  # type: ignore[arg-type]
                    title=str(row["title"]),
                    status=str(row["status"]),  # type: ignore[arg-type]
                    confidence=float(row["confidence"] or 0.0),
                    created_at=float(row["created_at"] or 0.0),
                    last_observed_at=float(row["last_observed_at"] or 0.0),
                    last_reconciled_at=(float(row["last_reconciled_at"]) if row.get("last_reconciled_at") is not None else None),
                    latest_snapshot=str(row.get("latest_snapshot") or ""),
                )
                snap_id = artefact.latest_snapshot
                if not snap_id:
                    continue
                item_rows = await self.store.list_intent_artefact_items(
                    artefact_id=artefact.id,
                    snapshot_id=snap_id,
                )
                items = [
                    IntentArtefactItem(
                        id=str(ir["id"]),
                        artefact_id=artefact.id,
                        text=str(ir["text"]),
                        kind=str(ir["kind"]),  # type: ignore[arg-type]
                        state=str(ir["state"]),  # type: ignore[arg-type]
                        section_path=str(ir.get("section_path") or ""),
                        parent_item=(str(ir["parent_item"]) if ir.get("parent_item") else None),
                        related_files=_json.loads(str(ir.get("related_files_json") or "[]")),
                        related_entities=_json.loads(str(ir.get("related_entities_json") or "[]")),
                        evidence_refs=_json.loads(str(ir.get("evidence_refs_json") or "[]")),
                    )
                    for ir in item_rows
                ]
                bundle.append((artefact, items))
                # Cache raw item rows for the per-cycle artefact_item
                # prediction-spec emitter. Storing the raw dicts avoids
                # reparsing the dataclasses there.
                self._active_artefact_items_cache[artefact.id] = list(item_rows)
            candidates.extend(hints_from_artefacts(bundle))
        except Exception:
            # Best-effort per source — see branch-name comment above.
            pass

        merged = merge_hints(candidates)
        if not merged:
            return

        # Upsert each inferred goal. User-declared goals are unaffected
        # (the merge coordinator would have priority-picked user_declared
        # if any was present in the candidate stream; this code path
        # skips them because we only emit candidates from inference
        # sources above).
        for goal in merged:
            try:
                await self.store.upsert_workspace_goal(
                    id=goal.id,
                    title=goal.title,
                    description=goal.description,
                    source=goal.source,
                    confidence=goal.confidence,
                    status=goal.status,
                    evidence_json=_json.dumps([{"kind": ev.kind, "value": ev.value, "weight": ev.weight} for ev in goal.evidence]),
                    related_files_json=_json.dumps(goal.related_files),
                    artefact_refs_json=(_json.dumps(goal.artefact_refs) if goal.artefact_refs else None),
                    subgoal_of=goal.subgoal_of,
                    pc_freshness=goal.pc_freshness,
                    pc_reconciliation_state=goal.pc_reconciliation_state,
                    pc_unfinished_item_state=goal.pc_unfinished_item_state,
                )
            except Exception:
                continue

    async def _apply_artefact_reconciliation(self, cycle_signals: list) -> None:
        """0.8.2 WS3 — run reconciliation for every active artefact when
        a ``commit`` or ``file_change`` signal fires this cycle.

        Per spec §10.1, reconciliation is gated on *any* relevant signal
        arrival, not on pure time. This preserves the no-wall-clock-
        decay invariant — a cycle with no underlying state change never
        runs a reconciliation pass.

        Writes one :class:`ReconciliationOutcome` per artefact,
        emits a ``progress_reconciled`` ``SignalEvent`` into the signal
        log, and applies the resulting item-state deltas to the
        prediction registry. Runs best-effort: a per-artefact failure
        is logged but does not stop reconciliation for other artefacts.
        """

        from vaner.intent.reconcile import ReconcileContext, reconcile_artefact

        # Only proceed when a structural signal actually fired.
        relevant = [s for s in cycle_signals if getattr(s, "kind", "") in ("commit", "file_change")]
        if not relevant:
            return

        # Collect triggering information for the reconcile context.
        changed_files: set[str] = set()
        for sig in relevant:
            if getattr(sig, "kind", "") == "file_change":
                payload = getattr(sig, "payload", {}) or {}
                changed_files.update(str(p) for p in payload.get("changed_paths", []) or [])

        # Fetch recent commit subjects for commit-correlation matcher.
        from vaner.daemon.signals.git_reader import read_commit_subjects

        try:
            commit_subjects = tuple(read_commit_subjects(self.config.repo_root, last_n=5))
        except Exception:
            commit_subjects = ()

        # Iterate active artefacts and reconcile each.
        try:
            artefact_rows = await self.store.list_intent_artefacts(status="active", limit=50)
        except Exception:
            return

        for artefact_row in artefact_rows:
            artefact_id = str(artefact_row["id"])
            context = ReconcileContext(
                artefact_id=artefact_id,
                triggering_signal_id=None,
                changed_files=frozenset(changed_files),
                commit_subjects=commit_subjects,
            )
            try:
                result = await reconcile_artefact(context, store=self.store)
            except Exception:
                continue
            if result is None:
                continue
            if result.signal_event is not None:
                try:
                    await self.store.insert_signal_event(result.signal_event)
                except Exception:
                    # Best-effort: dropping the signal-event insert here
                    # only costs external observers (signal_events table)
                    # the reconciliation notification. The authoritative
                    # ReconciliationOutcome record already persisted.
                    pass
            # Route item-state deltas through the registry so artefact-
            # item-anchored predictions get adopted / demoted / staled.
            registry = self._prediction_registry
            if registry is not None and result.item_state_deltas:
                for delta in result.item_state_deltas:
                    try:
                        registry.apply_item_state_delta(
                            item_id=delta.item_id,
                            from_state=delta.from_state,
                            to_state=delta.to_state,
                        )
                    except Exception:
                        continue
            # Apply the progress_reconciled signal to the registry too
            # — today a no-op per WS3 design (pointer-only payload), but
            # kept for symmetry with the other invalidation sweeps.
            if registry is not None and result.signal is not None:
                try:
                    registry.apply_invalidation_signals([result.signal])
                except Exception:
                    # Best-effort: progress_reconciled is a no-op in the
                    # WS3 registry today (pointer-only payload); a
                    # throw here would only mean the symmetric sweep
                    # was skipped, which is harmless.
                    pass

    def _emit_artefact_item_specs(
        self,
        goal_rows: list[dict[str, object]],
    ) -> list[PredictionSpec]:
        """Produce ``source="artefact_item"`` prediction specs for every
        eligible item under an active artefact-backed goal.

        Iterates goals whose ``artefact_refs_json`` names one or more
        :class:`IntentArtefact` ids in the engine's per-cycle item
        cache. For each item with state ∈ {pending, in_progress,
        stalled} emits a single :class:`PredictionSpec`. The spec's
        anchor is the item id so reconciliation (WS3) can route
        ``progress_reconciled`` signals directly at the owning
        predictions.

        Hypothesis type follows item state: ``pending`` /
        ``in_progress`` → ``likely_next`` (the user's declared next
        step); ``stalled`` → ``possible_branch`` (still in scope but
        demoted). Specificity defaults to ``concrete`` when the item
        names ≥1 related file, else ``category``.

        Confidence = goal.confidence × item_state_weight × artefact
        freshness (read from ``pc_freshness`` on the goal row;
        defaults to 1.0 on legacy rows). Bounded to [0, 1].
        """

        import json as _json

        if not goal_rows:
            return []

        item_cache = getattr(self, "_active_artefact_items_cache", None) or {}
        if not item_cache:
            return []

        specs: list[PredictionSpec] = []
        seen_item_ids: set[str] = set()
        for goal_row in goal_rows:
            refs_json = goal_row.get("artefact_refs_json")
            if not refs_json:
                continue
            try:
                refs = _json.loads(str(refs_json))
            except Exception:
                continue
            if not isinstance(refs, list) or not refs:
                continue
            goal_confidence = float(goal_row.get("confidence") or 0.0)
            goal_freshness = float(goal_row.get("pc_freshness") or 1.0)
            goal_title = str(goal_row.get("title") or "").strip()
            for artefact_id in refs:
                artefact_id_str = str(artefact_id)
                item_rows = item_cache.get(artefact_id_str, [])
                for item_row in item_rows:
                    state = str(item_row.get("state") or "").strip()
                    if state not in ("pending", "in_progress", "stalled"):
                        continue
                    item_id = str(item_row.get("id") or "").strip()
                    if not item_id or item_id in seen_item_ids:
                        continue
                    seen_item_ids.add(item_id)
                    text = str(item_row.get("text") or "").strip()
                    if not text:
                        continue
                    weight = _ITEM_STATE_WEIGHTS.get(state, 0.5)
                    confidence = min(1.0, max(0.0, goal_confidence * weight * goal_freshness))
                    if confidence <= 0.0:
                        continue
                    label = f"Step: {text[:60]}"
                    description = f"Artefact item under goal {goal_title!r}: {text}"
                    hypothesis_type: HypothesisType = "likely_next" if state in ("pending", "in_progress") else "possible_branch"
                    try:
                        related_files = _json.loads(str(item_row.get("related_files_json") or "[]"))
                    except Exception:
                        related_files = []
                    specificity: Specificity = "concrete" if isinstance(related_files, list) and related_files else "category"
                    pid = prediction_id("artefact_item", item_id, label)
                    specs.append(
                        PredictionSpec(
                            id=pid,
                            label=label,
                            description=description,
                            source="artefact_item",
                            anchor=item_id,
                            confidence=confidence,
                            hypothesis_type=hypothesis_type,
                            specificity=specificity,
                        )
                    )
        return specs

    # ------------------------------------------------------------------
    # Phase 4: prediction enrolment
    # ------------------------------------------------------------------

    def _apply_cycle_invalidation(self, recent_query_text: list[str]) -> list:
        """WS6 — sweep the registry for invalidation signals before each cycle's merge.

        Runs at cycle top. Compares:
          - **git HEAD SHA** against ``self._last_observed_head_sha`` —
            a moved HEAD stales phase/category-anchored predictions.
          - **Per-prediction file content hashes** against the current
            disk state — changed paths demote the owning prediction's
            weight and clear its briefing.
          - **Recent category streak** against the prediction population
            — a persistent switch away from an anchor category stales
            predictions anchored on that category.

        The method is a no-op on the very first cycle (nothing has been
        observed yet) and also when there are no predictions to touch.

        Returns the list of :class:`InvalidationSignal` records it
        emitted this cycle. The engine's WS3 reconciliation step reads
        this list to decide which artefacts need a reconcile pass.
        """
        registry = self._prediction_registry
        if registry is None or len(registry) == 0:
            # Nothing to invalidate yet. Still capture head SHA + categories
            # so the next cycle has a baseline to diff against.
            try:
                self._last_observed_head_sha = read_head_sha(self.config.repo_root)
            except Exception:
                self._last_observed_head_sha = ""
            self._last_observed_categories = [classify_query_category(q) for q in recent_query_text if q]
            return []

        signals = []

        # 1) Commit signal — HEAD moved.
        try:
            current_head = read_head_sha(self.config.repo_root)
        except Exception:
            current_head = ""
        commit_sig = build_commit_signal(self._last_observed_head_sha, current_head)
        if commit_sig is not None:
            signals.append(commit_sig)

        # 2) File-change signal — the union of hashes captured by any
        #    active prediction's briefing is the set we need to compare
        #    against disk. Paths never captured aren't interesting here.
        watched_paths: set[str] = set()
        captured_all: dict[str, str] = {}
        for prompt in registry.all():
            if prompt.is_terminal():
                continue
            for path, sha in prompt.artifacts.file_content_hashes.items():
                watched_paths.add(path)
                # Last-write-wins on the aggregated map is fine — all
                # predictions watching the same path captured it at the
                # same time (they share the same cycle's disk state).
                captured_all[path] = sha
        if watched_paths:
            try:
                fresh_hashes = read_content_hashes(self.config.repo_root, sorted(watched_paths))
            except Exception:
                fresh_hashes = {}
            file_sig = build_file_change_signal(captured_all, fresh_hashes)
            if file_sig is not None:
                signals.append(file_sig)

        # 3) Category-shift signal — sustained move away from a previous
        #    anchor. We compare the current cycle's recent categories
        #    against what we saw last cycle so the shift has to be
        #    observable-persistent, not a one-prompt dip.
        current_categories = [classify_query_category(q) for q in recent_query_text if q]
        cat_sig = build_category_shift_signal(current_categories)
        if cat_sig is not None:
            signals.append(cat_sig)

        if signals:
            registry.apply_invalidation_signals(signals)

        # Refresh observations for the next cycle's diff.
        self._last_observed_head_sha = current_head
        self._last_observed_categories = current_categories
        return signals

    def _merge_prediction_specs(
        self,
        *,
        recent_query_text: list[str],
        prompt_macros: list[dict[str, object]],
        patterns: list[dict[str, object]],
    ) -> tuple[PredictionRegistry, dict[str, str], dict[str, str]]:
        """Build this cycle's spec batch and merge it into the persistent registry.

        WS6 replacement for ``_build_prediction_registry``: the registry is
        instantiated once per engine lifetime (lazily on first call). Each
        cycle:

        - Derives this cycle's candidate specs from arc / pattern / history.
        - Calls ``registry.merge(specs, cycle_n=...)`` — specs already in the
          registry have their ``last_seen_cycle`` bumped and ``run.updated_at``
          refreshed, preserving accumulated ``evidence_score``, ``scenarios_complete``,
          ``prepared_briefing``, ``draft_answer``, ``thinking_traces``, and
          ``file_content_hashes``. New specs are enrolled via the standard
          batch-weight formula.

        Predictions that existed before but aren't in this cycle's batch
        persist untouched — they're invalidated only by signals (see
        ``apply_invalidation_signals``), not by absence-from-cycle.

        Returns ``(registry, category_to_pid, macro_to_pid)`` — the two maps
        are consumed by the frontier seed methods so scenarios get tagged
        with their parent prediction_id.
        """
        ecfg = self.config.exploration
        if self._prediction_registry is None:
            # Pool sized by expected scenarios × rough token cost; fixed at
            # engine-init time because it's a budgeting concept that
            # shouldn't drift once predictions start accumulating state.
            cycle_token_pool = max(512, int(ecfg.frontier_max_size) * 32)
            self._prediction_registry = PredictionRegistry(cycle_token_pool=cycle_token_pool)
        registry = self._prediction_registry
        specs: list[PredictionSpec] = []
        # Routing maps the engine hands to frontier.seed_from_*. Built in-step
        # with spec enrolment so every spec has a mapping entry. When multiple
        # enrolment sources produce the same category/macro, first-wins keeps
        # behaviour deterministic.
        category_to_pid: dict[str, str] = {}
        macro_to_pid: dict[str, str] = {}

        # Arc source — category-level predictions with UX labels.
        if recent_query_text:
            current_category = classify_query_category(recent_query_text[-1])
            descriptions = self._arc_model.describe_next(
                current_category,
                top_k=3,
                recent_queries=recent_query_text,
            )
            for desc in descriptions:
                pid = prediction_id("arc", desc.anchor, desc.label)
                specs.append(
                    PredictionSpec(
                        id=pid,
                        label=desc.label,
                        description=desc.description,
                        source="arc",
                        anchor=desc.anchor,
                        confidence=min(1.0, max(0.0, float(desc.confidence))),
                        hypothesis_type=desc.hypothesis_type,  # type: ignore[arg-type]
                        specificity=desc.specificity,  # type: ignore[arg-type]
                    )
                )
                # First-wins: arc predictions get routing priority over history
                # for the same category, since arc carries explicit confidence.
                category_to_pid.setdefault(desc.category, pid)

        # Pattern source — repeated prompt macros are evidence of recurring intent.
        for macro in prompt_macros[:2]:
            macro_key = str(macro.get("macro_key", "")).strip()
            if not macro_key:
                continue
            use_count = int(macro.get("use_count", 0))
            confidence = min(1.0, float(macro.get("confidence", 0.0)) or (use_count / 10.0))
            category = str(macro.get("category", "understanding"))
            label = f"Recurring: {macro_key[:60]}"
            pid = prediction_id("pattern", macro_key, label)
            specs.append(
                PredictionSpec(
                    id=pid,
                    label=label,
                    description=f"Prompt macro '{macro_key}' ({use_count}x) in category {category}",
                    source="pattern",
                    anchor=macro_key,
                    confidence=confidence,
                    hypothesis_type="likely_next" if confidence >= 0.6 else "possible_branch",
                    specificity="concrete",
                )
            )
            macro_to_pid.setdefault(macro_key, pid)

        # History source — the last observed category as a continuation hint.
        if recent_query_text:
            last_category = classify_query_category(recent_query_text[-1])
            label = f"Continue: {last_category}"
            pid = prediction_id("history", last_category, label)
            specs.append(
                PredictionSpec(
                    id=pid,
                    label=label,
                    description=f"Continuation of the most recent {last_category} turn",
                    source="history",
                    anchor=last_category,
                    confidence=0.4,
                    hypothesis_type="possible_branch",
                    specificity="category",
                )
            )
            category_to_pid.setdefault(last_category, pid)

        # WS7: goal source — active workspace goals seed predictions with
        # long-horizon anchors. Each goal becomes a prediction whose
        # scenarios can accumulate across many cycles (WS6 persistence
        # makes this pay off). Goals are fetched best-effort — a missing
        # table on legacy DBs falls back to the pre-WS7 behaviour.
        try:
            goal_rows = self._active_goals_cache
        except AttributeError:
            goal_rows = []
        for row in goal_rows:
            title = str(row.get("title", "")).strip()
            if not title:
                continue
            confidence = float(row.get("confidence", 0.5))
            label = f"Goal: {title[:60]}"
            anchor = str(row.get("id", "")).strip() or title
            pid = prediction_id("goal", anchor, label)
            specs.append(
                PredictionSpec(
                    id=pid,
                    label=label,
                    description=str(row.get("description", "")) or f"Workspace goal: {title}",
                    source="goal",
                    anchor=anchor,
                    confidence=min(1.0, max(0.0, confidence)),
                    # Long-horizon by nature — a workspace-scale goal is
                    # rarely the literal next prompt, but it's the right
                    # anchor for invested preparation.
                    hypothesis_type="possible_branch",
                    specificity="anchor",
                )
            )

        # 0.8.2 WS2: artefact_item source — intent-bearing artefacts of
        # active goals emit one prediction per pending/in_progress/stalled
        # item. Carries richer anchors (item id + related files) than the
        # goal-level spec above, so scoring can prefer prepared context
        # that touches the user's declared next step. Best-effort — the
        # pre-0.8.2 fallback is the goal-level spec already emitted
        # above.
        specs.extend(self._emit_artefact_item_specs(goal_rows))

        # Deduplicate by id (two sources can collide on identical anchor/label).
        seen_ids: set[str] = set()
        unique_specs: list[PredictionSpec] = []
        for spec in specs:
            if spec.id in seen_ids:
                continue
            seen_ids.add(spec.id)
            unique_specs.append(spec)

        if unique_specs:
            # WS6: merge-per-cycle replaces enroll_batch-per-cycle. Existing
            # predictions keep their accumulated state; brand-new ones get
            # enrolled via the standard weight formula.
            registry.merge(unique_specs, cycle_n=self._precompute_cycles)
        return registry, category_to_pid, macro_to_pid

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
        # WS2.b: look up the parent prediction's remaining token budget so we
        # can clamp max_tokens on the LLM call. Reasoning models need room to
        # think; dense models should be kept tight. The config-level caps
        # (max_response_tokens + reasoning_token_budget) are a ceiling.
        prediction_max_tokens: int | None = None
        parent_pid = scenario.prediction_id
        if parent_pid is not None and self._prediction_registry is not None and parent_pid in self._prediction_registry:
            prompt_obj = self._prediction_registry.get(parent_pid)
            if prompt_obj is not None:
                remaining = max(0, prompt_obj.run.token_budget - prompt_obj.run.tokens_used)
                cfg_cap = int(self.config.backend.max_response_tokens)
                if self.config.backend.reasoning_mode != "off":
                    cfg_cap += int(self.config.backend.reasoning_token_budget)
                prediction_max_tokens = min(cfg_cap, remaining) if remaining > 0 else cfg_cap
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

        # WS2.b: prefer the structured client when the engine was built with
        # one. It captures reasoning-model thinking traces separately, so the
        # JSON-parsing path sees only the content field and never chokes on
        # preambles. Legacy bare-string `self.llm` remains the fallback.
        captured_thinking: str = ""
        try:
            if self.structured_llm is not None:
                response: LLMResponse = await self.structured_llm(prompt, max_tokens=prediction_max_tokens)
                llm_output = response.content
                captured_thinking = response.thinking
            else:
                llm_output = await self.llm(prompt)  # type: ignore[misc]
        except Exception:
            return [], [], "", 0.0

        # Record the thinking trace against the parent prediction (best-effort).
        if captured_thinking and parent_pid is not None and self._prediction_registry is not None:
            async with self._prediction_registry.lock:
                try:
                    self._prediction_registry.attach_artifact(parent_pid, thinking=captured_thinking)
                except (KeyError, ValueError):
                    pass

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

    async def _precompute_predicted_responses(
        self,
        *,
        max_per_cycle: int,
        min_use_count: int,
        deadline: float | None,
        available_paths: list[str],
        recent_queries: list[str],
        prediction_id_for_macro: dict[str, str] | None = None,
    ) -> int:
        """Generate and cache draft responses for the top validated macros.

        This is the "actually do it" half of next-prompt prediction: instead
        of only preparing *context* for a probable prompt, we also let the
        LLM draft the *response*. When the user then sends the expected
        prompt, the cache hit exposes ``predicted_response`` in the
        enrichment so the agent can surface the draft immediately and refine
        from there rather than starting from cold.

        Guards:

        - respects the cycle deadline (checks before each LLM round-trip);
        - skips macros below ``min_use_count`` (unvalidated patterns waste
          budget on drafts that will never match);
        - skips any macro that already has a ``predicted_response`` cached
          for the same macro_key within the current cycle;
        - stops after ``max_per_cycle`` drafts so one cycle can't starve the
          exploration loop of the next one.

        Returns the number of drafts actually generated.
        """
        if max_per_cycle <= 0:
            return 0
        if not callable(self.llm):
            return 0
        import logging as _logging

        _log = _logging.getLogger(__name__)
        try:
            macros = await self.store.list_prompt_macros(limit=25)
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning("Vaner: failed to list prompt macros for predicted response: %s", exc)
            return 0
        qualifying = [m for m in macros if int(m.get("use_count", 0)) >= min_use_count]
        if not qualifying:
            return 0

        artefacts_by_key = {a.key: a for a in await self.store.list(limit=2000)}
        # A tiny pool of recent file_summary artefacts to pin into the
        # predicted-response prompt. These act as the drafting LLM's
        # grounding — we want the draft to reference real code, not
        # hallucinate files that don't exist.
        recent_path_pool: list[str] = []
        for path in available_paths:
            if len(recent_path_pool) >= 12:
                break
            recent_path_pool.append(path)

        generated = 0
        quality_snapshot = await self._metrics_store.memory_quality_snapshot()
        prior_draft_usefulness = float(quality_snapshot.get("draft_usefulness_rate", 0.0))
        for macro in qualifying[:max_per_cycle]:
            if deadline is not None and time.monotonic() >= deadline:
                break
            macro_key = str(macro.get("macro_key", "")).strip()
            example_query = str(macro.get("example_query", macro_key)).strip()
            category = str(macro.get("category", "understanding"))
            if not macro_key:
                continue

            file_summaries: list[str] = []
            for path in recent_path_pool[:6]:
                artefact = artefacts_by_key.get(f"file_summary:{path}")
                if artefact is None:
                    continue
                content = getattr(artefact, "content", "")
                snippet = content[:400].replace("\n", " ")
                file_summaries.append(f"- {path}: {snippet}")
            "\n".join(file_summaries) or "(no artefact summaries available)"
            "\n".join(recent_queries[-5:]) or "(no recent queries)"
            posterior_confidence = min(1.0, float(macro.get("confidence", 0.0)))
            # Fraction of the paths we actually tried to look up that had indexed
            # summaries. Divide by the pool size, not a hardcoded max, so a small
            # repo with all files indexed scores 1.0, not 1/6.
            # When the pool returned nothing at all treat quality as neutral (0.5)
            # so the gate doesn't block on an indexing gap rather than real evidence absence.
            evidence_quality = len(file_summaries) / max(1, len(recent_path_pool)) if file_summaries else 0.5
            evidence_volatility = float(self._cycle_policy_state.get("volatility_score", 0.0))
            draft_budget_min_s = float(self._cycle_policy_state.get("draft_budget_min_ms", 2000.0)) / 1000.0
            has_budget = deadline is None or (deadline - time.monotonic()) >= draft_budget_min_s
            # WS10: gates consolidated into the Drafter.
            if not self._drafter.passes_gates(
                posterior_confidence=posterior_confidence,
                evidence_quality=evidence_quality,
                evidence_volatility=evidence_volatility,
                prior_draft_usefulness=prior_draft_usefulness,
                has_budget=has_budget,
                gates=self._cycle_policy_state,
            ):
                continue

            # Partial regeneration: if a recent draft cached a rewritten prompt
            # for this macro AND volatility is low, reuse the rewrite and skip
            # Stage A — halves LLM cost on stable codebases.
            reuse_rewrite: str | None = None
            if evidence_volatility < 0.2:
                try:
                    prior = await self._cache.match(example_query or macro_key)
                except Exception:
                    prior = None
                prior_enrichment = None
                if prior is not None and prior.tier in {"full_hit", "partial_hit"}:
                    prior_enrichment = getattr(prior, "enrichment", None)
                if isinstance(prior_enrichment, dict):
                    cached_prompt = prior_enrichment.get("predicted_prompt")
                    if isinstance(cached_prompt, str) and cached_prompt.strip():
                        reuse_rewrite = cached_prompt.strip()[:500]

            # WS10: we need a PredictedPrompt to pass to the Drafter so the
            # briefing carries correct provenance (label, anchor, confidence,
            # scenarios_complete, evidence_score). When a real registry
            # entry exists (normal precompute_cycle path) we use it;
            # otherwise (direct test-harness call to this method) we
            # construct a synthetic prompt from the macro fields so the
            # drafting pipeline still runs.
            prompt_obj_for_draft = None
            pid_for_macro: str | None = None
            if prediction_id_for_macro and self._prediction_registry is not None:
                pid_for_macro = prediction_id_for_macro.get(macro_key)
                if pid_for_macro is not None:
                    prompt_obj_for_draft = self._prediction_registry.get(pid_for_macro)
            if prompt_obj_for_draft is None:
                from vaner.intent.prediction import (
                    PredictedPrompt,
                    PredictionArtifacts,
                    PredictionRun,
                    PredictionSpec,
                )

                synthetic_spec = PredictionSpec(
                    id=prediction_id("pattern", macro_key, macro_key),
                    label=f"Recurring: {macro_key[:60]}",
                    description=f"Prompt macro '{macro_key}' in category {category}",
                    source="pattern",
                    anchor=macro_key,
                    confidence=posterior_confidence,
                    hypothesis_type="likely_next" if posterior_confidence >= 0.6 else "possible_branch",
                    specificity="concrete",
                )
                prompt_obj_for_draft = PredictedPrompt(
                    spec=synthetic_spec,
                    run=PredictionRun(weight=0.5, token_budget=2048),
                    artifacts=PredictionArtifacts(),
                )

            draft_result = await self._drafter.draft_for_prediction(
                prompt_obj_for_draft,
                candidate_prompt=example_query or macro_key,
                category=category,
                recent_queries=recent_queries,
                file_summaries=file_summaries,
                available_paths=recent_path_pool[:6],
                reuse_rewrite=reuse_rewrite,
                deadline=deadline,
            )
            if draft_result is None:
                continue
            if reuse_rewrite is not None:
                try:
                    await self._metrics_store.increment_counter("draft_partial_regenerated_total")
                except Exception:
                    pass
            predicted_prompt = draft_result.predicted_prompt
            draft = draft_result.draft_answer or ""
            if not draft:
                continue

            question = f"predicted_response:{macro_key}"
            package, keys = await self._build_package_for_paths(
                question,
                recent_path_pool[:6],
                heuristic_paths=self._last_heuristic_paths,
            )
            enrichment: dict[str, object] = {
                "relevant_keys": keys,
                "source_units": recent_path_pool[:6],
                "anchor_files": recent_path_pool[:6],
                "anchor_units": recent_path_pool[:6],
                "scenario_question": question,
                "predicted_prompt": predicted_prompt,
                "confidence": min(1.0, float(macro.get("confidence", 0.6))),
                "rationale": f"predicted-response draft for macro '{macro_key}' ({macro.get('use_count', 0)}x uses)",
                "exploration_source": "predicted_response",
                "predicted_response": draft[:4000],
                "predicted_response_macro": macro_key,
                "predicted_response_category": category,
            }
            try:
                await self._cache.store_entry(
                    prompt_hint=example_query or macro_key,
                    package=package,
                    enrichment=enrichment,
                )
            except Exception as exc:  # pragma: no cover - defensive
                _log.debug("Vaner: caching predicted response for %r failed: %s", macro_key, exc)
                continue
            generated += 1

            # WS1.f: a successful draft advances the parent prediction through
            # evidence_gathering → drafting → ready and attaches the draft +
            # briefing so ``vaner.predictions.adopt`` returns a usable package.
            # Best-effort: the draft is cached regardless of registry state.
            registry = self._prediction_registry
            if registry is not None and prediction_id_for_macro:
                pid = prediction_id_for_macro.get(macro_key)
                if pid and pid in registry:
                    async with registry.lock:
                        try:
                            prompt_obj = registry.get(pid)
                            if prompt_obj is not None:
                                state = prompt_obj.run.readiness
                                if state == "queued":
                                    registry.transition(pid, "grounding", reason="draft produced")
                                    state = "grounding"
                                if state == "grounding":
                                    registry.transition(
                                        pid,
                                        "evidence_gathering",
                                        reason="draft produced",
                                    )
                                    state = "evidence_gathering"
                                if state == "evidence_gathering":
                                    registry.transition(
                                        pid,
                                        "drafting",
                                        reason=f"macro '{macro_key}' draft cached",
                                    )
                                    state = "drafting"
                                briefing_text = "\n".join(file_summaries) or None
                                # WS6: capture per-path content hashes for the
                                # files this draft/briefing leans on so the
                                # invalidation sweep can spot disk edits and
                                # demote the prediction when the evidence moves.
                                try:
                                    briefing_paths = recent_path_pool[:6]
                                    hashes_now = read_content_hashes(self.config.repo_root, list(briefing_paths))
                                except Exception:
                                    hashes_now = {}
                                registry.attach_artifact(
                                    pid,
                                    draft=draft[:4000],
                                    briefing=briefing_text,
                                    file_content_hashes=hashes_now or None,
                                )
                                if state == "drafting":
                                    registry.transition(
                                        pid,
                                        "ready",
                                        reason="draft + briefing attached",
                                    )
                        except (KeyError, ValueError):
                            # Registry bookkeeping failures must not kill the
                            # draft-caching path — the cache entry is already
                            # persisted above.
                            pass
        return generated

    async def propagate_related_keys(self, source_key: str, depth: int = 2) -> list[str]:
        await self.initialize()
        graph = await self._load_graph()
        return graph.propagate(source_key, depth=depth)

    async def _resume_deep_run_on_restart(self) -> None:
        """Resume / expire / clear the cached Deep-Run session on startup.

        Called from ``initialize()``. Idempotent — flips
        ``_deep_run_loaded`` after the first successful run so subsequent
        ``initialize()`` calls (which happen on every ``observe`` and
        ``prepare``) are no-ops.

        Two cases:

        1. The DB has a session whose ``ends_at`` is in the past — the
           daemon was likely killed during a window. Mark it ``ended``
           with ``cancelled_reason="expired_on_restart"`` and clear the
           cache.
        2. The DB has a session whose ``ends_at`` is still in the
           future — restore it to the in-memory cache. Counters and
           ``status`` are taken from the persisted row verbatim; any
           in-flight policy state (preset bias, locality, etc.) is
           re-applied by the next cycle.
        """

        if self._deep_run_loaded:
            return
        await deep_run_store.close_expired_sessions(self.store.db_path, now=time.time())
        self._active_deep_run_session = await deep_run_store.get_active_session(self.store.db_path)
        # WS2: publish the restored session to the routing singleton +
        # cost-gate state so the router enforces locality / cost from
        # the moment the engine comes up. ``None`` clears both.
        set_active_session_for_routing(self._active_deep_run_session)
        reset_cost_gate(self._active_deep_run_session)
        self._deep_run_loaded = True

    async def start_deep_run(
        self,
        *,
        ends_at: float,
        preset: DeepRunPreset = "balanced",
        focus: DeepRunFocus = "active_goals",
        horizon_bias: DeepRunHorizonBias = "balanced",
        locality: DeepRunLocality = "local_preferred",
        cost_cap_usd: float = 0.0,
        workspace_root: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> DeepRunSession:
        """Start a new Deep-Run session.

        Single-active-session is enforced at the store layer — a second
        concurrent ``start_deep_run`` raises
        :class:`DeepRunActiveSessionExistsError`. Surfaces (CLI / MCP /
        cockpit / desktop) translate that into a stable user-facing
        message.

        ``cost_cap_usd`` defaults to ``0.0``, which means *no remote
        spend permitted* for the session — the safe default. Callers
        wanting cloud spend opt in explicitly per session.
        """

        await self.initialize()
        session = DeepRunSession.new(
            ends_at=ends_at,
            preset=preset,
            focus=focus,
            horizon_bias=horizon_bias,
            locality=locality,
            cost_cap_usd=cost_cap_usd,
            workspace_root=workspace_root or str(self.config.repo_root),
            metadata=metadata,
        )
        await deep_run_store.create_session(self.store.db_path, session)
        self._active_deep_run_session = session
        # WS2: publish to the router + reset the in-memory cost gate
        # so the very next remote-call decision sees the new policy.
        set_active_session_for_routing(session)
        reset_cost_gate(session)
        return session

    async def stop_deep_run(self, *, kill: bool = False, reason: str | None = None) -> DeepRunSummary | None:
        """Stop the currently active Deep-Run session.

        Returns the final :class:`DeepRunSummary`, or ``None`` if no
        session was active. ``kill=True`` records ``status="killed"``
        for the audit trail; the caller is responsible for halting
        in-flight cycles. ``kill=False`` (default) records
        ``status="ended"`` for a clean stop.
        """

        await self.initialize()
        session = self._active_deep_run_session
        if session is None:
            return None
        now = time.time()
        new_status = "killed" if kill else "ended"
        await deep_run_store.update_session_status(
            self.store.db_path,
            session.id,
            status=new_status,
            ended_at=now,
            cancelled_reason=reason if reason is not None else session.cancelled_reason,
        )
        # Refresh from disk so the summary reflects any concurrent
        # counter increments the cycle loop wrote in parallel.
        refreshed = await deep_run_store.get_session(self.store.db_path, session.id)
        self._active_deep_run_session = None
        # WS2: clear the routing singleton + cost gate so subsequent
        # remote calls fall back to the router's normal behaviour.
        set_active_session_for_routing(None)
        reset_cost_gate(None)
        if refreshed is None:
            return None
        return DeepRunSummary.from_session(refreshed)

    async def current_deep_run(self) -> DeepRunSession | None:
        """Return the active Deep-Run session, or ``None``.

        Reads from the in-memory cache. The cache is populated by
        ``initialize()`` / ``start_deep_run`` and cleared by
        ``stop_deep_run``; it is the canonical record every Vaner
        surface should render.
        """

        await self.initialize()
        return self._active_deep_run_session

    async def list_deep_run_sessions(self, *, limit: int = 20) -> list[DeepRunSession]:
        """Recent Deep-Run sessions, newest first. Includes terminated
        sessions for the history surface."""

        await self.initialize()
        return await deep_run_store.list_sessions(self.store.db_path, limit=limit)

    def set_resource_gate_probe(
        self,
        probe: ResourceGateProbe,
        *,
        config: ResourceGateConfig | None = None,
    ) -> None:
        """Inject a platform-specific :class:`ResourceGateProbe`.

        Production daemon wires a psutil-backed probe at startup; tests
        compose with the mutable :class:`NoOpResourceGateProbe`. Safe to
        call at any time — the next gate evaluation uses the new probe.
        """

        self._resource_gate_probe = probe
        if config is not None:
            self._resource_gate_config = config

    async def evaluate_deep_run_gates(self) -> list[DeepRunPauseReason]:
        """Evaluate the resource gates against the current probe.

        If a session is active and the gates report constraints that
        differ from the session's recorded ``pause_reasons``, persist
        the new set and flip the session's status accordingly:

        - non-empty constraints + ``status == 'active'`` ⇒ ``paused``
        - empty constraints + ``status == 'paused'`` ⇒ ``active``
        - same constraints ⇒ no-op (avoids spurious DB writes)

        Returns the current set of pause reasons (empty when the
        session may run, or when no session is active).
        """

        session = self._active_deep_run_session
        if session is None:
            return []
        reasons = evaluate_resource_gates(
            probe=self._resource_gate_probe,
            config=self._resource_gate_config,
        )
        same = sorted(reasons) == sorted(session.pause_reasons)
        if same and ((reasons and session.status == "paused") or (not reasons and session.status == "active")):
            return reasons
        new_status = "paused" if reasons else "active"
        await deep_run_store.update_session_status(
            self.store.db_path,
            session.id,
            status=new_status,
            pause_reasons=reasons,
        )
        session.pause_reasons = list(reasons)
        session.status = new_status
        return reasons

    async def flush_deep_run_cost_to_store(self) -> float | None:
        """Push the in-memory cost-gate spend into the persisted session row.

        Returns the persisted ``spend_usd`` after the flush, or ``None``
        if no session / no cost gate. Engine calls this at end of cycle
        so the cockpit / CLI / desktop see up-to-date durable spend.
        """

        session = self._active_deep_run_session
        if session is None:
            return None
        in_memory = cost_gate_spent_usd()
        if in_memory is None:
            return None
        delta = max(0.0, in_memory - session.spend_usd)
        if delta > 0:
            await deep_run_store.increment_session_counters(self.store.db_path, session.id, spend_usd=delta)
            session.spend_usd = in_memory
        return session.spend_usd

    async def increment_deep_run_cycle_counter(self, *, promoted_count: int = 0) -> None:
        """Advance the active session's ``cycles_run`` (+optional promoted
        count) by one cycle. Called by the engine at the end of every
        cycle that ran while a Deep-Run session was active."""

        session = self._active_deep_run_session
        if session is None:
            return
        await deep_run_store.increment_session_counters(
            self.store.db_path,
            session.id,
            cycles_run=1,
            promoted_count=promoted_count,
        )
        session.cycles_run += 1
        session.promoted_count += promoted_count

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
            # ``source_paths`` is a compat mirror of ``source_units`` written by
            # cache.py for forward/backward compatibility — read from it but
            # don't treat its presence as an error.
            for key in ("anchor_units", "source_units", "anchor_files", "source_paths"):
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
            "source_units": scenario.file_paths,
            "anchor_files": scenario.file_paths,
            "anchor_units": scenario.file_paths,
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

    async def _load_user_profile(self) -> None:
        """Hydrate the in-memory UserProfile from SQLite, migrating legacy JSON if present.

        The JSON file at ``self._user_profile_json_path`` is imported on first run
        and deleted once SQLite has confirmed the data is persisted. If the JSON
        is missing or unreadable, we start from an empty profile.
        """
        if self._user_profile_loaded:
            return
        await self._user_profile_store.migrate_from_json(self._user_profile_json_path)
        self._user_profile = await self._user_profile_store.load()
        self._user_profile_loaded = True

    def _try_load_trained_scorer(self) -> None:
        if self._intent_scorer.model_path is not None:
            return
        if not self._scorer_model_path.exists():
            fallback_model = self._defaults_bundle.search.scorer_model_path
            if fallback_model is None or not fallback_model.exists():
                return
            scorer = IntentScorer(model_path=fallback_model)
            if scorer.model_path is not None:
                influence = (
                    self._defaults_bundle.search.scorer_metadata.model_influence
                    if self._defaults_bundle.search.scorer_metadata is not None
                    else None
                )
                if influence is not None:
                    scorer.set_model_influence(float(influence))
                self._install_calibration(scorer)
                self._intent_scorer = scorer
                self._trainer.scorer = scorer
            return
        scorer = IntentScorer(model_path=self._scorer_model_path)
        if scorer.model_path is not None:
            self._install_calibration(scorer)
            self._intent_scorer = scorer
            self._trainer.scorer = scorer

    def _install_calibration(self, scorer: IntentScorer) -> None:
        """Load the isotonic calibration curve from the defaults bundle, if one ships.

        Fail-closed: on malformed JSON or missing file, the scorer returns
        uncalibrated predictions (same behavior as pre-0.8.0 bundles).
        """
        curve_path = self._defaults_bundle.calibration_curve_path
        if curve_path is None or not curve_path.exists():
            return
        scorer.load_calibration(curve_path)

    async def _grade_draft_at_serve(
        self,
        *,
        prompt: str,
        predicted_prompt: str,
        draft_referenced_paths: set[str],
        served_paths: set[str],
    ) -> tuple[float, bool]:
        """Compute best-effort draft-quality signals at serve time.

        Returns ``(answer_reuse_ratio, directionally_correct)``.

        - **Reuse ratio:** Jaccard of draft-referenced files vs. files actually
          served. High values mean the draft cited the right code.
        - **Directional correctness:** cosine similarity between the embedded
          predicted prompt and the actual prompt (threshold 0.70). If no embed
          callable is available, falls back to token Jaccard >= 0.40.

        We fire these at serve time rather than on feedback because the signal
        is most useful while the user still has the draft in front of them;
        feedback-time grading is handled by ``record_scenario_feedback``.
        """
        reuse_ratio = jaccard_reuse(sorted(draft_referenced_paths), sorted(served_paths))
        directional = False
        if predicted_prompt and prompt:
            if callable(self.embed):
                try:
                    vecs = await self.embed([prompt, predicted_prompt])
                    if vecs and len(vecs) == 2 and vecs[0] and vecs[1]:
                        a, b = vecs[0], vecs[1]
                        dot = sum(x * y for x, y in zip(a, b, strict=False))
                        mag_a = sum(x * x for x in a) ** 0.5
                        mag_b = sum(y * y for y in b) ** 0.5
                        if mag_a > 0.0 and mag_b > 0.0:
                            directional = (dot / (mag_a * mag_b)) >= 0.70
                except Exception:
                    directional = False
            if not directional:
                # Fallback: token Jaccard on the two prompt strings.
                prompt_tokens = {t for t in prompt.lower().split() if t}
                predicted_tokens = {t for t in predicted_prompt.lower().split() if t}
                if prompt_tokens or predicted_tokens:
                    union = prompt_tokens | predicted_tokens
                    if union:
                        directional = (len(prompt_tokens & predicted_tokens) / len(union)) >= 0.40
        return (reuse_ratio, directional)

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

        cycle_state_row = await self.store.get_learning_state("cycle_policy_state")
        if cycle_state_row:
            try:
                stored = cycle_state_row.get("state_json")
                if isinstance(stored, str):
                    parsed = json.loads(stored)
                    if isinstance(parsed, dict):
                        for key, value in parsed.items():
                            if key in self._cycle_policy_state and isinstance(value, (int, float)):
                                self._cycle_policy_state[key] = float(value)
            except Exception:
                pass

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
                "trained_at": now,
            },
        )
        scorer_state = self._intent_scorer.export_metadata()
        scorer_state["trained_at"] = now
        await self.store.upsert_learning_state(key="intent_scorer", value=scorer_state)
        persistent_cycle_keys = {
            "exploit_ratio",
            "hedge_ratio",
            "invest_ratio",
            "no_regret_ratio",
            "breadth_coverage_threshold",
            "deep_drill_priority_threshold",
            "entropy_abstain_threshold",
            "draft_posterior_threshold",
            "draft_evidence_threshold",
            "draft_volatility_ceiling",
            "draft_budget_min_ms",
        }
        cycle_state_to_persist = {k: v for k, v in self._cycle_policy_state.items() if k in persistent_cycle_keys}
        await self.store.upsert_learning_state(
            key="cycle_policy_state",
            value={"state_json": json.dumps(cycle_state_to_persist), "trained_at": now},
        )
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
        macros = self._arc_model.mine_prompt_macros()
        await self.store.replace_prompt_macros(macros)
        scenario_store = ScenarioStore(self.config.repo_root / ".vaner" / "scenarios.db")
        await scenario_store.initialize()
        for macro in macros:
            macro_key = str(macro.get("macro_key", "")).strip()
            if not macro_key:
                continue
            centroid = str(macro.get("category", "understanding"))
            confidence = float(macro.get("confidence", 0.0))
            support = int(macro.get("use_count", 0))
            await scenario_store.upsert_prompt_macro_cluster(
                macro_key=macro_key,
                centroid_label=centroid,
                confidence=confidence,
                support_count=support,
            )
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

            if ecfg.economics_first_routing:
                eligible = sorted(
                    list(ecfg.endpoints),
                    key=lambda entry: (
                        float(getattr(entry, "cost_per_1k_tokens", 0.0)),
                        float(getattr(entry, "latency_p50_ms", 800.0)),
                        -int(getattr(entry, "context_window", 8192)),
                    ),
                )
                if eligible:
                    chosen = eligible[0]
                    single_pool = ExplorationEndpointPool.from_endpoints([chosen])
                    _log.info(
                        "Vaner exploration LLM: economics-first endpoint=%s model=%s cost/1k=%s latency_p50_ms=%s",
                        chosen.url,
                        chosen.model,
                        getattr(chosen, "cost_per_1k_tokens", 0.0),
                        getattr(chosen, "latency_p50_ms", 800.0),
                    )
                    return single_pool
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
