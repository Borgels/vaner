# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    return repo


@pytest.fixture(autouse=True)
def _isolate_deep_run_routing_state():
    """Reset the global Deep-Run routing state between every test.

    ``set_active_session_for_routing`` is a process-wide singleton used
    by ``vaner.router.backends`` to enforce per-session cost / locality
    gates on remote LLM calls. Several test files set it via their own
    autouse fixtures, but a leak from one test file into another (under
    pytest-randomly's order-shuffling) was occasionally tripping
    ``test_router/test_backends.py`` with a stale ``cost_cap_usd=0.0``
    session blocking the call. Anchoring the reset at conftest level
    eliminates the cross-file leak class.
    """
    try:
        from vaner.intent.deep_run_gates import (
            reset_cost_gate,
            set_active_session_for_routing,
        )
    except ImportError:
        # deep_run_gates may not import in minimal environments.
        yield
        return
    set_active_session_for_routing(None)
    reset_cost_gate(None)
    try:
        yield
    finally:
        set_active_session_for_routing(None)
        reset_cost_gate(None)
