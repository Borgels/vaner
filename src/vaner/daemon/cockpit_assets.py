from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles


def cockpit_dist_dir() -> Path | None:
    """Return the built React cockpit asset directory, if available."""
    packaged = Path(__file__).resolve().parent / "cockpit_dist"
    dev = Path(__file__).resolve().parents[3] / "ui" / "cockpit" / "dist"
    for candidate in (packaged, dev):
        if (candidate / "index.html").exists():
            return candidate
    return None


def mount_cockpit_assets(app: FastAPI, cockpit_dist: Path | None) -> None:
    if cockpit_dist is None:
        return
    for route, dirname, name in (
        ("/assets", "assets", "cockpit-assets"),
        ("/brand", "brand", "cockpit-brand"),
    ):
        directory = cockpit_dist / dirname
        if directory.exists():
            app.mount(route, StaticFiles(directory=directory), name=name)


def cockpit_response(cockpit_dist: Path | None) -> HTMLResponse:
    if cockpit_dist is None:
        return HTMLResponse(_missing_assets_html(), status_code=503)
    return HTMLResponse((cockpit_dist / "index.html").read_text(encoding="utf-8"))


def _missing_assets_html() -> str:
    return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Vaner Cockpit assets missing</title>
    <style>
      :root { color-scheme: dark; font-family: Inter, system-ui, sans-serif; }
      body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #101014; color: #f0f0f3; }
      main { max-width: 680px; padding: 32px; }
      h1 { margin: 0 0 12px; font-size: 24px; }
      p { color: #b9bac3; line-height: 1.5; }
      code { background: #1b1c22; border: 1px solid #333641; border-radius: 4px; padding: 2px 5px; }
    </style>
  </head>
  <body>
    <main>
      <h1>Vaner Cockpit assets are not built</h1>
      <p>The legacy cockpit has been removed. Build the React cockpit before opening the web UI:</p>
      <p><code>npm --prefix ui/cockpit ci</code><br /><code>npm --prefix ui/cockpit run build</code></p>
    </main>
  </body>
</html>"""
