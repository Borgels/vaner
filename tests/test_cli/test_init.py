# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.cli.commands.init import init_repo


def test_init_creates_config(temp_repo):
    config_path = init_repo(temp_repo)
    assert config_path.exists()
