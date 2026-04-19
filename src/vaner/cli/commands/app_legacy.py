# SPDX-License-Identifier: Apache-2.0
# mypy: ignore-errors

from __future__ import annotations

import json
import os
import traceback
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import aiosqlite
import httpx
import typer

from vaner import api
from vaner.cli.commands.config import load_config, set_compute_value, set_config_value
from vaner.cli.commands.daemon import daemon_status, run_daemon_forever, start_daemon, stop_daemon
from vaner.cli.commands.explain import render_human, render_json
from vaner.cli.commands.init import init_repo, write_mcp_configs
from vaner.cli.commands.inspect import inspect_decision as inspect_decision_output
from vaner.cli.commands.inspect import inspect_last as inspect_last_output
from vaner.cli.commands.inspect import list_decisions as list_decisions_output
from vaner.cli.commands.profile import export_pins, import_pins, pin_fact, profile_show, unpin_fact
from vaner.daemon.http import create_daemon_http_app
from vaner.daemon.runner import VanerDaemon
from vaner.eval import evaluate_repo, run_eval
from vaner.router.backends import forward_chat_completion_with_request
from vaner.router.proxy import create_app
from vaner.store.scenarios import ScenarioStore
from vaner.telemetry.metrics import MetricsStore

app = typer.Typer(help="Vaner CLI")
daemon_app = typer.Typer(help="Daemon controls")
config_app = typer.Typer(help="Show config")
profile_app = typer.Typer(help="Profile memory controls")
scenarios_app = typer.Typer(help="Scenario cockpit commands")
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


def _detect_local_runtime() -> dict[str, object]:
    probes = [
        ("ollama", "http://127.0.0.1:11434/api/tags"),
        ("lmstudio", "http://127.0.0.1:1234/v1/models"),
        ("openai-compatible", "http://127.0.0.1:8000/v1/models"),
    ]
    for name, url in probes:
        try:
            response = httpx.get(url, timeout=1.2)
            if response.status_code < 400:
                return {"detected": True, "name": name, "url": url}
        except Exception:
            continue
    return {"detected": False}


def _detect_hardware_profile() -> dict[str, object]:
    profile: dict[str, object] = {"device": "cpu", "gpu_count": 0, "vram_gb": 0}
    try:  # pragma: no cover - torch/cuda depends on environment
        import torch

        if torch.cuda.is_available():
            profile["device"] = "cuda"
            profile["gpu_count"] = torch.cuda.device_count()
            if torch.cuda.device_count() > 0:
                props = torch.cuda.get_device_properties(0)
                profile["vram_gb"] = round(props.total_memory / (1024**3), 1)
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            profile["device"] = "mps"
    except Exception:
        pass
    return profile


async def _scenario_store(repo_root: Path) -> ScenarioStore:
    store = ScenarioStore(repo_root / ".vaner" / "scenarios.db")
    await store.initialize()
    return store


@app.callback()
def app_callback(
    verbose: bool = typer.Option(False, "--verbose", help="Show full traceback on errors"),
) -> None:
    global _VERBOSE
    _VERBOSE = verbose


@app.command("init")
def init(
    path: str | None = typer.Option(None, help="Repository root"),
    no_mcp: bool = typer.Option(False, "--no-mcp", help="Skip writing MCP client config files"),
) -> None:
    repo_root = _repo_root(path)
    config_path = init_repo(repo_root)
    typer.echo(f"Initialized Vaner at {config_path}")
    if not no_mcp:
        try:
            written = write_mcp_configs(repo_root)
            typer.echo("Configured MCP clients:")
            for item in written:
                typer.echo(f"  - {item}")
        except Exception as exc:
            typer.echo(f"Warning: could not write MCP client configs: {exc}")
    runtime = _detect_local_runtime()
    hardware = _detect_hardware_profile()
    if runtime.get("detected"):
        typer.echo(f"Detected local runtime: {runtime['name']} ({runtime['url']})")
    else:
        typer.echo("No local runtime detected on localhost ports (11434/1234/8000).")
        typer.echo("Recommended: curl -fsSL https://vaner.ai/install.sh | bash -s -- --with-ollama")
    typer.echo(f"Hardware profile: device={hardware['device']} gpu_count={hardware['gpu_count']} vram_gb={hardware['vram_gb']}")
    has_vscode = (repo_root / ".vscode").exists() or os.environ.get("TERM_PROGRAM") == "vscode"
    has_cursor = os.environ.get("CURSOR_TRACE_ID") is not None or os.environ.get("CURSOR_AGENT") is not None
    if has_vscode or has_cursor:
        typer.echo("Detected VS Code/Cursor environment.")
        typer.echo("Install extension: cd ide/vscode && npm install && npm run build")
        typer.echo("Then load the extension and run `Vaner: Open Cockpit`.")


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


