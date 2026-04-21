# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from vaner.cli.commands.config import set_config_value
from vaner.cli.commands.init import init_repo
from vaner.eval import load_cases, run_eval


def test_load_cases_reads_default_cases(temp_repo):
    init_repo(temp_repo)
    set_config_value(temp_repo, "exploration", "embedding_model", "")
    cases = load_cases(temp_repo)
    assert len(cases) >= 1
    assert cases[0].case_id


def test_run_eval_writes_report_file(temp_repo):
    init_repo(temp_repo)
    set_config_value(temp_repo, "exploration", "embedding_model", "")
    report = run_eval(temp_repo)
    assert report.results_path
    assert Path(report.cases_path).name == "default.json"
    assert report.total_elapsed_seconds >= 0.0
    assert report.prepare_elapsed_seconds >= 0.0
