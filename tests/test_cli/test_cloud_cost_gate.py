# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from vaner.cli.commands.app import app

runner = CliRunner()

if "--accept-cloud-costs" not in runner.invoke(app, ["init", "--help"]).stdout:
    pytest.skip("cloud-cost gate flags unavailable on this branch surface", allow_module_level=True)


def test_init_cloud_backend_requires_explicit_ack_non_interactive(temp_repo) -> None:
    result = runner.invoke(
        app,
        [
            "init",
            "--path",
            str(temp_repo),
            "--backend-preset",
            "openai",
            "--no-interactive",
        ],
    )
    assert result.exit_code == 1
    assert "requires explicit acknowledgement" in result.stderr
    assert "--accept-cloud-costs" in result.stderr


def test_init_cloud_backend_accepts_when_flag_provided(temp_repo) -> None:
    result = runner.invoke(
        app,
        [
            "init",
            "--path",
            str(temp_repo),
            "--backend-preset",
            "openai",
            "--no-interactive",
            "--accept-cloud-costs",
        ],
    )
    assert result.exit_code == 0
    assert "Applied backend preset 'openai'" in result.stdout
