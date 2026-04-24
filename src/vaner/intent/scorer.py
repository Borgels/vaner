# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from vaner.broker.selector import score_artefact
from vaner.intent.calibration import IsotonicCalibrator
from vaner.intent.features import feature_vector_for_artefact
from vaner.models.artefact import Artefact

logger = logging.getLogger(__name__)

try:
    import lightgbm as lgb  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    lgb = None

try:
    import xgboost as xgb  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    xgb = None

try:
    from catboost import CatBoostRegressor  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    CatBoostRegressor = None  # type: ignore[assignment]


class IntentScorer:
    def __init__(self, model_path: Path | None = None, *, backend_preference: str = "lightgbm") -> None:
        self.model_path = model_path
        self.backend_preference = backend_preference
        self._booster = None
        self._backend = "heuristic"
        self.model_influence = 0.05
        self.model_influence_max = 0.35
        self.last_train_metrics: dict[str, float | str] = {}
        self._calibrator: IsotonicCalibrator | None = None
        self.calibration_path: Path | None = None
        if model_path is not None:
            self.load_model(model_path)

    def load_calibration(self, calibration_path: Path) -> bool:
        """Load an isotonic calibration curve to apply after model inference.

        Fails closed: on malformed JSON or missing file, the scorer keeps
        returning uncalibrated predictions (same behavior as before).
        Returns True when a calibrator was successfully installed.
        """
        calibrator = IsotonicCalibrator.load(calibration_path)
        if calibrator is None:
            return False
        self._calibrator = calibrator
        self.calibration_path = calibration_path
        return True

    def load_model(self, model_path: Path, *, backend: str | None = None) -> bool:
        if not model_path.exists():
            return False
        requested = (backend or "").strip().lower()
        suffix = model_path.suffix.lower()
        candidates: list[str]
        if requested:
            candidates = [requested]
        elif suffix == ".txt":
            candidates = ["lightgbm"]
        elif suffix in {".json", ".ubj"}:
            candidates = ["xgboost", "lightgbm"]
        elif suffix == ".cbm":
            candidates = ["catboost"]
        else:
            candidates = ["lightgbm", "xgboost", "catboost"]
        for name in candidates:
            loaded = self._try_load_backend(name, model_path)
            if loaded:
                return True
        return False

    def _try_load_backend(self, backend: str, model_path: Path) -> bool:
        if backend == "lightgbm":
            if lgb is None:
                return False
            try:
                self._booster = lgb.Booster(model_file=str(model_path))
                self.model_path = model_path
                self._backend = "lightgbm"
                return True
            except Exception as exc:
                logger.warning("Failed to load LightGBM model '%s': %s", model_path, exc)
                return False
        if backend == "xgboost":
            if xgb is None:
                return False
            try:
                booster = xgb.Booster()
                booster.load_model(str(model_path))
                self._booster = booster
                self.model_path = model_path
                self._backend = "xgboost"
                return True
            except Exception as exc:
                logger.warning("Failed to load XGBoost model '%s': %s", model_path, exc)
                return False
        if backend == "catboost":
            if CatBoostRegressor is None:
                return False
            try:
                model = CatBoostRegressor()
                model.load_model(str(model_path))
                self._booster = model
                self.model_path = model_path
                self._backend = "catboost"
                return True
            except Exception as exc:
                logger.warning("Failed to load CatBoost model '%s': %s", model_path, exc)
                return False
        return False

    def score(self, prompt: str, artefact: Artefact, *, features: dict[str, float] | None = None) -> float:
        base = score_artefact(prompt, artefact)
        if self._booster is None:
            return base
        runtime_features = dict(features or {})
        privacy_zone = str(artefact.metadata.get("privacy_zone", "")).lower()
        corpus_id = str(artefact.metadata.get("corpus_id", "")).lower()
        runtime_features.setdefault("artefact_privacy_private", 1.0 if privacy_zone == "private_local" else 0.0)
        runtime_features.setdefault("artefact_corpus_repo", 1.0 if corpus_id == "repo" else 0.0)
        source = str(artefact.metadata.get("exploration_source", "")).lower()
        runtime_features.setdefault("frontier_source_graph", 1.0 if source == "graph" else 0.0)
        runtime_features.setdefault("frontier_source_arc", 1.0 if source == "arc" else 0.0)
        runtime_features.setdefault("frontier_source_pattern", 1.0 if source == "pattern" else 0.0)
        runtime_features.setdefault("frontier_source_llm_branch", 1.0 if source == "llm_branch" else 0.0)
        vec = feature_vector_for_artefact(runtime_features, artefact)
        prediction = self._predict(vec)
        if self._calibrator is not None:
            prediction = self._calibrator.transform(prediction)
        influence = max(0.0, min(self.model_influence_max, self.model_influence))
        # Guardrail: keep model correction bounded by heuristic scale.
        blended = ((1.0 - influence) * base) + (influence * prediction)
        delta_cap = max(0.25, abs(base) * 0.75)
        delta = max(-delta_cap, min(delta_cap, blended - base))
        return base + delta

    def _predict(self, vector: list[float]) -> float:
        if self._booster is None:
            return 0.0
        if self._backend == "lightgbm":
            try:
                return float(self._booster.predict([vector])[0])
            except Exception:
                self._booster = None
                return 0.0
        if self._backend == "xgboost":
            if xgb is None:
                return 0.0
            try:
                matrix = xgb.DMatrix([vector])
                return float(self._booster.predict(matrix)[0])
            except Exception:
                # Feature-count mismatch when new signals are added before the
                # model is retrained. Disable the booster so subsequent calls
                # fall through to the heuristic scorer without retrying.
                self._booster = None
                return 0.0
        if self._backend == "catboost":
            try:
                return float(self._booster.predict([vector])[0])
            except Exception:
                self._booster = None
                return 0.0
        return 0.0

    def set_model_influence(self, value: float) -> None:
        self.model_influence = max(0.0, min(self.model_influence_max, float(value)))

    @staticmethod
    def confidence(scores: list[float]) -> float:
        if not scores:
            return 0.0
        ordered = sorted(scores, reverse=True)
        if len(ordered) == 1:
            return min(1.0, max(0.0, ordered[0]))
        gap = ordered[0] - ordered[1]
        return max(0.0, min(1.0, 0.5 + gap / 4.0))

    def train(
        self,
        train_vectors: list[list[float]],
        labels: list[float],
        *,
        output_path: Path,
        backend: str | None = None,
        random_seed: int = 42,
        valid_vectors: list[list[float]] | None = None,
        valid_labels: list[float] | None = None,
        num_threads: int = 0,
    ) -> Path | None:
        if not train_vectors:
            return None
        selected = (backend or self.backend_preference or "lightgbm").strip().lower()
        # Backward-compatible alias.
        if selected == "auto":
            selected = "xgboost" if xgb is not None else "lightgbm"
        if selected == "xgboost":
            path = self._train_xgboost(train_vectors, labels, output_path=output_path, random_seed=random_seed, num_threads=num_threads)
        elif selected == "catboost":
            path = self._train_catboost(train_vectors, labels, output_path=output_path, random_seed=random_seed, num_threads=num_threads)
        else:
            path = self._train_lightgbm(train_vectors, labels, output_path=output_path, random_seed=random_seed, num_threads=num_threads)
        if path is None and selected != "lightgbm":
            path = self._train_lightgbm(
                train_vectors,
                labels,
                output_path=output_path.with_suffix(".txt"),
                random_seed=random_seed,
                num_threads=num_threads,
            )
        if path is None:
            return None
        self.last_train_metrics = self._eval_metrics(valid_vectors or [], valid_labels or [])
        self.last_train_metrics["backend"] = self._backend
        return path

    def _train_lightgbm(
        self,
        train_vectors: list[list[float]],
        labels: list[float],
        *,
        output_path: Path,
        random_seed: int,
        num_threads: int,
    ) -> Path | None:
        if lgb is None:
            return None
        matrix = np.asarray([[float(v) for v in row] for row in train_vectors], dtype=float)
        y = np.asarray([float(v) for v in labels], dtype=float)
        dataset = lgb.Dataset(matrix, label=y)
        booster = lgb.train(
            {
                "objective": "regression",
                "metric": "l2",
                "learning_rate": 0.05,
                "num_leaves": 31,
                "feature_fraction_seed": random_seed,
                "bagging_seed": random_seed,
                "seed": random_seed,
                "num_threads": num_threads if num_threads > 0 else -1,
                "verbose": -1,
            },
            dataset,
            num_boost_round=80,
        )
        booster.save_model(str(output_path))
        self._booster = booster
        self.model_path = output_path
        self._backend = "lightgbm"
        return output_path

    def _train_xgboost(
        self,
        train_vectors: list[list[float]],
        labels: list[float],
        *,
        output_path: Path,
        random_seed: int,
        num_threads: int,
    ) -> Path | None:
        if xgb is None:
            return None
        matrix = xgb.DMatrix(train_vectors, label=labels)
        booster = xgb.train(
            {
                "objective": "reg:squarederror",
                "eta": 0.05,
                "max_depth": 6,
                "subsample": 1.0,
                "colsample_bytree": 1.0,
                "seed": random_seed,
                "tree_method": "hist",
                "nthread": num_threads if num_threads > 0 else 0,
            },
            matrix,
            num_boost_round=100,
        )
        booster.save_model(str(output_path))
        self._booster = booster
        self.model_path = output_path
        self._backend = "xgboost"
        return output_path

    def _train_catboost(
        self,
        train_vectors: list[list[float]],
        labels: list[float],
        *,
        output_path: Path,
        random_seed: int,
        num_threads: int,
    ) -> Path | None:
        if CatBoostRegressor is None:
            return None
        model = CatBoostRegressor(
            loss_function="RMSE",
            iterations=120,
            learning_rate=0.05,
            depth=6,
            random_seed=random_seed,
            thread_count=num_threads if num_threads > 0 else -1,
            verbose=False,
        )
        model.fit(train_vectors, labels)
        model.save_model(str(output_path))
        self._booster = model
        self.model_path = output_path
        self._backend = "catboost"
        return output_path

    def _eval_metrics(self, vectors: list[list[float]], labels: list[float]) -> dict[str, float]:
        if not vectors or not labels:
            return {"mae": 0.0, "baseline_mae": 0.0, "improvement": 0.0}
        preds = [self._predict(vec) for vec in vectors]
        mean_label = sum(labels) / len(labels)
        mae = sum(abs(pred - label) for pred, label in zip(preds, labels, strict=False)) / len(labels)
        baseline_mae = sum(abs(mean_label - label) for label in labels) / len(labels)
        improvement = baseline_mae - mae
        return {"mae": round(mae, 6), "baseline_mae": round(baseline_mae, 6), "improvement": round(improvement, 6)}

    def export_metadata(self) -> dict[str, object]:
        return {
            "has_model": self._booster is not None,
            "model_path": str(self.model_path) if self.model_path is not None else None,
            "backend": self._backend if self._booster is not None else "heuristic",
            "backend_preference": self.backend_preference,
            "model_influence": self.model_influence,
            "model_influence_max": self.model_influence_max,
            "train_metrics": dict(self.last_train_metrics),
        }

    def export_metadata_json(self) -> str:
        return json.dumps(self.export_metadata(), sort_keys=True)
