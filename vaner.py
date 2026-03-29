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
_load_env(REPO_ROOT / "apps" / "vaner-builder" / ".env")
_load_env(REPO_ROOT / "apps" / "repo-analyzer" / ".env")


async def run_supervisor(user_input: str, thread_id: str = "main") -> str:
    from supervisor.graph import build_graph as build_supervisor_graph
    supervisor_graph = await build_supervisor_graph()
    config = {"configurable": {"thread_id": thread_id}}
    final_response = ""
    last_node = None
    async for chunk in supervisor_graph.astream(
        {"user_input": user_input},
        config=config,
        stream_mode="updates",
    ):
        for node_name, state_update in chunk.items():
            # Print node transition indicator
            if node_name != last_node:
                print(f"\n[{node_name}] ...", end="", flush=True)
                last_node = node_name
            # Stream any new response content
            if isinstance(state_update, dict) and state_update.get("response"):
                response = state_update["response"]
                if response != final_response:
                    new_content = response[len(final_response):]
                    print(new_content, end="", flush=True)
                    final_response = response
    print()  # Final newline
    return final_response


async def run_broker_direct(user_input: str, thread_id: str = "main") -> str:
    from agent.graph import build_graph as build_broker_graph
    broker_graph = await build_broker_graph()
    config = {"configurable": {"thread_id": thread_id}}
    final_response = ""
    last_node = None
    async for chunk in broker_graph.astream(
        {"user_input": user_input},
        config=config,
        stream_mode="updates",
    ):
        for node_name, state_update in chunk.items():
            # Print node transition indicator
            if node_name != last_node:
                print(f"\n[{node_name}] ...", end="", flush=True)
                last_node = node_name
            # Stream any new response content
            if isinstance(state_update, dict) and state_update.get("response"):
                response = state_update["response"]
                if response != final_response:
                    new_content = response[len(final_response):]
                    print(new_content, end="", flush=True)
                    final_response = response
    print()  # Final newline
    return final_response


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


def _cmd_status() -> None:
    import subprocess as sp
    from vaner_daemon.daemon import daemon_status
    from vaner_tools.artefact_store import count_artefacts

    status = daemon_status(REPO_ROOT)
    print("Vaner Status")
    print("=" * 44)
    if status["running"]:
        uptime = f"{status['uptime_seconds']:.0f}s" if status["uptime_seconds"] else "?"
        print(f"Daemon:    running (PID {status['pid']}, uptime {uptime})")
        print(f"Branch:    {status['branch'] or '?'}")
        files = status["active_files"]
        print(f"Active:    {', '.join(files[:3]) or 'none'}{'...' if len(files) > 3 else ''}")
        proxy_port = status.get("proxy_port", 11435)
        proxy_on = status.get("proxy_running", False)
        print(f"Proxy:     {'running on :' + str(proxy_port) if proxy_on else 'stopped'}")
    else:
        print("Daemon:    stopped")
    try:
        n = count_artefacts()
        print(f"Artifacts: {n} in cache")
    except Exception:
        print("Artifacts: unavailable")
    try:
        r = sp.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            print(f"GPU:       {r.stdout.strip()}")
    except Exception:
        pass
    import os
    if os.environ.get("LANGSMITH_TRACING") == "true":
        print("LangSmith: https://eu.smith.langchain.com (tracing enabled)")
    else:
        print("LangSmith: disabled")


def _cmd_inspect(path: str | None) -> None:
    import time as _time
    from vaner_tools.artefact_store import list_artefacts, read_artefact

    if path:
        a = read_artefact("file_summary", path) or read_artefact("diff_summary", path)
        if not a:
            print(f"No artifact found for: {path}")
            return
        import datetime
        ts = datetime.datetime.fromtimestamp(a.generated_at).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{a.kind}: {a.source_path}")
        print(f"Generated: {ts}  Model: {a.model}")
        print("─" * 55)
        print(a.content)
    else:
        arts = sorted(list_artefacts(), key=lambda x: x.generated_at, reverse=True)
        print(f"Artifacts in cache ({len(arts)} total):\n")
        for a in arts[:20]:
            age_s = _time.time() - a.generated_at
            age = f"{int(age_s//60)}m" if age_s > 60 else f"{int(age_s)}s"
            chars = len(a.content)
            print(f"  [{a.kind:<14}]  {a.source_path:<40} age: {age:<6} chars: {chars}")


