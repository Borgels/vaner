# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from vaner.models.work_product import (
    WorkProduct,
    WorkProductAdoptability,
    WorkProductEvidenceRef,
    WorkProductFreshness,
    WorkProductSelfEval,
    WorkProductSourceSnapshot,
    WorkProductStatus,
    WorkProductType,
)
from vaner.store.artefacts import ArtefactStore

from .conftest import call_tool, parse_content


def test_prepared_work_dashboard_returns_cards(mcp_server, temp_repo: Path) -> None:
    async def _seed() -> None:
        now = time.time()
        store = ArtefactStore(temp_repo / ".vaner" / "artefacts.db")
        await store.initialize()
        await store.upsert_work_product(
            WorkProduct(
                id="wp-mcp",
                type=WorkProductType.RESEARCH_BRIEF,
                title="Brief prepared",
                summary="Source-backed brief is ready.",
                body="Brief body",
                evidence_refs=[WorkProductEvidenceRef(kind="file", path="notes.md", reason="source evidence")],
                source_snapshot=WorkProductSourceSnapshot(
                    project_id="proj",
                    relative_paths=["notes.md"],
                    file_hashes={"notes.md": "abc"},
                    generated_at=now,
                ),
                confidence=0.8,
                freshness=WorkProductFreshness.FRESH,
                status=WorkProductStatus.SURFACED,
                adoptability=WorkProductAdoptability.INSPECTABLE,
                self_eval=WorkProductSelfEval(
                    evidence_coverage=0.8,
                    groundedness=0.8,
                    reason="direct source evidence supports this prepared work",
                ),
                created_at=now,
                updated_at=now,
                target_key="notes.md",
            )
        )

    asyncio.run(_seed())

    result = call_tool(mcp_server, "vaner.prepared_work.dashboard", {"limit": 5})
    payload = parse_content(result)

    assert payload["prepared_work"][0]["id"] == "work_product:wp-mcp"
    assert payload["prepared_work"][0]["primary_action"]["kind"] == "inspect"
    assert "adoptability" not in payload["prepared_work"][0]


def test_prepared_work_dashboard_tier4_returns_resource_link(mcp_server, temp_repo: Path) -> None:
    from mcp.server.lowlevel.server import RequestContext, request_ctx

    from vaner.integrations.capability import detect_tier, record_tier

    async def _seed() -> None:
        now = time.time()
        store = ArtefactStore(temp_repo / ".vaner" / "artefacts.db")
        await store.initialize()
        await store.upsert_work_product(
            WorkProduct(
                id="wp-tier4",
                type=WorkProductType.RESEARCH_BRIEF,
                title="Brief prepared",
                summary="Source-backed brief is ready.",
                body="Brief body",
                evidence_refs=[WorkProductEvidenceRef(kind="file", path="notes.md", reason="source evidence")],
                source_snapshot=WorkProductSourceSnapshot(
                    project_id="proj",
                    relative_paths=["notes.md"],
                    file_hashes={"notes.md": "abc"},
                    generated_at=now,
                ),
                confidence=0.8,
                freshness=WorkProductFreshness.FRESH,
                status=WorkProductStatus.SURFACED,
                adoptability=WorkProductAdoptability.INSPECTABLE,
                self_eval=WorkProductSelfEval(
                    evidence_coverage=0.8,
                    groundedness=0.8,
                    reason="direct source evidence supports this prepared work",
                ),
                created_at=now,
                updated_at=now,
                target_key="notes.md",
            )
        )

    asyncio.run(_seed())

    class _Session:
        pass

    session = _Session()
    session.client_params = SimpleNamespace(
        clientInfo=SimpleNamespace(name="tier4-client", version="1.0"),
        capabilities=SimpleNamespace(
            experimental={"io.modelcontextprotocol/ui": {}},
            roots=SimpleNamespace(),
            sampling=None,
        ),
    )
    record_tier(session, detect_tier(session.client_params))
    token = request_ctx.set(RequestContext(request_id="prepared-work-test", meta=None, session=session, lifespan_context=None))
    try:
        result = call_tool(mcp_server, "vaner.prepared_work.dashboard", {"limit": 5})
    finally:
        request_ctx.reset(token)

    assert result.root.structuredContent["prepared_work"][0]["id"] == "work_product:wp-tier4"
    assert any(getattr(item, "type", None) == "resource_link" for item in result.root.content)
