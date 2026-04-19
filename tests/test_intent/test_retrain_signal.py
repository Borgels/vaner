# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.intent.trainer import RetrainSignal


def test_no_retrain_when_busy() -> None:
    signal = RetrainSignal(baseline_mae=0.1, min_new_samples=1)
    for _ in range(30):
        signal.observe("repo-a", pred=0.9, label=0.1)
    decision = signal.should_retrain(idle_duration_s=0.0, dist_kl={"repo-a": 1.0})
    assert decision.should_retrain is False
    assert decision.reason == "not_idle"


def test_retrain_fires_on_mae_and_kl() -> None:
    signal = RetrainSignal(baseline_mae=0.1, min_new_samples=5, retrain_cooldown_s=0.1)
    for _ in range(120):
        signal.observe("repo-a", pred=0.95, label=0.05)
    decision = signal.should_retrain(idle_duration_s=1.0, dist_kl={"repo-a": 0.6})
    assert decision.should_retrain is True
    assert decision.bucket == "repo-a"


def test_retrain_skips_tiny_buckets() -> None:
    signal = RetrainSignal(baseline_mae=0.1, min_bucket_samples=20, min_new_samples=1)
    for _ in range(5):
        signal.observe("repo-a", pred=0.9, label=0.0)
    decision = signal.should_retrain(idle_duration_s=10.0, dist_kl={"repo-a": 1.0})
    assert decision.should_retrain is False
