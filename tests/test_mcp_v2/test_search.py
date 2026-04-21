from __future__ import annotations

import asyncio

from vaner.models.scenario import EvidenceRef, Scenario
from vaner.store.scenarios import ScenarioStore

from .conftest import call_tool, parse_content, seed_scenario


def test_search_returns_results(temp_repo, mcp_server) -> None:
    seed_scenario(temp_repo, scenario_id="scn_search")
    result = call_tool(mcp_server, "vaner.search", {"query": "auth policy", "mode": "hybrid"})
    payload = parse_content(result)
    assert "results" in payload


def test_search_downranks_vaner_managed_files(temp_repo, mcp_server) -> None:
    async def _seed() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="scn_managed",
                kind="research",
                score=0.95,
                confidence=0.9,
                entities=[".cursor/mcp.json"],
                evidence=[
                    EvidenceRef(
                        key="file_summary:.cursor/mcp.json",
                        source_path=".cursor/mcp.json",
                        excerpt="Generated Cursor MCP wiring",
                        weight=1.0,
                    )
                ],
                prepared_context="Generated Cursor MCP config.",
            )
        )
        await store.upsert(
            Scenario(
                id="scn_install",
                kind="change",
                score=0.8,
                confidence=0.8,
                entities=["scripts/install.sh", "install", "installer"],
                evidence=[
                    EvidenceRef(
                        key="file_summary:scripts/install.sh",
                        source_path="scripts/install.sh",
                        excerpt="Installer entrypoint",
                        weight=1.0,
                    )
                ],
                prepared_context="Installer logic lives in scripts/install.sh.",
            )
        )

    asyncio.run(_seed())

    result = call_tool(
        mcp_server,
        "vaner.search",
        {"query": "Where should I edit the repository to change how Vaner installs itself?", "mode": "hybrid", "limit": 1},
    )
    payload = parse_content(result)

    assert payload["results"][0]["source"] == "scn_install"
