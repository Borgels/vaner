# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner import api
from vaner.cli.commands.config import set_config_value
from vaner.cli.commands.init import init_repo


def test_api_prepare_and_query(temp_repo):
    init_repo(temp_repo)
    set_config_value(temp_repo, "exploration", "embedding_model", "")
    written = api.prepare(temp_repo)
    assert written >= 1

    package = api.query("explain sample module", temp_repo)
    assert package.token_used <= package.token_budget
    assert package.selections


def test_api_inspect_and_forget(temp_repo):
    api.prepare(temp_repo)
    inspect_output = api.inspect(temp_repo)
    assert "file_summary:" in inspect_output

    removed = api.forget(temp_repo)
    assert removed >= 1
