# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json

from typer.testing import CliRunner

from vaner.cli.commands import app_legacy

runner = CliRunner()


def test_status_json_output(temp_repo, capsys, monkeypatch):
    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        """
[backend]
base_url = "http://127.0.0.1:11434/v1"
model = "qwen2.5-coder:7b"
""".strip(),
        encoding="utf-8",
    )

    class _Resp:
        status_code = 200
        text = "ok"

    monkeypatch.setattr(app_legacy.httpx, "get", lambda *args, **kwargs: _Resp())
    result = runner.invoke(app_legacy.app, ["status", "--path", str(temp_repo), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["proxy"]["reachable"] is True
    assert payload["backend"]["model"] == "qwen2.5-coder:7b"


def test_doctor_fails_when_config_missing(temp_repo):
    result = runner.invoke(app_legacy.app, ["doctor", "--path", str(temp_repo), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert any(item["name"] == "config_exists" for item in payload["checks"])


def test_watch_renders_compact_line(capsys, monkeypatch):
    payload = {"id": "abc123", "cache_tier": "hit", "token_used": 128, "selection_count": 3}

    class _FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield f"data: {json.dumps(payload)}"

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def stream(self, *args, **kwargs):
            return _FakeStream()

    monkeypatch.setattr(app_legacy.httpx, "Client", lambda *args, **kwargs: _FakeClient())
    result = runner.invoke(app_legacy.app, ["watch", "--limit", "1"])
    assert result.exit_code == 0
    assert "[hit] 128 tok • 3 files • decision abc123" in result.stdout
