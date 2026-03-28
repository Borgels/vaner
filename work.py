#!/usr/bin/env python3
"""Supervised task runner for vaner-builder.

Feeds one task at a time to the local qwen2.5-coder:32b builder agent,
shows the output, and waits for approval before the next step.

Usage:
    python work.py "implement X"                    # single task
    python work.py --plan tasks/prep_engine.md      # run tasks from a file, one at a time
    python work.py --status                         # show current todos
"""
import asyncio
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


async def run_task(task: str, thread_id: str = "work") -> str:
    from agent.graph import build_graph
    g = await build_graph()
    result = await g.ainvoke(
        {"user_input": task},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result.get("response", "")


def show_todos() -> None:
    todos_path = REPO_ROOT / ".vaner" / "todos.json"
    if todos_path.exists():
        import json
        todos = json.loads(todos_path.read_text())
        print("\nCurrent task plan:")
        for t in todos:
            status = t.get("status", "?")
            icon = {"done": "✓", "in_progress": "→", "pending": "○"}.get(status, "?")
            print(f"  {icon} [{status}] {t.get('task', '')}")
    else:
        print("No active task plan.")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Supervised vaner-builder task runner")
    parser.add_argument("task", nargs="?", help="Task to execute")
    parser.add_argument("--plan", help="Path to a task plan file (one task per line)")
    parser.add_argument("--status", action="store_true", help="Show current todos")
    parser.add_argument("--thread", default="work", help="Conversation thread ID")
    parser.add_argument("--yes", "-y", action="store_true", help="Auto-approve (no prompts)")
    args = parser.parse_args()

    if args.status:
        show_todos()
        return

    tasks = []
    if args.task:
        tasks = [args.task]
    elif args.plan:
        plan_file = Path(args.plan)
        if not plan_file.exists():
            print(f"Plan file not found: {plan_file}")
            sys.exit(1)
        tasks = [
            line.strip()
            for line in plan_file.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
    else:
        parser.print_help()
        sys.exit(1)

    print(f"\nvaner-builder (qwen2.5-coder:32b) — {len(tasks)} task(s) queued\n")

    for i, task in enumerate(tasks, 1):
        print(f"{'='*60}")
        print(f"Task {i}/{len(tasks)}: {task}")
        print(f"{'='*60}\n")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response = loop.run_until_complete(run_task(task, thread_id=args.thread))
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

        print(f"\n--- Builder response ---\n")
        print(response)
        print()

        show_todos()

        if not args.yes and i < len(tasks):
            answer = input(f"\nProceed to task {i+1}? [y/N/q] ").strip().lower()
            if answer == "q":
                print("Stopped.")
                break
            elif answer != "y":
                print("Paused. Re-run to continue or provide a different task.")
                break

    print("\nDone.")


if __name__ == "__main__":
    main()
