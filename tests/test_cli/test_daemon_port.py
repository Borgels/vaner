# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from vaner.cli.commands import app

runner = CliRunner()

if not hasattr(app, "_next_free_port"):
    pytest.skip("_next_free_port unavailable on this CLI surface", allow_module_level=True)


def test_serve_http_busy_port_prints_remediation(monkeypatch, temp_repo) -> None:
    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        """
[backend]
base_url = "http://127.0.0.1:11434/v1"
model = "qwen3.5:35b"
""".strip(),
        encoding="utf-8",
    )

    class _Uvicorn:
        @staticmethod
        def run(*_args, **_kwargs):
            raise OSError(98, "address already in use")

    monkeypatch.setitem(__import__("sys").modules, "uvicorn", _Uvicorn)
    monkeypatch.setattr(app, "_next_free_port", lambda *_args, **_kwargs: 8474)

    result = runner.invoke(app.app, ["daemon", "serve-http", "--path", str(temp_repo), "--port", "8473"])
    assert result.exit_code == 1
    assert "port 127.0.0.1:8473 is busy" in result.output
    assert "--port 8474" in result.output
