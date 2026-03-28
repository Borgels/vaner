"""Supervisor graph — orchestrates the analyzer and broker.

Flow:
    __start__
        → check_cache_freshness
        → (conditional) refresh_cache OR route_to_broker
        → route_to_broker
        → log_task
        → END
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from vaner_tools.artefact_store import read_repo_index
from vaner_tools.paths import REPO_ROOT

# Import sub-graphs
from analyzer.graph import graph as analyzer_graph
from agent.graph import graph as broker_graph

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
# Graph assembly
# ---------------------------------------------------------------------------

checkpointer = MemorySaver()

graph = (
    StateGraph(SupervisorState)
    .add_node("check_cache_freshness", check_cache_freshness)
    .add_node("refresh_cache", refresh_cache)
    .add_node("route_to_broker", route_to_broker)
    .add_node("log_task", log_task)
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
    .add_edge("log_task", END)
    .compile(name="Vaner Supervisor", checkpointer=checkpointer)
)
