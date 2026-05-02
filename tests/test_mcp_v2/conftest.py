from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from vaner.models.scenario import Scenario
from vaner.store.scenarios import ScenarioStore


class OfflineDaemonClient:
    """Test stub used by every test in `test_mcp_v2/`. Each method
    raises ``VanerDaemonUnavailable`` so the MCP server's local-
    fallback code paths run without a real cockpit on the wire.

    PR #211's cockpit refresh added new daemon endpoints
    (``/predictions/active?include_all=true``, ``/scenarios/{id}``,
    extensions to ``/work-products/{id}/inspect``, ``/goals``,
    ``/artefacts``, ``/learning/recent``) and the MCP server gained
    call sites for each. The stub funnels all of them to the same
    daemon-unavailable signal so the local-fallback paths exercise
    correctly without a manual per-method override."""

    async def _unavailable(self):
        from vaner.clients.daemon import VanerDaemonUnavailable

        raise VanerDaemonUnavailable("offline test daemon")

    async def get_prepared_work(self, **_kwargs):
        await self._unavailable()

    async def list_work_products(self, **_kwargs):
        await self._unavailable()

    async def inspect_work_product(self, _product_id: str):
        await self._unavailable()

    async def export_work_product(self, _product_id: str):
        await self._unavailable()

    async def dismiss_work_product(self, _product_id: str):
        await self._unavailable()

    async def feedback_work_product(self, _product_id: str, _feedback_state: str):
        await self._unavailable()

    async def get_predictions_active(self, **_kwargs):
        await self._unavailable()

    async def get_status(self):
        await self._unavailable()

    async def get_prediction(self, _prediction_id: str):
        await self._unavailable()

    async def adopt_prediction(self, _prediction_id: str):
        await self._unavailable()

    async def get_goals(self):
        await self._unavailable()

    async def get_artefacts(self, **_kwargs):
        await self._unavailable()

    async def get_artefact(self, _artefact_id: str):
        await self._unavailable()

    async def get_learning_recent(self, **_kwargs):
        await self._unavailable()

    async def get_scenario(self, _scenario_id: str):
        await self._unavailable()

    async def resolve(self, *_args, **_kwargs):
        await self._unavailable()


@pytest.fixture
def mcp_server(temp_repo: Path):
    if importlib.util.find_spec("mcp") is None:  # pragma: no cover - CI matrix dependent
        pytest.skip("mcp package is unavailable in this test environment")
    from vaner.mcp.server import build_server

    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )
    return build_server(temp_repo, daemon_client=OfflineDaemonClient())


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
