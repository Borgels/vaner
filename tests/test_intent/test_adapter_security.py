# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio

import pytest

from vaner.intent.adapter import CodeRepoAdapter


def test_code_repo_adapter_get_item_rejects_escape(temp_repo) -> None:
    adapter = CodeRepoAdapter(temp_repo)

    with pytest.raises(ValueError):
        asyncio.run(adapter.get_item("file:../outside.py"))


def test_code_repo_adapter_default_scan_reaches_past_500_files(temp_repo) -> None:
    for idx in range(520):
        (temp_repo / f"f_{idx:03d}.py").write_text(f"# file {idx}\n", encoding="utf-8")
    target = temp_repo / "src" / "vaner" / "intent" / "cache.py"
    target.parent.mkdir(parents=True)
    target.write_text("class TieredPredictionCache: ...\n", encoding="utf-8")

    adapter = CodeRepoAdapter(temp_repo)
    items = asyncio.run(adapter.list_items())

    assert any(item.metadata.get("path") == "src/vaner/intent/cache.py" for item in items)
