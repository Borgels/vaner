# SPDX-License-Identifier: Apache-2.0

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
from vaner.cli.commands.config import load_config, set_compute_value
from vaner.cli.commands.daemon import daemon_status, run_daemon_forever, start_daemon, stop_daemon
from vaner.cli.commands.explain import render_human, render_json
from vaner.cli.commands.init import init_repo
from vaner.cli.commands.inspect import inspect_decision as inspect_decision_output
from vaner.cli.commands.inspect import inspect_last as inspect_last_output
from vaner.cli.commands.inspect import list_decisions as list_decisions_output
from vaner.cli.commands.profile import export_pins, import_pins, pin_fact, profile_show, unpin_fact
from vaner.daemon.runner import VanerDaemon
from vaner.eval import evaluate_repo, run_eval
from vaner.router.backends import forward_chat_completion_with_request
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


@app.callback()
def app_callback(
    verbose: bool = typer.Option(False, "--verbose", help="Show full traceback on errors"),
) -> None:
    global _VERBOSE
    _VERBOSE = verbose


@app.command("init")
def init(path: str | None = typer.Option(None, help="Repository root")) -> None:
    repo_root = _repo_root(path)
    config_path = init_repo(repo_root)
    typer.echo(f"Initialized Vaner at {config_path}")
    runtime = _detect_local_runtime()
    hardware = _detect_hardware_profile()
    if runtime.get("detected"):
        typer.echo(f"Detected local runtime: {runtime['name']} ({runtime['url']})")
    else:
        typer.echo("No local runtime detected on localhost ports (11434/1234/8000).")
        typer.echo("Recommended: curl -fsSL https://vaner.ai/install.sh | bash -s -- --with-ollama")
    typer.echo(
        f"Hardware profile: device={hardware['device']} gpu_count={hardware['gpu_count']} vram_gb={hardware['vram_gb']}"
    )
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
    key: str = typer.Argument(..., help="Setting path, currently supports compute.*"),
    value: str = typer.Argument(..., help="Setting value"),
    path: str | None = typer.Option(None, help="Repository root"),
) -> None:
    if not key.startswith("compute."):
        typer.secho("Only compute.* settings are supported by `vaner config set` for now.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    compute_key = key.split(".", 1)[1]
    converters = {
        "device": str,
        "embedding_device": str,
        "cpu_fraction": float,
        "gpu_memory_fraction": float,
        "idle_only": lambda raw: str(raw).lower() in {"1", "true", "yes", "on"},
        "idle_cpu_threshold": float,
        "idle_gpu_threshold": float,
        "exploration_concurrency": int,
        "max_parallel_precompute": int,
    }
    parser = converters.get(compute_key)
    if parser is None:
        typer.secho(f"Unsupported compute setting: {compute_key}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    try:
        parsed_value = parser(value)
    except ValueError as exc:
        typer.secho(f"Invalid value for {key}: {value}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    config_path = set_compute_value(_repo_root(path), compute_key, parsed_value)
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


@app.command("watch")
def watch(
    proxy_url: str = typer.Option("http://127.0.0.1:8471", "--proxy-url", help="Vaner proxy URL"),
    as_json: bool = typer.Option(False, "--json", help="Emit raw decision events as JSON"),
    contains: str | None = typer.Option(None, "--filter", help="Substring filter against serialized decision payload"),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Stop after N matching events"),
) -> None:
    """Tail live decision events from the proxy SSE stream."""
    stream_url = f"{proxy_url.rstrip('/')}/decisions/stream"
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
                    decision_id = event.get("id", "unknown")
                    tier = event.get("cache_tier", "unknown")
                    tokens = event.get("token_used", 0)
                    selections = event.get("selection_count", 0)
                    typer.echo(f"[{tier}] {tokens} tok • {selections} files • decision {decision_id}")
                matched += 1
                if limit is not None and matched >= limit:
                    return


@app.command("show")
def show(
    proxy_url: str = typer.Option("http://127.0.0.1:8471", "--proxy-url", help="Vaner proxy URL"),
) -> None:
    """Open the local web cockpit in the default browser."""
    import webbrowser

    cockpit_url = f"{proxy_url.rstrip('/')}/ui"
    opened = webbrowser.open(cockpit_url)
    if opened:
        typer.echo(f"Opened {cockpit_url}")
    else:
        typer.echo(f"Open this URL manually: {cockpit_url}")


@app.command("status")
def status(
    path: str | None = typer.Option(None, help="Repository root"),
    proxy_url: str = typer.Option("http://127.0.0.1:8471", "--proxy-url", help="Vaner proxy URL"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Show one-screen Vaner health and compute status."""
    repo_root = _repo_root(path)
    config = load_config(repo_root)
    daemon = daemon_status(repo_root)
    latest = api.inspect_last_decision(repo_root)

    proxy_health = {"reachable": False, "detail": ""}
    try:
        response = httpx.get(f"{proxy_url.rstrip('/')}/health", timeout=2.0)
        proxy_health["reachable"] = response.status_code == 200
        proxy_health["detail"] = response.text
    except Exception as exc:
        proxy_health["detail"] = str(exc)

    payload = {
        "repo_root": str(repo_root),
        "daemon": daemon,
        "proxy_url": proxy_url,
        "proxy": proxy_health,
        "compute": config.compute.model_dump(mode="json"),
        "backend": {
            "base_url": config.backend.base_url,
            "model": config.backend.model,
            "gateway_passthrough": config.gateway.passthrough_enabled,
        },
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
    typer.echo(f"proxy:    {'ok' if proxy_health['reachable'] else 'down'} ({proxy_url})")
    typer.echo(f"backend:  {config.backend.base_url or '(unset)'} [{config.backend.model or '(unset)'}]")
    typer.echo(
        "compute:  "
        f"device={config.compute.device} "
        f"cpu_fraction={config.compute.cpu_fraction} "
        f"gpu_fraction={config.compute.gpu_memory_fraction}"
    )
    typer.echo(f"decision: {payload['last_decision'] or 'none'}")


@app.command("doctor")
def doctor(
    path: str | None = typer.Option(None, help="Repository root"),
    proxy_url: str = typer.Option("http://127.0.0.1:8471", "--proxy-url", help="Vaner proxy URL"),
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
        health = httpx.get(f"{proxy_url.rstrip('/')}/health", timeout=2.0)
        checks.append(
            {
                "name": "proxy_reachable",
                "ok": health.status_code == 200,
                "detail": f"status={health.status_code}",
                "fix": "Start the proxy with `vaner proxy`.",
            }
        )
    except Exception as exc:
        checks.append(
            {
                "name": "proxy_reachable",
                "ok": False,
                "detail": str(exc),
                "fix": "Start the proxy with `vaner proxy`.",
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
