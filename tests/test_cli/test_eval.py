# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os

import pytest

from vaner.cli.commands.config import set_config_value
from vaner.cli.commands.init import init_repo
from vaner.eval import evaluate_repo

if os.name == "nt":
    pytest.skip("eval CLI test is flaky on Windows CI", allow_module_level=True)


def test_eval_report_shape(temp_repo):
    init_repo(temp_repo)
    set_config_value(temp_repo, "exploration", "embedding_model", "")
    report = evaluate_repo(temp_repo)
    assert report.repo_root
    assert report.cases
    assert report.results_path
