# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from vaner.store.artefacts import ArtefactStore


@pytest.mark.asyncio
async def test_upsert_and_list_divergence(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    store = ArtefactStore(db_path)
    await store.initialize()
    await store.upsert_prior_divergence("understanding", 1.2, 80)
    rows = await store.list_prior_divergence()
    assert rows
    first = rows[0]
    assert first["category"] == "understanding"
    assert first["kl_divergence"] == pytest.approx(1.2)
    assert first["sample_count"] == 80
