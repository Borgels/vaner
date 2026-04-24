# SPDX-License-Identifier: Apache-2.0
"""`vaner integrations` CLI — introspect the client-facing integration surface."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

from vaner.cli.commands.config import load_config
from vaner.integrations.guidance import current_version, load_guidance

integrations_app = typer.Typer(
    help="Inspect and diagnose Vaner's integration layer.",
    no_args_is_help=True,
)

_DAEMON_URL = "http://127.0.0.1:8473"


@integrations_app.command("doctor", help="Run the integration-layer health check.")
def doctor_cmd(
    repo_root: Annotated[
        Path | None,
        typer.Option("--repo-root", "-C", help="Repo root whose `.vaner/` directory holds the config."),
    ] = None,
    format: Annotated[
        str,
        typer.Option("--format", help="Output format: pretty (default), json."),
    ] = "pretty",
    daemon_url: Annotated[
        str,
        typer.Option("--daemon-url", help="Override the local daemon URL."),
    ] = _DAEMON_URL,
) -> None:
    root = repo_root if repo_root is not None else Path.cwd()
    report = _collect_report(repo_root=root, daemon_url=daemon_url)
    if format == "json":
        typer.echo(json.dumps(report, indent=2, default=str))
        return
    _render_pretty(report)


def _collect_report(*, repo_root: Path, daemon_url: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "repo_root": str(repo_root),
        "daemon_url": daemon_url,
    }

    # Config snapshot
    cfg = None
    try:
        cfg = load_config(repo_root)
        integrations_cfg = cfg.integrations.model_dump(mode="json")
    except Exception as exc:  # pragma: no cover - defensive
        integrations_cfg = {"error": f"{type(exc).__name__}: {exc}"}
    report["integrations_config"] = integrations_cfg

    # Guidance asset
    try:
        doc = load_guidance("canonical")
        report["guidance"] = {
            "version": doc.version,
            "minimum_vaner_version": doc.minimum_vaner_version,
            "updated_at": doc.updated_at,
            "source_path": str(doc.source_path),
        }
    except Exception as exc:  # pragma: no cover - defensive
        report["guidance"] = {"error": f"{type(exc).__name__}: {exc}"}

    # AGENTS.md primer parity
    agents_md = _find_agents_md(repo_root)
    if agents_md is not None:
        report["agents_md_parity"] = _check_primer_parity(agents_md)
    else:
        report["agents_md_parity"] = {"present": False}

    # Daemon reachability
    report["daemon"] = _probe_daemon(daemon_url)

    # Pending-adopt handoff freshness
    report["handoff"] = _probe_handoff(repo_root)

    # Overall ok/fail signal
    report["ok"] = _overall_ok(report)
    return report


def _find_agents_md(repo_root: Path) -> Path | None:
    candidate = repo_root / "AGENTS.md"
    if candidate.exists():
        return candidate
    # Also check if we're running from a subdirectory of the Vaner repo itself.
    mod_root = Path(__file__).resolve().parents[3]
    candidate = mod_root / "AGENTS.md"
    if candidate.exists():
        return candidate
    return None


def _check_primer_parity(agents_md: Path) -> dict[str, Any]:
    """Run `scripts/sync_agents_primer.py --check` if available; else heuristic."""
    try:
        mod_root = Path(__file__).resolve().parents[3]
        script = mod_root / "scripts" / "sync_agents_primer.py"
        if script.exists():
            result = subprocess.run(
                [sys.executable, str(script), "--check"],
                cwd=str(mod_root),
                capture_output=True,
                text=True,
            )
            return {
                "present": True,
                "in_sync": result.returncode == 0,
                "path": str(agents_md),
                "detail": result.stderr.strip() if result.returncode != 0 else "synced",
            }
    except Exception as exc:  # pragma: no cover - defensive
        return {"present": True, "path": str(agents_md), "error": f"{type(exc).__name__}: {exc}"}

    # Fallback: check the marker exists and includes a version.
    text = agents_md.read_text(encoding="utf-8")
    marker = "<!-- vaner-primer:start"
    return {
        "present": True,
        "in_sync": marker in text,
        "path": str(agents_md),
        "detail": "marker_found" if marker in text else "marker_missing",
    }


def _probe_daemon(url: str) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=1.5) as client:
            resp = client.get(f"{url}/health")
            resp.raise_for_status()
            guidance_resp = client.get(f"{url}/integrations/guidance")
            guidance_ok = guidance_resp.status_code == 200
            return {
                "reachable": True,
                "url": url,
                "health": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else "ok",
                "guidance_endpoint_ok": guidance_ok,
            }
    except httpx.HTTPError as exc:
        return {"reachable": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}


def _probe_handoff(repo_root: Path) -> dict[str, Any]:
    path = repo_root / ".vaner" / "pending-adopt.json"
    if not path.exists():
        return {"present": False, "path": str(path)}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return {
            "present": True,
            "path": str(path),
            "adopted_from_prediction_id": data.get("adopted_from_prediction_id"),
            "resolution_id": data.get("resolution_id"),
            "age_hint": data.get("adopted_at"),
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {"present": True, "path": str(path), "parse_error": str(exc)}


def _overall_ok(report: dict[str, Any]) -> bool:
    if "error" in report.get("guidance", {}):
        return False
    if report.get("agents_md_parity", {}).get("present") and not report["agents_md_parity"].get("in_sync"):
        return False
    return True


def _render_pretty(report: dict[str, Any]) -> None:
    console = Console()
    console.rule("[bold]Vaner integrations doctor[/bold]")
    console.print(f"Repo root: [cyan]{report['repo_root']}[/cyan]")
    console.print(f"Daemon URL: [cyan]{report['daemon_url']}[/cyan]")
    console.print()

    # Guidance
    table = Table(title="Guidance asset", show_header=True, header_style="bold")
    table.add_column("Field")
    table.add_column("Value")
    guidance = report["guidance"]
    if "error" in guidance:
        table.add_row("error", f"[red]{guidance['error']}[/red]")
    else:
        table.add_row("version", str(guidance["version"]))
        table.add_row("minimum_vaner_version", guidance["minimum_vaner_version"])
        table.add_row("updated_at", guidance["updated_at"])
        table.add_row("source", guidance["source_path"])
    console.print(table)

    # Primer parity
    parity = report["agents_md_parity"]
    status = "✓ synced" if parity.get("in_sync") else ("[red]drift[/red]" if parity.get("present") else "[yellow]missing[/yellow]")
    console.print(f"AGENTS.md primer: {status}")
    if parity.get("detail"):
        console.print(f"  {parity['detail']}")
    console.print()

    # Config
    cfg_table = Table(title="Integrations config", show_header=True, header_style="bold")
    cfg_table.add_column("Key")
    cfg_table.add_column("Value")
    cfg = report["integrations_config"]
    if "error" in cfg:
        cfg_table.add_row("error", f"[red]{cfg['error']}[/red]")
    else:
        cfg_table.add_row("guidance_variant", str(cfg["guidance_variant"]))
        cfg_table.add_row("advertise_guidance_resource", str(cfg["advertise_guidance_resource"]))
        cfg_table.add_row("capability_detection_enabled", str(cfg["capability_detection_enabled"]))
        ci = cfg["context_injection"]
        cfg_table.add_row("context_injection.mode", str(ci["mode"]))
        cfg_table.add_row("context_injection.digest_token_budget", str(ci["digest_token_budget"]))
        cfg_table.add_row(
            "context_injection.adopted_package_token_budget",
            str(ci["adopted_package_token_budget"]),
        )
        cfg_table.add_row(
            "context_injection.max_context_fraction",
            f"{ci['max_context_fraction']:.2f}",
        )
        cfg_table.add_row("context_injection.ttl_seconds", str(ci["ttl_seconds"]))
    console.print(cfg_table)

    # Daemon + handoff
    daemon = report["daemon"]
    if daemon["reachable"]:
        console.print(f"Daemon: [green]reachable[/green]  guidance endpoint: {'ok' if daemon['guidance_endpoint_ok'] else 'FAIL'}")
    else:
        console.print(f"Daemon: [red]unreachable[/red]  ({daemon.get('error', 'unknown')})")
    handoff = report["handoff"]
    if handoff["present"]:
        console.print(f"Pending handoff: present  ({handoff.get('path')})")
    else:
        console.print(f"Pending handoff: none  ({handoff.get('path')})")
    console.print()
    console.print("Overall: [green]OK[/green]" if report["ok"] else "Overall: [red]needs attention[/red]")


@integrations_app.command("tier", help="Print the current guidance version; helpful in CI probes.")
def tier_cmd() -> None:
    typer.echo(f"guidance_version={current_version()}")
