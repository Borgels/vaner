# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import importlib.util
import time
from pathlib import Path

import pytest

from vaner.models.work_product import (
    WorkProduct,
    WorkProductAdoptability,
    WorkProductFreshness,
    WorkProductSourceSnapshot,
    WorkProductStatus,
    WorkProductType,
)
from vaner.store.artefacts import ArtefactStore

from .conftest import call_tool, parse_content


def _server(temp_repo: Path):
    if importlib.util.find_spec("mcp") is None:  # pragma: no cover
        pytest.skip("mcp package is unavailable in this test environment")
    from vaner.mcp.server import build_server

    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )
    return build_server(temp_repo)


def _seed(repo: Path, *, adoptability: WorkProductAdoptability = WorkProductAdoptability.EXPORTABLE) -> str:
    async def _run() -> str:
        now = time.time()
        product = WorkProduct(
            id="wp-mcp",
            type=WorkProductType.RESEARCH_BRIEF,
            title="Brief",
            summary="Summary",
            body="Body",
            source_snapshot=WorkProductSourceSnapshot(project_id="proj", relative_paths=["notes.md"], file_hashes={}, generated_at=now),
            confidence=0.8,
            freshness=WorkProductFreshness.FRESH,
            status=WorkProductStatus.SURFACED,
            adoptability=adoptability,
            created_at=now,
            updated_at=now,
        )
        store = ArtefactStore(repo / ".vaner" / "artefacts.db")
        await store.initialize()
        await store.upsert_work_product(product)
        return product.id

    return asyncio.run(_run())


def test_work_product_tools_list_inspect_export_and_dismiss(temp_repo: Path) -> None:
    pid = _seed(temp_repo)
    server = _server(temp_repo)

    listed = parse_content(call_tool(server, "vaner.work_products.list"))
    assert listed["work_products"][0]["id"] == pid

    inspected = parse_content(call_tool(server, "vaner.work_products.inspect", {"work_product_id": pid}))
    assert inspected["summary"] == "Summary"

    exported = parse_content(call_tool(server, "vaner.work_products.export", {"work_product_id": pid}))
    assert exported["body"] == "Body"

    feedback = parse_content(
        call_tool(server, "vaner.work_products.feedback", {"work_product_id": pid, "feedback_state": "partial"})
    )
    assert feedback["ok"] is True

    dismissed = parse_content(call_tool(server, "vaner.work_products.dismiss", {"work_product_id": pid}))
    assert dismissed["ok"] is True
    assert parse_content(call_tool(server, "vaner.work_products.list"))["work_products"] == []


def test_work_product_export_blocks_inspectable_artifact(temp_repo: Path) -> None:
    pid = _seed(temp_repo, adoptability=WorkProductAdoptability.INSPECTABLE)
    server = _server(temp_repo)

    result = parse_content(call_tool(server, "vaner.work_products.export", {"work_product_id": pid}))
    assert result["code"] == "not_exportable"

