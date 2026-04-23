# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

_DEFAULTS_DIR = Path(__file__).resolve().parent

logger = logging.getLogger(__name__)

# Bump this when the defaults bundle consumer contract changes in a
# backwards-incompatible way (e.g. fields removed, semantics flipped).
_CURRENT_READER_VERSION = "0.8.0"


class DefaultsIntegrityError(RuntimeError):
    """Raised when a defaults file's SHA256 does not match the manifest."""

    def __init__(self, key: str, expected: str, actual: str) -> None:
        super().__init__(f"checksum mismatch for {key}: expected {expected}, got {actual}")
        self.key = key
        self.expected = expected
        self.actual = actual


class DefaultsVersionError(RuntimeError):
    """Raised when the defaults bundle requires a newer Vaner than the current runtime."""


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for segment in version.strip().split("."):
        digits = ""
        for c in segment:
            if c.isdigit():
                digits += c
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else (0,)


# Module-level log of checksum mismatches encountered in permissive mode.
# The engine can drain these on initialize() and emit telemetry counters.
_MISMATCH_LOG: list[tuple[str, str, str]] = []


def drain_checksum_mismatches() -> list[tuple[str, str, str]]:
    """Return and clear the list of checksum mismatches observed under permissive mode."""
    pending = list(_MISMATCH_LOG)
    _MISMATCH_LOG.clear()
    return pending


# Phase 4 / WS2.c: reasoning-model manifest helpers.

_ReasoningPattern = dict[str, Any]


def load_reasoning_model_patterns() -> list[_ReasoningPattern]:
    """Return the list of reasoning-model prefix patterns from the top
    manifest. Empty list if the manifest lacks the ``reasoning_models``
    section or it's malformed — never raises, so startup paths can rely on it.
    """
    top = _read_json(_DEFAULTS_DIR / "manifest.json") or {}
    models = top.get("reasoning_models") or {}
    patterns_raw = models.get("patterns") or []
    if not isinstance(patterns_raw, list):
        return []
    out: list[_ReasoningPattern] = []
    for entry in patterns_raw:
        if not isinstance(entry, dict):
            continue
        match = str(entry.get("match", "")).strip().lower()
        if not match:
            continue
        out.append(
            {
                "match": match,
                "reasoning_mode": str(entry.get("reasoning_mode", "provider_default")),
                "extra_body": dict(entry.get("extra_body") or {}),
                "notes": str(entry.get("notes", "")),
            }
        )
    return out


def reasoning_defaults_for_model(model: str) -> _ReasoningPattern | None:
    """Look up the best-matching reasoning-model entry for ``model``.

    Matching is case-insensitive substring on the ``match`` pattern.
    Longest pattern wins — so ``qwen3.5-35b-a3b`` beats the broader
    ``qwen3`` prefix when both match. Returns ``None`` when nothing matches
    — callers then fall back to BackendConfig defaults.
    """
    if not model:
        return None
    needle = model.lower()
    best: _ReasoningPattern | None = None
    best_len = -1
    for entry in load_reasoning_model_patterns():
        if entry["match"] in needle and len(entry["match"]) > best_len:
            best = entry
            best_len = len(entry["match"])
    return best


class ArcTransitionsModel(BaseModel):
    schema_version: str | None = None
    future_compat: dict[str, str] = Field(default_factory=dict, alias="__future_compat__")
    categories: list[str] = Field(default_factory=list)
    transitions: dict[str, dict[str, float]] = Field(default_factory=dict)
    phase_affinity: dict[str, dict[str, float]] = Field(default_factory=dict)


class PhaseClassifierModel(BaseModel):
    schema_version: str | None = None
    future_compat: dict[str, str] = Field(default_factory=dict, alias="__future_compat__")
    phases: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    phase_affinity: dict[str, dict[str, float]] = Field(default_factory=dict)


class IntentScorerMetaModel(BaseModel):
    future_compat: dict[str, str] = Field(default_factory=dict, alias="__future_compat__")
    has_model: bool = False
    model_path: str = ""
    backend: str | None = None
    model_influence: float | None = None