def _cmd_migrate() -> None:
    from vaner_tools.artefact_store import migrate_from_json
    print("Migrating artifact cache from JSON to SQLite...")
    n = migrate_from_json(REPO_ROOT / ".vaner" / "cache")
    print(f"Migrated {n} artifacts.")
    print("Done. Run 'python vaner.py daemon start' to resume.")


def _cmd_proxy_config() -> None:
    from vaner_daemon.daemon import daemon_status
    from vaner_tools.artefact_store import count_artefacts

    status = daemon_status(REPO_ROOT)
    proxy_port = status.get("proxy_port", 11435)
    proxy_on = status.get("proxy_running", False) and status.get("running", False)
    try:
        n = count_artefacts()
    except Exception:
        n = 0

    print("Vaner Proxy Configuration")
    print("=" * 44)
    print(f"Status:    {'running (port ' + str(proxy_port) + ')' if proxy_on else 'stopped'}")
    print("Upstream:  http://localhost:11434")
    print(f"Artifacts: {n} in cache")
    print()
    print("Editor setup:")
    print(f"  Cursor:         Settings > API Base URL > http://localhost:{proxy_port}")
    print(f"  VS Code (Continue): set apiBase to http://localhost:{proxy_port} in config.json")
    print(f"  Any OpenAI client:  set base URL to http://localhost:{proxy_port}")
    print()
    print(f"Test with:  curl -s http://localhost:{proxy_port}/health")


def _cmd_metrics_eval(days: int = 7, db_path: str | None = None) -> None:
    """Print a weekly summary table of eval signals."""
    from pathlib import Path as _Path
    from vaner_runtime.eval import load_signals

    _db = _Path(db_path) if db_path else _Path.home() / ".vaner" / "eval.db"
    signals = load_signals(_db, since_days=days)

    if not signals:
        print(f"No eval signals found in {_db} for the last {days} day(s).")
        return

    injected = [s for s in signals if s.injected]
    non_injected = [s for s in signals if not s.injected]
    scored = [s for s in signals if s.helpfulness is not None]
    avg_h = sum(s.helpfulness for s in scored) / len(scored) if scored else None
    reprompt_inj = sum(1 for s in injected if s.reprompted) / len(injected) if injected else 0.0
    reprompt_non = sum(1 for s in non_injected if s.reprompted) / len(non_injected) if non_injected else 0.0
    model_ref_pct = sum(1 for s in signals if s.model_referenced) / len(signals)

    print(f"\nEval Metrics — last {days} day(s)  (source: {_db})")
    print("=" * 52)
    print(f"  Total signals:              {len(signals)}")
    print(f"  Injected:                   {len(injected)}")
    print(f"  Non-injected:               {len(non_injected)}")
    print(f"  Avg helpfulness:            {avg_h:.3f}" if avg_h is not None else "  Avg helpfulness:            n/a")
    print(f"  Reprompt rate (injected):   {reprompt_inj:.1%}")
    print(f"  Reprompt rate (baseline):   {reprompt_non:.1%}")
    print(f"  Model-referenced:           {model_ref_pct:.1%}")
    print()


def _cmd_metrics_eval() -> None:
    from vaner_runtime.eval import load_signals

    db_path = REPO_ROOT / ".vaner" / "eval.db"
    signals = load_signals(db_path, since_days=7)

    total = len(signals)
    with_ctx = sum(1 for s in signals if s.injected)
    without_ctx = total - with_ctx

    print("Vaner Eval Metrics (last 7 days)")
    print("==================================")
    print(f"Signals recorded:        {total}")
    print(f"With context injected:   {with_ctx}")
    print(f"Without injection:       {without_ctx}")

    if total == 0:
        print()
        print("No data yet — run some sessions first.")
        return

    scores = [s.helpfulness for s in signals if s.helpfulness is not None]
    avg_score = sum(scores) / len(scores) if scores else None

    with_reprompts = sum(1 for s in signals if s.injected and s.reprompted)
    without_reprompts = sum(1 for s in signals if not s.injected and s.reprompted)

    rate_with = (with_reprompts / with_ctx * 100) if with_ctx else 0
    rate_without = (without_reprompts / without_ctx * 100) if without_ctx else 0

    print()
    if avg_score is not None:
        print(f"Avg helpfulness score:   {avg_score:.2f}")
    print(f"Reprompt rate (with):    {rate_with:.0f}%")
    print(f"Reprompt rate (without): {rate_without:.0f}%")


