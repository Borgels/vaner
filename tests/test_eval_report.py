# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.eval import evaluate_repo


def test_evaluate_repo_returns_report(temp_repo):
    report = evaluate_repo(temp_repo)
    assert 0.0 <= report.overall_score <= 1.0
    assert len(report.cases) >= 1
