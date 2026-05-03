from __future__ import annotations

import asyncio
import importlib.util
import time

import pytest

from vaner.models.scenario import EvidenceRef, Scenario
from vaner.store.scenarios import ScenarioStore

from .conftest import call_tool, parse_content, seed_scenario


class _SlowDaemonClient:
    async def get_status(self):
        await asyncio.sleep(2.0)
        return {"repo_root": "/slow"}

    async def get_predictions_active(self, **_kwargs):
        await asyncio.sleep(2.0)
        return {"predictions": []}


def test_suggest_returns_candidates(temp_repo, mcp_server) -> None:
    seed_scenario(temp_repo, scenario_id="scn_suggest")
    result = call_tool(mcp_server, "vaner.suggest", {"query": "where auth is enforced"})
    payload = parse_content(result)
    assert "suggestions" in payload
    assert payload["decision"]["action"] == "answer_normally"
    assert payload["guidance"]["recommended_action"]["tool"] == "answer_normally"
    assert "adopt at most one prediction per turn" in payload["guidance"]["guardrails"]
    assert "never wait for Vaner; answer normally when nothing clearly useful is ready" in payload["guidance"]["guardrails"]


def test_suggest_does_not_wait_on_slow_prediction_snapshot(temp_repo) -> None:
    if importlib.util.find_spec("mcp") is None:  # pragma: no cover - CI matrix dependent
        pytest.skip("mcp package is unavailable in this test environment")
    from vaner.mcp.server import build_server

    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )
    server = build_server(temp_repo, daemon_client=_SlowDaemonClient())

    started = time.monotonic()
    result = call_tool(server, "vaner.suggest", {"query": "Review a small helper"})
    elapsed = time.monotonic() - started
    payload = parse_content(result)

    assert elapsed < 1.5
    assert payload["decision"]["action"] == "answer_normally"
    assert payload["guidance"]["engine_unavailable"] is True


def test_suggest_does_not_resolve_optional_from_score_only(temp_repo, mcp_server) -> None:
    async def _seed() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="scn_unrelated_high_score",
                kind="research",
                score=0.99,
                confidence=0.99,
                entities=["billing", "invoice", "ledger"],
                evidence=[],
                prepared_context="Billing context.",
            )
        )

    asyncio.run(_seed())

    result = call_tool(mcp_server, "vaner.suggest", {"query": "Review desktop copy", "limit": 1})
    payload = parse_content(result)

    assert payload["suggestions"][0]["query_overlap"] == 0
    assert payload["decision"]["action"] == "answer_normally"


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


def test_suggest_keeps_managed_docs_when_domain_is_not_code(temp_repo, mcp_server) -> None:
    async def _seed() -> None:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="scn_feedback_skill",
                kind="research",
                score=0.95,
                confidence=0.9,
                entities=[".cursor/skills/vaner/vaner-feedback/SKILL.md", "feedback"],
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

    asyncio.run(_seed())

    result = call_tool(
        mcp_server,
        "vaner.suggest",
        {"query": "How should feedback be recorded?", "limit": 1, "context": {"domain": "docs"}},
    )
    payload = parse_content(result)

    assert payload["suggestions"][0]["scenario_id"] == "scn_feedback_skill"
