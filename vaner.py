#!/usr/bin/env python3
"""
Vaner CLI — talk to the orchestration system.

Usage:
    python vaner.py "your task or question"
    python vaner.py "your task" --thread work-session-1
    python vaner.py "your task" --no-supervisor   # bypass supervisor, direct broker
    python vaner.py --analyze                     # run analyzer only
    python vaner.py --history                     # show recent task log
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add all app src paths so we can import their graphs directly
REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(REPO_ROOT / "apps/supervisor/src"))
sys.path.insert(0, str(REPO_ROOT / "apps/studio-agent/src"))
sys.path.insert(0, str(REPO_ROOT / "apps/repo-analyzer/src"))
sys.path.insert(0, str(REPO_ROOT / "libs/vaner-tools/src"))


async def run_supervisor(user_input: str, thread_id: str = "main") -> str:
    from supervisor.graph import graph as supervisor_graph
    result = await supervisor_graph.ainvoke(
        {"user_input": user_input},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result.get("response", "")


async def run_broker_direct(user_input: str, thread_id: str = "main") -> str:
    from agent.graph import graph as broker_graph
    result = await broker_graph.ainvoke(
        {"user_input": user_input},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result.get("response", "")


async def run_analyzer() -> None:
    from analyzer.graph import graph as analyzer_graph
    print("Running repo-analyzer...")
    result = await analyzer_graph.ainvoke({
        "target_path": ".",
        "force_refresh": True,
    })
    written = result.get("artefacts_written", [])
    errors = result.get("errors", [])
    print(f"Done: {len(written)} artefacts written")
    if errors:
        print(f"Errors: {errors}")


def show_history() -> None:
    tasks_path = REPO_ROOT / ".vaner" / "tasks.md"
    if not tasks_path.exists():
        print("No task history yet.")
        return
    content = tasks_path.read_text(encoding="utf-8")
    # Show last ~2000 chars
    if len(content) > 2000:
        content = "...\n" + content[-2000:]
    print(content)


def main():
    parser = argparse.ArgumentParser(description="Vaner orchestration CLI")
    parser.add_argument("input", nargs="?", help="Task or question")
    parser.add_argument("--thread", default="main", help="Thread ID for conversation memory")
    parser.add_argument("--no-supervisor", action="store_true", help="Bypass supervisor, direct to broker")
    parser.add_argument("--analyze", action="store_true", help="Run repo-analyzer only")
    parser.add_argument("--history", action="store_true", help="Show recent task log")
    args = parser.parse_args()

    if args.history:
        show_history()
        return

    if args.analyze:
        asyncio.run(run_analyzer())
        return

    if not args.input:
        parser.print_help()
        sys.exit(1)

    if args.no_supervisor:
        response = asyncio.run(run_broker_direct(args.input, args.thread))
    else:
        response = asyncio.run(run_supervisor(args.input, args.thread))

    print(response)


if __name__ == "__main__":
    main()
