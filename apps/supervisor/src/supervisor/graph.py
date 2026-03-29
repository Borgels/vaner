"""Supervisor graph — orchestrates the analyzer and broker.

Flow:
    __start__
        → check_cache_freshness
        → (conditional) refresh_cache OR route_to_broker
        → route_to_broker
        → log_task
        → self_check
        → END
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import aiosqlite

# Import sub-graphs
from analyzer.graph import graph as analyzer_graph
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph
from vaner_tools.artefact_store import read_repo_index
from vaner_tools.paths import REPO_ROOT

from agent.graph import build_graph as build_broker_graph

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class SupervisorState:
    user_input: str = ""
    response: str = ""
    cache_refreshed: bool = False
    plan: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    _needs_refresh: bool = False


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def route_after_freshness_check(state: SupervisorState) -> str:
    return "refresh_cache" if state._needs_refresh else "route_to_broker"


# ---------------------------------------------------------------------------
# Node: check_cache_freshness
# ---------------------------------------------------------------------------


async def check_cache_freshness(state: SupervisorState) -> dict[str, Any]:
    index = read_repo_index()
    if index is None:
        return {"_needs_refresh": True}

    age = time.time() - index.get("generated_at", 0)
    needs_refresh = age > 1800  # 30 minutes
    return {"_needs_refresh": needs_refresh}


# ---------------------------------------------------------------------------
# Node: refresh_cache
# ---------------------------------------------------------------------------


async def refresh_cache(state: SupervisorState) -> dict[str, Any]:
    print("[supervisor] Cache stale — running repo-analyzer...")
    try:
        result = await analyzer_graph.ainvoke({
            "target_path": ".",
            "force_refresh": False,
        })
        written = result.get("artefacts_written", [])
        print(f"[supervisor] Analyzer done: {len(written)} artefacts updated")
    except Exception as e:
        print(f"[supervisor] Analyzer error: {e}")
        return {"cache_refreshed": False, "errors": list(state.errors) + [str(e)]}

    return {"cache_refreshed": True}


# ---------------------------------------------------------------------------
# Node: route_to_broker
# ---------------------------------------------------------------------------


async def route_to_broker(state: SupervisorState) -> dict[str, Any]:
    try:
        broker_graph = await build_broker_graph()
        result = await broker_graph.ainvoke(
            {"user_input": state.user_input},
            config={"configurable": {"thread_id": "default"}},
        )
        return {"response": result.get("response", "")}
    except Exception as e:
        error_msg = f"Broker error: {e}"
        print(f"[supervisor] {error_msg}")
        return {"response": error_msg, "errors": list(state.errors) + [str(e)]}


# ---------------------------------------------------------------------------
# Node: log_task
# ---------------------------------------------------------------------------


async def log_task(state: SupervisorState) -> dict[str, Any]:
    tasks_path = REPO_ROOT / ".vaner" / "tasks.md"
    tasks_path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = (
        f"\n## {timestamp}\n"
        f"**Input:** {state.user_input}\n\n"
        f"**Response:**\n{state.response}\n\n"
        f"---\n"
    )

    with tasks_path.open("a", encoding="utf-8") as f:
        f.write(entry)

    return {}


# ---------------------------------------------------------------------------
# Node: self_check
# ---------------------------------------------------------------------------


async def self_check(state: SupervisorState) -> dict[str, Any]:
    """Run post-task quality gates: ruff, pytest, stale-reference scan."""
    issues: list[str] = []

    # 1. Ruff lint check
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "python", "-m", "ruff", "check", ".",
                "--ignore", "E501,D,T201,ANN",
                "--statistics",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        if result.returncode != 0:
            summary = (result.stdout + result.stderr).strip()[:500]
            issues.append(f"[self_check] ruff: {summary}")
            print(f"[supervisor] ⚠️  ruff issues found:\n{summary}")
        else:
            print("[supervisor] ✅ ruff: clean")
    except Exception as e:
        print(f"[supervisor] self_check: ruff unavailable — {e}")

    # 2. Pytest
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "python", "-m", "pytest",
                "apps/vaner-daemon/tests/",
                "-q", "--tb=short",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        lines = result.stdout.splitlines()
        summary = next(
            (l for l in reversed(lines) if "passed" in l or "failed" in l or "error" in l),
            result.stdout.strip()[:300],
        )
        if result.returncode != 0:
            issues.append(f"[self_check] pytest: {summary}")
            print(f"[supervisor] ⚠️  pytest failures: {summary}")
        else:
            print(f"[supervisor] ✅ pytest: {summary}")
    except Exception as e:
        print(f"[supervisor] self_check: pytest unavailable — {e}")

    # 3. Stale import paths
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "grep", "-r", "--include=*.py", "-l",
                "-e", "vaner-broker",
                "-e", "studio-agent",
                ".",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        stale = [f for f in result.stdout.splitlines() if f.strip() and ".venv" not in f]
        if stale:
            msg = f"[self_check] stale refs in: {', '.join(stale)}"
            issues.append(msg)
            print(f"[supervisor] ⚠️  {msg}")
        else:
            print("[supervisor] ✅ no stale import paths (vaner-broker / studio-agent)")
    except Exception as e:
        print(f"[supervisor] self_check: grep unavailable — {e}")

    if issues:
        return {"errors": list(state.errors) + issues}
    return {}


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

os.makedirs(str(REPO_ROOT / ".vaner"), exist_ok=True)
_SUPERVISOR_DB = str(REPO_ROOT / ".vaner" / "supervisor.db")

_builder = (
    StateGraph(SupervisorState)
    .add_node("check_cache_freshness", check_cache_freshness)
    .add_node("refresh_cache", refresh_cache)
    .add_node("route_to_broker", route_to_broker)
    .add_node("log_task", log_task)
    .add_node("self_check", self_check)
    .add_edge("__start__", "check_cache_freshness")
    .add_conditional_edges(
        "check_cache_freshness",
        route_after_freshness_check,
        {
            "refresh_cache": "refresh_cache",
            "route_to_broker": "route_to_broker",
        },
    )
    .add_edge("refresh_cache", "route_to_broker")
    .add_edge("route_to_broker", "log_task")
    .add_edge("log_task", "self_check")
    .add_edge("self_check", END)
)

# graph without checkpointer — for import-time validation / test
graph = _builder.compile(name="Vaner Supervisor")


async def build_graph():
    """Return a compiled supervisor graph with AsyncSqliteSaver checkpointer.

    Must be called from within a running event loop (e.g. inside asyncio.run()).
    Creates a fresh aiosqlite connection bound to the current event loop.
    """
    _conn_cm = aiosqlite.connect(_SUPERVISOR_DB)
    conn = await _conn_cm.__aenter__()
    checkpointer = AsyncSqliteSaver(conn)
    return _builder.compile(name="Vaner Supervisor", checkpointer=checkpointer)
