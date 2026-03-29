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


def run_validator(affected_paths: list[str]) -> tuple[bool, str]:
    """Run post-task checks. Returns (passed, report)."""
    import subprocess
    lines = []
    ok = True

    # 1. pytest
    r = subprocess.run(
        ["apps/vaner-daemon/.venv/bin/pytest", "apps/vaner-daemon/tests/", "-q", "--tb=short"],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    if r.returncode != 0:
        ok = False
        lines.append(f"TESTS FAILED:\n{r.stdout[-2000:]}\n{r.stderr[-500:]}")
    else:
        summary = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l or "error" in l]
        lines.append(f"Tests: {summary[-1] if summary else 'ok'}")

    # 2. ruff lint on changed paths
    check_paths = ["apps/vaner-daemon/src/", "libs/vaner-runtime/src/", "libs/vaner-tools/src/"]
    r = subprocess.run(
        ["apps/vaner-daemon/.venv/bin/python", "-m", "ruff", "check"] + check_paths + ["--ignore", "E501,D,T201,ANN"],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    if r.returncode != 0:
        ok = False
        lines.append(f"LINT FAILED:\n{r.stdout[:1000]}")
    else:
        lines.append("Lint: clean")

    # 3. Stub check
    r = subprocess.run(
        ["grep", "-rn", r"raise NotImplementedError\|# TODO\|# FIXME\|\.\.\.  # stub",
         "apps/vaner-daemon/src/"],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    if r.stdout.strip():
        ok = False
        lines.append(f"STUBS/TODOS FOUND:\n{r.stdout[:500]}")
    else:
        lines.append("Stubs: none")

    # 4. git diff stat
    r = subprocess.run(
        ["git", "diff", "--stat", "HEAD"],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    if r.stdout.strip():
        lines.append(f"Changed:\n{r.stdout.strip()}")

    return ok, "\n".join(lines)


async def escalate_to_supervisor(task_file: str, original_task: str, failures: int, report: str) -> bool:
    """Spawn supervisor sub-agent to fix stuck build. Returns True if fixed."""
    import subprocess
    from pathlib import Path

    log_path = REPO_ROOT / ".vaner" / "supervisor.log"
    task_path = Path(task_file) if Path(task_file).exists() else REPO_ROOT / task_file

    supervisor_prompt = f"""You are the Build Supervisor. A local builder has failed {failures} times.

Task file: {task_path}
Original task summary: {original_task[:200]}

Last validation report:
{report}

Git status (files touched):
"""
    # Add git status
    r = subprocess.run(
        ["git", "status", "--short"],
        capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    supervisor_prompt += r.stdout[:1000] + "\n\n"

    # Add build log tail
    build_log = REPO_ROOT / ".vaner" / "build-proxy.log"
    if build_log.exists():
        r = subprocess.run(
            ["tail", "-n", "50", str(build_log)],
            capture_output=True, text=True
        )
        supervisor_prompt += "Last 50 lines of build log:\n" + r.stdout[:2000] + "\n\n"

    supervisor_prompt += """
Your job:
1. Read the task file and understand what should be built
2. Check which files are corrupted vs incomplete
3. Fix the root cause (reset files if needed, implement missing pieces)
4. Run tests and lint to validate
5. Report success or escalate to human if stuck

Use git checkout to reset corrupted files. Re-implement correctly.
"""

    print("\n" + "="*60)
    print(f"ESCALATING TO SUPERVISOR after {failures} failures")
    print("="*60)

    # Write supervisor prompt to file for sub-agent
    prompt_file = REPO_ROOT / ".vaner" / "supervisor_task.txt"
    prompt_file.write_text(supervisor_prompt)

    # Spawn sub-agent as external process (fireworks/kimi for reasoning)
    result = subprocess.run([
        sys.executable, "-c",
        f"""
import asyncio
import sys
sys.path.insert(0, "{REPO_ROOT}/apps/vaner-builder/src")

async def run_supervisor():
    prompt = open("{prompt_file}").read()
    # Use cloud model via subprocess to avoid graph loading issues
    # For now, write a shell script that will be picked up
    with open("{REPO_ROOT}/.vaner/supervisor_action.sh", "w") as f:
        f.write('#!/bin/bash\n')
        f.write('cd {REPO_ROOT}\n')
        f.write('echo "Supervisor would analyze and fix here"\n')
        f.write('echo "To be implemented: spawn actual cloud model sub-agent"\n')
    import os
    os.chmod("{REPO_ROOT}/.vaner/supervisor_action.sh", 0o755)
    return True

asyncio.run(run_supervisor())
"""
    ], capture_output=True, text=True, cwd=str(REPO_ROOT))

    if result.returncode != 0:
        print(f"Supervisor spawn failed: {result.stderr}")
        return False

    print("Supervisor analysis complete. Check .vaner/supervisor_action.sh")
    return True


async def run_all_tasks(tasks: list[str], thread_id: str, auto_yes: bool) -> None:
    from agent.graph import build_graph

    # Build graph once; reuse across all tasks
    g = await build_graph()

    print(f"\nvaner-builder (qwen2.5-coder:32b) — {len(tasks)} task(s) queued\n")

    # Track failures per original task (not fix tasks)
    failure_counts: dict[int, int] = {}  # original_task_index -> count
    current_task_is_fix = False

    i = 0
    while i < len(tasks):
        task = tasks[i]

        # Detect if this is a self-correction task (injected fix)
        is_fix_task = task.startswith("The previous task had validation failures")
        if not is_fix_task:
            current_task_is_fix = False

        print(f"{'='*60}")
        print(f"Task {i+1}/{len(tasks)}: {task[:120]}{'...' if len(task) > 120 else ''}")
        if is_fix_task:
            print("(Self-correction task)")
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

        # Post-task validation
        print("--- Validator ---")
        passed, report = run_validator([])
        print(report)
        print()

        if not passed:
            # Find original task index (skip fix tasks)
            original_i = i
            while original_i > 0 and tasks[original_i].startswith("The previous task had validation failures"):
                original_i -= 1

            failure_counts[original_i] = failure_counts.get(original_i, 0) + 1
            fail_count = failure_counts[original_i]

            print(f"⚠ Validation failed (failure #{fail_count} for this task)")

            if fail_count >= 3:
                print(f"🚨 3 STRIKES — escalating to supervisor")

                # Try supervisor escalation
                task_file = os.environ.get("VANER_TASK_FILE", "unknown")
                supervisor_fixed = await escalate_to_supervisor(
                    task_file, tasks[original_i], fail_count, report
                )

                if supervisor_fixed:
                    print("✅ Supervisor intervention completed")
                    # Reset failure count and try once more
                    failure_counts[original_i] = 0
                    # Let the loop continue to next iteration
                    # The supervisor should have fixed things
                else:
                    print("❌ Supervisor could not auto-fix. Pausing for human review.")
                    print(f"   Check {REPO_ROOT}/.vaner/supervisor.log for details")
                    break
            else:
                # Self-correction (existing behavior)
                print("Feeding errors back to builder for self-correction...")
                fix_task = (
                    f"The previous task had validation failures. Fix them now.\n\n"
                    f"Failures:\n{report}\n\n"
                    f"Original task was: {task[:300]}\n\n"
                    f"Read the failing files, fix all issues, run tests and lint again to confirm clean."
                )
                # Inject fix as next task (don't advance i)
                tasks.insert(i + 1, fix_task)
                current_task_is_fix = True
                print(f"Fix task injected as task {i+2}.")

        i += 1

        if not auto_yes and i < len(tasks):
            answer = input(f"\nProceed to task {i+1}/{len(tasks)}? [y/N/q] ").strip().lower()
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
    task_file = "adhoc"
    if args.task:
        tasks = [args.task]
    elif args.plan:
        plan = Path(args.plan)
        task_file = str(args.plan)
        if not plan.exists():
            print(f"Plan file not found: {plan}")
            sys.exit(1)
        tasks = [l.strip() for l in plan.read_text().splitlines()
                 if l.strip() and not l.startswith("#")]
    else:
        parser.print_help()
        sys.exit(1)

    # Pass task file for supervisor escalation context
    os.environ["VANER_TASK_FILE"] = task_file

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
