from __future__ import annotations

import asyncio

from vaner.models.scenario import EvidenceRef, Scenario
from vaner.store.scenarios import ScenarioStore

from .conftest import call_tool, parse_content, seed_scenario


def test_suggest_returns_candidates(temp_repo, mcp_server) -> None:
    seed_scenario(temp_repo, scenario_id="scn_suggest")
    result = call_tool(mcp_server, "vaner.suggest", {"query": "where auth is enforced"})
    payload = parse_content(result)
    assert "suggestions" in payload


def test_suggest_downranks_vaner_managed_files(temp_repo, mcp_server) -> None:
    async def _seed() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="scn_managed",
                kind="research",
                score=0.95,
                confidence=0.9,
                entities=[".cursor/skills/vaner/vaner-feedback/SKILL.md"],
                evidence=[
                    EvidenceRef(
                        key="file_summary:.cursor/skills/vaner/vaner-feedback/SKILL.md",
                        source_path=".cursor/skills/vaner/vaner-feedback/SKILL.md",
                        excerpt="managed feedback skill",
                        weight=1.0,
                    )
                ],
                prepared_context="Managed Vaner feedback skill.",
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
                        excerpt="installer entrypoint",
                        weight=1.0,
                    )
                ],
                prepared_context="Installer logic lives in scripts/install.sh.",
            )
        )

    asyncio.run(_seed())

    result = call_tool(
        mcp_server,
        "vaner.suggest",
        {"query": "Where should I edit the repository to change how Vaner installs itself?", "limit": 1},
    )
    payload = parse_content(result)

    assert payload["suggestions"][0]["scenario_id"] == "scn_install"
