# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess
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


def _product(
    *,
    product_id: str = "wp-1",
    target_key: str = "target",
    confidence: float = 0.8,
    adoptability: WorkProductAdoptability = WorkProductAdoptability.EXPORTABLE,
) -> WorkProduct:
    now = time.time()
    return WorkProduct(
        id=product_id,
        type=WorkProductType.VIRTUAL_DIFF,
        title="Small virtual diff",
        summary="Adds newline",
        body="```diff\n--- a/sample.py\n+++ b/sample.py\n```",
        source_snapshot=WorkProductSourceSnapshot(
            project_id="proj",
            relative_paths=["sample.py"],
            file_hashes={"sample.py": "abc123"},
            base_commit=None,
            generated_at=now,
        ),
        confidence=confidence,
        freshness=WorkProductFreshness.FRESH,
        status=WorkProductStatus.SURFACED,
        adoptability=adoptability,
        created_at=now,
        updated_at=now,
        target_key=target_key,
    )


@pytest.mark.asyncio
async def test_work_product_lifecycle_and_export(tmp_path: Path) -> None:
    store = ArtefactStore(tmp_path / "artefacts.db")
    await store.initialize()
    await store.upsert_work_product(_product())

    rows = await store.list_work_products()
    assert [row.id for row in rows] == ["wp-1"]

    exported = await store.export_work_product("wp-1")
    assert exported.id == "wp-1"
    assert exported.type == WorkProductType.VIRTUAL_DIFF

    assert await store.feedback_work_product("wp-1", "useful") is True
    refreshed = await store.get_work_product("wp-1")
    assert refreshed is not None
    assert refreshed.feedback_state == "useful"

    assert await store.dismiss_work_product("wp-1") is True
    assert await store.list_work_products() == []
    dismissed = await store.get_work_product("wp-1")
    assert dismissed is not None
    assert dismissed.status == WorkProductStatus.DISMISSED


@pytest.mark.asyncio
async def test_hidden_and_non_exportable_are_filtered_and_blocked(tmp_path: Path) -> None:
    store = ArtefactStore(tmp_path / "artefacts.db")
    await store.initialize()
    await store.upsert_work_product(_product(product_id="hidden", adoptability=WorkProductAdoptability.HIDDEN))

    assert await store.list_work_products() == []
    assert len(await store.list_work_products(include_hidden=True)) == 1
    with pytest.raises(PermissionError):
        await store.export_work_product("hidden")


@pytest.mark.asyncio
async def test_supersession_keeps_newer_stronger_product(tmp_path: Path) -> None:
    store = ArtefactStore(tmp_path / "artefacts.db")
    await store.initialize()
    old = _product(product_id="old", target_key="same", confidence=0.5)
    new = _product(product_id="new", target_key="same", confidence=0.9)
    await store.upsert_work_product(old)
    count = await store.supersede_work_products(target_key="same", replacement=new)
    await store.upsert_work_product(new)

    assert count == 1
    rows = await store.list_work_products()
    assert [row.id for row in rows] == ["new"]
    assert (await store.get_work_product("old")).status == WorkProductStatus.SUPERSEDED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_expired_work_products_are_hidden_and_not_exportable(tmp_path: Path) -> None:
    store = ArtefactStore(tmp_path / "artefacts.db")
    await store.initialize()
    product = _product(product_id="expired")
    product.expires_at = time.time() - 1
    await store.upsert_work_product(product)

    assert await store.list_work_products() == []
    expired = await store.get_work_product("expired")
    assert expired is not None
    assert expired.status == WorkProductStatus.EXPIRED
    assert expired.adoptability == WorkProductAdoptability.HIDDEN
    with pytest.raises(PermissionError):
        await store.export_work_product("expired")


@pytest.mark.asyncio
async def test_virtual_diff_stales_when_target_hash_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "sample.py").write_text("print('one')\n", encoding="utf-8")
    subprocess.run(["git", "add", "sample.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    from vaner.intent.work_products import build_source_snapshot

    store = ArtefactStore(repo / ".vaner" / "artefacts.db")
    await store.initialize()
    product = _product()
    product.source_snapshot = build_source_snapshot(repo, ["sample.py"])
    await store.upsert_work_product(product)

    (repo / "sample.py").write_text("print('two')\n", encoding="utf-8")
    changed = await store.refresh_work_product_staleness(repo)

    assert changed == 1
    stale = await store.get_work_product(product.id)
    assert stale is not None
    assert stale.freshness == WorkProductFreshness.STALE
    assert stale.adoptability == WorkProductAdoptability.INSPECTABLE
    with pytest.raises(PermissionError):
        await store.export_work_product(product.id)
