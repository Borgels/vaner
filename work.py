#!/usr/bin/env python3
"""Supervised task runner for vaner-builder (qwen2.5-coder:32b).

Feeds one task at a time to the local builder agent,
shows output, and waits for approval before the next step.

Usage:
    python work.py "implement X"
    python work.py --plan tasks/prep_engine.md
    python work.py --plan tasks/prep_engine.md --yes
    python work.py --status
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*Event loop is closed.*")

REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(REPO_ROOT / "apps/supervisor/src"))
sys.path.insert(0, str(REPO_ROOT / "apps/vaner-builder/src"))
sys.path.insert(0, str(REPO_ROOT / "apps/repo-analyzer/src"))
sys.path.insert(0, str(REPO_ROOT / "libs/vaner-tools/src"))
sys.path.insert(0, str(REPO_ROOT / "libs/vaner-runtime/src"))


def _load_env(p: Path) -> None:
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()


_load_env(REPO_ROOT / "apps/vaner-builder/.env")
_load_env(REPO_ROOT / "apps/supervisor/.env")


async def run_all_tasks(tasks: list[str], thread_id: str, auto_yes: bool) -> None:
    from agent.graph import build_graph

    # Build graph once; reuse across all tasks
    g = await build_graph()

    print(f"\nvaner-builder (qwen2.5-coder:32b) — {len(tasks)} task(s) queued\n")

    for i, task in enumerate(tasks, 1):
        print(f"{'='*60}")
        print(f"Task {i}/{len(tasks)}: {task[:120]}{'...' if len(task) > 120 else ''}")
        print(f"{'='*60}\n")

        result = await g.ainvoke(
            {"user_input": task},
            config={"configurable": {"thread_id": thread_id}},
        )
        response = result.get("response", "")

        print("--- Builder response ---\n")
        print(response)
        print()

        # Show todos if present
        todos_path = REPO_ROOT / ".vaner" / "todos.json"
        if todos_path.exists():
            todos = json.loads(todos_path.read_text())
            print("Task plan:")
            for t in todos:
                icon = {"done": "✓", "in_progress": "→", "pending": "○"}.get(t.get("status", "?"), "?")
                print(f"  {icon} {t.get('task', '')}")
            print()

        if not auto_yes and i < len(tasks):
            answer = input(f"Proceed to task {i+1}/{len(tasks)}? [y/N/q] ").strip().lower()
            if answer == "q":
                print("Stopped.")
                break
            elif answer != "y":
                print("Paused.")
                break

    print("\nAll tasks complete.")


def show_todos() -> None:
    todos_path = REPO_ROOT / ".vaner" / "todos.json"
    if todos_path.exists():
        todos = json.loads(todos_path.read_text())
        print("\nCurrent task plan:")
        for t in todos:
            icon = {"done": "✓", "in_progress": "→", "pending": "○"}.get(t.get("status", "?"), "?")
            print(f"  {icon} [{t.get('status')}] {t.get('task', '')}")
    else:
        print("No active task plan.")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Supervised vaner-builder task runner")
    parser.add_argument("task", nargs="?", help="Single task to execute")
    parser.add_argument("--plan", help="Task plan file (one task per line, # = comment)")
    parser.add_argument("--status", action="store_true", help="Show current todos")
    parser.add_argument("--thread", default="work", help="Conversation thread ID")
    parser.add_argument("--yes", "-y", action="store_true", help="Auto-approve all tasks")
    args = parser.parse_args()

    if args.status:
        show_todos()
        return

    tasks = []
    if args.task:
        tasks = [args.task]
    elif args.plan:
        plan = Path(args.plan)
        if not plan.exists():
            print(f"Plan file not found: {plan}")
            sys.exit(1)
        tasks = [l.strip() for l in plan.read_text().splitlines()
                 if l.strip() and not l.startswith("#")]
    else:
        parser.print_help()
        sys.exit(1)

    # Single event loop for the entire run
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_all_tasks(tasks, args.thread, args.yes))
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    main()
