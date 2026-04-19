# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.eval import evaluate_repo


def test_eval_report_shape(temp_repo):
    report = evaluate_repo(temp_repo)
    assert report.repo_root
    assert report.cases
    assert report.results_path