@daemon_app.command("serve-http")
def daemon_serve_http(
    path: str | None = typer.Option(None, help="Repository root"),
    host: str = typer.Option("127.0.0.1", "--host", help="Cockpit host"),
    port: int = typer.Option(8473, "--port", help="Cockpit port"),
) -> None:
    import uvicorn

    repo_root = _repo_root(path)
    config = load_config(repo_root)
    app_instance = create_daemon_http_app(config)
    uvicorn.run(app_instance, host=host, port=port)


@app.command("inspect")
def inspect(
    path: str | None = typer.Option(None, help="Repository root"),
    last: bool = typer.Option(False, "--last", help="Show last context decision"),
    verbose: bool = typer.Option(False, "--verbose", help="Show detailed scoring factors"),
    as_json: bool = typer.Option(False, "--json", help="Show decision record as JSON"),
) -> None:
    if last:
        typer.echo(inspect_last_output(_repo_root(path), verbose=verbose, as_json=as_json))
        return
    typer.echo(api.inspect(_repo_root(path)))


@app.command("query")
def query(
    prompt: str,
    path: str | None = typer.Option(None, help="Repository root"),
    explain: bool = typer.Option(False, "--explain", help="Show why this context package was chosen"),
    verbose: bool = typer.Option(False, "--verbose", help="Include detailed score factors with --explain"),
    as_json: bool = typer.Option(False, "--json", help="Show explanation as JSON with --explain"),
) -> None:
    package = api.query(prompt, _repo_root(path))
    typer.echo(package.injected_context)
    if not explain:
        return
    decision_record = api.inspect_last_decision(_repo_root(path))
    if decision_record is None:
        typer.echo("\nNo context decisions recorded yet.")
        return
    typer.echo("")
    typer.echo(render_json(decision_record) if as_json else render_human(decision_record, verbose=verbose))


