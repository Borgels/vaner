# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio

import pytest

from vaner.intent.adapter import CodeRepoAdapter


def test_code_repo_adapter_get_item_rejects_escape(temp_repo) -> None:
    adapter = CodeRepoAdapter(temp_repo)

    with pytest.raises(ValueError):
        asyncio.run(adapter.get_item("file:../outside.py"))
