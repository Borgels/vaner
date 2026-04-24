# SPDX-License-Identifier: Apache-2.0
"""0.8.2 WS1 — classifier evaluation against the labeled fixture corpus.

Enforces the spec §14.1 ship gates:

- Per-domain **precision ≥ 0.80** (developer / writer / researcher / planner)
- Per-domain **recall ≥ 0.70**
- Aggregate **precision ≥ 0.85** and **recall ≥ 0.70**
- **Cross-domain coverage**: ≥20 positive fixtures per domain

Aggregate passing is not sufficient — every domain must independently
clear its gate. If any domain falls below, the release does not ship
until the fixture corpus is rebalanced or the classifier tuning is
fixed.

The fixture manifest lives at
``tests/fixtures/intent_artefacts/manifest.json`` and is the authoritative
labeled corpus. New fixtures land there; this test re-evaluates
automatically.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from vaner.intent.adapter import RawArtefact
from vaner.intent.ingest.classifier import classify_structural

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "intent_artefacts" / "manifest.json"

# Spec §14.1 per-domain gates.
PER_DOMAIN_PRECISION_GATE = 0.80
PER_DOMAIN_RECALL_GATE = 0.70
AGGREGATE_PRECISION_GATE = 0.85
AGGREGATE_RECALL_GATE = 0.70
POSITIVE_FIXTURE_FLOOR_PER_DOMAIN = 20


@pytest.fixture(scope="module")
def fixtures() -> list[dict]:
    """Load the labeled fixture corpus once per module."""

    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return list(payload["fixtures"])


def _evaluate(fixtures: list[dict]) -> dict:
    """Run the classifier on every fixture; return per-domain + aggregate
    precision / recall plus the confusion matrix."""

    per_domain: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
    confusion: dict[str, list[str]] = defaultdict(list)

    for fx in fixtures:
        raw = RawArtefact(
            source_uri=f"fixture://{fx['name']}",
            connector="fixture",
            tier="T1",
            text=fx["text"],
            last_modified=0.0,
            title_hint=fx.get("title_hint"),
        )
        result = classify_structural(raw)
        is_positive_label = fx["label"] == "intent_bearing"
        predicted = result.is_intent_bearing
        domain = fx["domain"]
        if is_positive_label and predicted:
            per_domain[domain]["tp"] += 1
        elif is_positive_label and not predicted:
            per_domain[domain]["fn"] += 1
            confusion["false_negatives"].append(f"{domain}/{fx['name']} conf={result.confidence:.2f}")
        elif not is_positive_label and predicted:
            per_domain[domain]["fp"] += 1
            confusion["false_positives"].append(f"{domain}/{fx['name']} conf={result.confidence:.2f}")
        else:
            per_domain[domain]["tn"] += 1

    domain_stats = {}
    agg_tp = agg_fp = agg_fn = 0
    for domain, counts in per_domain.items():
        tp = counts["tp"]
        fp = counts["fp"]
        fn = counts["fn"]
        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / (tp + fn) if (tp + fn) else 1.0
        domain_stats[domain] = {
            "precision": precision,
            "recall": recall,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": counts["tn"],
        }
        agg_tp += tp
        agg_fp += fp
        agg_fn += fn

    aggregate_precision = agg_tp / (agg_tp + agg_fp) if (agg_tp + agg_fp) else 1.0
    aggregate_recall = agg_tp / (agg_tp + agg_fn) if (agg_tp + agg_fn) else 1.0

    return {
        "per_domain": domain_stats,
        "aggregate_precision": aggregate_precision,
        "aggregate_recall": aggregate_recall,
        "confusion": dict(confusion),
    }


def test_fixture_corpus_has_minimum_positive_coverage_per_domain(fixtures: list[dict]) -> None:
    """Spec §14.1 cross-domain coverage gate: ≥20 positive fixtures per
    domain. Guards the ship posture even if future fixture pruning
    accidentally drops a domain below the floor."""

    positives: Counter[str] = Counter()
    for fx in fixtures:
        if fx["label"] == "intent_bearing":
            positives[fx["domain"]] += 1

    expected_domains = {"developer", "writer", "researcher", "planner"}
    assert expected_domains.issubset(positives.keys()), f"fixture corpus missing domains: {expected_domains - positives.keys()}"
    for domain in expected_domains:
        assert positives[domain] >= POSITIVE_FIXTURE_FLOOR_PER_DOMAIN, (
            f"{domain}: only {positives[domain]} positive fixtures (floor: {POSITIVE_FIXTURE_FLOOR_PER_DOMAIN})"
        )


def test_classifier_per_domain_precision_gate(fixtures: list[dict]) -> None:
    """Spec §14.1: per-domain precision ≥ 0.80 — hard ship gate."""

    results = _evaluate(fixtures)
    failures: list[str] = []
    for domain, stats in results["per_domain"].items():
        if stats["precision"] < PER_DOMAIN_PRECISION_GATE:
            failures.append(f"{domain}: precision={stats['precision']:.2f} (tp={stats['tp']} fp={stats['fp']} fn={stats['fn']})")
    assert not failures, (
        "per-domain precision gate failed:\n  "
        + "\n  ".join(failures)
        + "\n\nfalse positives:\n  "
        + "\n  ".join(results["confusion"].get("false_positives", []))
    )


def test_classifier_per_domain_recall_gate(fixtures: list[dict]) -> None:
    """Spec §14.1: per-domain recall ≥ 0.70 — hard ship gate."""

    results = _evaluate(fixtures)
    failures: list[str] = []
    for domain, stats in results["per_domain"].items():
        if stats["recall"] < PER_DOMAIN_RECALL_GATE:
            failures.append(f"{domain}: recall={stats['recall']:.2f} (tp={stats['tp']} fp={stats['fp']} fn={stats['fn']})")
    assert not failures, (
        "per-domain recall gate failed:\n  "
        + "\n  ".join(failures)
        + "\n\nfalse negatives:\n  "
        + "\n  ".join(results["confusion"].get("false_negatives", []))
    )


def test_classifier_aggregate_precision_gate(fixtures: list[dict]) -> None:
    """Aggregate precision ≥ 0.85."""

    results = _evaluate(fixtures)
    assert results["aggregate_precision"] >= AGGREGATE_PRECISION_GATE, (
        f"aggregate precision {results['aggregate_precision']:.3f} below gate {AGGREGATE_PRECISION_GATE:.2f}"
    )


def test_classifier_aggregate_recall_gate(fixtures: list[dict]) -> None:
    """Aggregate recall ≥ 0.70."""

    results = _evaluate(fixtures)
    assert results["aggregate_recall"] >= AGGREGATE_RECALL_GATE, (
        f"aggregate recall {results['aggregate_recall']:.3f} below gate {AGGREGATE_RECALL_GATE:.2f}"
    )
