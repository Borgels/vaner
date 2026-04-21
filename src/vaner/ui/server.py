# SPDX-License-Identifier: Apache-2.0
"""Factory for the unified Vaner cockpit FastAPI app.

The cockpit SPA is the same regardless of which Vaner surface a user
launches (``vaner daemon serve-http``, ``vaner proxy``, or ``vaner mcp``).
Only the ``mode`` in the bootstrap payload changes so the SPA can adapt
mode-specific affordances. All control-plane endpoints (backend preset,
compute, context limits, MCP transport) are served from this single
factory so the three surfaces stay behaviourally identical.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from vaner._version import VERSION
from vaner.cli.commands.config import (
    load_config,
    set_backend_value,
    set_compute_value,
    set_limits_value,
    set_mcp_value,
)
from vaner.cli.commands.init import BACKEND_PRESETS
from vaner.events import STAGES as EVENT_STAGES
from vaner.events import get_bus as get_event_bus
from vaner.models.config import BackendConfig, ComputeConfig, MCPConfig, VanerConfig
from vaner.models.scenario import Scenario
from vaner.store.scenarios import ScenarioStore
from vaner.telemetry.metrics import MetricsStore

CockpitMode = Literal["daemon", "mcp", "proxy"]


_DEFAULT_SKILLS: list[dict[str, Any]] = [
    {"name": "vaner-research", "desc": "Probe unknown code regions", "weight": 0.34},
    {"name": "vaner-explain", "desc": "Summarise + link-resolve", "weight": 0.22},
    {"name": "vaner-change", "desc": "Surface recent edits + peers", "weight": 0.19},
    {"name": "vaner-debug", "desc": "Follow failing tests + traces", "weight": 0.12},
    {"name": "vaner-feedback", "desc": "Attribute outcomes back", "weight": 0.08},
    {"name": "vaner-refactor", "desc": "Map co-change clusters", "weight": 0.05},
]

_COMPUTE_ALLOWED_KEYS = {
    "device",
    "cpu_fraction",
    "gpu_memory_fraction",
    "idle_only",
    "idle_cpu_threshold",
    "idle_gpu_threshold",
    "embedding_device",
    "exploration_concurrency",
    "max_parallel_precompute",
    "max_cycle_seconds",
    "max_session_minutes",
}

_BACKEND_ALLOWED_KEYS = {
    "name",
    "base_url",
    "model",
    "api_key_env",
    "prefer_local",
    "fallback_enabled",
    "fallback_base_url",
    "fallback_model",
    "fallback_api_key_env",
    "remote_budget_per_hour",
}


def cockpit_dist_dir() -> Path:
    """Return the bundled React SPA ``dist`` directory.

    The SPA is checked-in under ``src/vaner/daemon/cockpit_assets/dist`` so
    that ``pip install vaner`` ships a runnable UI without requiring users
    to have Node installed.
    """

    return Path(__file__).resolve().parent.parent / "daemon" / "cockpit_assets" / "dist"


def cockpit_bundle_sha() -> str:
    """Return a short SHA of the currently-shipped ``index.html``.

    The SPA compares this against its build-time constant and renders a
    "restart vaner" banner when the two differ, which is what flags the
    "running process was started before the SPA was rebuilt" failure
    mode we hit in local testing.
    """

    index_path = cockpit_dist_dir() / "index.html"
    if not index_path.exists():
        return ""
    digest = hashlib.sha256(index_path.read_bytes()).hexdigest()
    return digest[:12]


def _index_html(mode: str) -> str:
    index_path = cockpit_dist_dir() / "index.html"
    if not index_path.exists():
        return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Vaner Cockpit</title>
  </head>
  <body>
    <h1>Vaner Cockpit assets missing</h1>
    <p>Run <code>npm run build --prefix ui/cockpit</code> to build the UI bundle.</p>
    <script>window.__VANER_MODE__ = "{mode}"</script>
  </body>
</html>"""
    return index_path.read_text(encoding="utf-8").replace("__VANER_MODE_VALUE__", mode)


