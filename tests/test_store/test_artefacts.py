# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

import pytest

from vaner.models.artefact import Artefact, ArtefactKind
from vaner.store.artefacts import ArtefactStore


@pytest.mark.asyncio
async def test_store_upsert_and_list(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    artefact = Artefact(
        key="file_summary:sample.py",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path="sample.py",
        source_mtime=time.time(),
        generated_at=time.time(),
        model="test",
        content="summary",
    )
    await store.upsert(artefact)
    rows = await store.list()
    assert len(rows) == 1
    assert rows[0].key == artefact.key