@app.command("why")
def why(
    decision_id: str | None = typer.Argument(None, help="Decision id, defaults to latest"),
    path: str | None = typer.Option(None, help="Repository root"),
    list_ids: bool = typer.Option(False, "--list", help="List recent decision ids"),
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="Number of ids to list"),
    verbose: bool = typer.Option(False, "--verbose", help="Show detailed scoring factors"),
    as_json: bool = typer.Option(False, "--json", help="Show decision record as JSON"),
) -> None:
    repo_root = _repo_root(path)
    if list_ids:
        typer.echo(list_decisions_output(repo_root, limit=limit))
        return
    typer.echo(inspect_decision_output(repo_root, decision_id, verbose=verbose, as_json=as_json))


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


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Setting path, supports compute.* and exploration.*"),
    value: str = typer.Argument(..., help="Setting value"),
    path: str | None = typer.Option(None, help="Repository root"),
) -> None:
    if "." not in key:
        typer.secho("Key must include section prefix, e.g. compute.cpu_fraction", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    section, field = key.split(".", 1)
    converters_by_section = {
        "compute": {
            "device": str,
            "embedding_device": str,
            "cpu_fraction": float,
            "gpu_memory_fraction": float,
            "idle_only": lambda raw: str(raw).lower() in {"1", "true", "yes", "on"},
            "idle_cpu_threshold": float,
            "idle_gpu_threshold": float,
            "exploration_concurrency": int,
            "max_parallel_precompute": int,
        },
        "exploration": {
            "endpoint": str,
            "model": str,
            "backend": str,
            "enabled": lambda raw: str(raw).lower() in {"1", "true", "yes", "on"},
        },
    }
    parser = converters_by_section.get(section, {}).get(field)
    if parser is None:
        typer.secho(f"Unsupported setting: {key}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    try:
        parsed_value = parser(value)
    except ValueError as exc:
        typer.secho(f"Invalid value for {key}: {value}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    if section == "compute":
        config_path = set_compute_value(_repo_root(path), field, parsed_value)
    else:
        config_path = set_config_value(_repo_root(path), section, field, parsed_value)
    typer.echo(f"Updated {key} in {config_path}")


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
    typer.echo("vaner proxy is an optional capability for non-MCP clients. MCP-first flow remains default.")
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
        usage = await store.mode_usage_summary()
        return summary, rows, usage

    summary, rows, usage = asyncio.run(_load())

    if output == "json":
        typer.echo(json.dumps({"summary": summary, "requests": rows, "usage_by_mode": usage}, indent=2))
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
    if usage:
        typer.echo("\nIntegration usage:")
        for mode, count in usage.items():
            typer.echo(f"  {mode:<14} {count:>5}")
    typer.echo("")


@app.command("impact")
def impact(
    path: str | None = typer.Option(None, help="Repository root"),
    last: int = typer.Option(500, "--last", "-n", help="Number of shadow pairs to aggregate"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Show before/after impact from sampled shadow runs."""
    import asyncio

    from vaner.telemetry.metrics import MetricsStore

    repo_root = _repo_root(path)
    db_path = repo_root / ".vaner" / "metrics.db"
    if not db_path.exists():
        typer.secho("No metrics database found yet.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    store = MetricsStore(db_path)

    async def _load():
        await store.initialize()
        return await store.shadow_summary(last_n=last)

    summary = asyncio.run(_load())
    idle_path = repo_root / ".vaner" / "runtime" / "idle_usage.json"
    idle_seconds = 0.0
    if idle_path.exists():
        try:
            parsed = json.loads(idle_path.read_text(encoding="utf-8"))
            idle_seconds = float(parsed.get("idle_seconds_used", 0.0))
        except Exception:
            idle_seconds = 0.0
    summary["idle_seconds_used"] = round(idle_seconds, 3)
    if as_json:
        typer.echo(json.dumps(summary, indent=2))
        return
    if summary.get("count", 0) == 0:
        typer.echo("No shadow comparisons recorded yet. Set [gateway.shadow] rate > 0 and send traffic through `vaner proxy`.")
        return
    typer.echo("Vaner impact")
    typer.echo("=" * 40)
    typer.echo(f"pairs:            {summary['count']}")
    typer.echo(f"win rate:         {summary['win_rate'] * 100:.1f}%")
    typer.echo(f"avg latency gain: {summary['avg_latency_gain_ms']:.2f} ms")
    typer.echo(f"avg token delta:  {summary['avg_token_delta']:.2f}")
    typer.echo(f"idle seconds used:{summary['idle_seconds_used']:.2f}")


@app.command("compare")
def compare(
    prompt: str = typer.Argument(..., help="Prompt to compare with and without Vaner context"),
    path: str | None = typer.Option(None, help="Repository root"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Run the same prompt with and without context and print deltas."""
    import asyncio

    repo_root = _repo_root(path)
    config = load_config(repo_root)

    async def _run() -> dict[str, object]:
        base_payload = {"messages": [{"role": "user", "content": prompt}], "stream": False}
        started_plain = perf_counter()
        plain = await forward_chat_completion_with_request(config, base_payload, authorization_header=None)
        plain_ms = (perf_counter() - started_plain) * 1000.0

        package = await api.aquery(prompt, repo_root, config=config, top_n=6)
        enriched_payload = {
            "messages": [
                {"role": "system", "content": "Use provided context when relevant.\n\n" + package.injected_context},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        started_enriched = perf_counter()
        enriched = await forward_chat_completion_with_request(config, enriched_payload, authorization_header=None)
        enriched_ms = (perf_counter() - started_enriched) * 1000.0

        def _assistant_text(result: dict[str, object]) -> str:
            choices = result.get("choices", [])
            if not isinstance(choices, list) or not choices:
                return ""
            first = choices[0]
            if not isinstance(first, dict):
                return ""
            message = first.get("message", {})
            if isinstance(message, dict):
                content = message.get("content", "")
                return content if isinstance(content, str) else ""
            return ""

        enriched_text = _assistant_text(enriched)
        plain_text = _assistant_text(plain)
        return {
            "prompt": prompt,
            "context_tokens": package.token_used,
            "with_context_ms": round(enriched_ms, 2),
            "without_context_ms": round(plain_ms, 2),
            "latency_gain_ms": round(plain_ms - enriched_ms, 2),
            "with_context_chars": len(enriched_text),
            "without_context_chars": len(plain_text),
            "char_delta": len(enriched_text) - len(plain_text),
        }

    result = asyncio.run(_run())
    if as_json:
        typer.echo(json.dumps(result, indent=2))
        return
    typer.echo("Vaner compare")
    typer.echo("=" * 40)
    typer.echo(f"context tokens:   {result['context_tokens']}")
    typer.echo(f"with context:     {result['with_context_ms']} ms")
    typer.echo(f"without context:  {result['without_context_ms']} ms")
    typer.echo(f"latency gain:     {result['latency_gain_ms']} ms")
    typer.echo(f"response chars Δ: {result['char_delta']}")


@scenarios_app.command("list")
def scenarios_list(
    path: str | None = typer.Option(None, help="Repository root"),
    kind: str | None = typer.Option(None, "--kind", help="Filter by kind"),
    limit: int = typer.Option(10, "--limit", min=1, max=100, help="Maximum scenarios"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    import asyncio

    repo_root = _repo_root(path)

    async def _run() -> list[dict[str, object]]:
        store = await _scenario_store(repo_root)
        rows = await store.list_top(kind=kind, limit=limit)
        return [row.model_dump(mode="json") for row in rows]

    rows = asyncio.run(_run())
    if as_json:
        typer.echo(json.dumps({"count": len(rows), "scenarios": rows}, indent=2))
        return
    typer.echo(f"Scenarios ({len(rows)})")
    typer.echo("=" * 40)
    for row in rows:
        typer.echo(
            f"{row['id']} [{row['kind']}] score={row['score']:.3f} "
            f"freshness={row['freshness']} entities={len(row['entities'])}"
        )


@scenarios_app.command("show")
def scenarios_show(
    scenario_id: str,
    path: str | None = typer.Option(None, help="Repository root"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    import asyncio

    repo_root = _repo_root(path)

    async def _run() -> dict[str, object] | None:
        store = await _scenario_store(repo_root)
        row = await store.get(scenario_id)
        return row.model_dump(mode="json") if row else None

    row = asyncio.run(_run())
    if row is None:
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(row, indent=2))
        return
    typer.echo(f"{row['id']} [{row['kind']}] score={row['score']:.3f}")
    typer.echo(f"freshness={row['freshness']} cost={row['cost_to_expand']} confidence={row['confidence']}")
    typer.echo(f"entities: {', '.join(row['entities'])}")
    typer.echo("\nPrepared context\n" + "-" * 40)
    typer.echo(str(row["prepared_context"]))


@scenarios_app.command("expand")
def scenarios_expand(
    scenario_id: str,
    path: str | None = typer.Option(None, help="Repository root"),
) -> None:
    import asyncio

    repo_root = _repo_root(path)
    config = load_config(repo_root)

    async def _run() -> dict[str, object] | None:
        await api.aprecompute(repo_root, config=config)
        store = await _scenario_store(repo_root)
        await store.record_expansion(scenario_id)
        row = await store.get(scenario_id)
        return row.model_dump(mode="json") if row else None

    row = asyncio.run(_run())
    if row is None:
        typer.echo(f"Scenario not found: {scenario_id}")
        raise typer.Exit(code=1)
    typer.echo(json.dumps({"ok": True, "scenario": row}, indent=2))


@scenarios_app.command("compare")
def scenarios_compare(
    scenario_a: str,
    scenario_b: str,
    path: str | None = typer.Option(None, help="Repository root"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    import asyncio

    repo_root = _repo_root(path)

    async def _run() -> dict[str, object]:
        store = await _scenario_store(repo_root)
        a = await store.get(scenario_a)
        b = await store.get(scenario_b)
        if a is None or b is None:
            return {}
        set_a = set(a.entities)
        set_b = set(b.entities)
        shared = sorted(set_a & set_b)
        return {
            "shared_entities": shared,
            "a": {"id": a.id, "kind": a.kind, "score": a.score, "unique_entities": sorted(set_a - set_b)},
            "b": {"id": b.id, "kind": b.kind, "score": b.score, "unique_entities": sorted(set_b - set_a)},
            "recommended": a.id if a.score >= b.score else b.id,
        }

    payload = asyncio.run(_run())
    if not payload:
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(f"shared entities: {len(payload['shared_entities'])}")
    typer.echo(f"recommended: {payload['recommended']}")


@scenarios_app.command("outcome")
def scenarios_outcome(
    scenario_id: str,
    result: str = typer.Option(..., "--result", help="useful|irrelevant|partial"),
    note: str = typer.Option("", "--note", help="Optional note"),
    path: str | None = typer.Option(None, help="Repository root"),
) -> None:
    import asyncio

    if result not in {"useful", "irrelevant", "partial"}:
        typer.echo("result must be useful|irrelevant|partial")
        raise typer.Exit(code=1)

    repo_root = _repo_root(path)

    async def _run() -> None:
        store = await _scenario_store(repo_root)
        await store.record_outcome(scenario_id, result)
        metrics = MetricsStore(repo_root / ".vaner" / "metrics.db")
        await metrics.initialize()
        await metrics.record_scenario_outcome(scenario_id=scenario_id, result=result, note=note)

    asyncio.run(_run())
    typer.echo(f"Recorded outcome for {scenario_id}: {result}")


@app.command("watch")
def watch(
    cockpit_url: str = typer.Option("http://127.0.0.1:8473", "--cockpit-url", help="Vaner cockpit URL"),
    as_json: bool = typer.Option(False, "--json", help="Emit raw decision events as JSON"),
    contains: str | None = typer.Option(None, "--filter", help="Substring filter against serialized decision payload"),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Stop after N matching events"),
) -> None:
    """Tail live scenario events from the cockpit SSE stream."""
    stream_url = f"{cockpit_url.rstrip('/')}/scenarios/stream"
    matched = 0
    typer.echo(f"Watching decisions on {stream_url} ... (Ctrl+C to stop)")
    with httpx.Client(timeout=None) as client:
        with client.stream("GET", stream_url, headers={"Accept": "text/event-stream"}) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if contains and contains not in payload:
                    continue
                event = json.loads(payload)
                if as_json:
                    typer.echo(json.dumps(event))
                else:
                    scenario_id = event.get("id", "unknown")
                    kind = event.get("kind", "unknown")
                    score = event.get("score", 0.0)
                    freshness = event.get("freshness", "unknown")
                    typer.echo(f"[{kind}] score={score:.3f} freshness={freshness} scenario {scenario_id}")
                matched += 1
                if limit is not None and matched >= limit:
                    return


@app.command("show")
def show(
    cockpit_url: str = typer.Option("http://127.0.0.1:8473", "--cockpit-url", help="Vaner cockpit URL"),
) -> None:
    """Open the local web cockpit in the default browser."""
    import webbrowser

    target_url = f"{cockpit_url.rstrip('/')}/ui"
    opened = webbrowser.open(target_url)
    if opened:
        typer.echo(f"Opened {target_url}")
    else:
        typer.echo(f"Open this URL manually: {target_url}")


@app.command("status")
def status(
    path: str | None = typer.Option(None, help="Repository root"),
    cockpit_url: str = typer.Option("http://127.0.0.1:8473", "--cockpit-url", help="Vaner cockpit URL"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Show one-screen Vaner health and compute status."""
    repo_root = _repo_root(path)
    config = load_config(repo_root)
    daemon = daemon_status(repo_root)
    latest = api.inspect_last_decision(repo_root)

    cockpit_health = {"reachable": False, "detail": ""}
    try:
        response = httpx.get(f"{cockpit_url.rstrip('/')}/health", timeout=2.0)
        cockpit_health["reachable"] = response.status_code == 200
        cockpit_health["detail"] = response.text
    except Exception as exc:
        cockpit_health["detail"] = str(exc)

    scenario_counts: dict[str, int] = {"fresh": 0, "recent": 0, "stale": 0, "total": 0}
    try:
        store = ScenarioStore(repo_root / ".vaner" / "scenarios.db")
        import asyncio

        async def _counts() -> dict[str, int]:
            await store.initialize()
            return await store.freshness_counts()

        scenario_counts = asyncio.run(_counts())
    except Exception:
        scenario_counts = {"fresh": 0, "recent": 0, "stale": 0, "total": 0}

    payload = {
        "repo_root": str(repo_root),
        "daemon": daemon,
        "cockpit_url": cockpit_url,
        "cockpit": cockpit_health,
        "mcp": config.mcp.model_dump(mode="json"),
        "compute": config.compute.model_dump(mode="json"),
        "backend": {
            "base_url": config.backend.base_url,
            "model": config.backend.model,
            "gateway_passthrough": config.gateway.passthrough_enabled,
        },
        "scenarios_ready": scenario_counts["total"],
        "scenario_counts": scenario_counts,
        "last_decision": latest.id if latest else None,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if as_json:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo("Vaner status")
    typer.echo("=" * 40)
    typer.echo(f"repo:     {payload['repo_root']}")
    typer.echo(f"daemon:   {payload['daemon']}")
    typer.echo(f"cockpit:  {'ok' if cockpit_health['reachable'] else 'down'} ({cockpit_url})")
    typer.echo(f"mcp:      transport={config.mcp.transport} sse={config.mcp.http_host}:{config.mcp.http_port}")
    typer.echo(f"backend:  {config.backend.base_url or '(unset)'} [{config.backend.model or '(unset)'}]")
    typer.echo(
        "compute:  "
        f"device={config.compute.device} "
        f"cpu_fraction={config.compute.cpu_fraction} "
        f"gpu_fraction={config.compute.gpu_memory_fraction}"
    )
    typer.echo(f"decision: {payload['last_decision'] or 'none'}")
    typer.echo(f"scenarios:{payload['scenarios_ready']}")
    typer.echo(
        "freshness:"
        f" fresh={scenario_counts['fresh']}"
        f" recent={scenario_counts['recent']}"
        f" stale={scenario_counts['stale']}"
    )


@app.command("doctor")
def doctor(
    path: str | None = typer.Option(None, help="Repository root"),
    cockpit_url: str = typer.Option("http://127.0.0.1:8473", "--cockpit-url", help="Vaner cockpit URL"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Run local diagnostics and print actionable fixes."""
    repo_root = _repo_root(path)
    checks: list[dict[str, object]] = []
    config_path = repo_root / ".vaner" / "config.toml"
    checks.append(
        {
            "name": "config_exists",
            "ok": config_path.exists(),
            "detail": str(config_path),
            "fix": "Run `vaner init` in your repository." if not config_path.exists() else "",
        }
    )

    config = load_config(repo_root) if config_path.exists() else None
    if config is not None:
        has_backend = bool(config.backend.base_url.strip() and config.backend.model.strip())
        has_routes = bool(config.gateway.routes)
        checks.append(
            {
                "name": "backend_configured",
                "ok": has_backend or has_routes,
                "detail": "backend/base_url+model or gateway/routes must be set",
                "fix": "Set [backend] base_url/model or add [gateway.routes] entries in .vaner/config.toml.",
            }
        )
        checks.append(
            {
                "name": "compute_fraction_valid",
                "ok": 0.0 < config.compute.cpu_fraction <= 1.0 and 0.0 < config.compute.gpu_memory_fraction <= 1.0,
                "detail": f"cpu_fraction={config.compute.cpu_fraction}, gpu_memory_fraction={config.compute.gpu_memory_fraction}",
                "fix": "Use `vaner config set compute.cpu_fraction 0.2` and `vaner config set compute.gpu_memory_fraction 0.5`.",
            }
        )
        runtime = _detect_local_runtime()
        checks.append(
            {
                "name": "local_runtime_detected",
                "ok": bool(runtime.get("detected")),
                "detail": runtime.get("url", "No local runtime on 11434/1234/8000"),
                "fix": "Install local runtime: curl -fsSL https://vaner.ai/install.sh | bash -s -- --with-ollama",
            }
        )
        checks.append(
            {
                "name": "exploration_llm_reachable",
                "ok": bool(runtime.get("detected")),
                "detail": runtime.get("url", "No local runtime on 11434/1234/8000"),
                "fix": "Start Ollama/LM Studio/vLLM so Vaner can precompute scenarios with local hardware.",
            }
        )
        is_cloud_backend = config.backend.base_url.startswith("https://")
        if is_cloud_backend and not runtime.get("detected"):
            checks.append(
                {
                    "name": "cloud_only_advisory",
                    "ok": False,
                    "detail": "Backend is remote and no local runtime detected.",
                    "fix": "Point [backend] to local Ollama/LM Studio/vLLM to use existing hardware.",
                }
            )

    try:
        health = httpx.get(f"{cockpit_url.rstrip('/')}/health", timeout=2.0)
        checks.append(
            {
                "name": "cockpit_reachable",
                "ok": health.status_code == 200,
                "detail": f"status={health.status_code}",
                "fix": "Start the cockpit server with `vaner daemon serve-http`.",
            }
        )
    except Exception as exc:
        checks.append(
            {
                "name": "cockpit_reachable",
                "ok": False,
                "detail": str(exc),
                "fix": "Start the cockpit server with `vaner daemon serve-http`.",
            }
        )

    cursor_mcp_path = repo_root / ".cursor" / "mcp.json"
    claude_mcp_path = Path.home() / ".claude" / "claude_desktop_config.json"
    checks.append(
        {
            "name": "mcp_config_present",
            "ok": cursor_mcp_path.exists() or claude_mcp_path.exists(),
            "detail": f"cursor={cursor_mcp_path.exists()} claude={claude_mcp_path.exists()}",
            "fix": "Run `vaner init` to scaffold MCP client configuration files.",
        }
    )
    try:
        import asyncio

        async def _scenario_store_check() -> dict[str, object]:
            store = ScenarioStore(repo_root / ".vaner" / "scenarios.db")
            await store.initialize()
            rows = await store.list_top(limit=1)
            return {"ok": True, "detail": f"reachable top_scenarios={len(rows)}"}

        scenario_store_check = asyncio.run(_scenario_store_check())
        checks.append(
            {
                "name": "scenario_store_reachable",
                "ok": bool(scenario_store_check["ok"]),
                "detail": str(scenario_store_check["detail"]),
                "fix": "",
            }
        )
    except Exception as exc:
        checks.append(
            {
                "name": "scenario_store_reachable",
                "ok": False,
                "detail": str(exc),
                "fix": "Run `vaner daemon start --path .` or remove a corrupted `.vaner/scenarios.db` file.",
            }
        )

    overall_ok = all(bool(item["ok"]) for item in checks)
    payload = {"ok": overall_ok, "checks": checks}
    if as_json:
        typer.echo(json.dumps(payload, indent=2))
        raise typer.Exit(code=0 if overall_ok else 1)

    typer.echo("Vaner doctor")
    typer.echo("=" * 40)
    for check in checks:
        mark = "PASS" if check["ok"] else "FAIL"
        typer.echo(f"[{mark}] {check['name']}: {check['detail']}")
        if not check["ok"] and check["fix"]:
            typer.echo(f"       fix: {check['fix']}")
    raise typer.Exit(code=0 if overall_ok else 1)


app.add_typer(daemon_app, name="daemon")
app.add_typer(config_app, name="config")
app.add_typer(profile_app, name="profile")
app.add_typer(scenarios_app, name="scenarios")


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
