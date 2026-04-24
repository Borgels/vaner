# SPDX-License-Identifier: Apache-2.0
"""0.8.2 WS1 — MCP intent-artefact tool tests.

Exercises the five new tools end-to-end through the MCP protocol:
``vaner.artefacts.list``, ``vaner.artefacts.inspect``,
``vaner.artefacts.set_status``, ``vaner.artefacts.influence``, and
``vaner.sources.status``. Includes the spec §MCP-inspectability
hard requirement: influence data must be queryable in both directions
once WS2/WS3 wire it up — WS1 asserts the shape.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("mcp")

from mcp.types import CallToolRequest

from vaner.mcp.server import build_server


def _write_backend_config(temp_repo) -> None:
    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )


async def _seed_artefact(repo_root, *, source_uri: str, text: str, title_hint: str) -> dict:
    from vaner.intent.adapter import RawArtefact
    from vaner.intent.ingest.pipeline import ingest_artefact
    from vaner.store.artefacts import ArtefactStore

    store = ArtefactStore(repo_root / ".vaner" / "artefacts.db")
    await store.initialize()
    raw = RawArtefact(
        source_uri=source_uri,
        connector="local_plan",
        tier="T1",
        text=text,
        last_modified=0.0,
        title_hint=title_hint,
    )
    result = await ingest_artefact(raw, store=store)
    assert result.accepted, f"pipeline rejected fixture: {result.classification.reasoning}"
    return {"artefact_id": result.artefact.id, "snapshot_id": result.snapshot.id}


async def _call(server, name: str, arguments: dict | None = None) -> dict:
    handler = server.request_handlers[CallToolRequest]
    result = await handler(
        CallToolRequest(
            method="tools/call",
            params={"name": name, "arguments": arguments or {}},
        )
    )
    return json.loads(result.root.content[0].text)


def test_artefacts_list_and_inspect_roundtrip(temp_repo) -> None:
    async def _run() -> None:
        meta = await _seed_artefact(
            temp_repo,
            source_uri="file:///tmp/release.md",
            text="# Release 0.8.2\n\n## Phase 1\n- [ ] a\n- [ ] b\n- [x] c\n- [ ] d\n",
            title_hint="release.md",
        )
        _write_backend_config(temp_repo)
        server = build_server(temp_repo)

        list_payload = await _call(server, "vaner.artefacts.list", {})
        assert any(a["id"] == meta["artefact_id"] for a in list_payload["artefacts"])

        inspect_payload = await _call(server, "vaner.artefacts.inspect", {"artefact_id": meta["artefact_id"]})
        assert inspect_payload["artefact"]["id"] == meta["artefact_id"]
        assert inspect_payload["snapshot_id"] == meta["snapshot_id"]
        assert len(inspect_payload["items"]) >= 4
        assert "related_files" in inspect_payload["items"][0]
        assert "related_entities" in inspect_payload["items"][0]

    asyncio.run(_run())


def test_artefacts_set_status_user_override(temp_repo) -> None:
    async def _run() -> None:
        meta = await _seed_artefact(
            temp_repo,
            source_uri="file:///tmp/plan.md",
            text="# Plan\n\n- [ ] a\n- [ ] b\n- [ ] c\n- [ ] d\n",
            title_hint="plan.md",
        )
        _write_backend_config(temp_repo)
        server = build_server(temp_repo)
        payload = await _call(
            server,
            "vaner.artefacts.set_status",
            {"artefact_id": meta["artefact_id"], "status": "archived"},
        )
        assert payload["status"] == "archived"
        archived_listing = await _call(server, "vaner.artefacts.list", {"status": "archived"})
        assert any(a["id"] == meta["artefact_id"] for a in archived_listing["artefacts"])

    asyncio.run(_run())


def test_artefacts_set_status_rejects_unknown_status(temp_repo) -> None:
    """The tool's inputSchema restricts ``status`` to the five valid values
    so the MCP framework surfaces a schema-level validation error; the
    handler never runs. We assert the protocol-level rejection rather
    than a handler-returned JSON payload."""

    async def _run() -> None:
        meta = await _seed_artefact(
            temp_repo,
            source_uri="file:///tmp/plan.md",
            text="# Plan\n\n- [ ] a\n- [ ] b\n- [ ] c\n- [ ] d\n",
            title_hint="plan.md",
        )
        _write_backend_config(temp_repo)
        server = build_server(temp_repo)
        handler = server.request_handlers[CallToolRequest]
        result = await handler(
            CallToolRequest(
                method="tools/call",
                params={
                    "name": "vaner.artefacts.set_status",
                    "arguments": {"artefact_id": meta["artefact_id"], "status": "not_a_status"},
                },
            )
        )
        assert result.root.isError is True
        # Schema validation message, not a JSON payload.
        assert "not one of" in result.root.content[0].text.lower()

    asyncio.run(_run())


def test_artefacts_influence_returns_stable_shape(temp_repo) -> None:
    # Spec §MCP-inspectability hard requirement: influence returns
    # backing_goals / anchored_predictions / recent_reconciliation_outcomes
    # with a stable shape even in WS1, before WS2 populates goals and
    # WS3 populates reconciliation outcomes.
    async def _run() -> None:
        meta = await _seed_artefact(
            temp_repo,
            source_uri="file:///tmp/plan.md",
            text="# Plan\n\n- [ ] a\n- [ ] b\n- [ ] c\n- [ ] d\n",
            title_hint="plan.md",
        )
        _write_backend_config(temp_repo)
        server = build_server(temp_repo)
        payload = await _call(
            server,
            "vaner.artefacts.influence",
            {"artefact_id": meta["artefact_id"]},
        )
        assert payload["artefact_id"] == meta["artefact_id"]
        for key in ("backing_goals", "anchored_predictions", "recent_reconciliation_outcomes"):
            assert key in payload
            assert isinstance(payload[key], list)

    asyncio.run(_run())


def test_artefacts_influence_rejects_unknown_artefact(temp_repo) -> None:
    async def _run() -> None:
        _write_backend_config(temp_repo)
        server = build_server(temp_repo)
        payload = await _call(server, "vaner.artefacts.influence", {"artefact_id": "nonexistent"})
        assert payload.get("code") == "not_found"

    asyncio.run(_run())


def test_sources_status_surfaces_config_and_counts(temp_repo) -> None:
    async def _run() -> None:
        await _seed_artefact(
            temp_repo,
            source_uri="file:///tmp/plan.md",
            text="# Plan\n\n- [ ] a\n- [ ] b\n- [ ] c\n- [ ] d\n",
            title_hint="plan.md",
        )
        _write_backend_config(temp_repo)
        server = build_server(temp_repo)
        payload = await _call(server, "vaner.sources.status", {})
        assert "sources" in payload
        assert payload["sources"]["enabled"] is True
        assert payload["sources"]["tiers"]["T1"] == "auto"
        assert payload["sources"]["github_issues"]["enabled"] is False
        assert payload["ingest_counts"]["total"] >= 1
        assert payload["ingest_counts"]["by_connector"].get("local_plan") == 1

    asyncio.run(_run())
