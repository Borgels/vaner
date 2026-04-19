# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import time

from vaner.cli.commands.query import run_query
from vaner.models.artefact import Artefact, ArtefactKind
from vaner.store.artefacts import ArtefactStore


def test_run_query_returns_injected_context_and_writes_inspect(temp_repo):
    store = ArtefactStore(temp_repo / ".vaner" / "store.db")

    async def _seed() -> None:
        await store.initialize()
        await store.upsert(
            Artefact(
                key="file_summary:sample.py",
                kind=ArtefactKind.FILE_SUMMARY,
                source_path="sample.py",
                source_mtime=time.time(),
                generated_at=time.time(),
                model="test",
                content="authentication flow and token validation",
            )
        )

    asyncio.run(_seed())
    result = run_query(temp_repo, "explain authentication flow")

    inspect_path = temp_repo / ".vaner" / "runtime" / "last_context.md"
    assert "sample.py" in result
    assert inspect_path.exists()
