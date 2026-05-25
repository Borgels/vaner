# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def bundle_rejection_reason(bundle_dir: Path | str, *, allow_experimental_data: bool = False) -> str | None:
    """Return why a training bundle should not be installed by default."""

    bundle_path = Path(bundle_dir)
    manifest = _load_json(bundle_path / "manifest.json")
    metrics = manifest.get("training_metrics")
    if isinstance(metrics, dict):
        if metrics.get("rejected_for_threshold") is True:
            return "bundle was rejected by the training promotion threshold"
        if metrics.get("trained") is False:
            return "bundle manifest says the scorer was not promoted"
    validation = manifest.get("finance_validation")
    if isinstance(validation, dict) and validation.get("passed") is False:
        return "bundle finance temporal validation did not pass"
    governance = manifest.get("dataset_governance")
    if (
        isinstance(governance, dict)
        and governance.get("release_eligible") is False
        and not allow_experimental_data
    ):
        return "bundle dataset governance is not release eligible"

    metadata = _load_json(bundle_path / "intent_scorer_metadata.json")
    if str(metadata.get("reason", "")).strip().lower() == "rejected":
        return "bundle scorer metadata marks the model as rejected"
    return None


def bundle_public_summary(bundle_dir: Path | str) -> dict[str, Any]:
    """Small non-sensitive summary for CLI/status output."""

    bundle_path = Path(bundle_dir)
    manifest = _load_json(bundle_path / "manifest.json")
    metrics = manifest.get("training_metrics") if isinstance(manifest.get("training_metrics"), dict) else {}
    quality = manifest.get("training_quality") if isinstance(manifest.get("training_quality"), dict) else {}
    return {
        "bundle_id": manifest.get("bundle_id") or bundle_path.name,
        "created_at": manifest.get("created_at"),
        "feature_schema_version": manifest.get("feature_schema_version"),
        "records_used": manifest.get("records_used"),
        "scorer_backend": manifest.get("scorer_backend"),
        "trained": metrics.get("trained") if isinstance(metrics, dict) else None,
        "rejected_for_threshold": metrics.get("rejected_for_threshold") if isinstance(metrics, dict) else None,
        "mae": metrics.get("mae") if isinstance(metrics, dict) else None,
        "baseline_mae": metrics.get("baseline_mae") if isinstance(metrics, dict) else None,
        "improvement": metrics.get("improvement") if isinstance(metrics, dict) else None,
        "feature_coverage_pct": quality.get("feature_coverage_pct") if isinstance(quality, dict) else None,
        "release_eligible": (
            manifest.get("dataset_governance", {}).get("release_eligible")
            if isinstance(manifest.get("dataset_governance"), dict)
            else None
        ),
        "finance_temporal_passed": (
            manifest.get("finance_validation", {}).get("passed") if isinstance(manifest.get("finance_validation"), dict) else None
        ),
        "finance_temporal_improvement": (
            manifest.get("finance_validation", {}).get("improvement")
            if isinstance(manifest.get("finance_validation"), dict)
            else None
        ),
        "rejection_reason": bundle_rejection_reason(bundle_path),
        "experimental_data_installable": bundle_rejection_reason(bundle_path, allow_experimental_data=True) is None,
    }
