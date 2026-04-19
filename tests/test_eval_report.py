# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.eval import evaluate_repo


def test_evaluate_repo_returns_report(temp_repo):
    report = evaluate_repo(temp_repo)
    assert 0.0 <= report.overall_hit_at_3 <= 1.0
    assert 0.0 <= report.overall_path_coverage <= 1.0
    assert report.total_elapsed_seconds >= 0.0
    assert report.prepare_elapsed_seconds >= 0.0
    assert all(case.elapsed_seconds >= 0.0 for case in report.cases)
    assert len(report.cases) >= 1
