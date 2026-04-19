# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from vaner.eval import evaluate_repo

pytest.importorskip("sentence_transformers")
pytestmark = pytest.mark.integration


def test_eval_report_shape(temp_repo):
    report = evaluate_repo(temp_repo)
    assert report.repo_root
    assert report.cases
    assert report.results_path
