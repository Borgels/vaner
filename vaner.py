#!/usr/bin/env python3
"""
Vaner CLI — talk to the orchestration system.

Usage:
    python vaner.py "your task or question"
    python vaner.py "your task" --thread work-session-1
    python vaner.py "your task" --no-supervisor   # bypass supervisor, direct broker
    python vaner.py --analyze                     # run analyzer only
    python vaner.py --history                     # show recent task log
    python vaner.py init                          # install git hooks, create config
    python vaner.py daemon start                  # start background daemon
    python vaner.py daemon stop                   # stop background daemon
    python vaner.py daemon status                 # show daemon status
"""

import argparse
import asyncio
import logging
import os
import sys
import warnings
from pathlib import Path

# Suppress aiosqlite thread-cleanup noise on loop close (cosmetic, not a real error)
warnings.filterwarnings("ignore", message=".*Event loop is closed.*")
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

# Add all app src paths so we can import their graphs directly
REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(REPO_ROOT / "apps/supervisor/src"))
sys.path.insert(0, str(REPO_ROOT / "apps/vaner-builder/src"))
sys.path.insert(0, str(REPO_ROOT / "apps/repo-analyzer/src"))
sys.path.insert(0, str(REPO_ROOT / "apps/vaner-daemon/src"))
sys.path.insert(0, str(REPO_ROOT / "libs/vaner-tools/src"))
sys.path.insert(0, str(REPO_ROOT / "libs/vaner-runtime/src"))


def _load_env(env_path) -> None:
    """Minimal .env loader — no external deps."""
    if not Path(env_path).exists():
        return
    for line in Path(env_path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


# Load all agent .env files so LangSmith tracing works regardless of which agent runs
_load_env(REPO_ROOT / "apps" / "supervisor" / ".env")
_load_env(REPO_ROOT / "apps" / "studio-agent" / ".env")
_load_env(REPO_ROOT / "apps" / "repo-analyzer" / ".env")


async def run_supervisor(user_input: str, thread_id: str = "main") -> str:
    from supervisor.graph import build_graph as build_supervisor_graph
    supervisor_graph = await build_supervisor_graph()
    result = await supervisor_graph.ainvoke(
        {"user_input": user_input},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result.get("response", "")


async def run_broker_direct(user_input: str, thread_id: str = "main") -> str:
    from agent.graph import build_graph as build_broker_graph
    broker_graph = await build_broker_graph()
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


def cmd_init(repo_path: Path) -> None:
    """Initialize vaner in a repo: create config, install git hooks, update .gitignore."""
    import shutil
    import stat

    # 1. Create .vaner/config.json with defaults
    from vaner_daemon.config import DaemonConfig
    cfg = DaemonConfig.load(repo_path)
    cfg.save(repo_path)
    print(f"  ✓ Created {repo_path / '.vaner' / 'config.json'}")

    # 2. Install git hooks
    hooks_dir = repo_path / ".git" / "hooks"
    scripts_dir = REPO_ROOT / "apps" / "vaner-daemon" / "scripts"

    if not hooks_dir.exists():
        print(f"  ✗ .git/hooks/ not found at {hooks_dir} — is this a git repo?")
    else:
        for hook_name in ("post-commit", "post-checkout"):
            src = scripts_dir / hook_name
            dst = hooks_dir / hook_name
            if src.exists():
                if dst.exists():
                    # Append to existing hook rather than overwrite
                    existing = dst.read_text()
                    if "vaner" not in existing:
                        with open(dst, "a") as f:
                            f.write(f"\n# vaner hook\n{src.read_text()}\n")
                        print(f"  ✓ Appended to existing {hook_name} hook")
                    else:
                        print(f"  ~ Skipped {hook_name} (vaner already present)")
                else:
                    shutil.copy2(src, dst)
                    dst.chmod(dst.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
                    print(f"  ✓ Installed {hook_name} hook")
            else:
                print(f"  ✗ Hook script not found: {src}")

    # 3. Update .gitignore
    gitignore_path = repo_path / ".gitignore"
    vaner_entry = ".vaner/"
    if gitignore_path.exists():
        existing = gitignore_path.read_text()
        if vaner_entry not in existing:
            with open(gitignore_path, "a") as f:
                f.write(f"\n# vaner\n{vaner_entry}\n")
            print(f"  ✓ Added {vaner_entry} to .gitignore")
        else:
            print(f"  ~ .gitignore already contains {vaner_entry}")
    else:
        gitignore_path.write_text(f"# vaner\n{vaner_entry}\n")
        print(f"  ✓ Created .gitignore with {vaner_entry}")

    print()
    print("Vaner initialized! Next steps:")
    print("  python vaner.py daemon start    — start the background daemon")
    print("  python vaner.py daemon status   — check daemon status")
    print("  python vaner.py daemon stop     — stop the daemon")


def main():
    parser = argparse.ArgumentParser(description="Vaner orchestration CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Legacy positional input (keep backward compatibility)
    parser.add_argument("input", nargs="?", help="Task or question")
    parser.add_argument("--thread", default="main", help="Thread ID for conversation memory")
    parser.add_argument("--no-supervisor", action="store_true", help="Bypass supervisor, direct to broker")
    parser.add_argument("--analyze", action="store_true", help="Run repo-analyzer only")
    parser.add_argument("--history", action="store_true", help="Show recent task log")

    # daemon subcommand
    daemon_parser = subparsers.add_parser("daemon", help="Manage the vaner background daemon")
    daemon_parser.add_argument("daemon_action", choices=["start", "stop", "status"])

    # init subcommand
    subparsers.add_parser("init", help="Initialize vaner in this repo (config + git hooks)")

    args = parser.parse_args()

    if args.command == "init":
        print("Initializing vaner...")
        cmd_init(REPO_ROOT)
        return

    if args.command == "daemon":
        from vaner_daemon.daemon import daemon_start, daemon_stop, daemon_status
        if args.daemon_action == "start":
            daemon_start(REPO_ROOT)
            print("Daemon started")
        elif args.daemon_action == "stop":
            daemon_stop(REPO_ROOT)
            print("Daemon stopped")
        elif args.daemon_action == "status":
            status = daemon_status(REPO_ROOT)
            print(f"Running: {status['running']}")
            if status["running"]:
                print(f"PID: {status['pid']}")
                if status["uptime_seconds"] is not None:
                    print(f"Uptime: {status['uptime_seconds']:.0f}s")
                print(f"Branch: {status['branch']}")
                print(f"Active files: {status['active_files']}")
        return

    if args.history:
        show_history()
        return

    if args.analyze:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_analyzer())
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
        return

    if not args.input:
        parser.print_help()
        sys.exit(1)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        if args.no_supervisor:
            response = loop.run_until_complete(run_broker_direct(args.input, args.thread))
        else:
            response = loop.run_until_complete(run_supervisor(args.input, args.thread))
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()

    print(response)


if __name__ == "__main__":
    main()
