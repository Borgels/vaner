# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from vaner.intent.scorer import IntentScorer
from vaner.learning.reward import RewardInput, compute_reward
from vaner.store.artefacts import ArtefactStore

FEATURE_SCHEMA_VERSION = "v4"
FEATURE_KEYS: tuple[str, ...] = (
    "signal_count_recent_15m",
    "query_count_total",
    "hypothesis_count",
    "quality_issue_count",
    "relationship_edge_count",
    "relationship_degree",
    "hypothesis_prompt_overlap",
    "prompt_macro_support",
    "prompt_macro_confidence",
    "habit_transition_support",
    "workflow_phase_known",
    "artefact_privacy_private",
    "artefact_corpus_repo",
    "pin_focus_match",
    "pin_avoid_match",
    "frontier_source_graph",
    "frontier_source_arc",
    "frontier_source_pattern",
    "frontier_source_llm_branch",
    "policy_signal_graph",
    "policy_signal_arc",
    "policy_signal_coverage_gap",
    "policy_signal_pattern",
    "policy_signal_freshness",
    "skill_presence",
    "skill_kind_match",
    "follow_up_offer_strength",
    "access_count",
    "artefact_age_seconds",
)


@dataclass(slots=True)
class TrainingConfig:
    online_update: bool = True
    batch_cadence: str = "nightly"
    replay_batch_size: int = 256
    replay_priority_alpha: float = 0.6
    holdout_fraction: float = 0.2
    promotion_threshold: float = 0.01
    distillation_enabled: bool = True
    distillation_start_after_queries: int = 500
    transfer_export_enabled: bool = False
    gpu_device: str = "auto"
    random_seed: int = 42
    scorer_backend: str = "lightgbm"
    strict_feature_schema: bool = True
    num_threads: int = 0


class IntentTrainer:
    def __init__(self, store: ArtefactStore, scorer: IntentScorer, *, config: TrainingConfig | None = None) -> None:
        self.store = store
        self.scorer = scorer
        self.config = config or TrainingConfig()
        self.last_train_metrics: dict[str, float | str | int | bool] = {}

    async def online_update(
        self,
        *,
        prompt: str,
        tier: str,
        similarity: float,
        quality_lift: float,
        host_outcome: float | None = None,
        judge_score: float | None = None,
        feature_snapshot: dict[str, float] | None = None,
        access_count: float = 0.0,
        artefact_age_seconds: float = 0.0,
        latency_ms: float | None = None,
    ) -> None:
        if not self.config.online_update:
            return
        reward = compute_reward(
            RewardInput(
                cache_tier=tier,
                similarity=similarity,
                quality_lift=quality_lift,
                host_outcome=host_outcome,
                judge_score=judge_score,
                latency_ms=latency_ms,
            )
        )
        # Keep low-reward entries hot in replay so we learn from misses.
        priority = 1.0 + ((1.0 - reward.reward_total) * 0.5)
        payload: dict[str, object] = {
            "prompt": prompt,
            "tier": tier,
            "similarity": similarity,
            "quality_lift": quality_lift,
            "reward_total": reward.reward_total,
            "reward_components": reward.reward_components,
            "access_count": float(access_count),
            "artefact_age_seconds": float(artefact_age_seconds),
        }
        if feature_snapshot:
            payload["feature_snapshot"] = feature_snapshot
        await self.store.insert_replay_entry(
            payload=payload,
            priority=priority,
        )

    async def train_batch(self, output_dir: Path) -> Path | None:
        output_dir.mkdir(parents=True, exist_ok=True)
        samples = await self.store.sample_replay_entries(limit=self.config.replay_batch_size)
        if not samples:
            return None

        train_vectors: list[list[float]] = []
        labels: list[float] = []
        rolled_over_rows = 0
        for sample in samples:
            payload = sample.get("payload", {})
            if not isinstance(payload, dict):
                continue
            snapshot = payload.get("feature_snapshot", {})
            feature_snapshot = snapshot if isinstance(snapshot, dict) else {}
            if "follow_up_offer_strength" not in feature_snapshot:
                rolled_over_rows += 1
            vector = [float(feature_snapshot.get(name, 0.0)) for name in FEATURE_KEYS[:-2]]
            vector.append(float(payload.get("access_count", 0.0)))
            vector.append(float(payload.get("artefact_age_seconds", 0.0)))
            if self.config.strict_feature_schema and len(vector) != len(FEATURE_KEYS):
                continue
            train_vectors.append(vector)
            reward_total = float(payload.get("reward_total", 0.0))
            labels.append((max(-1.0, min(1.0, reward_total)) + 1.0) * 0.5)

        if not train_vectors:
            self.last_train_metrics = {"trained": False, "reason": "no_vectors"}
            return None

        holdout_count = int(len(train_vectors) * max(0.0, min(0.4, self.config.holdout_fraction)))
        if holdout_count >= len(train_vectors):
            holdout_count = max(0, len(train_vectors) - 1)
        rng = random.Random(self.config.random_seed)
        indices = list(range(len(train_vectors)))
        rng.shuffle(indices)
        valid_idx = set(indices[:holdout_count])
        train_x: list[list[float]] = []
        train_y: list[float] = []
        valid_x: list[list[float]] = []
        valid_y: list[float] = []
        for idx, vec in enumerate(train_vectors):
            if idx in valid_idx:
                valid_x.append(vec)
                valid_y.append(labels[idx])
            else:
                train_x.append(vec)
                train_y.append(labels[idx])

        backend = self.config.scorer_backend
        extension = ".txt"
        if backend == "xgboost":
            extension = ".json"
        elif backend == "catboost":
            extension = ".cbm"
        model_path = output_dir / f"intent_scorer{extension}"
        trained_path = self.scorer.train(
            train_x,
            train_y,
            output_path=model_path,
            backend=backend,
            random_seed=self.config.random_seed,
            valid_vectors=valid_x,
            valid_labels=valid_y,
            num_threads=self.config.num_threads,
        )
        metrics = dict(self.scorer.last_train_metrics)
        metrics["feature_schema_version"] = FEATURE_SCHEMA_VERSION
        metrics["feature_schema_rolled_over"] = rolled_over_rows > 0
        metrics["feature_schema_rolled_over_rows"] = rolled_over_rows
        metrics["train_rows"] = len(train_x)
        metrics["valid_rows"] = len(valid_x)
        self.last_train_metrics = metrics
        if not trained_path:
            return None
        improvement = float(metrics.get("improvement", 0.0))
        if valid_x and improvement < self.config.promotion_threshold:
            self.last_train_metrics["trained"] = False
            self.last_train_metrics["rejected_for_threshold"] = True
            self.last_train_metrics["promotion_threshold"] = self.config.promotion_threshold
            return None
        self.last_train_metrics["trained"] = True
        return trained_path


