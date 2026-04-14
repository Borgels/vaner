# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

import pytest

from vaner.models.session import WorkingSet
from vaner.store.artefacts import ArtefactStore


@pytest.mark.asyncio
async def test_upsert_and_get_latest_working_set(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    now = time.time()
    first = WorkingSet(session_id="a", artefact_keys=["k1"], updated_at=now, reason="first")
    second = WorkingSet(session_id="b", artefact_keys=["k2", "k3"], updated_at=now + 10, reason="second")
    await store.upsert_working_set(first)
    await store.upsert_working_set(second)

    latest = await store.get_latest_working_set()

    assert latest is not None
    assert latest.session_id == "b"
    assert latest.artefact_keys == ["k2", "k3"]
