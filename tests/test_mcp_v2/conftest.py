from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from vaner.models.scenario import Scenario
from vaner.store.scenarios import ScenarioStore


@pytest.fixture
def mcp_server(temp_repo: Path):
    try:
        from vaner.mcp.server import build_server
    except ModuleNotFoundError as exc:  # pragma: no cover - CI matrix dependent
        if exc.name == "mcp":
            pytest.skip("mcp package is unavailable in this test environment")
        raise

    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )
    return build_server(temp_repo)


def seed_scenario(repo: Path, *, scenario_id: str = "scn_1", memory_state: str = "candidate", confidence: float = 0.8) -> None:
    async def _seed() -> None:
        store = ScenarioStore(repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id=scenario_id,
                kind="change",
                score=0.9,
                confidence=confidence,
                entities=["auth", "pipeline", "route", "tenant"],
                evidence=[],
                prepared_context="Auth enforced in middleware.",
                coverage_gaps=[],
                freshness="fresh",
                cost_to_expand="medium",
                memory_state=memory_state,
                memory_confidence=confidence,
            )
        )

    asyncio.run(_seed())


def call_tool(server, name: str, arguments: dict | None = None):
    async def _call():
        from mcp.types import CallToolRequest

        handler = server.request_handlers[CallToolRequest]
        return await handler(CallToolRequest(method="tools/call", params={"name": name, "arguments": arguments or {}}))

    return asyncio.run(_call())


def parse_content(result) -> dict:
    return json.loads(result.root.content[0].text)