@dataclass(slots=True)
class RetrainDecision:
    should_retrain: bool
    reason: str
    bucket: str | None = None


class RetrainSignal:
    """Heuristic retrain trigger based on recent error and drift."""

    def __init__(
        self,
        *,
        baseline_mae: float,
        min_new_samples: int = 50,
        min_bucket_samples: int = 10,
        retrain_cooldown_s: float = 900.0,
    ) -> None:
        self.baseline_mae = float(baseline_mae)
        self.min_new_samples = int(min_new_samples)
        self.min_bucket_samples = int(min_bucket_samples)
        self.retrain_cooldown_s = float(retrain_cooldown_s)
        self._bucket_errors: dict[str, list[float]] = {}
        self._new_samples = 0
        self._last_retrain_at = 0.0

    def observe(self, bucket: str, *, pred: float, label: float) -> None:
        err = abs(float(pred) - float(label))
        self._bucket_errors.setdefault(bucket, []).append(err)
        self._new_samples += 1

    def should_retrain(self, *, idle_duration_s: float, dist_kl: dict[str, float]) -> RetrainDecision:
        if idle_duration_s <= 0.0:
            return RetrainDecision(False, "not_idle")
        if self._new_samples < self.min_new_samples:
            return RetrainDecision(False, "insufficient_new_samples")
        if idle_duration_s < self.retrain_cooldown_s and self._last_retrain_at > 0.0:
            return RetrainDecision(False, "cooldown")

        candidate_bucket: str | None = None
        candidate_score = 0.0
        for bucket, errors in self._bucket_errors.items():
            if len(errors) < self.min_bucket_samples:
                continue
            mae = sum(errors) / len(errors)
            kl = float(dist_kl.get(bucket, 0.0))
            score = max(0.0, mae - self.baseline_mae) + kl
            if score > candidate_score:
                candidate_bucket = bucket
                candidate_score = score

        if candidate_bucket is None:
            return RetrainDecision(False, "no_eligible_bucket")
        if candidate_score <= 0.0:
            return RetrainDecision(False, "below_threshold")

        self._last_retrain_at = idle_duration_s
        self._new_samples = 0
        self._bucket_errors.clear()
        return RetrainDecision(True, "triggered", bucket=candidate_bucket)
