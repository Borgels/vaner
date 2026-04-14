# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.cli.commands.daemon import daemon_status, start_daemon


def test_start_daemon_once_runs_prepare(temp_repo):
    written = start_daemon(temp_repo, once=True)
    assert written >= 1
    assert daemon_status(temp_repo) == "stopped"
