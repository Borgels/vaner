# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.integrations.mcp_consumer.client import _merged_env


def test_stdio_consumer_env_inherits_parent_secret_without_persisting(monkeypatch) -> None:
    monkeypatch.setenv("SAXO_ACCESS_TOKEN", "from-parent")

    env = _merged_env({"SAXO_ENVIRONMENT": "sim"})

    assert env["SAXO_ACCESS_TOKEN"] == "from-parent"
    assert env["SAXO_ENVIRONMENT"] == "sim"
