# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json

from typer.testing import CliRunner

from vaner.cli.commands import app
from vaner.models.scenario import Scenario
from vaner.store.scenarios import ScenarioStore

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

    monkeypatch.setattr(app.httpx, "get", lambda *args, **kwargs: _Resp())
    result = runner.invoke(app.app, ["status", "--path", str(temp_repo), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["cockpit"]["reachable"] is True
    assert payload["backend"]["model"] == "qwen2.5-coder:7b"


def test_status_text_shows_freshness_counts(temp_repo, monkeypatch):
    async def _seed() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="scn_status_1",
                kind="debug",
                score=0.9,
                confidence=0.8,
                entities=["src/main.py"],
                evidence=[],
                prepared_context="ctx",
                coverage_gaps=[],
                freshness="fresh",
                cost_to_expand="medium",
            )
        )

    asyncio.run(_seed())

    class _Resp:
        status_code = 200
        text = "ok"

    monkeypatch.setattr(app.httpx, "get", lambda *args, **kwargs: _Resp())
    result = runner.invoke(app.app, ["status", "--path", str(temp_repo)])
    assert result.exit_code == 0
    assert "freshness: fresh=1 recent=0 stale=0" in result.stdout


def test_doctor_fails_when_config_missing(temp_repo):
    result = runner.invoke(app.app, ["doctor", "--path", str(temp_repo), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert any(item["name"] == "config_exists" for item in payload["checks"])


def test_doctor_includes_mcp_and_store_checks(temp_repo):
    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        """
[backend]
base_url = "http://127.0.0.1:11434/v1"
model = "qwen2.5-coder:7b"
""".strip(),
        encoding="utf-8",
    )
    result = runner.invoke(app.app, ["doctor", "--path", str(temp_repo), "--json"])
    payload = json.loads(result.stdout)
    names = {item["name"] for item in payload["checks"]}
    assert "mcp_config_present" in names
    assert "scenario_store_reachable" in names
    assert "exploration_llm_reachable" in names


def test_watch_renders_compact_line(capsys, monkeypatch):
    payload = {"id": "abc123", "kind": "debug", "score": 0.91, "freshness": "fresh"}

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

    monkeypatch.setattr(app.httpx, "Client", lambda *args, **kwargs: _FakeClient())
    result = runner.invoke(app.app, ["watch", "--limit", "1"])
    assert result.exit_code == 0
    assert "[debug] score=0.910 freshness=fresh scenario abc123" in result.stdout
