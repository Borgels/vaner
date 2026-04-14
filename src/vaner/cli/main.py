# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import typer

from vaner import api
from vaner.cli.commands.config import load_config
from vaner.cli.commands.daemon import daemon_status, run_daemon_forever, start_daemon, stop_daemon
from vaner.cli.commands.init import init_repo
from vaner.daemon.runner import VanerDaemon
from vaner.eval import evaluate_repo
from vaner.router.proxy import create_app

app = typer.Typer(help="Vaner CLI")
daemon_app = typer.Typer(help="Daemon controls")
config_app = typer.Typer(help="Show config")


def _repo_root(path: str | None) -> Path:
    return Path(path).resolve() if path else Path.cwd()


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


@app.command("forget")
def forget(path: str | None = typer.Option(None, help="Repository root")) -> None:
    removed = api.forget(_repo_root(path))
    typer.echo(f"Removed {removed} local state files.")


@config_app.command("show")
def config_show(path: str | None = typer.Option(None, help="Repository root")) -> None:
    config = load_config(_repo_root(path))
    typer.echo(config.model_dump_json(indent=2))


@app.command("eval")
def eval_repo(path: str | None = typer.Option(None, help="Repository root")) -> None:
    result = evaluate_repo(_repo_root(path))
    typer.echo(result.model_dump_json(indent=2))


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
    uvicorn.run(app_instance, host=host, port=port)


app.add_typer(daemon_app, name="daemon")
app.add_typer(config_app, name="config")


def run() -> None:
    app()


if __name__ == "__main__":
    run()