def _scenario_title(scenario: Scenario) -> str:
    path = scenario.entities[0] if scenario.entities else scenario.id
    return Path(path).name or scenario.id


def _scenario_path(scenario: Scenario) -> str:
    return scenario.entities[0] if scenario.entities else ""


def _scenario_reason(scenario: Scenario) -> str:
    if scenario.coverage_gaps:
        return scenario.coverage_gaps[0]
    return f"{scenario.kind} · score {scenario.score:.3f}"


def _scenario_decision_state(scenario: Scenario) -> str:
    if scenario.pinned:
        return "chosen"
    if scenario.memory_state == "demoted":
        return "rejected"
    if scenario.last_outcome == "useful":
        return "chosen"
    if scenario.last_outcome == "partial":
        return "partial"
    if scenario.last_outcome in {"irrelevant", "wrong"}:
        return "rejected"
    return "pending"


def _evidence_payload(scenario: Scenario) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for item in scenario.evidence:
        entry: dict[str, Any] = {
            "key": item.key,
            "file": item.source_path or item.key,
            "source_path": item.source_path,
            "excerpt": item.excerpt,
            "note": item.excerpt,
            "weight": item.weight,
        }
        if item.start_line is not None:
            entry["start_line"] = int(item.start_line)
        if item.end_line is not None:
            entry["end_line"] = int(item.end_line)
        payload.append(entry)
    return payload


def _enrich_scenario(scenario: Scenario) -> dict[str, Any]:
    payload = scenario.model_dump(mode="json")
    payload["title"] = _scenario_title(scenario)
    payload["path"] = _scenario_path(scenario)
    payload["depth"] = 0
    payload["parent"] = None
    payload["skill"] = None
    payload["decision_state"] = _scenario_decision_state(scenario)
    payload["reason"] = _scenario_reason(scenario)
    payload["pinned"] = bool(scenario.pinned)
    payload["evidence"] = _evidence_payload(scenario)
    return payload


async def _snapshot_payload(store: ScenarioStore, limit: int) -> str:
    rows = await store.list_top(limit=limit)
    counts = await store.freshness_counts()
    payload: dict[str, Any] = {
        "summary": counts,
        "top_scenarios": [_enrich_scenario(row) for row in rows],
    }
    if rows:
        payload["top_scenario"] = _enrich_scenario(rows[0])
    return json.dumps(payload)


def _metrics_path(repo_root: Path) -> Path:
    return repo_root / ".vaner" / "metrics.db"


