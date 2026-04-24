from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from vaner.cli.commands.config import load_config, set_compute_value
from vaner.daemon.cockpit_html import build_cockpit_html
from vaner.events.bus import build_stage_payloads
from vaner.models.config import VanerConfig
from vaner.store.scenarios import ScenarioStore
from vaner.telemetry.metrics import MetricsStore


def _metrics_path(repo_root: Path) -> Path:
    return repo_root / ".vaner" / "metrics.db"


# How long to wait between precompute cycles when the daemon holds a live
# engine. Reusing the existing idle-gate + timing-aware cycle budget, so this
# is just the outer rhythm — the engine itself may return early under load.
_PRECOMPUTE_INTERVAL_SECONDS = 90.0


async def _periodic_precompute(engine: Any) -> None:
    """Run engine.precompute_cycle() on a loop.

    Errors are swallowed — the background task must not crash the daemon
    process. The engine's own ``idle_only`` gate handles load-shedding.
    """
    import logging as _logging

    _log = _logging.getLogger(__name__)
    try:
        await engine.initialize()
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("daemon: engine.initialize() failed: %s", exc)
        return
    while True:
        try:
            await engine.precompute_cycle()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning("daemon: precompute_cycle failed: %s", exc)
        try:
            await asyncio.sleep(_PRECOMPUTE_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise


def create_daemon_http_app(config: VanerConfig, *, engine: Any | None = None) -> FastAPI:
    """Build the daemon FastAPI app.

    ``engine`` is optional — when not supplied (serve-http mode), the
    predictions endpoints return an empty snapshot. In-process embedders
    (tests, MCP server wrappers) can pass a live engine so the
    ``/predictions/*`` surface reflects real state.
    """
    scenario_store = ScenarioStore(config.repo_root / ".vaner" / "scenarios.db")
    metrics_store = MetricsStore(_metrics_path(config.repo_root))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await scenario_store.initialize()
        await metrics_store.initialize()
        # Phase 4 / WS3.a: when a live engine is wired in, spin up a
        # background task that periodically runs precompute_cycle so the
        # prediction registry stays populated for MCP queries.
        precompute_task: asyncio.Task[None] | None = None
        if engine is not None:
            precompute_task = asyncio.create_task(_periodic_precompute(engine))
        try:
            yield
        finally:
            if precompute_task is not None:
                precompute_task.cancel()
                try:
                    await precompute_task
                except (asyncio.CancelledError, Exception):
                    pass

    app = FastAPI(title="Vaner Cockpit", version="0.2.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status")
    async def status() -> JSONResponse:
        top = await scenario_store.list_top(limit=1)
        freshness = await scenario_store.freshness_counts()
        quality = await metrics_store.memory_quality_snapshot()
        calibration = await metrics_store.calibration_snapshot()
        # Per-bucket budget breakdown — the counters flow through SSE in the
        # ``budget`` stage, but the cockpit's initial render happens before the
        # first SSE tick, so the structure is mirrored here.
        bucket_budgets = {
            bucket: {
                "allocated_ms": float(quality.get(f"bucket_budget_{bucket}_allocated_ms_total", 0.0) or 0.0),
                "used_ms": float(quality.get(f"bucket_budget_{bucket}_used_ms_total", 0.0) or 0.0),
            }
            for bucket in ("exploit", "hedge", "invest", "no_regret")
        }
        prediction_metrics = {**quality, "bucket_budgets": bucket_budgets}
        return JSONResponse(
            {
                "health": "ok",
                "gateway_enabled": config.gateway.passthrough_enabled,
                "compute": config.compute.model_dump(mode="json"),
                "backend": config.backend.model_dump(mode="json"),
                "mcp": config.mcp.model_dump(mode="json"),
                "scenario_counts": freshness,
                "top_scenario": top[0].id if top else None,
                "prediction_metrics": prediction_metrics,
                "prediction_calibration": calibration,
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
    async def list_items(kind: str | None = None, limit: int = 10) -> JSONResponse:
        rows = await scenario_store.list_top(kind=kind, limit=max(1, min(limit, 100)))
        return JSONResponse({"count": len(rows), "scenarios": [row.model_dump(mode="json") for row in rows]})

    @app.get("/scenarios/{scenario_id}")
    async def fetch_item(scenario_id: str) -> JSONResponse:
        row = await scenario_store.get(scenario_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        return JSONResponse(row.model_dump(mode="json"))

    @app.post("/scenarios/{scenario_id}/expand")
    async def expand_item(scenario_id: str) -> JSONResponse:
        row = await scenario_store.get(scenario_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        await scenario_store.record_expansion(scenario_id)
        refreshed = await scenario_store.get(scenario_id)
        if refreshed is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        return JSONResponse({"ok": True, "scenario": refreshed.model_dump(mode="json")})

    @app.post("/scenarios/{scenario_id}/outcome")
    async def record_feedback(scenario_id: str, payload: dict[str, Any]) -> JSONResponse:
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

    # ------------------------------------------------------------------
    # Phase 4 / Phase C: predictions surface
    # ------------------------------------------------------------------

    def _serialize_prediction(prompt: Any) -> dict[str, Any]:
        """Render a PredictedPrompt into a JSON-safe dict."""
        spec = prompt.spec
        run = prompt.run
        artifacts = prompt.artifacts
        return {
            "id": spec.id,
            "spec": {
                "label": spec.label,
                "description": spec.description,
                "source": spec.source,
                "anchor": spec.anchor,
                "confidence": spec.confidence,
                "hypothesis_type": spec.hypothesis_type,
                "specificity": spec.specificity,
                "created_at": spec.created_at,
            },
            "run": {
                "weight": run.weight,
                "token_budget": run.token_budget,
                "tokens_used": run.tokens_used,
                "model_calls": run.model_calls,
                "scenarios_spawned": run.scenarios_spawned,
                "scenarios_complete": run.scenarios_complete,
                "readiness": run.readiness,
                "updated_at": run.updated_at,
            },
            "artifacts": {
                "scenario_ids": list(artifacts.scenario_ids),
                "evidence_score": artifacts.evidence_score,
                "has_draft": artifacts.draft_answer is not None,
                "has_briefing": artifacts.prepared_briefing is not None,
                "thinking_trace_count": len(artifacts.thinking_traces),
            },
        }

    @app.get("/predictions/active")
    async def predictions_active() -> JSONResponse:
        if engine is None:
            return JSONResponse({"predictions": []})
        active = engine.get_active_predictions()
        return JSONResponse({"predictions": [_serialize_prediction(p) for p in active]})

    @app.get("/predictions/{prediction_id}")
    async def predictions_one(
        prediction_id: str,
        include: str | None = None,
    ) -> JSONResponse:
        """Fetch one prediction.

        ``?include=draft,briefing,thinking`` opts into returning the full
        artifact content alongside the summary fields. Callers that only need
        a row summary omit the query param.
        """
        if engine is None or engine.prediction_registry is None:
            raise HTTPException(status_code=404, detail="prediction registry unavailable")
        prompt = engine.prediction_registry.get(prediction_id)
        if prompt is None:
            raise HTTPException(status_code=404, detail=f"no such prediction: {prediction_id}")
        body = _serialize_prediction(prompt)
        if include:
            wanted = {item.strip() for item in include.split(",") if item.strip()}
            extra: dict[str, Any] = {}
            if "draft" in wanted and prompt.artifacts.draft_answer is not None:
                extra["draft_answer"] = prompt.artifacts.draft_answer
            if "briefing" in wanted and prompt.artifacts.prepared_briefing is not None:
                extra["prepared_briefing"] = prompt.artifacts.prepared_briefing
            if "thinking" in wanted and prompt.artifacts.thinking_traces:
                extra["thinking_traces"] = list(prompt.artifacts.thinking_traces)
            if extra:
                body = {**body, "artifacts_content": extra}
        return JSONResponse(body)

    @app.post("/predictions/{prediction_id}/adopt")
    async def predictions_adopt(prediction_id: str) -> JSONResponse:
        pid = prediction_id.strip()
        if not pid:
            return JSONResponse(
                {"code": "invalid_input", "message": "prediction_id is required"},
                status_code=400,
            )
        if engine is None or engine.prediction_registry is None:
            return JSONResponse(
                {"code": "engine_unavailable", "message": "prediction registry unavailable"},
                status_code=409,
            )
        prompt = engine.prediction_registry.get(pid)
        if prompt is None:
            return JSONResponse(
                {"code": "not_found", "message": f"no such prediction: {pid}"},
                status_code=404,
            )
        # Lazy import to keep the daemon module load light; mcp.server has
        # heavyweight imports we don't need until someone actually adopts.
        from vaner.mcp.server import _build_adopt_resolution

        resolution = _build_adopt_resolution(prompt)
        return JSONResponse(resolution.model_dump(mode="json"))

    @app.post("/resolve")
    async def resolve_endpoint(request: Request) -> JSONResponse:
        """0.8.1: expose :meth:`VanerEngine.resolve_query` over HTTP.

        The MCP ``vaner.resolve`` handler now forwards to this endpoint
        (via :class:`VanerDaemonClient`) when no in-process engine is
        injected. Keeping a single canonical query→Resolution path in
        the engine, with HTTP as the transport, removes the parallel
        scenario-store path that WS8 documented as dead-code risk.
        """
        if engine is None:
            return JSONResponse(
                {"code": "engine_unavailable", "message": "daemon engine unavailable"},
                status_code=409,
            )
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"code": "invalid_input", "message": "request body must be JSON"},
                status_code=400,
            )
        if not isinstance(body, dict):
            return JSONResponse(
                {"code": "invalid_input", "message": "request body must be a JSON object"},
                status_code=400,
            )
        query = str(body.get("query", "")).strip()
        if not query:
            return JSONResponse(
                {"code": "invalid_input", "message": "query is required"},
                status_code=400,
            )
        context_raw = body.get("context")
        context = context_raw if isinstance(context_raw, dict) else None
        include_briefing = bool(body.get("include_briefing", True))
        include_predicted_response = bool(body.get("include_predicted_response", True))
        resolution = await engine.resolve_query(
            query,
            context=context,
            include_briefing=include_briefing,
            include_predicted_response=include_predicted_response,
        )
        return JSONResponse(resolution.model_dump(mode="json"))

    @app.get("/scenarios/stream")
    async def scenario_stream(limit: int | None = None) -> StreamingResponse:
        async def event_gen() -> AsyncIterator[str]:
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

    @app.get("/events/stream")
    async def events_stream(stages: str | None = None, limit: int | None = None) -> StreamingResponse:
        selected = {item.strip() for item in (stages or "").split(",") if item.strip()}
        if not selected:
            env_stages = os.environ.get("VANER_EVENT_STAGES", "").strip()
            if env_stages:
                selected = {item.strip() for item in env_stages.split(",") if item.strip()}
            else:
                selected = {"scenarios", "prediction", "calibration", "draft", "budget", "predictions"}

        async def event_gen() -> AsyncIterator[str]:
            last_scenario_fingerprint = ""
            stage_fingerprints: dict[str, str] = {}
            sent = 0
            while True:
                if "scenarios" in selected:
                    rows = await scenario_store.list_top(limit=10)
                    scenario_payload = json.dumps(
                        [
                            {
                                "id": row.id,
                                "score": row.score,
                                "freshness": row.freshness,
                                "kind": row.kind,
                            }
                            for row in rows
                        ],
                        sort_keys=True,
                    )
                    if scenario_payload != last_scenario_fingerprint:
                        yield f"data: {json.dumps({'stage': 'scenarios', 'payload': json.loads(scenario_payload)})}\n\n"
                        last_scenario_fingerprint = scenario_payload
                        sent += 1
                stage_payloads = await build_stage_payloads(metrics_store)
                for stage, payload in stage_payloads.items():
                    if stage not in selected:
                        continue
                    serialized = json.dumps(payload, sort_keys=True)
                    if serialized != stage_fingerprints.get(stage, ""):
                        yield f"data: {json.dumps({'stage': stage, 'payload': payload})}\n\n"
                        stage_fingerprints[stage] = serialized
                        sent += 1
                # Phase 4 / Phase C: predictions snapshot. The stream emits the
                # current list of active predictions whenever the snapshot
                # changes. Typed-event replay is handled client-side via the
                # SnapshotRebuilder — this stage is the convenience polling
                # surface for clients that don't want to manage that.
                if "predictions" in selected:
                    if engine is not None:
                        active = engine.get_active_predictions()
                        predictions_payload = [_serialize_prediction(p) for p in active]
                    else:
                        predictions_payload = []
                    serialized_p = json.dumps(predictions_payload, sort_keys=True, default=str)
                    if serialized_p != stage_fingerprints.get("predictions", ""):
                        yield f"data: {json.dumps({'stage': 'predictions', 'payload': predictions_payload})}\n\n"
                        stage_fingerprints["predictions"] = serialized_p
                        sent += 1
                if limit is not None and sent >= max(1, limit):
                    return
                yield ": keepalive\n\n"
                await asyncio.sleep(2.0)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @app.get("/", response_class=HTMLResponse)
    async def cockpit() -> str:
        return build_cockpit_html("daemon")

    @app.get("/ui")
    async def ui() -> RedirectResponse:
        return RedirectResponse(url="/", status_code=307)

    return app
