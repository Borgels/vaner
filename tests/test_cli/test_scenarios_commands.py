# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from typer.testing import CliRunner

from vaner.cli.commands import app
from vaner.models.scenario import EvidenceRef, Scenario
from vaner.store.scenarios import ScenarioStore

runner = CliRunner()


def _seed(temp_repo) -> None:
    async def _run() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="scn_test_1",
                kind="debug",
                score=0.91,
                confidence=0.83,
                entities=["src/main.py"],
                evidence=[EvidenceRef(key="file_summary:src/main.py", source_path="src/main.py", excerpt="main flow", weight=0.8)],
                prepared_context="Likely regression in main flow",
                coverage_gaps=["No test diff summary"],
                freshness="fresh",
                cost_to_expand="medium",
            )
        )
        await store.upsert(
            Scenario(
                id="scn_test_2",
                kind="change",
                score=0.72,
                confidence=0.66,
                entities=["src/other.py"],
                evidence=[EvidenceRef(key="file_summary:src/other.py", source_path="src/other.py", excerpt="other", weight=0.5)],
                prepared_context="Secondary candidate",
                coverage_gaps=[],
                freshness="recent",
                cost_to_expand="low",
            )
        )

    asyncio.run(_run())


def test_scenarios_list_json(temp_repo):
    _seed(temp_repo)
    result = runner.invoke(app.app, ["scenarios", "list", "--path", str(temp_repo), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["count"] >= 1
    assert payload["scenarios"][0]["id"] == "scn_test_1"


def test_scenarios_show_and_outcome(temp_repo):
    _seed(temp_repo)
    shown = runner.invoke(app.app, ["scenarios", "show", "scn_test_1", "--path", str(temp_repo), "--json"])
    assert shown.exit_code == 0
    payload = json.loads(shown.stdout)
    assert payload["kind"] == "debug"

    outcome = runner.invoke(
        app.app,
        ["scenarios", "outcome", "scn_test_1", "--result", "useful", "--path", str(temp_repo)],
    )
    assert outcome.exit_code == 0


def test_scenarios_expand_and_compare(temp_repo, monkeypatch):
    _seed(temp_repo)

    async def _fake_aprecompute(*args, **kwargs):
        return 1

    monkeypatch.setattr(app.api, "aprecompute", _fake_aprecompute)
    expanded = runner.invoke(app.app, ["scenarios", "expand", "scn_test_1", "--path", str(temp_repo)])
    assert expanded.exit_code == 0
    expanded_payload = json.loads(expanded.stdout)
    assert expanded_payload["ok"] is True

    async def _seed_second() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="scn_test_2",
                kind="change",
                score=0.81,
                confidence=0.72,
                entities=["src/main.py", "src/utils.py"],
                evidence=[],
                prepared_context="Alt scenario",
                coverage_gaps=[],
                freshness="recent",
                cost_to_expand="medium",
            )
        )

    asyncio.run(_seed_second())
    compared = runner.invoke(
        app.app,
        ["scenarios", "compare", "scn_test_1", "scn_test_2", "--path", str(temp_repo), "--json"],
    )
    assert compared.exit_code == 0
    compare_payload = json.loads(compared.stdout)
    assert "recommended" in compare_payload


def test_init_writes_cursor_mcp_config(temp_repo, monkeypatch):
    fake_home = temp_repo / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    result = runner.invoke(app.app, ["init", "--path", str(temp_repo)])
    assert result.exit_code == 0
    cursor_mcp = temp_repo / ".cursor" / "mcp.json"
    assert cursor_mcp.exists()
    payload = json.loads(cursor_mcp.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["vaner"]["command"] == "vaner"


def test_scenarios_expand_and_compare_json(temp_repo, monkeypatch):
    _seed(temp_repo)

    async def _fake_precompute(*args, **kwargs) -> int:
        return 1

    monkeypatch.setattr(app.api, "aprecompute", _fake_precompute)
    expanded = runner.invoke(app.app, ["scenarios", "expand", "scn_test_1", "--path", str(temp_repo)])
    assert expanded.exit_code == 0
    expanded_payload = json.loads(expanded.stdout)
    assert expanded_payload["ok"] is True

    compared = runner.invoke(
        app.app,
        ["scenarios", "compare", "scn_test_1", "scn_test_2", "--path", str(temp_repo), "--json"],
    )
    assert compared.exit_code == 0
    compare_payload = json.loads(compared.stdout)
    assert compare_payload["a"]["id"] == "scn_test_1"
    assert compare_payload["b"]["id"] == "scn_test_2"


def test_init_writes_mcp_configs(temp_repo, monkeypatch):
    fake_home = temp_repo / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    result = runner.invoke(app.app, ["init", "--path", str(temp_repo)])
    assert result.exit_code == 0
    assert (temp_repo / ".cursor" / "mcp.json").exists()
    assert (fake_home / ".claude" / "claude_desktop_config.json").exists()