def _skills_with_overrides(overrides: dict[str, float]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for skill in _DEFAULT_SKILLS:
        name_value = skill["name"]
        weight_value = skill["weight"]
        name = str(name_value)
        default_weight = float(weight_value) if isinstance(weight_value, (int, float)) else 0.0
        result.append({**skill, "weight": overrides.get(name, default_weight)})
    return result


def _probe_compute_devices(selected: str) -> dict[str, Any]:
    """Probe available compute devices.

    Uniform across daemon / proxy / MCP. ``warning`` is set if the probe
    fails (e.g. torch missing) so the SPA can explain why only CPU is
    available.
    """

    devices: list[dict[str, Any]] = [{"id": "cpu", "label": "CPU", "kind": "cpu"}]
    probe_warning: str | None = None
    try:  # pragma: no cover - depends on runtime GPU visibility
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
    except Exception as exc:  # pragma: no cover - depends on env
        probe_warning = str(exc)
    result: dict[str, Any] = {"devices": devices, "selected": selected}
    if probe_warning:
        result["warning"] = probe_warning
    return result


def _preset_payload() -> dict[str, Any]:
    items = []
    for preset in BACKEND_PRESETS.values():
        items.append(
            {
                "name": preset.name,
                "base_url": preset.base_url,
                "default_model": preset.default_model,
                "api_key_env": preset.api_key_env,
            }
        )
    return {"presets": items}


def build_cockpit_app(
    config: VanerConfig,
    *,
    mode: CockpitMode,
    scenario_store: ScenarioStore | None = None,
    metrics_store: MetricsStore | None = None,
    app_title: str | None = None,
    extra_lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    """Build the shared cockpit FastAPI app for ``mode``.

    ``scenario_store`` and ``metrics_store`` are optional so callers that
    already own these (the proxy, for example) can inject them and avoid
    opening a second connection to the same SQLite file.
    """

    scenarios = scenario_store or ScenarioStore(config.repo_root / ".vaner" / "scenarios.db")
    metrics = metrics_store or MetricsStore(_metrics_path(config.repo_root))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await scenarios.initialize()
        await metrics.initialize()
        if extra_lifespan is not None:
            async with extra_lifespan(app):
                yield
        else:
            yield

    title = app_title or {
        "daemon": "Vaner Cockpit",
        "proxy": "Vaner Proxy",
        "mcp": "Vaner MCP Cockpit",
    }.get(mode, "Vaner Cockpit")

    app = FastAPI(title=title, version=VERSION, lifespan=lifespan)
    app.state.scenario_store = scenarios
    app.state.metrics_store = metrics
    app.state.cockpit_mode = mode
    app.state.config = config

    assets_dir = cockpit_dist_dir() / "assets"
    app.mount("/assets", StaticFiles(directory=assets_dir, check_dir=False), name="cockpit-assets")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/cockpit/bootstrap.json")
    async def bootstrap() -> JSONResponse:
        return JSONResponse(
            {
                "mode": mode,
                "version": VERSION,
                "cockpit_sha": cockpit_bundle_sha(),
            }
        )

    @app.get("/status")
    async def status() -> JSONResponse:
        top = await scenarios.list_top(limit=1)
        freshness = await scenarios.freshness_counts()
        return JSONResponse(
            {
                "health": "ok",
                "mode": mode,
                "gateway_enabled": config.gateway.passthrough_enabled,
                "compute": config.compute.model_dump(mode="json"),
                "backend": config.backend.model_dump(mode="json"),
                "mcp": config.mcp.model_dump(mode="json"),
                "limits": {
                    "max_age_seconds": config.max_age_seconds,
                    "max_context_tokens": config.max_context_tokens,
                },
                "scenario_counts": freshness,
                "top_scenario": top[0].id if top else None,
            }
        )

    @app.get("/backend/presets")
    async def backend_presets() -> JSONResponse:
        return JSONResponse(_preset_payload())

    @app.post("/backend")
    async def update_backend(payload: dict[str, Any]) -> JSONResponse:
        if not payload:
            raise HTTPException(status_code=400, detail="Empty backend payload")
        merged = {**config.backend.model_dump(mode="python")}
        for key, value in payload.items():
            if key not in _BACKEND_ALLOWED_KEYS:
                raise HTTPException(status_code=400, detail=f"Unsupported backend key: {key}")
            merged[key] = value
        try:
            validated = BackendConfig(**merged)
        except Exception as exc:  # pydantic.ValidationError
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        for key, value in payload.items():
            # Persist whatever the user sent; validation above proved the
            # merged object parses, so we don't coerce types here.
            to_write = value if value is not None else ""
            set_backend_value(config.repo_root, key, to_write)
        refreshed = load_config(config.repo_root)
        config.backend = refreshed.backend
        return JSONResponse({"ok": True, "backend": validated.model_dump(mode="json")})

    @app.get("/compute/devices")
    async def compute_devices() -> JSONResponse:
        return JSONResponse(_probe_compute_devices(config.compute.device))

    @app.post("/compute")
    async def update_compute(payload: dict[str, Any]) -> JSONResponse:
        if not payload:
            raise HTTPException(status_code=400, detail="Empty compute payload")
        merged = config.compute.model_dump(mode="python")
        for key, value in payload.items():
            if key not in _COMPUTE_ALLOWED_KEYS:
                raise HTTPException(status_code=400, detail=f"Unsupported compute key: {key}")
            merged[key] = value
        try:
            validated = ComputeConfig(**merged)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        for key, value in payload.items():
            set_compute_value(config.repo_root, key, value)
        refreshed = load_config(config.repo_root)
        config.compute = refreshed.compute
        return JSONResponse({"ok": True, "compute": validated.model_dump(mode="json")})

    @app.post("/context")
    async def update_context(payload: dict[str, Any]) -> JSONResponse:
        if "max_context_tokens" not in payload:
            raise HTTPException(status_code=400, detail="max_context_tokens is required")
        try:
            tokens = int(payload["max_context_tokens"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="max_context_tokens must be integer") from exc
        if tokens <= 0 or tokens > 1_000_000:
            raise HTTPException(status_code=400, detail="max_context_tokens out of range")
        set_limits_value(config.repo_root, "max_context_tokens", tokens)
        refreshed = load_config(config.repo_root)
        config.max_context_tokens = refreshed.max_context_tokens
        return JSONResponse({"ok": True, "limits": {"max_context_tokens": tokens}})

    @app.post("/mcp")
    async def update_mcp(payload: dict[str, Any]) -> JSONResponse:
        if not payload:
            raise HTTPException(status_code=400, detail="Empty mcp payload")
        allowed = {"transport", "http_host", "http_port"}
        for key in payload:
            if key not in allowed:
                raise HTTPException(status_code=400, detail=f"Unsupported mcp key: {key}")
        transport = payload.get("transport", config.mcp.transport)
        if transport not in {"stdio", "sse"}:
            raise HTTPException(status_code=400, detail="transport must be stdio or sse")
        merged = config.mcp.model_dump(mode="python")
        merged.update(payload)
        try:
            validated = MCPConfig(**merged)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        for key, value in payload.items():
            set_mcp_value(config.repo_root, key, value)
        refreshed = load_config(config.repo_root)
        config.mcp = refreshed.mcp
        return JSONResponse({"ok": True, "mcp": validated.model_dump(mode="json")})

    @app.get("/skills")
    async def list_skills() -> JSONResponse:
        overrides = await scenarios.list_skill_weights()
        return JSONResponse({"skills": _skills_with_overrides(overrides)})

    @app.post("/skills/{name}/nudge")
    async def nudge_skill(name: str, payload: dict[str, Any]) -> JSONResponse:
        try:
            delta = float(payload.get("delta", 0.0))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="delta must be numeric") from exc
        default_weight: float | None = None
        for skill in _DEFAULT_SKILLS:
            if skill["name"] == name:
                weight_value = skill["weight"]
                if isinstance(weight_value, (int, float)):
                    default_weight = float(weight_value)
                break
        if default_weight is None:
            raise HTTPException(status_code=404, detail=f"Unknown skill: {name}")
        overrides = await scenarios.list_skill_weights()
        current = overrides.get(name, default_weight)
        updated = await scenarios.set_skill_weight(name, current + delta)
        return JSONResponse({"ok": True, "name": name, "weight": updated})

    @app.get("/pinned-facts")
    async def get_pinned_facts() -> JSONResponse:
        rows = await scenarios.list_pinned_facts()
        return JSONResponse({"facts": [{"id": row["id"], "text": row["text"]} for row in rows]})

    @app.post("/pinned-facts")
    async def create_pinned_fact(payload: dict[str, Any]) -> JSONResponse:
        text = str(payload.get("text", "")).strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        fact = await scenarios.add_pinned_fact(text)
        return JSONResponse({"fact": {"id": fact["id"], "text": fact["text"]}}, status_code=201)

    @app.delete("/pinned-facts/{fact_id}")
    async def remove_pinned_fact(fact_id: str) -> Response:
        await scenarios.delete_pinned_fact(fact_id)
        return Response(status_code=204)

    @app.get("/scenarios")
    async def list_scenarios(kind: str | None = None, limit: int = 10) -> JSONResponse:
        rows = await scenarios.list_top(kind=kind, limit=max(1, min(limit, 100)))
        return JSONResponse({"count": len(rows), "scenarios": [_enrich_scenario(row) for row in rows]})

    @app.get("/scenarios/stream")
    async def scenario_stream(limit: int | None = None) -> StreamingResponse:
        queue = scenarios.subscribe()

        async def event_gen() -> AsyncIterator[str]:
            sent = 0
            try:
                yield f"data: {await _snapshot_payload(scenarios, 10)}\n\n"
                sent += 1
                if limit is not None and sent >= limit:
                    return
                while True:
                    try:
                        await asyncio.wait_for(queue.get(), timeout=10.0)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {await _snapshot_payload(scenarios, 10)}\n\n"
                    sent += 1
                    if limit is not None and sent >= limit:
                        return
            finally:
                scenarios.unsubscribe(queue)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @app.get("/scenarios/{scenario_id}")
    async def get_scenario(scenario_id: str) -> JSONResponse:
        row = await scenarios.get(scenario_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        return JSONResponse(_enrich_scenario(row))

    @app.post("/scenarios/{scenario_id}/expand")
    async def expand_scenario(scenario_id: str) -> JSONResponse:
        row = await scenarios.get(scenario_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        await scenarios.record_expansion(scenario_id)
        refreshed = await scenarios.get(scenario_id)
        if refreshed is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        return JSONResponse({"ok": True, "scenario": _enrich_scenario(refreshed)})

    @app.post("/scenarios/{scenario_id}/pin")
    async def pin_scenario(scenario_id: str, payload: dict[str, Any]) -> JSONResponse:
        row = await scenarios.get(scenario_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        await scenarios.set_pinned(scenario_id, bool(payload.get("pinned", True)))
        refreshed = await scenarios.get(scenario_id)
        return JSONResponse({"ok": True, "scenario": _enrich_scenario(refreshed) if refreshed else None})

    @app.post("/scenarios/{scenario_id}/outcome")
    async def report_outcome(scenario_id: str, payload: dict[str, Any]) -> JSONResponse:
        row = await scenarios.get(scenario_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        result = str(payload.get("result", "")).strip()
        note = str(payload.get("note", "")).strip()
        if result not in {"useful", "partial", "irrelevant"}:
            raise HTTPException(status_code=400, detail="result must be one of useful|partial|irrelevant")
        await scenarios.record_outcome(scenario_id, result)
        await metrics.record_scenario_outcome(scenario_id=scenario_id, result=result, note=note)
        refreshed = await scenarios.get(scenario_id)
        return JSONResponse({"ok": True, "scenario": _enrich_scenario(refreshed) if refreshed else None})

    @app.get("/events/stream")
    async def event_stream(limit: int | None = None, stages: str | None = None) -> StreamingResponse:
        """Unified pipeline event stream.

        Emits :class:`vaner.events.VanerEvent` JSON (with legacy
        ``tag``/``t``/``color``/``msg``/``scn`` fields preserved for one
        release). ``stages`` is an optional comma-separated filter
        (``signals,targets,model,artefacts,scenarios,decisions,system``).
        """

        allowed: set[str] | None
        if stages:
            requested = {value.strip() for value in stages.split(",") if value.strip()}
            allowed = requested & set(EVENT_STAGES)
            if not allowed:
                allowed = set()
        else:
            allowed = None

        bus = get_event_bus()
        queue = bus.subscribe()

        async def event_gen() -> AsyncIterator[str]:
            sent = 0
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=10.0)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if event is None:
                        return
                    if allowed is not None and event.stage not in allowed:
                        continue
                    yield f"data: {json.dumps(event.to_dict())}\n\n"
                    sent += 1
                    if limit is not None and sent >= limit:
                        return
            finally:
                bus.unsubscribe(queue)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @app.get("/", response_class=HTMLResponse)
    async def root() -> str:
        return _index_html(mode)

    @app.get("/ui")
    async def ui() -> RedirectResponse:
        return RedirectResponse(url="/", status_code=307)

    return app
