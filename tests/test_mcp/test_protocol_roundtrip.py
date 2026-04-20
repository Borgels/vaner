from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from vaner.mcp.server import build_server
from vaner.models.scenario import Scenario
from vaner.store.scenarios import ScenarioStore


async def _seed_scenario(repo_root: Path) -> None:
    store = ScenarioStore(repo_root / ".vaner" / "scenarios.db")
    await store.initialize()
    await store.upsert(
        Scenario(
            id="scn_roundtrip_1",
            kind="change",
            score=0.9,
            confidence=0.8,
            entities=["src/main.py"],
            evidence=[],
            prepared_context="prepared",
            coverage_gaps=[],
            freshness="fresh",
            cost_to_expand="medium",
        )
    )


def test_mcp_protocol_roundtrip(temp_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> None:
        memory = pytest.importorskip("mcp.shared.memory")
        await _seed_scenario(temp_repo)
        (temp_repo / ".vaner" / "config.toml").write_text(
            '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))

        server = build_server(temp_repo)
        async with memory.create_connected_server_and_client_session(server) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
            assert names == {
                "list_scenarios",
                "get_scenario",
                "expand_scenario",
                "compare_scenarios",
                "report_outcome",
            }

            listed_scenarios = await session.call_tool("list_scenarios", {"limit": 5})
            assert listed_scenarios.isError is not True
            listed_payload = json.loads(listed_scenarios.content[0].text)
            assert listed_payload["count"] >= 1

            fetched = await session.call_tool("get_scenario", {"id": "scn_roundtrip_1"})
            assert fetched.isError is not True
            assert json.loads(fetched.content[0].text)["id"] == "scn_roundtrip_1"

            expanded = await session.call_tool("expand_scenario", {"id": "scn_roundtrip_1"})
            assert expanded.isError is not True
            assert json.loads(expanded.content[0].text)["id"] == "scn_roundtrip_1"

            outcome = await session.call_tool("report_outcome", {"id": "scn_roundtrip_1", "result": "useful"})
            assert outcome.isError is not True
            assert json.loads(outcome.content[0].text)["ok"] is True

            compared = await session.call_tool("compare_scenarios", {"ids": ["scn_roundtrip_1", "scn_roundtrip_1"]})
            assert compared.isError is not True
            compare_payload = json.loads(compared.content[0].text)
            assert compare_payload["recommended_scenario_id"] == "scn_roundtrip_1"

    asyncio.run(_run())
