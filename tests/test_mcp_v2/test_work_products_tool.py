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
    WorkProductEvidenceRef,
    WorkProductExternalInput,
    WorkProductFreshness,
    WorkProductSelfEval,
    WorkProductSensitivity,
    WorkProductSourceSnapshot,
    WorkProductStatus,
    WorkProductType,
)
from vaner.store.artefacts import ArtefactStore

from .conftest import OfflineDaemonClient, call_tool, parse_content


def _server(temp_repo: Path):
    if importlib.util.find_spec("mcp") is None:  # pragma: no cover
        pytest.skip("mcp package is unavailable in this test environment")
    from vaner.mcp.server import build_server

    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )
    return build_server(temp_repo, daemon_client=OfflineDaemonClient())


def _seed(repo: Path, *, adoptability: WorkProductAdoptability = WorkProductAdoptability.EXPORTABLE) -> str:
    async def _run() -> str:
        now = time.time()
        product = WorkProduct(
            id="wp-mcp",
            type=WorkProductType.RESEARCH_BRIEF,
            title="Brief",
            summary="Summary",
            body="Body",
            evidence_refs=[WorkProductEvidenceRef(kind="file", path="notes.md", reason="source evidence")],
            source_snapshot=WorkProductSourceSnapshot(project_id="proj", relative_paths=["notes.md"], file_hashes={}, generated_at=now),
            confidence=0.8,
            freshness=WorkProductFreshness.FRESH,
            status=WorkProductStatus.SURFACED,
            adoptability=adoptability,
            self_eval=WorkProductSelfEval(
                evidence_coverage=0.8,
                groundedness=0.8,
                reason="direct source evidence supports this prepared work",
            ),
            created_at=now,
            updated_at=now,
        )
        store = ArtefactStore(repo / ".vaner" / "artefacts.db")
        await store.initialize()
        await store.upsert_work_product(product)
        return product.id

    return asyncio.run(_run())


def _seed_sensitive_finance(repo: Path) -> str:
    async def _run() -> str:
        now = time.time()
        product = WorkProduct(
            id="wp-finance-sensitive",
            type=WorkProductType.FINANCE_POSITION_BRIEF,
            title="Position brief",
            summary="Prepared from account snapshot",
            body="Advisory only.",
            evidence_refs=[WorkProductEvidenceRef(kind="record", reason="external-state snapshot")],
            source_snapshot=WorkProductSourceSnapshot(project_id="proj", relative_paths=[], file_hashes={}, generated_at=now),
            confidence=0.82,
            freshness=WorkProductFreshness.FRESH,
            status=WorkProductStatus.SURFACED,
            adoptability=WorkProductAdoptability.INSPECTABLE,
            self_eval=WorkProductSelfEval(
                evidence_coverage=0.9,
                groundedness=0.9,
                stale_risk=0.2,
                reason="external finance snapshot supports this prepared work",
            ),
            sensitivity_class=WorkProductSensitivity.POSITION_SPECIFIC,
            external_inputs=[
                WorkProductExternalInput(
                    provider_id="provider",
                    capability="list_positions",
                    snapshot_id="snap-1",
                    freshness_class="account_snapshot",
                    captured_at=now,
                    expires_at=now + 60,
                    payload_fingerprint="fingerprint-only",
                )
            ],
            stale_after=now + 60,
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
    assert "adoptability" not in inspected

    exported = parse_content(call_tool(server, "vaner.work_products.export", {"work_product_id": pid}))
    assert exported["body"] == "Body"

    feedback = parse_content(call_tool(server, "vaner.work_products.feedback", {"work_product_id": pid, "feedback_state": "not_useful"}))
    assert feedback["ok"] is True

    dismissed = parse_content(call_tool(server, "vaner.work_products.dismiss", {"work_product_id": pid}))
    assert dismissed["ok"] is True
    assert parse_content(call_tool(server, "vaner.work_products.list"))["work_products"] == []


def test_work_product_tools_forward_to_daemon_when_available(temp_repo: Path) -> None:
    if importlib.util.find_spec("mcp") is None:  # pragma: no cover
        pytest.skip("mcp package is unavailable in this test environment")
    from vaner.mcp.server import build_server

    class FakeDaemon:
        async def list_work_products(self, **_kwargs):
            return {"work_products": [{"id": "wp-daemon", "title": "Daemon product"}]}

        async def inspect_work_product(self, product_id: str):
            return {"id": product_id, "summary": "From daemon"}

        async def export_work_product(self, product_id: str):
            return {"id": product_id, "body": "Exported from daemon"}

        async def feedback_work_product(self, product_id: str, feedback_state: str):
            return {"ok": True, "id": product_id, "feedback_state": feedback_state}

        async def dismiss_work_product(self, product_id: str):
            return {"ok": True, "id": product_id}

    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    server = build_server(temp_repo, daemon_client=FakeDaemon())

    listed = parse_content(call_tool(server, "vaner.work_products.list"))
    assert listed["work_products"][0]["id"] == "wp-daemon"

    inspected = parse_content(call_tool(server, "vaner.work_products.inspect", {"work_product_id": "wp-daemon"}))
    assert inspected["summary"] == "From daemon"

    exported = parse_content(call_tool(server, "vaner.work_products.export", {"work_product_id": "wp-daemon"}))
    assert exported["body"] == "Exported from daemon"

    feedback = parse_content(
        call_tool(server, "vaner.work_products.feedback", {"work_product_id": "wp-daemon", "feedback_state": "not_useful"})
    )
    assert feedback["feedback_state"] == "not_useful"

    dismissed = parse_content(call_tool(server, "vaner.work_products.dismiss", {"work_product_id": "wp-daemon"}))
    assert dismissed["ok"] is True


def test_work_product_export_blocks_inspectable_artifact(temp_repo: Path) -> None:
    pid = _seed(temp_repo, adoptability=WorkProductAdoptability.INSPECTABLE)
    server = _server(temp_repo)

    result = parse_content(call_tool(server, "vaner.work_products.export", {"work_product_id": pid}))
    assert result["code"] == "not_exportable"


def test_sensitive_finance_work_product_surfaces_labels_and_blocks_export(temp_repo: Path) -> None:
    pid = _seed_sensitive_finance(temp_repo)
    server = _server(temp_repo)

    listed = parse_content(call_tool(server, "vaner.work_products.list", {"include_hidden": True}))
    row = listed["work_products"][0]
    assert row["sensitivity_class"] == "position_specific"
    assert row["fresh_precheck_required"] is True
    assert row["external_inputs"][0]["payload_fingerprint"] == "fingerprint-only"

    inspected = parse_content(call_tool(server, "vaner.work_products.inspect", {"work_product_id": pid}))
    assert inspected["sensitivity_class"] == "position_specific"
    assert inspected["fresh_precheck_required"] is True
    assert inspected["external_input_count"] == 1
    assert "execution" in inspected["prohibited_actions"]
    assert any("Fresh external-state precheck" in warning for warning in inspected["warnings"])

    exported = parse_content(call_tool(server, "vaner.work_products.export", {"work_product_id": pid}))
    assert exported["code"] == "fresh_precheck_required"

    async def _events() -> list[dict[str, object]]:
        store = ArtefactStore(temp_repo / ".vaner" / "artefacts.db")
        await store.initialize()
        return await store.list_work_product_events(pid)

    events = asyncio.run(_events())
    inspect_event = next(event for event in events if event["event_type"] == "inspect")
    metadata = inspect_event["metadata"]
    assert metadata["sensitivity_class"] == "position_specific"
    assert metadata["fresh_precheck_required"] is True
    assert metadata["external_input_count"] == 1
