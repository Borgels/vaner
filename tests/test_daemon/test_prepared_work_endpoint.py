# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import platform
import time
from concurrent.futures import ThreadPoolExecutor
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
    WorkProductExternalInput,
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


@pytest.mark.asyncio
async def test_prepared_work_endpoint_returns_ui_safe_cards(temp_repo: Path) -> None:
    now = time.time()
    (temp_repo / "src").mkdir()
    (temp_repo / "src" / "parser.py").write_text("print('ok')\n", encoding="utf-8")
    hashes = read_content_hashes(temp_repo, ["src/parser.py"])
    product = WorkProduct(
        id="wp-prepared",
        type=WorkProductType.VIRTUAL_DIFF,
        title="Prepared parser fix",
        summary="A one-file patch is ready for inspection.",
        body="```diff\n+ok\n```",
        evidence_refs=[WorkProductEvidenceRef(kind="file", path="src/parser.py", reason="touched file")],
        source_snapshot=WorkProductSourceSnapshot(
            project_id="proj",
            relative_paths=["src/parser.py"],
            file_hashes=hashes,
            generated_at=now,
        ),
        confidence=0.86,
        freshness=WorkProductFreshness.FRESH,
        status=WorkProductStatus.SURFACED,
        adoptability=WorkProductAdoptability.EXPORTABLE,
        self_eval=WorkProductSelfEval(
            evidence_coverage=0.85,
            groundedness=0.85,
            reason="direct source evidence supports this prepared diff",
        ),
        created_at=now,
        updated_at=now,
        target_key="src/parser.py",
    )
    store = ArtefactStore(temp_repo / ".vaner" / "artefacts.db")
    await store.initialize()
    await store.upsert_work_product(product)

    app = create_daemon_http_app(_config(temp_repo))

    with TestClient(app) as client:
        response = client.get("/prepared-work?surface=cockpit&limit=5")

    assert response.status_code == 200
    payload = response.json()
    assert list(payload) == ["prepared_work"]
    card = payload["prepared_work"][0]
    assert card["id"] == "work_product:wp-prepared"
    assert card["kind"] == "diff"
    assert card["primary_action"]["kind"] == "export"
    assert card["primary_action"]["endpoint"] == "/work-products/wp-prepared/export"
    assert card["secondary_actions"][0]["endpoint"] == "/work-products/wp-prepared/inspect"
    assert card["target_label"] == "src/parser.py"
    assert card["why_prepared"]
    assert card["freshness_state"] == "fresh"
    assert "status" not in card
    assert "adoptability" not in card
    assert "self_eval" not in card
    assert "score" not in card


@pytest.mark.asyncio
async def test_prepared_work_endpoint_surfaces_finance_cards_as_trading_prep(temp_repo: Path) -> None:
    now = time.time()
    product = WorkProduct(
        id="wp-finance",
        type=WorkProductType.FINANCE_POSITION_BRIEF,
        title="Prepared finance snapshot brief",
        summary="Read-only external finance snapshots prepared for inspection.",
        body="Finance preparation from fresh external-state snapshots.",
        evidence_refs=[WorkProductEvidenceRef(kind="record", reason="external-state snapshot for screen_market")],
        source_snapshot=WorkProductSourceSnapshot(project_id="proj", relative_paths=[], file_hashes={}, generated_at=now),
        confidence=0.72,
        freshness=WorkProductFreshness.FRESH,
        status=WorkProductStatus.SURFACED,
        adoptability=WorkProductAdoptability.ADVISORY,
        self_eval=WorkProductSelfEval(evidence_coverage=0.7, groundedness=0.7, stale_risk=0.45),
        sensitivity_class="position_specific",
        fresh_precheck_required=True,
        external_inputs=[
            WorkProductExternalInput(provider_id="provider", capability="screen_market", snapshot_id="ext-1"),
            WorkProductExternalInput(provider_id="search", capability="search_news", snapshot_id="ext-2"),
        ],
        prohibited_actions=["execution"],
        created_at=now,
        updated_at=now,
        target_key="finance:external",
    )
    store = ArtefactStore(temp_repo / ".vaner" / "artefacts.db")
    await store.initialize()
    await store.upsert_work_product(product)
    app = create_daemon_http_app(_config(temp_repo))

    with TestClient(app) as client:
        response = client.get("/prepared-work?surface=cockpit&include_advisory=true&limit=5")

    assert response.status_code == 200
    card = response.json()["prepared_work"][0]
    assert card["kind"] == "finance"
    assert card["title"] == "Trading strategy prep"
    assert card["badge"] == "Trading"
    assert card["external_input_count"] == 2
    assert card["fresh_precheck_required"] is True
    assert "screen_market" in card["summary"]
    assert "search_news" in card["summary"]


@pytest.mark.asyncio
async def test_prepared_work_and_status_survive_concurrent_reads(temp_repo: Path) -> None:
    now = time.time()
    (temp_repo / "src").mkdir()
    (temp_repo / "src" / "parser.py").write_text("print('ok')\n", encoding="utf-8")
    product = WorkProduct(
        id="wp-concurrent",
        type=WorkProductType.VIRTUAL_DIFF,
        title="Prepared concurrent fix",
        summary="A small patch is ready.",
        body="```diff\n+ok\n```",
        evidence_refs=[WorkProductEvidenceRef(kind="file", path="src/parser.py", reason="touched file")],
        source_snapshot=WorkProductSourceSnapshot(
            project_id="proj",
            relative_paths=["src/parser.py"],
            file_hashes=read_content_hashes(temp_repo, ["src/parser.py"]),
            generated_at=now,
        ),
        confidence=0.86,
        freshness=WorkProductFreshness.FRESH,
        status=WorkProductStatus.SURFACED,
        adoptability=WorkProductAdoptability.EXPORTABLE,
        self_eval=WorkProductSelfEval(
            evidence_coverage=0.85,
            groundedness=0.85,
            reason="direct source evidence supports this prepared diff",
        ),
        created_at=now,
        updated_at=now,
        target_key="src/parser.py",
    )
    store = ArtefactStore(temp_repo / ".vaner" / "artefacts.db")
    await store.initialize()
    await store.upsert_work_product(product)
    app = create_daemon_http_app(_config(temp_repo))

    with TestClient(app) as client:
        paths = ["/prepared-work?surface=desktop&limit=5", "/status"] * 8
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(client.get, paths))

    assert all(response.status_code == 200 for response in responses)
    assert any(response.json().get("prepared_work") for response in responses if response.url.path == "/prepared-work")
