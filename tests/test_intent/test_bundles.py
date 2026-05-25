from __future__ import annotations

import json
from pathlib import Path

from vaner.intent.bundles import bundle_public_summary, bundle_rejection_reason


def _write_bundle(
    path: Path,
    *,
    temporal_passed: bool | None = None,
    release_eligible: bool | None = None,
) -> None:
    governance = {}
    if release_eligible is not None:
        governance = {"release_eligible": release_eligible}
    path.mkdir()
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "bundle_id": path.name,
                "training_metrics": {"trained": True},
                "finance_validation": (
                    {"passed": temporal_passed, "improvement": 0.0, "min_improvement": 0.005}
                    if temporal_passed is not None
                    else {}
                ),
                "dataset_governance": governance,
            }
        ),
        encoding="utf-8",
    )


def test_bundle_rejection_blocks_failed_finance_temporal_validation(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    _write_bundle(bundle, temporal_passed=False)

    assert bundle_rejection_reason(bundle) == "bundle finance temporal validation did not pass"
    assert bundle_public_summary(bundle)["finance_temporal_passed"] is False


def test_governance_pending_bundle_can_be_allowed_for_experimental_local_install(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    _write_bundle(bundle, temporal_passed=True, release_eligible=False)

    assert bundle_rejection_reason(bundle) == "bundle dataset governance is not release eligible"
    assert bundle_rejection_reason(bundle, allow_experimental_data=True) is None
    summary = bundle_public_summary(bundle)
    assert summary["release_eligible"] is False
    assert summary["experimental_data_installable"] is True