class PolicyDefaultsModel(BaseModel):
    future_compat: dict[str, str] = Field(default_factory=dict, alias="__future_compat__")
    score_weights: list[float] = Field(default_factory=list)
    depth_decay_rate: float | None = None
    freshness_half_life: float | None = None
    source_multipliers: dict[str, float] = Field(default_factory=dict)
    layer_multipliers: dict[str, float] = Field(default_factory=dict)
    feedback_hit_rate: float | None = None
    feedback_miss_rate: float | None = None
    branch_priority_decay: float | None = None
    cache_full_hit_path_threshold: float | None = None
    cache_partial_hit_path_threshold: float | None = None
    cache_full_hit_similarity_threshold: float | None = None
    cache_partial_hit_similarity_threshold: float | None = None
    cache_warm_similarity_threshold: float | None = None


@dataclass(slots=True)
class BehaviorPriors:
    arc_transitions: ArcTransitionsModel | None
    phase_classifier: PhaseClassifierModel | None
    category_centroids: dict[str, Any]
    prompt_macros_seed: list[dict[str, Any]]
    habit_transitions_seed: list[dict[str, Any]]


@dataclass(slots=True)
class SearchPriors:
    scorer_model_path: Path | None
    scorer_metadata: IntentScorerMetaModel | None
    skill_path_priors: dict[str, Any]


@dataclass(slots=True)
class DefaultsBundle:
    behavior: BehaviorPriors
    search: SearchPriors
    policy_defaults: PolicyDefaultsModel | None
    draft_gates: dict[str, float]
    calibration_curve_path: Path | None = None


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_from_manifest(group: str, key: str, *, fallback: str) -> Path:
    group_manifest = _read_json(_DEFAULTS_DIR / group / "manifest.json") or {}
    files = group_manifest.get("files", {})
    rel = fallback
    expected_sha = ""
    if isinstance(files, dict):
        entry = files.get(key)
        if isinstance(entry, str):
            rel = str(entry)
        elif isinstance(entry, dict):
            rel = str(entry.get("path", fallback))
            expected_sha = str(entry.get("sha256", "")).strip().lower()
    resolved = (_DEFAULTS_DIR / group / rel).resolve()
    if expected_sha and resolved.exists():
        actual = _sha256(resolved).lower()
        if actual != expected_sha:
            if os.environ.get("VANER_DEFAULTS_ALLOW_MISMATCH", "").strip() == "1":
                logger.warning(
                    "defaults checksum mismatch for %s: expected %s, got %s (continuing under VANER_DEFAULTS_ALLOW_MISMATCH=1)",
                    key,
                    expected_sha,
                    actual,
                )
                _MISMATCH_LOG.append((key, expected_sha, actual))
                return resolved
            raise DefaultsIntegrityError(key=key, expected=expected_sha, actual=actual)
    return resolved


def _enforce_top_manifest_version() -> None:
    top = _read_json(_DEFAULTS_DIR / "manifest.json") or {}
    min_reader = str(top.get("min_reader_version", "")).strip()
    if not min_reader:
        return
    if _version_tuple(min_reader) > _version_tuple(_CURRENT_READER_VERSION):
        raise DefaultsVersionError(
            f"defaults bundle requires vaner >= {min_reader}, running {_CURRENT_READER_VERSION}. "
            f"Upgrade vaner or downgrade the defaults bundle."
        )


