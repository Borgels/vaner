# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import platform
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vaner.daemon.http import create_daemon_http_app
from vaner.daemon.signals.git_reader import read_content_hashes
from vaner.models.config import VanerConfig
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

if platform.system().lower().startswith("win"):
    pytest.skip("daemon http TestClient is flaky on Windows runners", allow_module_level=True)


def _config(repo: Path) -> VanerConfig:
    return VanerConfig(
        repo_root=repo,
        store_path=repo / ".vaner" / "store.db",
        telemetry_path=repo / ".vaner" / "telemetry.db",
    )


async def _seed(
    repo: Path,
    *,
    adoptability: WorkProductAdoptability = WorkProductAdoptability.EXPORTABLE,
    product_type: WorkProductType = WorkProductType.RESEARCH_BRIEF,
    path: str = "notes.md",
    body: str = "Body",
) -> str:
    now = time.time()
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    if not (repo / path).exists():
        (repo / path).write_text("source\n", encoding="utf-8")
    product = WorkProduct(
        id="wp-http",
        type=product_type,
        title="Brief",
        summary="Summary",
        body=body,
        evidence_refs=[WorkProductEvidenceRef(kind="file", path=path, reason="source evidence")],
        source_snapshot=WorkProductSourceSnapshot(
            project_id="proj",
            relative_paths=[path],
            file_hashes=read_content_hashes(repo, [path]),
            generated_at=now,
        ),
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

        ui_inspected = client.get(f"/work-products/{pid}/inspect")
        assert ui_inspected.status_code == 200
        ui_payload = ui_inspected.json()
        assert ui_payload["title"] == "Brief"
        assert ui_payload["why_prepared"]
        # Cockpit refresh: /inspect now surfaces the per-product self-eval
        # scores, the lifecycle event log, and the lifecycle/adoptability
        # state so the UI can render confidence bars + a status strip.
        assert "self_eval" in ui_payload
        assert "evidence_coverage" in ui_payload["self_eval"]
        assert "adoptability" in ui_payload
        assert "status" in ui_payload
        assert "feedback_state" in ui_payload
        assert isinstance(ui_payload["events"], list)

        exported = client.post(f"/work-products/{pid}/export")
        assert exported.status_code == 200
        assert exported.json()["body"] == "Body"

        feedback = client.post(f"/work-products/{pid}/feedback", json={"feedback_state": "useful"})
        assert feedback.status_code == 200

        dismissed = client.post(f"/work-products/{pid}/dismiss")
        assert dismissed.status_code == 200
        assert client.get("/work-products").json()["work_products"] == []

    store = ArtefactStore(temp_repo / ".vaner" / "artefacts.db")
    await store.initialize()
    events = await store.list_work_product_events(pid)
    assert {"inspect", "export", "feedback", "dismiss"}.issubset({str(event["event_type"]) for event in events})


@pytest.mark.asyncio
async def test_work_products_http_blocks_non_exportable(temp_repo: Path) -> None:
    pid = await _seed(temp_repo, adoptability=WorkProductAdoptability.INSPECTABLE)
    app = create_daemon_http_app(_config(temp_repo))

    with TestClient(app) as client:
        response = client.post(f"/work-products/{pid}/export")

    assert response.status_code == 409
    assert response.json()["code"] == "not_exportable"


@pytest.mark.asyncio
async def test_virtual_diff_export_blocks_stale_without_mutating_worktree(temp_repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=temp_repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=temp_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=temp_repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=temp_repo, check=True)
    (temp_repo / "src").mkdir()
    target = temp_repo / "src" / "parser.py"
    target.write_text("print('old')\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/parser.py"], cwd=temp_repo, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=temp_repo, check=True, capture_output=True)
    before_status = subprocess.run(["git", "status", "--short"], cwd=temp_repo, check=True, capture_output=True, text=True).stdout

    pid = await _seed(
        temp_repo,
        product_type=WorkProductType.VIRTUAL_DIFF,
        path="src/parser.py",
        body="```diff\n-print('old')\n+print('new')\n```",
    )
    target.write_text("print('changed')\n", encoding="utf-8")
    app = create_daemon_http_app(_config(temp_repo))

    with TestClient(app) as client:
        response = client.post(f"/work-products/{pid}/export")

    after_status = subprocess.run(["git", "status", "--short"], cwd=temp_repo, check=True, capture_output=True, text=True).stdout
    assert response.status_code == 409
    assert response.json()["code"] == "stale_work_product"
    assert "src/parser.py" not in before_status
    assert " M src/parser.py\n" in after_status
    assert target.read_text(encoding="utf-8") == "print('changed')\n"
