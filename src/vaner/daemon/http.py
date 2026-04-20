from __future__ import annotations

# mypy: ignore-errors
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from vaner.cli.commands.config import load_config, set_compute_value
from vaner.models.config import VanerConfig
from vaner.store.scenarios import ScenarioStore
from vaner.telemetry.metrics import MetricsStore


def _metrics_path(repo_root: Path) -> Path:
    return repo_root / ".vaner" / "metrics.db"


def create_daemon_http_app(config: VanerConfig) -> FastAPI:
    scenario_store = ScenarioStore(config.repo_root / ".vaner" / "scenarios.db")
    metrics_store = MetricsStore(_metrics_path(config.repo_root))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await scenario_store.initialize()
        await metrics_store.initialize()
        yield

    app = FastAPI(title="Vaner Cockpit", version="0.2.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status")
    async def status() -> JSONResponse:
        top = await scenario_store.list_top(limit=1)
        freshness = await scenario_store.freshness_counts()
        return JSONResponse(
            {
                "health": "ok",
                "gateway_enabled": config.gateway.passthrough_enabled,
                "compute": config.compute.model_dump(mode="json"),
                "mcp": config.mcp.model_dump(mode="json"),
                "scenario_counts": freshness,
                "top_scenario": top[0].id if top else None,
            }
        )

    @app.get("/compute/devices")
    async def compute_devices() -> JSONResponse:
        devices: list[dict[str, Any]] = [{"id": "cpu", "label": "CPU", "kind": "cpu"}]
        probe_warning: str | None = None
        try:  # pragma: no cover
            import torch

            if torch.cuda.is_available():
                for idx in range(torch.cuda.device_count()):
                    props = torch.cuda.get_device_properties(idx)
                    devices.append(
                        {
                            "id": f"cuda:{idx}",
                            "label": props.name,
                            "kind": "cuda",
                            "total_memory_bytes": props.total_memory,
                        }
                    )
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                devices.append({"id": "mps", "label": "Apple Metal (MPS)", "kind": "mps"})
        except Exception as exc:
            probe_warning = str(exc)
        payload: dict[str, Any] = {"devices": devices, "selected": config.compute.device}
        if probe_warning:
            payload["warning"] = probe_warning
        return JSONResponse(payload)

    @app.post("/compute")
    async def update_compute(payload: dict[str, Any]) -> JSONResponse:
        allowed = {
            "device",
            "cpu_fraction",
            "gpu_memory_fraction",
            "idle_only",
            "idle_cpu_threshold",
            "idle_gpu_threshold",
            "embedding_device",
            "exploration_concurrency",
            "max_parallel_precompute",
        }
        for key, value in payload.items():
            if key not in allowed:
                raise HTTPException(status_code=400, detail=f"Unsupported compute key: {key}")
            set_compute_value(config.repo_root, key, value)
        refreshed = load_config(config.repo_root)
        config.compute = refreshed.compute
        return JSONResponse({"ok": True, "compute": config.compute.model_dump(mode="json")})

    @app.get("/scenarios")
    async def list_scenarios(kind: str | None = None, limit: int = 10) -> JSONResponse:
        rows = await scenario_store.list_top(kind=kind, limit=max(1, min(limit, 100)))
        return JSONResponse({"count": len(rows), "scenarios": [row.model_dump(mode="json") for row in rows]})

    @app.get("/scenarios/{scenario_id}")
    async def get_scenario(scenario_id: str) -> JSONResponse:
        row = await scenario_store.get(scenario_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        return JSONResponse(row.model_dump(mode="json"))

    @app.post("/scenarios/{scenario_id}/expand")
    async def expand_scenario(scenario_id: str) -> JSONResponse:
        row = await scenario_store.get(scenario_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        await scenario_store.record_expansion(scenario_id)
        refreshed = await scenario_store.get(scenario_id)
        if refreshed is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        return JSONResponse({"ok": True, "scenario": refreshed.model_dump(mode="json")})

    @app.post("/scenarios/{scenario_id}/outcome")
    async def report_outcome(scenario_id: str, payload: dict[str, Any]) -> JSONResponse:
        row = await scenario_store.get(scenario_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        result = str(payload.get("result", "")).strip()
        note = str(payload.get("note", "")).strip()
        if result not in {"useful", "partial", "irrelevant"}:
            raise HTTPException(status_code=400, detail="result must be one of useful|partial|irrelevant")
        await scenario_store.record_outcome(scenario_id, result)
        await metrics_store.record_scenario_outcome(scenario_id=scenario_id, result=result, note=note)
        refreshed = await scenario_store.get(scenario_id)
        return JSONResponse({"ok": True, "scenario": refreshed.model_dump(mode="json") if refreshed else None})

    @app.get("/scenarios/stream")
    async def scenario_stream(limit: int | None = None) -> StreamingResponse:
        async def event_gen():
            last_fingerprint = ""
            last_keepalive = 0.0
            sent = 0
            while True:
                rows = await scenario_store.list_top(limit=10)
                if rows:
                    fingerprint = json.dumps(
                        [
                            {
                                "id": row.id,
                                "score": row.score,
                                "freshness": row.freshness,
                                "last_outcome": row.last_outcome,
                                "last_refreshed_at": row.last_refreshed_at,
                            }
                            for row in rows
                        ],
                        sort_keys=True,
                    )
                    if fingerprint != last_fingerprint:
                        counts = await scenario_store.freshness_counts()
                        top = rows[0].model_dump(mode="json")
                        payload = json.dumps(
                            {
                                **top,
                                "summary": {
                                    "fresh": counts["fresh"],
                                    "recent": counts["recent"],
                                    "stale": counts["stale"],
                                    "total": counts["total"],
                                },
                                "top_scenarios": [
                                    {
                                        "id": row.id,
                                        "kind": row.kind,
                                        "score": row.score,
                                        "freshness": row.freshness,
                                    }
                                    for row in rows
                                ],
                            }
                        )
                        yield f"data: {payload}\n\n"
                        last_fingerprint = fingerprint
                        sent += 1
                        if limit is not None and sent >= limit:
                            return
                now = asyncio.get_event_loop().time()
                if now - last_keepalive >= 10.0:
                    yield ": keepalive\n\n"
                    last_keepalive = now
                await asyncio.sleep(2.0)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @app.get("/ui", response_class=HTMLResponse)
    async def ui() -> str:
        return """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Vaner Cockpit</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      body { font-family: system-ui, sans-serif; margin: 16px; line-height: 1.4; }
      pre { padding: 10px; border: 1px solid #ddd; border-radius: 6px; overflow-x: auto; }
      .row { margin: 12px 0; }
    </style>
  </head>
  <body>
    <h2>Vaner Cockpit</h2>
    <div class="row"><strong>Top Scenario</strong></div>
    <pre id="scenario">loading...</pre>
    <div class="row"><strong>Compute devices</strong></div>
    <pre id="devices">loading...</pre>
    <script>
      const scenarioEl = document.getElementById("scenario");
      const devicesEl = document.getElementById("devices");
      const stream = new EventSource("/scenarios/stream");
      stream.onmessage = (event) => {
        scenarioEl.textContent = JSON.stringify(JSON.parse(event.data), null, 2);
      };
      fetch("/compute/devices").then((r) => r.json()).then((d) => {
        devicesEl.textContent = JSON.stringify(d, null, 2);
      }).catch(() => {
        devicesEl.textContent = "Failed to load compute devices";
      });
    </script>
  </body>
</html>
"""

    return app
