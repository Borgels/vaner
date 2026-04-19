# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import traceback
from pathlib import Path

import aiosqlite
import httpx
import typer

from vaner import api
from vaner.cli.commands.config import load_config
from vaner.cli.commands.daemon import daemon_status, run_daemon_forever, start_daemon, stop_daemon
from vaner.cli.commands.init import init_repo
from vaner.cli.commands.profile import export_pins, import_pins, pin_fact, profile_show, unpin_fact
from vaner.daemon.runner import VanerDaemon
from vaner.eval import evaluate_repo, run_eval
from vaner.router.proxy import create_app

app = typer.Typer(help="Vaner CLI")
daemon_app = typer.Typer(help="Daemon controls")
config_app = typer.Typer(help="Show config")
profile_app = typer.Typer(help="Profile memory controls")
_VERBOSE = False


def _repo_root(path: str | None) -> Path:
    return Path(path).resolve() if path else Path.cwd()


def _friendly_error_message(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return f"File not found: {exc}. Check your repo path or run `vaner init`."
    if isinstance(exc, PermissionError):
        return f"Permission denied: {exc}. Check file permissions for `.vaner/` and your repo."
    if isinstance(exc, httpx.HTTPError):
        return f"Backend request failed: {exc}. Check network access and API key configuration."
    if isinstance(exc, aiosqlite.Error):
        return f"Database error: {exc}. Remove `.vaner/store.db` if it is corrupted, then rerun."
    return f"Vaner failed: {exc}"


@app.callback()
def app_callback(
    verbose: bool = typer.Option(False, "--verbose", help="Show full traceback on errors"),
) -> None:
    global _VERBOSE
    _VERBOSE = verbose


@app.command("init")
def init(path: str | None = typer.Option(None, help="Repository root")) -> None:
    config_path = init_repo(_repo_root(path))
    typer.echo(f"Initialized Vaner at {config_path}")


@daemon_app.command("start")
def daemon_start(
    path: str | None = typer.Option(None, help="Repository root"),
    once: bool = typer.Option(True, help="Run one cycle only"),
    interval_seconds: int = typer.Option(15, "--interval-seconds", help="Loop interval for background mode"),
) -> None:
    written = start_daemon(_repo_root(path), once=once, interval_seconds=interval_seconds)
    typer.echo(f"Daemon started. Artefacts written: {written}")


@daemon_app.command("stop")
def daemon_stop(path: str | None = typer.Option(None, help="Repository root")) -> None:
    ok = stop_daemon(_repo_root(path))
    typer.echo("Daemon stopped." if ok else "Daemon not running.")


@daemon_app.command("status")
def daemon_show_status(path: str | None = typer.Option(None, help="Repository root")) -> None:
    typer.echo(daemon_status(_repo_root(path)))


@daemon_app.command("run-forever", hidden=True)
def daemon_run_forever(
    path: str | None = typer.Option(None, help="Repository root"),
    interval_seconds: int = typer.Option(15, "--interval-seconds", help="Loop interval"),
) -> None:
    run_daemon_forever(_repo_root(path), interval_seconds=interval_seconds)


@app.command("inspect")
def inspect(
    path: str | None = typer.Option(None, help="Repository root"),
    last: bool = typer.Option(False, "--last", help="Show last context decision"),
) -> None:
    if last:
        typer.echo(api.inspect_last(_repo_root(path)))
        return
    typer.echo(api.inspect(_repo_root(path)))


@app.command("query")
def query(prompt: str, path: str | None = typer.Option(None, help="Repository root")) -> None:
    package = api.query(prompt, _repo_root(path))
    typer.echo(package.injected_context)


@app.command("prepare")
def prepare(path: str | None = typer.Option(None, help="Repository root")) -> None:
    generated = api.prepare(_repo_root(path))
    typer.echo(f"Prepared artefacts: {generated}")


@app.command("predict")
def predict(
    path: str | None = typer.Option(None, help="Repository root"),
    top_k: int = typer.Option(5, "--top-k", help="Number of predictions to return"),
) -> None:
    predictions = api.predict(_repo_root(path), top_k=top_k)
    typer.echo(json.dumps(predictions, indent=2))


@app.command("precompute")
def precompute(path: str | None = typer.Option(None, help="Repository root")) -> None:
    produced = api.precompute(_repo_root(path))
    typer.echo(f"Precompute cycle completed. Full packages cached: {produced}")


@app.command("forget")
def forget(path: str | None = typer.Option(None, help="Repository root")) -> None:
    removed = api.forget(_repo_root(path))
    typer.echo(f"Removed {removed} local state files.")


@config_app.command("show")
def config_show(path: str | None = typer.Option(None, help="Repository root")) -> None:
    config = load_config(_repo_root(path))
    typer.echo(config.model_dump_json(indent=2))


@profile_app.command("show")
def profile_show_command(path: str | None = typer.Option(None, help="Repository root")) -> None:
    payload = profile_show(_repo_root(path))
    typer.echo(json.dumps(payload, indent=2))


@profile_app.command("pin")
def profile_pin_command(
    assignment: str = typer.Argument(..., help="Pin as KEY=VALUE"),
    path: str | None = typer.Option(None, help="Repository root"),
    scope: str = typer.Option("user", "--scope", help="Pin scope: user|project|workflow"),
) -> None:
    saved = pin_fact(_repo_root(path), assignment, scope=scope)
    typer.echo(f"Pinned {saved['key']} in scope={saved['scope']}.")


@profile_app.command("unpin")
def profile_unpin_command(
    key: str = typer.Argument(..., help="Pin key to remove"),
    path: str | None = typer.Option(None, help="Repository root"),
) -> None:
    removed = unpin_fact(_repo_root(path), key)
    typer.echo(f"Removed pin '{key}'." if removed else f"Pin '{key}' not found.")


@profile_app.command("export")
def profile_export_command(
    path: str | None = typer.Option(None, help="Repository root"),
    out: str | None = typer.Option(None, "--out", help="Destination file"),
) -> None:
    output_path = export_pins(_repo_root(path), Path(out).resolve() if out else None)
    typer.echo(f"Exported pins to {output_path}")


@profile_app.command("import")
def profile_import_command(
    source: str = typer.Argument(..., help="Pin file to import"),
    path: str | None = typer.Option(None, help="Repository root"),
) -> None:
    imported = import_pins(_repo_root(path), Path(source).resolve())
    typer.echo(f"Imported {imported} pins.")


@app.command("eval")
def eval_repo(path: str | None = typer.Option(None, help="Repository root")) -> None:
    result = evaluate_repo(_repo_root(path))
    typer.echo(result.model_dump_json(indent=2))


@app.command("run-eval")
def run_eval_command(
    path: str | None = typer.Option(None, help="Repository root"),
    cases_file: str | None = typer.Option(None, "--cases-file", help="Path to eval cases JSON"),
    output_dir: str | None = typer.Option(None, "--output-dir", help="Directory for eval run JSON output"),
) -> None:
    repo_root = _repo_root(path)
    result = run_eval(
        repo_root,
        cases_path=Path(cases_file).resolve() if cases_file else None,
        output_dir=Path(output_dir).resolve() if output_dir else None,
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command("mcp")
def mcp_server(
    path: str | None = typer.Option(None, help="Repository root"),
    transport: str = typer.Option("stdio", "--transport", "-t", help="Transport: stdio | sse"),
    host: str = typer.Option("127.0.0.1", "--host", help="SSE server host (sse transport only)"),
    port: int = typer.Option(8472, "--port", "-p", help="SSE server port (sse transport only)"),
) -> None:
    """Start the Vaner MCP server for native IDE integration.

    Stdio mode (for Claude Desktop / Cursor):

        vaner mcp --path /your/repo

    SSE mode (for network/remote access):

        vaner mcp --path /your/repo --transport sse --port 8472

    See src/vaner/mcp/server.py for configuration examples.
    """
    import asyncio as _asyncio

    try:
        from vaner.mcp.server import run_sse, run_stdio
    except ImportError as exc:  # pragma: no cover
        typer.secho(f"MCP not available: {exc}. Install: pip install 'mcp[cli]>=1.0'", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    repo_root = _repo_root(path)

    if transport == "sse":
        typer.echo(f"Starting Vaner MCP server (SSE) on {host}:{port}  repo={repo_root}")
        _asyncio.run(run_sse(repo_root, host=host, port=port))
    else:
        _asyncio.run(run_stdio(repo_root))


@app.command("proxy")
def proxy(
    path: str | None = typer.Option(None, help="Repository root"),
    host: str = "127.0.0.1",
    port: int = 8471,
) -> None:
    import uvicorn

    repo_root = _repo_root(path)
    config = load_config(repo_root)
    daemon = VanerDaemon(config)
    app_instance = create_app(config, daemon.store)
    uvicorn.run(
        app_instance,
        host=host,
        port=port,
        ssl_certfile=config.proxy.ssl_certfile or None,
        ssl_keyfile=config.proxy.ssl_keyfile or None,
    )


@app.command("metrics")
def metrics_cmd(
    path: str | None = typer.Option(None, help="Repository root"),
    last: int = typer.Option(100, "--last", "-n", help="Number of recent requests to include"),
    output: str = typer.Option("summary", "--output", "-o", help="Output format: summary | json | csv"),
) -> None:
    """Show end-to-end latency and cache-hit metrics from the proxy."""
    import asyncio
    import csv
    import io

    from vaner.telemetry.metrics import MetricsStore

    repo_root = _repo_root(path)
    db_path = repo_root / ".vaner" / "metrics.db"
    if not db_path.exists():
        typer.secho("No metrics yet. Run `vaner proxy` and make some requests first.", fg=typer.colors.YELLOW)
        raise typer.Exit()

    store = MetricsStore(db_path)

    async def _load():
        await store.initialize()
        summary = await store.summary(last_n=last)
        rows = await store.recent(limit=last)
        return summary, rows

    summary, rows = asyncio.run(_load())

    if output == "json":
        typer.echo(json.dumps({"summary": summary, "requests": rows}, indent=2))
        return

    if output == "csv":
        if not rows:
            typer.echo("request_id,timestamp,cache_tier,context_retrieval_ms,llm_first_token_ms,llm_total_ms,total_e2e_ms")
            return
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        typer.echo(buf.getvalue())
        return

    # Default: human-readable summary
    if not rows:
        typer.secho("No requests recorded yet.", fg=typer.colors.YELLOW)
        return

    typer.echo(f"\nVaner Proxy Metrics (last {summary['count']} requests)")
    typer.echo("=" * 50)

    tiers = summary.get("cache_tiers", {})
    total = sum(tiers.values()) or 1
    typer.echo("\nCache performance:")
    for tier, count in sorted(tiers.items()):
        pct = round(count / total * 100, 1)
        typer.echo(f"  {tier:<14} {count:>5}  ({pct}%)")

    typer.echo("\nLatency (ms):")
    for label, key in [
        ("Context retrieval", "context_retrieval_ms"),
        ("LLM first token  ", "llm_first_token_ms"),
        ("LLM total        ", "llm_total_ms"),
        ("Total E2E        ", "total_e2e_ms"),
    ]:
        data = summary.get(key, {})
        avg = data.get("avg", 0.0)
        p95 = data.get("p95", 0.0)
        if avg > 0:
            typer.echo(f"  {label}  avg={avg:>8.1f}  p95={p95:>8.1f}")

    typer.echo(f"\n  Avg context tokens:  {summary.get('avg_context_tokens', 0):.0f}")
    typer.echo("")


app.add_typer(daemon_app, name="daemon")
app.add_typer(config_app, name="config")
app.add_typer(profile_app, name="profile")


def run() -> None:
    try:
        app()
    except (FileNotFoundError, PermissionError, httpx.HTTPError, aiosqlite.Error) as exc:
        typer.secho(_friendly_error_message(exc), fg=typer.colors.RED, err=True)
        if _VERBOSE:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # pragma: no cover - defensive CLI guard
        typer.secho(_friendly_error_message(exc), fg=typer.colors.RED, err=True)
        if _VERBOSE:
            traceback.print_exc()
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    run()
