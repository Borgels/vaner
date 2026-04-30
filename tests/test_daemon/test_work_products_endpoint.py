# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import platform
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vaner.daemon.http import create_daemon_http_app
from vaner.models.config import VanerConfig
from vaner.models.work_product import (
    WorkProduct,
    WorkProductAdoptability,
    WorkProductFreshness,
    WorkProductSourceSnapshot,
    WorkProductStatus,
    WorkProductType,
)
from vaner.store.artefacts import ArtefactStore

if platform.system().lower().startswith("win"):
    pytest.skip("daemon http TestClient is flaky on Windows runners", allow_module_level=True)


def _config(repo: Path) -> VanerConfig:
    return VanerConfig(
        repo_root=repo,
        store_path=repo / ".vaner" / "store.db",
        telemetry_path=repo / ".vaner" / "telemetry.db",
    )


async def _seed(repo: Path, *, adoptability: WorkProductAdoptability = WorkProductAdoptability.EXPORTABLE) -> str:
    now = time.time()
    product = WorkProduct(
        id="wp-http",
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


@pytest.mark.asyncio
async def test_work_products_http_lifecycle(temp_repo: Path) -> None:
    pid = await _seed(temp_repo)
    app = create_daemon_http_app(_config(temp_repo))

    with TestClient(app) as client:
        listed = client.get("/work-products")
        assert listed.status_code == 200
        assert listed.json()["work_products"][0]["id"] == pid

        inspected = client.get(f"/work-products/{pid}")
        assert inspected.status_code == 200
        assert inspected.json()["title"] == "Brief"

        exported = client.post(f"/work-products/{pid}/export")
        assert exported.status_code == 200
        assert exported.json()["body"] == "Body"

        feedback = client.post(f"/work-products/{pid}/feedback", json={"feedback_state": "useful"})
        assert feedback.status_code == 200

        dismissed = client.post(f"/work-products/{pid}/dismiss")
        assert dismissed.status_code == 200
        assert client.get("/work-products").json()["work_products"] == []


@pytest.mark.asyncio
async def test_work_products_http_blocks_non_exportable(temp_repo: Path) -> None:
    pid = await _seed(temp_repo, adoptability=WorkProductAdoptability.INSPECTABLE)
    app = create_daemon_http_app(_config(temp_repo))

    with TestClient(app) as client:
        response = client.post(f"/work-products/{pid}/export")

    assert response.status_code == 409
    assert response.json()["code"] == "not_exportable"

