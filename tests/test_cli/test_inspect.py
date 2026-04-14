# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import time

from vaner.cli.commands.inspect import inspect_cache, inspect_last
from vaner.models.artefact import Artefact, ArtefactKind
from vaner.store.artefacts import ArtefactStore


def test_inspect_cache_lists_entries(temp_repo):
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
                content="summary",
            )
        )

    asyncio.run(_seed())
    output = inspect_cache(temp_repo)
    assert "file_summary:sample.py" in output


def test_inspect_last_reads_runtime_file(temp_repo):
    path = temp_repo / ".vaner" / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    last = path / "last_context.md"
    last.write_text("token_used: 1/10", encoding="utf-8")
    assert "token_used" in inspect_last(temp_repo)
