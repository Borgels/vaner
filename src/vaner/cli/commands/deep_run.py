# SPDX-License-Identifier: Apache-2.0
"""WS4 — `vaner deep-run` CLI commands (0.8.3).

Five verbs: ``start``, ``stop``, ``status``, ``list``, ``show``.
Each is a thin shell over :mod:`vaner.server` async helpers — the
server module owns the engine construction + persistence; the CLI
owns argument parsing + rendering.

The output format is dual: human-friendly Rich rendering by default,
``--json`` for machine consumers (cockpit, desktop, agents, scripts).
The same JSON contract is used by :mod:`vaner.mcp.server` for the
``vaner.deep_run.*`` MCP tools so every surface speaks the same schema.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from vaner.intent.deep_run import DeepRunSession, DeepRunSummary
from vaner.server import (
    alist_deep_run_sessions,
    aresolve_deep_run_session,
    astart_deep_run,
    astatus_deep_run,
    astop_deep_run,
)

deep_run_app = typer.Typer(
    help=(
        "Overnight / Deep-Run mode: declare a long uninterrupted preparation window so Vaner can mature predictions and broaden coverage."
    )
)
_console = Console()


# ---------------------------------------------------------------------------
# `--until` parsing — accepts duration ("8h", "30m"), HH:MM, or ISO-8601
# ---------------------------------------------------------------------------


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_TIMEOFDAY_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def _parse_until(spec: str, *, now: float | None = None) -> float:
    """Parse ``--until`` into an absolute epoch timestamp.

    Accepted forms:
    - duration: ``30s`` / ``45m`` / ``8h`` / ``2d``
    - time of day: ``07:00`` (next occurrence; tomorrow if already past today)
    - ISO-8601: ``2026-04-25T07:00:00``

    Raises ``typer.BadParameter`` on parse error so the CLI shows a clean
    message rather than a stack trace.
    """

    base_ts = now if now is not None else time.time()
    text = spec.strip()
    if not text:
        raise typer.BadParameter("--until cannot be empty")

    m = _DURATION_RE.match(text)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * amount
        if seconds <= 0:
            raise typer.BadParameter(f"--until {spec!r} is non-positive")
        return base_ts + float(seconds)

    m = _TIMEOFDAY_RE.match(text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise typer.BadParameter(f"--until {spec!r} is not a valid time")
        now_dt = datetime.fromtimestamp(base_ts).astimezone()
        target = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target.timestamp() <= base_ts:
            target = target.replace(day=target.day + 1)
        return target.timestamp()

    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise typer.BadParameter(f"--until {spec!r} is not a recognised duration / time / ISO-8601") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC).astimezone()
    return dt.timestamp()


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _session_to_dict(session: DeepRunSession) -> dict[str, object]:
    return {
        "id": session.id,
        "status": session.status,
        "preset": session.preset,
        "focus": session.focus,
        "horizon_bias": session.horizon_bias,
        "locality": session.locality,
        "cost_cap_usd": session.cost_cap_usd,
        "spend_usd": session.spend_usd,
        "workspace_root": session.workspace_root,
        "started_at": session.started_at,
        "ends_at": session.ends_at,
        "ended_at": session.ended_at,
        "cycles_run": session.cycles_run,
        "matured_kept": session.matured_kept,
        "matured_discarded": session.matured_discarded,
        "matured_rolled_back": session.matured_rolled_back,
        "matured_failed": session.matured_failed,
        "promoted_count": session.promoted_count,
        "pause_reasons": list(session.pause_reasons),
        "cancelled_reason": session.cancelled_reason,
        "metadata": dict(session.metadata),
    }


def _summary_to_dict(summary: DeepRunSummary) -> dict[str, object]:
    return {
        "session_id": summary.session_id,
        "started_at": summary.started_at,
        "ended_at": summary.ended_at,
        "preset": summary.preset,
        "cycles_run": summary.cycles_run,
        "matured_kept": summary.matured_kept,
        "matured_discarded": summary.matured_discarded,
        "matured_rolled_back": summary.matured_rolled_back,
        "matured_failed": summary.matured_failed,
        "promoted_count": summary.promoted_count,
        "spend_usd": summary.spend_usd,
        "pause_reasons": list(summary.pause_reasons),
        "cancelled_reason": summary.cancelled_reason,
        "final_status": summary.final_status,
    }


def _human_session_panel(session: DeepRunSession) -> Table:
    """Single-session status table for `status` / `start` output."""

    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("session", session.id)
    table.add_row("status", session.status)
    table.add_row("preset", session.preset)
    table.add_row("focus / horizon", f"{session.focus} / {session.horizon_bias}")
    table.add_row("locality", session.locality)
    cap = f"${session.cost_cap_usd:.2f}" if session.cost_cap_usd > 0 else "0 (no remote spend permitted)"
    spend_pct = f" ({session.spend_usd / session.cost_cap_usd * 100:.0f}% of cap)" if session.cost_cap_usd > 0 else ""
    table.add_row("cost cap / spend", f"{cap}; spent ${session.spend_usd:.4f}{spend_pct}")
    started = datetime.fromtimestamp(session.started_at).astimezone().strftime("%H:%M:%S")
    ends = datetime.fromtimestamp(session.ends_at).astimezone().strftime("%H:%M:%S")
    table.add_row("started / ends", f"{started} → {ends}")
    table.add_row("cycles", str(session.cycles_run))
    table.add_row(
        "matured (4-count)",
        (
            f"kept={session.matured_kept} discarded={session.matured_discarded} "
            f"rolled_back={session.matured_rolled_back} failed={session.matured_failed}"
        ),
    )
    if session.pause_reasons:
        table.add_row("pause reasons", ", ".join(session.pause_reasons))
    if session.cancelled_reason:
        table.add_row("cancelled", session.cancelled_reason)
    return table


def _human_session_list(sessions: list[DeepRunSession]) -> Table:
    table = Table(show_lines=False)
    table.add_column("id", style="dim", overflow="crop", max_width=12)
    table.add_column("status")
    table.add_column("preset")
    table.add_column("started")
    table.add_column("cycles", justify="right")
    table.add_column("matured", justify="right")
    table.add_column("spend", justify="right")
    for s in sessions:
        started = datetime.fromtimestamp(s.started_at).astimezone().strftime("%m-%d %H:%M")
        matured = f"{s.matured_kept}/{s.matured_kept + s.matured_discarded + s.matured_rolled_back + s.matured_failed}"
        spend = f"${s.spend_usd:.2f}" if s.spend_usd > 0 else "—"
        table.add_row(s.id, s.status, s.preset, started, str(s.cycles_run), matured, spend)
    return table


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@deep_run_app.command("start", help="Start a Deep-Run session for a declared away window.")
def start(
    until: str = typer.Option(..., "--until", help="Window end. Forms: 8h, 07:00, 2026-04-25T07:00:00."),
    preset: str = typer.Option("balanced", "--preset", help="conservative | balanced | aggressive"),
    focus: str = typer.Option(
        "active_goals",
        "--focus",
        help="active_goals | current_workspace | all_recent",
    ),
    horizon: str = typer.Option(
        "balanced",
        "--horizon",
        help="likely_next | long_horizon | finish_partials | balanced",
    ),
    locality: str = typer.Option(
        "local_preferred",
        "--locality",
        help="local_only | local_preferred | allow_cloud",
    ),
    cost_cap_usd: float = typer.Option(
        0.0,
        "--cost-cap",
        help="USD cap for remote backend spend. 0 = no remote spend permitted.",
        min=0.0,
    ),
    workspace: str | None = typer.Option(None, "--workspace", help="Workspace root (defaults to cwd)"),
    tag: str | None = typer.Option(None, "--tag", help="Optional metadata tag"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    path: str | None = typer.Option(None, "--path", help="Repository root override"),
) -> None:
    repo_root = _repo_root(path)
    ends_at = _parse_until(until)
    metadata = {"caller": "cli"}
    if tag is not None:
        metadata["tag"] = tag
    session = asyncio.run(
        astart_deep_run(
            repo_root,
            ends_at=ends_at,
            preset=preset,
            focus=focus,
            horizon_bias=horizon,
            locality=locality,
            cost_cap_usd=cost_cap_usd,
            workspace_root=workspace,
            metadata=metadata,
        )
    )
    if as_json:
        typer.echo(json.dumps(_session_to_dict(session), indent=2))
        return
    _console.print("[bold green]Deep-Run started[/]")
    _console.print(_human_session_panel(session))


@deep_run_app.command("stop", help="Stop the currently active Deep-Run session.")
def stop(
    kill: bool = typer.Option(False, "--kill", help="Mark the session 'killed' (immediate stop)"),
    reason: str | None = typer.Option(None, "--reason", help="Optional cancellation reason"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    path: str | None = typer.Option(None, "--path", help="Repository root override"),
) -> None:
    repo_root = _repo_root(path)
    summary = asyncio.run(astop_deep_run(repo_root, kill=kill, reason=reason))
    if summary is None:
        if as_json:
            typer.echo(json.dumps(None))
        else:
            _console.print("[yellow]No active Deep-Run session to stop.[/]")
        raise typer.Exit(code=0 if not kill else 1)
    if as_json:
        typer.echo(json.dumps(_summary_to_dict(summary), indent=2))
        return
    _console.print(
        f"[bold]Deep-Run {summary.final_status}.[/] "
        f"{summary.cycles_run} cycle(s); matured kept={summary.matured_kept} "
        f"discarded={summary.matured_discarded} rolled_back={summary.matured_rolled_back} "
        f"failed={summary.matured_failed}; spent ${summary.spend_usd:.4f}."
    )


@deep_run_app.command("status", help="Show the current Deep-Run session state.")
def status(
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    path: str | None = typer.Option(None, "--path", help="Repository root override"),
) -> None:
    repo_root = _repo_root(path)
    session = asyncio.run(astatus_deep_run(repo_root))
    if session is None:
        if as_json:
            typer.echo(json.dumps(None))
        else:
            _console.print("[dim]No active Deep-Run session.[/]")
        return
    if as_json:
        typer.echo(json.dumps(_session_to_dict(session), indent=2))
        return
    _console.print(_human_session_panel(session))


@deep_run_app.command("list", help="List recent Deep-Run sessions.")
def list_(
    limit: int = typer.Option(10, "--limit", min=1, max=200, help="Max sessions to list"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    path: str | None = typer.Option(None, "--path", help="Repository root override"),
) -> None:
    repo_root = _repo_root(path)
    sessions = asyncio.run(alist_deep_run_sessions(repo_root, limit=limit))
    if as_json:
        typer.echo(json.dumps([_session_to_dict(s) for s in sessions], indent=2))
        return
    if not sessions:
        _console.print("[dim]No Deep-Run sessions recorded yet.[/]")
        return
    _console.print(_human_session_list(sessions))


@deep_run_app.command("show", help="Show a specific Deep-Run session by id.")
def show(
    session_id: str = typer.Argument(..., help="Session id (uuid hex)"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    path: str | None = typer.Option(None, "--path", help="Repository root override"),
) -> None:
    repo_root = _repo_root(path)
    session = asyncio.run(aresolve_deep_run_session(repo_root, session_id))
    if session is None:
        _console.print(f"[red]Session {session_id} not found.[/]")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(_session_to_dict(session), indent=2))
        return
    _console.print(_human_session_panel(session))


# ---------------------------------------------------------------------------
# Path helpers (mirror app.py's repo-root convention)
# ---------------------------------------------------------------------------


def _repo_root(path: str | None) -> Path:
    import os

    if path:
        return Path(path).resolve()
    env_path = os.environ.get("VANER_PATH", "").strip()
    return Path(env_path).resolve() if env_path else Path.cwd()


__all__ = ["deep_run_app"]
