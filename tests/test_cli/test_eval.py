# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.cli.commands.config import set_config_value
from vaner.cli.commands.init import init_repo
from vaner.eval import evaluate_repo


def test_eval_report_shape(temp_repo):
    init_repo(temp_repo)
    set_config_value(temp_repo, "exploration", "embedding_model", "")
    report = evaluate_repo(temp_repo)
    assert report.repo_root
    assert report.cases
    assert report.results_path
