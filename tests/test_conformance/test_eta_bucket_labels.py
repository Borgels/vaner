# SPDX-License-Identifier: Apache-2.0

"""Pin the daemon-side ETA bucket labels against the cross-language
conformance fixtures.

These fixtures are the wire contract every sibling client (Rust,
Swift, TypeScript) decodes against. PR #167 introduced the conformance
fixtures with hyphen-minus glyphs (``~10-20s``); the daemon emits
typographically-correct en-dashes (``~10–20s``) per the 3b.md spec.
This test fails the build the moment those two drift apart.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vaner.intent.readiness import EtaBucket, eta_bucket_label

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "conformance-fixtures"


@pytest.mark.parametrize(
    ("bucket", "expected_label"),
    [
        ("ready_now", "Ready now"),
        ("under_20s", "~10–20s"),
        ("under_1m", "~1 min"),
        ("working", "Working"),
        ("maturing", "Maturing in background"),
    ],
)
def test_eta_bucket_label_canonical(bucket: EtaBucket, expected_label: str) -> None:
    assert eta_bucket_label(bucket) == expected_label


def test_active_sample_fixture_matches_python_labels() -> None:
    fixture = _FIXTURE_DIR / "predictions_active_sample.json"
    if not fixture.exists():
        pytest.skip("conformance fixture missing; run from repo root")
    body = json.loads(fixture.read_text(encoding="utf-8"))
    for entry in body.get("predictions", []):
        bucket = entry.get("eta_bucket")
        label = entry.get("eta_bucket_label")
        if bucket is None or label is None:
            continue
        # Skip the unknown sentinel — fixtures may use it intentionally.
        if bucket == "unknown":
            continue
        assert eta_bucket_label(bucket) == label, (
            f"fixture eta_bucket_label drift for bucket={bucket!r}: fixture={label!r} python={eta_bucket_label(bucket)!r}"
        )


def test_single_sample_fixture_matches_python_labels() -> None:
    fixture = _FIXTURE_DIR / "predictions_single_sample.json"
    if not fixture.exists():
        pytest.skip("conformance fixture missing; run from repo root")
    entry = json.loads(fixture.read_text(encoding="utf-8"))
    bucket = entry.get("eta_bucket")
    label = entry.get("eta_bucket_label")
    if bucket is None or label is None:
        return
    if bucket == "unknown":
        return
    assert eta_bucket_label(bucket) == label
