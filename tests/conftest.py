# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import platform
import threading
from pathlib import Path

import pytest

_original_threading_excepthook = threading.excepthook


def _is_windows_aiosqlite_loop_closed(args: threading.ExceptHookArgs) -> bool:
    if not isinstance(args.exc_value, RuntimeError):
        return False
    if "Event loop is closed" not in str(args.exc_value):
        return False
    thread_name = getattr(args.thread, "name", "")
    if "_connection_worker_thread" not in thread_name:
        return False
    return True


if hasattr(threading, "excepthook") and platform.system().lower().startswith("win"):

    def _windows_threading_excepthook(args: threading.ExceptHookArgs) -> None:
        if _is_windows_aiosqlite_loop_closed(args):
            return
        _original_threading_excepthook(args)

    threading.excepthook = _windows_threading_excepthook


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    return repo