def load_defaults_bundle() -> DefaultsBundle:
    _enforce_top_manifest_version()
    arc_path = _resolve_from_manifest("behavior_priors", "arc_transitions", fallback="../arc_transitions.json")
    phase_path = _resolve_from_manifest("behavior_priors", "phase_classifier_weights", fallback="../phase_classifier_weights.json")
    scorer_meta_path = _resolve_from_manifest("search_priors", "intent_scorer_metadata", fallback="../intent_scorer_metadata.json")
    policy_path = _resolve_from_manifest("policy_defaults", "scoring_policy", fallback="../scoring_policy.json")
    draft_gates_path = _resolve_from_manifest("policy_defaults", "draft_gates", fallback="../draft_gates.json")
    category_centroids_path = _resolve_from_manifest("behavior_priors", "category_centroids", fallback="../category_centroids.json")
    skill_path_priors_path = _resolve_from_manifest("search_priors", "skill_path_priors", fallback="../skill_path_priors.json")
    macro_seed_path = _resolve_from_manifest("behavior_priors", "prompt_macros_seed", fallback="../prompt_macros_seed.json")
    habit_seed_path = _resolve_from_manifest("behavior_priors", "habit_transitions_seed", fallback="../habit_transitions_seed.json")

    arc_raw = _read_json(arc_path)
    phase_raw = _read_json(phase_path)
    scorer_meta_raw = _read_json(scorer_meta_path)
    policy_raw = _read_json(policy_path)
    draft_gates_raw = _read_json(draft_gates_path)
    category_centroids_raw = _read_json(category_centroids_path)
    skill_path_priors_raw = _read_json(skill_path_priors_path)
    macro_seed_raw = _read_json(macro_seed_path)
    habit_seed_raw = _read_json(habit_seed_path)

    arc = ArcTransitionsModel.model_validate(arc_raw) if arc_raw else None
    phase = PhaseClassifierModel.model_validate(phase_raw) if phase_raw else None
    scorer_meta = IntentScorerMetaModel.model_validate(scorer_meta_raw) if scorer_meta_raw else None
    policy_defaults = PolicyDefaultsModel.model_validate(policy_raw) if policy_raw else None
    prompt_macros_seed: list[dict[str, Any]] = []
    habit_transitions_seed: list[dict[str, Any]] = []
    if isinstance(macro_seed_raw, dict) and isinstance(macro_seed_raw.get("rows"), list):
        prompt_macros_seed = [row for row in macro_seed_raw.get("rows", []) if isinstance(row, dict)]
    if isinstance(habit_seed_raw, dict) and isinstance(habit_seed_raw.get("rows"), list):
        habit_transitions_seed = [row for row in habit_seed_raw.get("rows", []) if isinstance(row, dict)]
    draft_gates: dict[str, float] = {}
    if isinstance(draft_gates_raw, dict):
        for key in (
            "draft_posterior_threshold",
            "draft_evidence_threshold",
            "draft_volatility_ceiling",
            "draft_historical_threshold",
            "draft_budget_min_ms",
        ):
            try:
                draft_gates[key] = float(draft_gates_raw.get(key, 0.0))
            except (TypeError, ValueError):
                continue

    scorer_model_path: Path | None = None
    if scorer_meta and scorer_meta.model_path:
        candidate = (_DEFAULTS_DIR / scorer_meta.model_path).resolve()
        if candidate.exists():
            scorer_model_path = candidate
    if scorer_model_path is None:
        fallback_model = _resolve_from_manifest("search_priors", "intent_scorer_model", fallback="../intent_scorer.json")
        if fallback_model.exists():
            scorer_model_path = fallback_model

    # Calibration curve is optional. Only exposed when the bundle ships one.
    calibration_curve_path: Path | None = None
    try:
        candidate = _resolve_from_manifest("search_priors", "calibration_curve", fallback="../calibration_curve.json")
        if candidate.exists():
            calibration_curve_path = candidate
    except DefaultsIntegrityError:
        # Checksum mismatch already raised in strict mode; in permissive mode
        # the helper returned the path, so we fall through. Keep the except
        # branch for future defensive paths.
        raise

    return DefaultsBundle(
        behavior=BehaviorPriors(
            arc_transitions=arc,
            phase_classifier=phase,
            category_centroids=category_centroids_raw or {},
            prompt_macros_seed=prompt_macros_seed,
            habit_transitions_seed=habit_transitions_seed,
        ),
        search=SearchPriors(
            scorer_model_path=scorer_model_path,
            scorer_metadata=scorer_meta,
            skill_path_priors=skill_path_priors_raw or {},
        ),
        policy_defaults=policy_defaults,
        draft_gates=draft_gates,
        calibration_curve_path=calibration_curve_path,
    )