def main():
    parser = argparse.ArgumentParser(description="Vaner orchestration CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Legacy positional input (keep backward compatibility)
    parser.add_argument("input", nargs="?", help="Task or question")
    parser.add_argument("--thread", default="main", help="Thread ID for conversation memory")
    parser.add_argument("--no-supervisor", action="store_true", help="Bypass supervisor, direct to broker")
    parser.add_argument("--analyze", action="store_true", help="Run repo-analyzer only")
    parser.add_argument("--watch", action="store_true", help="Start file watcher for auto-refresh of stale artefacts")
    parser.add_argument("--history", action="store_true", help="Show recent task log")

    # daemon subcommand
    daemon_parser = subparsers.add_parser("daemon", help="Manage the vaner background daemon")
    daemon_parser.add_argument("daemon_action", choices=["start", "stop", "status"])

    # init subcommand
    subparsers.add_parser("init", help="Initialize vaner in this repo (config + git hooks)")

    # status subcommand
    subparsers.add_parser("status", help="Show full system health")

    # inspect subcommand
    inspect_parser = subparsers.add_parser("inspect", help="Inspect artifact cache")
    inspect_parser.add_argument("path", nargs="?", help="Source path to inspect")

    # migrate subcommand
    subparsers.add_parser("migrate", help="Migrate artifact cache from JSON to SQLite")

    # proxy subcommand
    proxy_parser = subparsers.add_parser("proxy", help="Proxy configuration")
    proxy_parser.add_argument("proxy_action", choices=["config"])

    # metrics subcommand
    metrics_parser = subparsers.add_parser("metrics", help="Show eval and performance metrics")
    metrics_parser.add_argument("--eval", action="store_true", help="Show eval loop metrics (last 7 days)")

    # metrics subcommand
    metrics_parser = subparsers.add_parser("metrics", help="Show evaluation metrics")
    metrics_parser.add_argument("--eval", action="store_true", help="Show weekly eval signal summary")
    metrics_parser.add_argument("--days", type=int, default=7, help="Look-back window in days (default: 7)")
    metrics_parser.add_argument("--db", default=None, help="Path to eval DB (default: ~/.vaner/eval.db)")

    args = parser.parse_args()

    # Start file watcher if --watch is set
    _watcher = None
    if getattr(args, "watch", False):
        try:
            from analyzer.watcher import start_watcher
            _watcher = start_watcher()
            print("File watcher started — watching for changes in", REPO_ROOT)
        except ImportError as _e:
            print(f"Warning: could not start file watcher: {_e}")

    if args.command == "init":
        print("Initializing vaner...")
        cmd_init(REPO_ROOT)
        return

    if args.command == "status":
        _cmd_status()
        return

    if args.command == "inspect":
        _cmd_inspect(getattr(args, "path", None))
        return

    if args.command == "migrate":
        _cmd_migrate()
        return

    if args.command == "proxy":
        _cmd_proxy_config()
        return

    if args.command == "metrics":
        if getattr(args, "eval", False):
            _cmd_metrics_eval()
        else:
            parser.parse_args(["metrics", "--help"])
        return

    if args.command == "metrics":
        if getattr(args, "eval", False):
            _cmd_metrics_eval(
                days=getattr(args, "days", 7),
                db_path=getattr(args, "db", None),
            )
        else:
            parser.parse_args(["metrics", "--help"])
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
            loop.run_until_complete(run_broker_direct(args.input, args.thread))
        else:
            loop.run_until_complete(run_supervisor(args.input, args.thread))
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    main()
