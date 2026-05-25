from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import time
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from vaner.cli.commands.config import load_config, set_compute_value, set_config_value
from vaner.daemon.cockpit_assets import cockpit_dist_dir, cockpit_response, mount_cockpit_assets
from vaner.daemon.live_work import read_live_work_events
from vaner.daemon.precompute_worker import read_prediction_snapshot, read_worker_status, request_precompute_wake, request_worker_control
from vaner.events.bus import build_stage_payloads
from vaner.external_state.configurator import (
    ProviderConfigInput,
    discover_provider,
    external_state_payload,
    save_external_state_enabled,
    save_finance_settings,
    save_model_native_search_settings,
    save_provider_config,
)
from vaner.focus import FocusManager
from vaner.intent.prediction_serialization import compact_serialized_prediction
from vaner.mcp.contracts import EvidenceItem, Provenance, Resolution
from vaner.models.config import VanerConfig
from vaner.models.work_product import WorkProductFeedbackState, WorkProductType
from vaner.plan_drafts import (
    extract_proposed_plan_blocks,
    latest_plan_draft,
    list_plan_drafts,
    plan_draft_to_public,
    record_plan_draft,
    update_plan_draft_status,
)
from vaner.store.scenarios import ScenarioStore
from vaner.telemetry.metrics import MetricsStore

logger = logging.getLogger(__name__)


def _safe_same_path(left: Path, right: Path) -> bool:
    left_path = os.path.abspath(os.path.expanduser(os.fspath(left)))
    right_path = os.path.abspath(os.path.expanduser(os.fspath(right)))
    return left_path == right_path


def _metrics_path(repo_root: Path) -> Path:
    return repo_root / ".vaner" / "metrics.db"


def _snapshot_prediction_resolution(row: dict[str, Any]) -> Resolution | None:
    """Build an adoptable Resolution from a precompute-worker snapshot row."""

    spec = row.get("spec") if isinstance(row.get("spec"), dict) else {}
    artifacts = row.get("artifacts") if isinstance(row.get("artifacts"), dict) else {}
    pid = str(row.get("id") or spec.get("id") or row.get("prediction_id") or "").strip()
    if not pid:
        return None
    label = str(row.get("label") or spec.get("label") or pid)
    summary = str(row.get("ui_summary") or row.get("description") or spec.get("description") or label)
    source = str(row.get("source") or spec.get("source") or "precompute_worker")
    confidence = float(row.get("confidence") or spec.get("confidence") or 0.0)
    scenario_ids = [str(item) for item in (artifacts.get("scenario_ids") or []) if item]
    watched_sources = [str(item) for item in (row.get("watched_sources") or []) if item]
    evidence: list[EvidenceItem] = [
        EvidenceItem(
            id=scenario_id,
            source=source,
            kind="record",
            locator={"prediction_id": pid, "scenario_id": scenario_id},
            reason=f"scenario explored under prediction {label!r}",
            overlay="predicted",
            freshness=str(row.get("freshness") or "fresh") if row.get("freshness") in {"fresh", "recent", "stale"} else "fresh",
        )
        for scenario_id in scenario_ids
    ]
    for index, path in enumerate(watched_sources[:8], start=1):
        evidence.append(
            EvidenceItem(
                id=f"{pid}:file:{index}",
                source=source,
                kind="file",
                locator={"path": path, "prediction_id": pid},
                reason="watched source used by the prepared prediction",
                overlay="predicted",
                freshness=str(row.get("freshness") or "fresh") if row.get("freshness") in {"fresh", "recent", "stale"} else "fresh",
            )
        )
    briefing: str | None = None
    if row.get("has_briefing") or artifacts.get("has_briefing") or evidence:
        evidence_lines = [f"- {item.locator.get('path') or item.locator.get('scenario_id') or item.id}" for item in evidence[:12]]
        briefing = "\n".join(
            [
                "## Prepared Prediction",
                summary,
                "",
                "## Evidence",
                *(evidence_lines or ["- Snapshot prediction reported no explicit evidence paths."]),
                "",
                "## Provenance",
                f"Adopted from precompute-worker snapshot prediction `{pid}`.",
            ]
        )
    return Resolution(
        intent=label,
        confidence=confidence,
        summary=summary,
        evidence=evidence,
        provenance=Provenance(mode="predictive_hit", cache="warm", freshness="fresh"),
        resolution_id=f"adopt-{pid}",
        prepared_briefing=briefing,
        predicted_response=None,
        briefing_token_used=(len(briefing or "") + 3) // 4,
        briefing_token_budget=int((row.get("run") or {}).get("token_budget") or 0) if isinstance(row.get("run"), dict) else 0,
        adopted_from_prediction_id=pid,
    )


def _sanitize_validation_errors(exc: Any) -> list[dict[str, Any]]:
    """Strip the raw ``input`` value from pydantic ValidationError payloads.

    0.8.7 WS7 hardening C1: pydantic v2's ``exc.errors()`` echoes the
    offending value under the ``input`` key. For an adapter bug that
    placed draft text into the wrong field, that would reflect raw text
    back to the client in the error envelope. We surface only the field
    path + error type + message — never the input value or any URL the
    pydantic library generated for the error catalog.
    """
    safe: list[dict[str, Any]] = []
    for err in exc.errors():
        safe.append(
            {
                "loc": err.get("loc"),
                "type": err.get("type"),
                "msg": err.get("msg"),
            }
        )
    return safe


_WORK_PRODUCT_STALENESS_REFRESH_INTERVAL_SECONDS = 30.0


def create_daemon_http_app(config: VanerConfig, *, engine: Any | None = None) -> FastAPI:
    """Build the daemon FastAPI app.

    ``engine`` is optional — when not supplied (serve-http mode), the
    predictions endpoints return an empty snapshot. In-process embedders
    (tests, MCP server wrappers) can pass a live engine so the
    ``/predictions/*`` surface reflects real state.
    """
    scenario_store = ScenarioStore(config.repo_root / ".vaner" / "scenarios.db")
    metrics_store = MetricsStore(_metrics_path(config.repo_root))
    focus_manager = FocusManager(config)
    daemon_started_at = time.time()
    work_product_store: Any | None = None
    work_product_store_lock = asyncio.Lock()
    artefact_store: Any | None = None
    artefact_store_lock = asyncio.Lock()
    work_product_staleness_lock = asyncio.Lock()
    work_product_staleness_checked_at = 0.0

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await scenario_store.initialize()
        await metrics_store.initialize()
        if engine is not None and hasattr(engine, "initialize"):
            await engine.initialize()
        try:
            yield
        finally:
            pass

    app = FastAPI(title="Vaner Cockpit", version="0.2.0", lifespan=lifespan)
    cockpit_dist = cockpit_dist_dir()
    mount_cockpit_assets(app, cockpit_dist)

    async def _best_effort(awaitable: Awaitable[Any], fallback: Any, *, label: str, timeout: float = 1.5) -> Any:
        try:
            return await asyncio.wait_for(awaitable, timeout=timeout)
        except Exception:
            logger.debug("daemon: %s unavailable within %.1fs", label, timeout, exc_info=True)
            return fallback

    def _prediction_health() -> dict[str, Any]:
        readiness_counts = {state: 0 for state in ["queued", "grounding", "evidence_gathering", "drafting", "ready", "stale"]}
        if engine is None or getattr(engine, "prediction_registry", None) is None:
            snapshot = read_prediction_snapshot(config.repo_root)
            if snapshot is not None:
                by_state = snapshot.get("by_state") if isinstance(snapshot.get("by_state"), dict) else {}
                total = 0
                active = 0
                for state, items in by_state.items():
                    count = len(items) if isinstance(items, list) else 0
                    readiness_counts[str(state)] = count
                    total += count
                    if str(state) == "ready":
                        active += count
                if not active:
                    predictions = snapshot.get("predictions") if isinstance(snapshot.get("predictions"), list) else []
                    active = len(predictions)
                return {
                    "engine_available": True,
                    "daemon_with_engine": False,
                    "worker_snapshot": True,
                    "active_prediction_count": active,
                    "total_prediction_count": total or active,
                    "readiness_counts": readiness_counts,
                    "pending_adoption_outcomes": 0,
                    "stale_or_invalidated_reasons": [],
                    "diagnostic_status": "healthy" if active > 0 else "cold",
                }
            return {
                "engine_available": engine is not None,
                "daemon_with_engine": engine is not None,
                "active_prediction_count": 0,
                "total_prediction_count": 0,
                "readiness_counts": readiness_counts,
                "pending_adoption_outcomes": 0,
                "stale_or_invalidated_reasons": [],
                "diagnostic_status": "engine_unavailable" if engine is None else "cold",
            }
        registry = engine.prediction_registry
        prompts = registry.all()
        stale_reasons: list[str] = []
        for prompt in prompts:
            readiness_counts[prompt.run.readiness] = readiness_counts.get(prompt.run.readiness, 0) + 1
            if prompt.run.invalidation_reason:
                stale_reasons.append(prompt.run.invalidation_reason)
        pending_lock = getattr(registry, "_pending_adoption_lock", None)
        pending_queue = getattr(registry, "_pending_adoption_descriptors", [])
        if pending_lock is not None:
            with pending_lock:
                pending_adoptions = len(pending_queue)
        else:
            pending_adoptions = len(pending_queue)
        active_count = len(registry.active())
        return {
            "engine_available": True,
            "daemon_with_engine": True,
            "active_prediction_count": active_count,
            "total_prediction_count": len(prompts),
            "readiness_counts": readiness_counts,
            "pending_adoption_outcomes": pending_adoptions,
            "stale_or_invalidated_reasons": stale_reasons[-8:],
            "diagnostic_status": "healthy" if active_count > 0 else "cold",
        }

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/bootstrap")
    async def bootstrap() -> dict[str, Any]:
        return {
            "mode": "daemon",
            "version": app.version,
            "cockpit_sha": os.environ.get("VANER_COCKPIT_SHA", ""),
            "daemon_started_at": daemon_started_at,
            "repo_root": str(config.repo_root.resolve()),
            "workspace_id": focus_manager.current_workspace_id,
        }

    @app.get("/status")
    async def status(full: bool = False) -> JSONResponse:
        if full:
            top = await _best_effort(scenario_store.list_top(limit=1), [], label="scenario top", timeout=0.5)
            freshness = await _best_effort(
                scenario_store.freshness_counts(),
                {"fresh": 0, "recent": 0, "stale": 0, "total": 0},
                label="scenario freshness",
                timeout=0.5,
            )
            quality = await _best_effort(metrics_store.memory_quality_snapshot(), {}, label="memory quality", timeout=0.5)
            calibration = await _best_effort(metrics_store.calibration_snapshot(), [], label="calibration", timeout=0.5)
        else:
            top = []
            freshness = {"fresh": 0, "recent": 0, "stale": 0, "total": 0}
            quality = {}
            calibration = []
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
                "repo_root": str(config.repo_root.resolve()),
                "gateway_enabled": config.gateway.passthrough_enabled,
                "compute": config.compute.model_dump(mode="json"),
                "backend": config.backend.model_dump(mode="json"),
                "mcp": config.mcp.model_dump(mode="json"),
                "scenario_counts": freshness,
                "top_scenario": top[0].id if top else None,
                "prediction_metrics": prediction_metrics,
                "prediction_calibration": calibration,
                "prediction_health": _prediction_health(),
                "focus": focus_manager.build_state().model_dump(mode="json"),
            }
        )

    @app.get("/focus")
    async def focus() -> JSONResponse:
        return JSONResponse(focus_manager.build_state().model_dump(mode="json"))

    @app.get("/focus/route")
    async def focus_route() -> JSONResponse:
        return JSONResponse(focus_manager.route_state().model_dump(mode="json"))

    async def _focus_json_body(request: Request) -> dict[str, Any] | JSONResponse:
        if not request.headers.get("content-length"):
            return {}
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"code": "invalid_input", "message": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"code": "invalid_input", "message": "JSON object body required"}, status_code=400)
        return body

    def _focus_action_path(workspace_id: str, body: dict[str, Any]) -> Path | JSONResponse:
        path = Path(str(body.get("path") or config.repo_root))
        expected_id = "current" if _safe_same_path(path, config.repo_root) else focus_manager.workspace_id_for_path(path)
        if workspace_id not in {"current", expected_id}:
            return JSONResponse(
                {
                    "code": "workspace_id_mismatch",
                    "message": "workspace id does not match the requested workspace path",
                },
                status_code=404,
            )
        return path

    @app.post("/focus/observations")
    async def focus_observations(request: Request) -> JSONResponse:
        body = await _focus_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        return JSONResponse(focus_manager.record_observation(body).model_dump(mode="json"))

    @app.post("/focus/workspaces/{workspace_id}/work-here")
    async def focus_work_here(workspace_id: str, request: Request) -> JSONResponse:
        body = await _focus_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        path = _focus_action_path(workspace_id, body)
        if isinstance(path, JSONResponse):
            return path
        try:
            ttl = int(body.get("ttl_seconds") or 1800)
        except (TypeError, ValueError):
            return JSONResponse({"code": "invalid_input", "message": "ttl_seconds must be an integer"}, status_code=400)
        return JSONResponse(focus_manager.work_here(path, ttl_seconds=ttl).model_dump(mode="json"))

    @app.post("/focus/workspaces/{workspace_id}/pin")
    async def focus_pin(workspace_id: str, request: Request) -> JSONResponse:
        body = await _focus_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        path = _focus_action_path(workspace_id, body)
        if isinstance(path, JSONResponse):
            return path
        return JSONResponse(focus_manager.pin(path).model_dump(mode="json"))

    @app.post("/focus/workspaces/{workspace_id}/unpin")
    async def focus_unpin(workspace_id: str) -> JSONResponse:
        return JSONResponse(focus_manager.unpin().model_dump(mode="json"))

    @app.post("/focus/workspaces/{workspace_id}/pause")
    async def focus_pause(workspace_id: str, request: Request) -> JSONResponse:
        body = await _focus_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        path = _focus_action_path(workspace_id, body)
        if isinstance(path, JSONResponse):
            return path
        state = focus_manager.pause(path).model_dump(mode="json")
        control = request_worker_control(
            config.repo_root,
            action="pause",
            reason="workspace paused by user",
            drain_queue=True,
        )
        return JSONResponse({**state, "worker_control": control})

    @app.post("/focus/workspaces/{workspace_id}/resume")
    async def focus_resume(workspace_id: str, request: Request) -> JSONResponse:
        body = await _focus_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        path = _focus_action_path(workspace_id, body)
        if isinstance(path, JSONResponse):
            return path
        return JSONResponse(focus_manager.resume(path).model_dump(mode="json"))

    @app.post("/focus/pause-all")
    async def focus_pause_all() -> JSONResponse:
        state = focus_manager.pause_all().model_dump(mode="json")
        control = request_worker_control(
            config.repo_root,
            action="pause_all",
            reason="all workspaces paused by user",
            drain_queue=True,
        )
        return JSONResponse({**state, "worker_control": control})

    @app.post("/focus/mode")
    async def focus_mode(request: Request) -> JSONResponse:
        body = await _focus_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        mode = str(body.get("mode") or "").strip()
        if mode not in {"auto", "manual-only", "paused"}:
            return JSONResponse({"code": "invalid_mode", "message": "mode must be auto|manual-only|paused"}, status_code=400)
        resource_mode = body.get("resource_mode")
        if resource_mode is not None and resource_mode not in {"balanced", "low_power", "performance"}:
            return JSONResponse(
                {"code": "invalid_resource_mode", "message": "resource_mode must be balanced|low_power|performance"},
                status_code=400,
            )
        state = focus_manager.set_mode(mode, resource_mode=resource_mode).model_dump(mode="json")  # type: ignore[arg-type]
        if mode in {"manual-only", "paused"}:
            control = request_worker_control(
                config.repo_root,
                action="pause_all",
                reason=f"focus mode set to {mode}",
                drain_queue=True,
            )
            state = {**state, "worker_control": control}
        return JSONResponse(state)

    @app.post("/focus/route")
    async def focus_route_update(request: Request) -> JSONResponse:
        nonlocal config
        body = await _focus_json_body(request)
        if isinstance(body, JSONResponse):
            return body
        try:
            workspace_path = None
            if body.get("workspace_path") is not None:
                expanded_workspace = os.path.expanduser(str(body.get("workspace_path")))
                if not os.path.isabs(expanded_workspace):
                    return JSONResponse({"code": "invalid_workspace", "message": "workspace_path must be absolute"}, status_code=400)
                workspace_text = os.path.abspath(expanded_workspace)
                available_paths = {
                    os.path.abspath(os.path.expanduser(str(workspace.get("canonical_path"))))
                    for workspace in focus_manager.route_state().workspace_options
                    if isinstance(workspace, dict) and workspace.get("canonical_path")
                }
                available_paths.add(os.path.abspath(os.path.expanduser(os.fspath(config.repo_root))))
                if workspace_text not in available_paths:
                    return JSONResponse(
                        {"code": "invalid_workspace", "message": "workspace_path must be a known workspace"},
                        status_code=400,
                    )
                workspace_path = Path(workspace_text)
            client_supplied = "client_id" in body
            client_id = body.get("client_id") if client_supplied else ...
            focus_manager.set_route_preferences(
                workspace_policy=str(body["workspace_policy"]) if "workspace_policy" in body else None,
                workspace_path=workspace_path,
                client_id=client_id,
                resource_mode=str(body["resource_mode"]) if "resource_mode" in body else None,
                ttl_seconds=int(body["ttl_seconds"]) if "ttl_seconds" in body and body.get("ttl_seconds") is not None else None,
            )
            if "compute_device" in body and body.get("compute_device") is not None:
                device = str(body.get("compute_device")).strip()
                allowed = {"auto", "cpu", "cuda", "mps", "metal"}
                allowed.update(
                    str(device.get("id"))
                    for device in focus_manager.route_state().hardware_options.get("devices", [])
                    if isinstance(device, dict)
                )
                if device not in allowed:
                    return JSONResponse({"code": "invalid_compute_device", "message": "compute_device is not available"}, status_code=400)
                set_compute_value(config.repo_root, "device", device)
            backend = body.get("backend")
            if backend is not None:
                if not isinstance(backend, dict):
                    return JSONResponse({"code": "invalid_backend", "message": "backend must be an object"}, status_code=400)
                if "api_key_env" in backend and backend["api_key_env"] is not None:
                    return JSONResponse(
                        {
                            "code": "invalid_backend",
                            "message": "api_key_env must be changed with `vaner config set backend.api_key_env <ENV_VAR>`",
                        },
                        status_code=400,
                    )
                for key in ("name", "base_url", "model"):
                    if key in backend and backend[key] is not None:
                        set_config_value(config.repo_root, "backend", key, str(backend[key]))
            config = load_config(config.repo_root)
            focus_manager.config = config
        except ValueError:
            return JSONResponse({"code": "invalid_route", "message": "invalid focus route"}, status_code=400)
        except FileNotFoundError:
            return JSONResponse({"code": "config_missing", "message": "Vaner config file was not found"}, status_code=400)
        return JSONResponse(focus_manager.route_state().model_dump(mode="json"))

    @app.get("/resources")
    async def resources() -> JSONResponse:
        return JSONResponse(focus_manager.resources_state().model_dump(mode="json"))

    @app.get("/jobs")
    async def jobs() -> JSONResponse:
        payload = focus_manager.jobs_state()
        worker = read_worker_status(config.repo_root)
        if worker is not None:
            worker_jobs = worker.get("jobs") if isinstance(worker.get("jobs"), list) else []
            payload["jobs"] = list(worker_jobs) + list(payload.get("jobs") or [])
            payload["worker"] = worker.get("worker")
            payload["queue"] = worker.get("queue")
            payload["profile"] = worker.get("profile")
        return JSONResponse(payload)

    @app.get("/plans/drafts")
    async def plans_drafts(limit: int = 20, include_inactive: bool = False) -> JSONResponse:
        drafts = list_plan_drafts(
            config.repo_root,
            limit=max(1, min(100, int(limit))),
            include_inactive=include_inactive,
        )
        return JSONResponse({"drafts": [plan_draft_to_public(draft) for draft in drafts], "count": len(drafts)})

    @app.get("/plans/drafts/{plan_id}")
    async def plans_draft(plan_id: str) -> JSONResponse:
        for draft in list_plan_drafts(config.repo_root, limit=100, include_inactive=True):
            if draft.id == plan_id:
                return JSONResponse({"draft": plan_draft_to_public(draft)})
        raise HTTPException(status_code=404, detail="plan draft not found")

    @app.post("/plans/drafts/{plan_id}/status")
    async def plans_draft_update_status(plan_id: str, request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"code": "invalid_input", "message": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"code": "invalid_input", "message": "body must be an object"}, status_code=400)
        status = str(body.get("status") or "").strip().lower()
        if not status:
            return JSONResponse({"code": "invalid_input", "message": "status is required"}, status_code=400)
        try:
            draft = update_plan_draft_status(
                config.repo_root,
                plan_id,
                status=status,
                accepted=body.get("accepted") if isinstance(body.get("accepted"), bool) else None,
            )
        except ValueError:
            return JSONResponse({"code": "invalid_status", "message": "unsupported plan draft status"}, status_code=400)
        if draft is None:
            raise HTTPException(status_code=404, detail="plan draft not found")
        wake = request_precompute_wake(config.repo_root, reason=f"plan_draft_{status}")
        return JSONResponse({"ok": True, "draft": plan_draft_to_public(draft), "wake_id": wake["id"]})

    @app.post("/plans/drafts/{plan_id}/complete")
    async def plans_draft_complete(plan_id: str) -> JSONResponse:
        draft = update_plan_draft_status(config.repo_root, plan_id, status="implemented", accepted=True)
        if draft is None:
            raise HTTPException(status_code=404, detail="plan draft not found")
        wake = request_precompute_wake(config.repo_root, reason="plan_draft_implemented")
        return JSONResponse({"ok": True, "draft": plan_draft_to_public(draft), "wake_id": wake["id"]})

    @app.post("/plans/draft")
    async def plans_draft_create(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"code": "invalid_input", "message": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"code": "invalid_input", "message": "body must be an object"}, status_code=400)
        text = str(body.get("text") or body.get("plan") or "").strip()
        if not text:
            return JSONResponse({"code": "invalid_input", "message": "text is required"}, status_code=400)
        draft = record_plan_draft(
            config.repo_root,
            text=text,
            source_client=str(body.get("source_client") or body.get("host_app") or "unknown")[:128],
            session_id=str(body.get("session_id") or "")[:256],
            turn_id=str(body.get("turn_id") or "")[:256],
            workspace_id=str(body.get("workspace_id") or "")[:2048],
            source_event_id=str(body.get("source_event_id") or "")[:256],
        )
        wake = request_precompute_wake(config.repo_root, reason="plan_draft")
        return JSONResponse({"ok": True, "draft": plan_draft_to_public(draft), "wake_id": wake["id"]})

    @app.get("/work/active")
    async def work_active() -> JSONResponse:
        return JSONResponse(await _active_work_snapshot())

    @app.get("/work/active/stream")
    async def work_active_stream() -> StreamingResponse:
        async def event_gen() -> AsyncIterator[str]:
            last = ""
            while True:
                snapshot = await _active_work_snapshot()
                serialized = json.dumps(snapshot, sort_keys=True, default=str)
                if serialized != last:
                    yield f"data: {serialized}\n\n"
                    last = serialized
                yield ": keepalive\n\n"
                await asyncio.sleep(1.5)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @app.get("/activity/recent")
    async def recent_activity(limit: int = 20, host_app: str | None = None, source: str | None = None) -> JSONResponse:
        store = await _work_product_store()
        rows = await store.list_query_history(limit=max(1, min(limit * 3, 200)))
        filtered: list[dict[str, Any]] = []
        for row in rows:
            if host_app and row.get("host_app") != host_app:
                continue
            if source and row.get("source") != source:
                continue
            filtered.append(row)
            if len(filtered) >= max(1, min(limit, 100)):
                break
        return JSONResponse({"items": filtered, "count": len(filtered)})

    @app.get("/events/recent")
    async def recent_events(limit: int = 50, corpus_id: str | None = None) -> JSONResponse:
        store = await _work_product_store()
        capped_limit = max(1, min(limit, 200))
        signal_rows = await _best_effort(
            store.list_signal_events(corpus_id=corpus_id, limit=capped_limit),
            [],
            label="recent signal events",
        )
        query_rows = await _best_effort(
            store.list_query_history(corpus_id=corpus_id, limit=capped_limit),
            [],
            label="recent query history",
        )
        events: list[dict[str, Any]] = []

        for signal in signal_rows:
            payload = dict(signal.payload)
            path = payload.get("path") or payload.get("workspace_rel_path")
            events.append(
                {
                    "id": signal.id,
                    "ts": signal.timestamp,
                    "stage": "signals",
                    "kind": "signal.ingest",
                    "payload": {
                        "source": signal.source,
                        "signal_kind": signal.kind,
                        "fs_scan": 1 if signal.source in {"fs", "fs_scan", "repo_scan"} else 0,
                        "workspace_changed": 1
                        if signal.source in {"git", "codex", "codex_prompt", "composer"} or signal.source.startswith("intent_artefact:")
                        else 0,
                        "path": path,
                    },
                    "path": path,
                    "cycle_id": payload.get("cycle_id"),
                    "msg": f"{signal.source} {signal.kind} observed",
                }
            )

        for row in query_rows:
            source = str(row.get("source") or "query")
            host_app = str(row.get("host_app") or "client")
            events.append(
                {
                    "id": f"query-{row['id']}",
                    "ts": row["timestamp"],
                    "stage": "signals",
                    "kind": "signal.ingest",
                    "payload": {
                        "source": source,
                        "host_app": host_app,
                        "session_id": row.get("session_id"),
                        "turn_id": row.get("turn_id"),
                        "capture_policy": row.get("capture_policy"),
                        "fs_scan": 0,
                        "workspace_changed": 1,
                    },
                    "path": None,
                    "cycle_id": None,
                    "msg": f"{host_app} activity observed",
                }
            )

        events.sort(key=lambda item: float(item.get("ts") or 0.0), reverse=True)
        events = events[:capped_limit]
        return JSONResponse({"events": events, "count": len(events)})

    @app.post("/jobs/{job_id}/cancel")
    async def jobs_cancel(job_id: str) -> JSONResponse:
        control = request_worker_control(
            config.repo_root,
            action="cancel_active",
            reason=f"cancel requested for {job_id}",
            job_id=job_id,
            drain_queue=False,
        )
        return JSONResponse(
            {
                "ok": True,
                "job_id": job_id,
                "status": "cancelled",
                "reason_code": "cancel_requested",
                "explanation": "Cancellable background work will be interrupted if it is currently active.",
                "worker_control": control,
            }
        )

    # ------------------------------------------------------------------
    # 0.8.3 WS4 — Deep-Run lifecycle HTTP endpoints. Cockpit + desktop
    # consume these. When the daemon is wired to a live engine, calls
    # route through engine methods so the routing singleton + cost gate
    # update in-process. When the engine is None (serve-http only),
    # calls fall back to vaner.server helpers that build a default
    # engine per call — durable but does not arm in-process gates.
    # ------------------------------------------------------------------

    def _serialize_session(session: Any) -> dict[str, Any] | None:
        from vaner.cli.commands.deep_run import _session_to_dict

        return _session_to_dict(session) if session is not None else None

    def _serialize_summary(summary: Any) -> dict[str, Any] | None:
        from vaner.cli.commands.deep_run import _summary_to_dict

        return _summary_to_dict(summary) if summary is not None else None

    @app.post("/deep-run/start")
    async def deep_run_start(request: Request) -> JSONResponse:
        body = await request.json() if request.headers.get("content-length") else {}
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="JSON object body required")
        ends_at = body.get("ends_at")
        if not isinstance(ends_at, (int, float)):
            raise HTTPException(status_code=400, detail="ends_at (epoch number) is required")
        kwargs: dict[str, Any] = {
            "ends_at": float(ends_at),
            "preset": str(body.get("preset", "balanced")),
            "focus": str(body.get("focus", "active_goals")),
            "horizon_bias": str(body.get("horizon_bias", "balanced")),
            "locality": str(body.get("locality", "local_preferred")),
            "cost_cap_usd": float(body.get("cost_cap_usd", 0.0)),
            "metadata": dict(body.get("metadata") or {}) | {"caller": "http"},
        }
        try:
            if engine is not None:
                session = await engine.start_deep_run(**kwargs)
            else:
                from vaner.server import astart_deep_run

                session = await astart_deep_run(config.repo_root, **kwargs)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(_serialize_session(session))

    @app.post("/deep-run/stop")
    async def deep_run_stop(request: Request) -> JSONResponse:
        body = await request.json() if request.headers.get("content-length") else {}
        if not isinstance(body, dict):
            body = {}
        kill = bool(body.get("kill", False))
        reason = body.get("reason") if isinstance(body.get("reason"), str) else None
        if engine is not None:
            summary = await engine.stop_deep_run(kill=kill, reason=reason)
        else:
            from vaner.server import astop_deep_run

            summary = await astop_deep_run(config.repo_root, kill=kill, reason=reason)
        return JSONResponse({"summary": _serialize_summary(summary)})

    @app.get("/deep-run/status")
    async def deep_run_status_endpoint() -> JSONResponse:
        if engine is not None:
            session = await engine.current_deep_run()
        else:
            from vaner.server import astatus_deep_run

            session = await astatus_deep_run(config.repo_root)
        return JSONResponse({"session": _serialize_session(session)})

    @app.get("/deep-run/sessions")
    async def deep_run_list_sessions(limit: int = 20) -> JSONResponse:
        limit = max(1, min(200, int(limit)))
        if engine is not None:
            sessions = await engine.list_deep_run_sessions(limit=limit)
        else:
            from vaner.server import alist_deep_run_sessions

            sessions = await alist_deep_run_sessions(config.repo_root, limit=limit)
        return JSONResponse({"sessions": [_serialize_session(s) for s in sessions]})

    @app.get("/deep-run/sessions/{session_id}")
    async def deep_run_show(session_id: str) -> JSONResponse:
        from vaner.server import aresolve_deep_run_session

        session = await aresolve_deep_run_session(config.repo_root, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"session {session_id!r} not found")
        return JSONResponse(_serialize_session(session))

    @app.get("/deep-run/defaults")
    async def deep_run_defaults_endpoint() -> JSONResponse:
        """Return the bundle-derived Deep-Run start-dialog seeds.

        Reads the active policy bundle (defaulting to ``hybrid_balanced``
        when no bundle has been selected) and the persisted SetupAnswers
        (defaulting to a neutral set when no Simple-Mode run has
        happened yet), then runs :func:`deep_run_defaults_for`. Pure
        read — no side effects.
        """

        from vaner.cli.commands.setup import _default_answers
        from vaner.intent.deep_run_defaults import (
            deep_run_defaults_for,
            defaults_to_dict,
        )
        from vaner.setup.answers import SetupAnswers
        from vaner.setup.catalog import bundle_by_id
        from vaner.setup.config_io import read_policy_section, read_setup_section

        repo_root = config.repo_root
        policy_section = read_policy_section(repo_root)
        selected_bundle_id = policy_section.get("selected_bundle_id") or "hybrid_balanced"
        try:
            bundle = bundle_by_id(str(selected_bundle_id))
        except KeyError:
            raise HTTPException(
                status_code=503,
                detail=f"unknown bundle id {selected_bundle_id!r}; run `vaner setup wizard`",
            ) from None

        setup_section = read_setup_section(repo_root)
        answers: SetupAnswers
        if setup_section:
            try:
                from vaner.setup.serializers import answers_from_payload

                answers = answers_from_payload(setup_section)
            except Exception:
                answers = _default_answers()
        else:
            answers = _default_answers()

        defaults = deep_run_defaults_for(bundle, answers)
        return JSONResponse(defaults_to_dict(defaults))

    # ------------------------------------------------------------------
    # 0.8.6 WS8 — Setup HTTP surface. Mirrors the WS7 MCP tools so
    # desktop apps that prefer HTTP can drive the wizard end-to-end.
    # Reuses setup serialisation helpers as the canonical contract for
    # the JSON shapes; the MCP tools use the same helpers so both
    # surfaces stay in lock-step.
    #
    # Hardware detection is cached for the daemon process lifetime
    # because probing reaches into /sys, runs subprocesses, etc — once
    # is enough per daemon. The cache is process-local; restart picks
    # up new hardware. Tests reset the cache via the helper below.
    # ------------------------------------------------------------------

    _hardware_cache: dict[str, Any] = {"profile": None}

    def _get_hardware_profile_cached() -> Any:
        from vaner.setup.hardware import detect

        if _hardware_cache["profile"] is None:
            _hardware_cache["profile"] = detect()
        return _hardware_cache["profile"]

    def _reset_hardware_cache_for_tests() -> None:
        _hardware_cache["profile"] = None

    # Expose the reset hook on the app so tests can clear the cache
    # between runs. Production callers never need this.
    app.state.reset_hardware_cache = _reset_hardware_cache_for_tests
    # Same for the wired engine, used by /policy/refresh.
    app.state.engine = engine

    def _read_setup_section_for_http(repo_root: Path) -> dict[str, Any]:
        from vaner.setup.config_io import read_setup_section

        return read_setup_section(repo_root)

    def _read_policy_section_for_http(repo_root: Path) -> dict[str, Any]:
        from vaner.setup.config_io import read_policy_section

        return read_policy_section(repo_root)

    def _bundle_to_dict_http(bundle: Any) -> dict[str, Any]:
        from vaner.setup.serializers import bundle_to_dict

        return bundle_to_dict(bundle)

    def _selection_to_dict_http(result: Any) -> dict[str, Any]:
        from vaner.setup.serializers import selection_to_dict

        return selection_to_dict(result)

    def _hardware_to_dict_http(hw: Any) -> dict[str, Any]:
        from vaner.setup.serializers import hardware_to_dict

        return hardware_to_dict(hw)

    def _answers_from_payload_http(raw: Any) -> Any:
        from vaner.setup.serializers import (
            AnswersValidationError,
            answers_from_payload,
        )

        try:
            return answers_from_payload(raw)
        except AnswersValidationError as exc:
            detail = str(exc)
            if detail == "answers payload must be a JSON object":
                detail = "answers must be a JSON object"
            raise HTTPException(status_code=400, detail=detail) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    from vaner.setup.questions import setup_questions_for_http

    # The five Simple-Mode questions in the stable daemon wire shape.
    _SETUP_QUESTIONS_PAYLOAD = setup_questions_for_http()

    @app.get("/setup/questions")
    async def setup_questions() -> JSONResponse:
        """Return the static five-question Simple-Mode payload.

        Contract-stable — desktop apps and MCP tools both consume this
        shape. ``version`` lets clients gate against schema drift.
        """

        return JSONResponse(_SETUP_QUESTIONS_PAYLOAD)

    @app.post("/setup/recommend")
    async def setup_recommend(request: Request) -> JSONResponse:
        """Run :func:`vaner.setup.select.select_policy_bundle` over a
        :class:`SetupAnswers` body and return :class:`SelectionResult`.

        Pure read — no persistence side effects. The hardware probe is
        cached for the daemon process lifetime.
        """

        from vaner.setup.select import select_policy_bundle

        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="request body must be JSON") from exc
        answers = _answers_from_payload_http(body)
        hardware = _get_hardware_profile_cached()
        selection = select_policy_bundle(answers, hardware)
        return JSONResponse(_selection_to_dict_http(selection))

    @app.post("/models/recommended")
    async def models_recommended(request: Request) -> JSONResponse:
        """Return the concrete runtime/model recommendation contract."""

        from vaner.setup.model_recommendation import recommend_local_model

        try:
            body = await request.json()
        except Exception:
            body = {}
        if body is None:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="request body must be a JSON object")
        raw_answers = body.get("answers")
        answers = _answers_from_payload_http(raw_answers) if isinstance(raw_answers, dict) else None
        hardware = _get_hardware_profile_cached()
        return JSONResponse(recommend_local_model(answers=answers, hardware=hardware))

    @app.post("/setup/apply")
    async def setup_apply(request: Request) -> JSONResponse:
        """Persist answers + selected bundle id to ``.vaner/config.toml``.

        Body shape mirrors the WS7 MCP ``vaner.setup.apply`` tool::

            {
              "answers": {...SetupAnswers...} | null,
              "bundle_id": "..." | null,
              "confirm_cloud_widening": false,
              "dry_run": false
            }

        WIDENS_CLOUD_POSTURE behaviour: if the new bundle's cloud
        posture is strictly more permissive than the previous bundle's
        posture, the response carries ``widens_cloud_posture=true`` and
        ``written=false`` unless ``confirm_cloud_widening=true``.
        """

        from vaner.cli.commands.setup import _default_answers
        from vaner.setup.apply import (
            WIDENS_CLOUD_POSTURE_SENTINEL,
            apply_policy_bundle,
        )
        from vaner.setup.catalog import bundle_by_id
        from vaner.setup.config_io import persist_runtime_recommendation, persist_setup_and_policy
        from vaner.setup.model_recommendation import recommend_local_model
        from vaner.setup.select import select_policy_bundle
        from vaner.setup.serializers import answers_from_payload

        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="request body must be JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="request body must be a JSON object")

        confirm_cloud_widening = bool(body.get("confirm_cloud_widening", False))
        dry_run = bool(body.get("dry_run", False))
        bundle_id_override = body.get("bundle_id")
        raw_answers = body.get("answers")

        repo_root = config.repo_root

        # Resolve answers + bundle_id ----------------------------------
        if bundle_id_override is not None:
            if not isinstance(bundle_id_override, str) or not bundle_id_override.strip():
                raise HTTPException(status_code=400, detail="bundle_id must be a non-empty string")
            try:
                bundle = bundle_by_id(bundle_id_override)
            except KeyError as exc:
                raise HTTPException(status_code=400, detail=f"unknown bundle id: {bundle_id_override!r}") from exc
            if isinstance(raw_answers, dict):
                answers = _answers_from_payload_http(raw_answers)
            else:
                existing = _read_setup_section_for_http(repo_root)
                if existing:
                    try:
                        answers = answers_from_payload(existing)
                    except Exception:
                        answers = _default_answers()
                else:
                    answers = _default_answers()
            chosen_bundle_id = bundle.id
            reasons: list[str] = ["explicit bundle_id override"]
        else:
            if isinstance(raw_answers, dict):
                answers = _answers_from_payload_http(raw_answers)
            else:
                existing = _read_setup_section_for_http(repo_root)
                if not existing:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "no answers provided and no [setup] section on disk; supply 'answers' in the body or run `vaner setup wizard`"
                        ),
                    )
                try:
                    answers = answers_from_payload(existing)
                except Exception as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"failed to parse persisted setup answers: {exc}",
                    ) from exc
            hardware = _get_hardware_profile_cached()
            selection = select_policy_bundle(answers, hardware)
            bundle = selection.bundle
            chosen_bundle_id = bundle.id
            reasons = list(selection.reasons)

        # Cloud-widening guard via apply_policy_bundle's diff ----------
        loaded = load_config(repo_root)
        prior_policy_section = _read_policy_section_for_http(repo_root)
        prior_bundle_id = prior_policy_section.get("selected_bundle_id")
        if isinstance(prior_bundle_id, str) and prior_bundle_id:
            loaded = loaded.model_copy(update={"policy": loaded.policy.model_copy(update={"selected_bundle_id": prior_bundle_id})})
        applied = apply_policy_bundle(loaded, bundle)
        widens = any(entry.startswith(WIDENS_CLOUD_POSTURE_SENTINEL) for entry in applied.overrides_applied)

        applied_summary: dict[str, Any] = {
            "bundle_id": applied.bundle_id,
            "overrides_applied": list(applied.overrides_applied),
        }

        # Block writes when cloud posture would widen and the caller
        # has not explicitly confirmed.
        if widens and not confirm_cloud_widening:
            return JSONResponse(
                {
                    "written": False,
                    "dry_run": dry_run,
                    "widens_cloud_posture": True,
                    "selected_bundle_id": chosen_bundle_id,
                    "reasons": reasons,
                    "applied_policy": applied_summary,
                    "bundle": _bundle_to_dict_http(bundle),
                    "message": ("Cloud posture would widen. Re-send with confirm_cloud_widening=true to proceed."),
                }
            )

        if dry_run:
            return JSONResponse(
                {
                    "written": False,
                    "dry_run": True,
                    "widens_cloud_posture": widens,
                    "selected_bundle_id": chosen_bundle_id,
                    "reasons": reasons,
                    "applied_policy": applied_summary,
                    "bundle": _bundle_to_dict_http(bundle),
                }
            )

        completed_at = datetime.now(UTC)
        config_path = persist_setup_and_policy(repo_root, answers, chosen_bundle_id, completed_at=completed_at)
        model_recommendation = recommend_local_model(answers=answers, hardware=_get_hardware_profile_cached())
        persist_runtime_recommendation(repo_root, model_recommendation)

        return JSONResponse(
            {
                "written": True,
                "dry_run": False,
                "widens_cloud_posture": widens,
                "selected_bundle_id": chosen_bundle_id,
                "reasons": reasons,
                "applied_policy": applied_summary,
                "bundle": _bundle_to_dict_http(bundle),
                "config_path": str(config_path),
                "model_recommendation": model_recommendation.get("user"),
            }
        )

    @app.get("/setup/status")
    async def setup_status() -> JSONResponse:
        """Return the same payload shape as the MCP ``vaner.setup.status`` tool.

        Carries: setup mode + answers, selected bundle id, applied
        policy summary (with overrides), hardware profile.
        """

        from vaner.setup.apply import apply_policy_bundle
        from vaner.setup.catalog import bundle_by_id

        repo_root = config.repo_root
        setup_section = _read_setup_section_for_http(repo_root)
        policy_section = _read_policy_section_for_http(repo_root)
        hardware = _get_hardware_profile_cached()

        selected_bundle_id = policy_section.get("selected_bundle_id") or "hybrid_balanced"
        applied_dict: dict[str, Any]
        bundle_dict: dict[str, Any] | None = None
        try:
            bundle = bundle_by_id(str(selected_bundle_id))
            bundle_dict = _bundle_to_dict_http(bundle)
            loaded = load_config(repo_root)
            loaded = loaded.model_copy(update={"policy": loaded.policy.model_copy(update={"selected_bundle_id": str(selected_bundle_id)})})
            applied = apply_policy_bundle(loaded, bundle)
            applied_dict = {
                "bundle_id": applied.bundle_id,
                "overrides_applied": list(applied.overrides_applied),
            }
        except KeyError:
            applied_dict = {"error": f"unknown bundle id {selected_bundle_id!r}"}

        mode = setup_section.get("mode") if isinstance(setup_section, dict) else None
        completed_at = setup_section.get("completed_at") if isinstance(setup_section, dict) else None
        completed = bool(setup_section) and completed_at is not None

        return JSONResponse(
            {
                "repo_root": str(repo_root),
                "mode": mode or "unconfigured",
                "completed": completed,
                "completed_at": completed_at,
                "selected_bundle_id": str(selected_bundle_id),
                "setup": setup_section,
                "policy": policy_section,
                "applied_policy": applied_dict,
                "bundle": bundle_dict,
                "hardware": _hardware_to_dict_http(hardware),
            }
        )

    @app.get("/policy/current")
    async def policy_current() -> JSONResponse:
        """Return the materialised :class:`AppliedPolicy` plus its bundle.

        Same payload shape as the WS7 MCP ``vaner.policy.show`` tool —
        bundle, applied-policy summary (with ``overrides_applied`` and
        the ``WIDENS_CLOUD_POSTURE`` sentinel passed through), the raw
        ``[policy]`` section from disk, and the engine's wired status.
        """

        from vaner.setup.apply import apply_policy_bundle
        from vaner.setup.catalog import bundle_by_id

        repo_root = config.repo_root
        policy_section = _read_policy_section_for_http(repo_root)
        selected_bundle_id = policy_section.get("selected_bundle_id") or "hybrid_balanced"

        try:
            bundle = bundle_by_id(str(selected_bundle_id))
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"unknown bundle id {selected_bundle_id!r}",
            ) from None

        loaded = load_config(repo_root)
        loaded = loaded.model_copy(update={"policy": loaded.policy.model_copy(update={"selected_bundle_id": str(selected_bundle_id)})})
        applied = apply_policy_bundle(loaded, bundle)
        applied_dict = {
            "bundle_id": applied.bundle_id,
            "overrides_applied": list(applied.overrides_applied),
        }

        return JSONResponse(
            {
                "selected_bundle_id": bundle.id,
                "bundle": _bundle_to_dict_http(bundle),
                "applied_policy": applied_dict,
                "policy_section": policy_section,
                "engine_wired": engine is not None,
            }
        )

    @app.get("/hardware/profile")
    async def hardware_profile_endpoint() -> JSONResponse:
        """Return :class:`HardwareProfile` JSON, cached for daemon lifetime.

        First call probes the system; subsequent calls return the
        cached result. Restart the daemon (or hit the test-only reset
        hook) to force a fresh probe.
        """

        hw = _get_hardware_profile_cached()
        return JSONResponse(_hardware_to_dict_http(hw))

    @app.post("/policy/refresh")
    async def policy_refresh(request: Request) -> JSONResponse:
        """Trigger ``engine._refresh_policy_bundle_state()`` on the live engine.

        Used by ``vaner setup apply`` (WS6) to get a hot reload without
        a daemon restart. Returns 503 when the engine is not wired or
        the refresh hook is missing.
        """

        live_engine = app.state.engine
        if live_engine is None:
            return JSONResponse(
                {
                    "code": "engine_unavailable",
                    "message": "daemon engine not wired; cannot refresh policy state",
                },
                status_code=503,
            )
        refresh_hook = getattr(live_engine, "_refresh_policy_bundle_state", None)
        if refresh_hook is None or not callable(refresh_hook):
            return JSONResponse(
                {
                    "code": "engine_unsupported",
                    "message": "engine does not expose _refresh_policy_bundle_state",
                },
                status_code=503,
            )
        try:
            refresh_hook()
        except Exception:
            return JSONResponse(
                {
                    "code": "refresh_failed",
                    "message": "policy refresh failed",
                },
                status_code=503,
            )

        applied = getattr(live_engine, "_applied_policy", None)
        applied_summary: dict[str, Any] | None = None
        if applied is not None:
            applied_summary = {
                "bundle_id": applied.bundle_id,
                "overrides_applied": list(applied.overrides_applied),
            }

        return JSONResponse(
            {
                "refreshed": True,
                "applied_policy_summary": applied_summary,
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

    @app.get("/backend/presets")
    async def backend_presets() -> JSONResponse:
        """Return cockpit-selectable backend presets.

        This endpoint is intentionally read-only metadata; applying a preset
        still flows through the existing config update surfaces.
        """

        return JSONResponse(
            {
                "presets": [
                    {
                        "name": "Ollama local",
                        "base_url": "http://127.0.0.1:11434/v1",
                        "default_model": config.backend.model or "qwen3.6:27b",
                        "api_key_env": "OPENAI_API_KEY",
                    },
                    {
                        "name": "vLLM OpenAI-compatible",
                        "base_url": "http://127.0.0.1:8000/v1",
                        "default_model": config.backend.model or "Qwen/Qwen3.6-27B-Instruct",
                        "api_key_env": "OPENAI_API_KEY",
                    },
                    {
                        "name": "OpenAI",
                        "base_url": "https://api.openai.com/v1",
                        "default_model": "gpt-5.2",
                        "api_key_env": "OPENAI_API_KEY",
                    },
                ]
            }
        )

    @app.get("/external-state")
    async def get_external_state() -> JSONResponse:
        return JSONResponse(external_state_payload(config).model_dump(mode="json"))

    @app.post("/external-state")
    async def update_external_state(payload: dict[str, Any]) -> JSONResponse:
        nonlocal config
        allowed = {"enabled", "max_calls_per_cycle", "max_cycle_ms"}
        for key in payload:
            if key not in allowed:
                raise HTTPException(status_code=400, detail=f"Unsupported external-state key: {key}")
        save_external_state_enabled(
            config.repo_root,
            enabled=bool(payload["enabled"]) if "enabled" in payload else None,
            max_calls_per_cycle=int(payload["max_calls_per_cycle"]) if "max_calls_per_cycle" in payload else None,
            max_cycle_ms=int(payload["max_cycle_ms"]) if "max_cycle_ms" in payload else None,
        )
        config = load_config(config.repo_root)
        focus_manager.config = config
        return JSONResponse(external_state_payload(config).model_dump(mode="json"))

    @app.post("/external-state/providers")
    async def upsert_external_state_provider(payload: dict[str, Any]) -> JSONResponse:
        nonlocal config
        provider_id = str(payload.get("id") or payload.get("provider_id") or "").strip()
        if not provider_id:
            raise HTTPException(status_code=400, detail="provider id is required")
        args = payload.get("args", [])
        if isinstance(args, str):
            args = [item for item in args.split(" ") if item]
        if not isinstance(args, list):
            raise HTTPException(status_code=400, detail="args must be a list")
        env = payload.get("env", {})
        if not isinstance(env, dict):
            raise HTTPException(status_code=400, detail="env must be an object")
        try:
            save_provider_config(
                config.repo_root,
                ProviderConfigInput(
                    provider_id=provider_id,
                    transport=str(payload.get("transport") or "stdio"),
                    command=str(payload.get("command") or ""),
                    args=[str(item) for item in args],
                    url=str(payload.get("url") or ""),
                    env={str(key): str(value) for key, value in env.items()},
                    timeout_ms=int(payload.get("timeout_ms") or 10000),
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        config = load_config(config.repo_root)
        focus_manager.config = config
        return JSONResponse(external_state_payload(config).model_dump(mode="json"))

    @app.post("/external-state/providers/{provider_id}/discover")
    async def discover_external_state_provider(provider_id: str, payload: dict[str, Any] | None = None) -> JSONResponse:
        nonlocal config
        body = payload or {}
        try:
            discovery = await discover_provider(
                config,
                provider_id,
                apply=bool(body.get("apply", False)),
                trust_unknown_read=bool(body.get("trust_unknown_read", False)),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="provider not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"provider discovery failed: {exc}") from exc
        if discovery.applied:
            config = load_config(config.repo_root)
            focus_manager.config = config
        return JSONResponse(discovery.model_dump(mode="json"))

    @app.post("/external-state/finance")
    async def update_external_state_finance(payload: dict[str, Any]) -> JSONResponse:
        nonlocal config
        allowed = {"enabled", "provider", "market_data_enabled", "account_state_enabled", "capability_tools"}
        for key in payload:
            if key not in allowed:
                raise HTTPException(status_code=400, detail=f"Unsupported finance external-state key: {key}")
        capability_tools = payload.get("capability_tools")
        if capability_tools is not None and not isinstance(capability_tools, dict):
            raise HTTPException(status_code=400, detail="capability_tools must be an object")
        try:
            save_finance_settings(
                config.repo_root,
                enabled=bool(payload["enabled"]) if "enabled" in payload else None,
                provider=str(payload["provider"]) if "provider" in payload and payload.get("provider") is not None else None,
                market_data_enabled=bool(payload["market_data_enabled"]) if "market_data_enabled" in payload else None,
                account_state_enabled=bool(payload["account_state_enabled"]) if "account_state_enabled" in payload else None,
                capability_tools={str(key): str(value) for key, value in capability_tools.items()}
                if isinstance(capability_tools, dict)
                else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        config = load_config(config.repo_root)
        focus_manager.config = config
        return JSONResponse(external_state_payload(config).model_dump(mode="json"))

    @app.post("/external-state/model-native-search")
    async def update_model_native_search(payload: dict[str, Any]) -> JSONResponse:
        nonlocal config
        allowed = {"enabled", "provider", "base_url", "api_key_env", "max_results", "timeout_seconds"}
        for key in payload:
            if key not in allowed:
                raise HTTPException(status_code=400, detail=f"Unsupported model-native search key: {key}")
        try:
            save_model_native_search_settings(
                config.repo_root,
                enabled=bool(payload["enabled"]) if "enabled" in payload else None,
                provider=str(payload["provider"]) if "provider" in payload and payload.get("provider") is not None else None,
                base_url=str(payload["base_url"]) if "base_url" in payload and payload.get("base_url") is not None else None,
                api_key_env=str(payload["api_key_env"])
                if "api_key_env" in payload and payload.get("api_key_env") is not None
                else None,
                max_results=int(payload["max_results"]) if "max_results" in payload else None,
                timeout_seconds=float(payload["timeout_seconds"]) if "timeout_seconds" in payload else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        config = load_config(config.repo_root)
        focus_manager.config = config
        return JSONResponse(external_state_payload(config).model_dump(mode="json"))

    @app.get("/skills")
    async def list_skills() -> JSONResponse:
        from vaner.intent.skills_discovery import discover_skills

        refs = discover_skills(
            config.repo_root,
            include_global=config.intent.include_global_skills,
            skill_roots=config.intent.skill_roots,
        )
        skills = []
        for ref in refs:
            payload = ref.as_signal_payload(config.repo_root)
            skills.append(
                {
                    "name": ref.name,
                    "desc": ref.description,
                    "weight": 0.5,
                    "path": payload["path"],
                    "tags": ref.tags,
                    "kind": ref.vaner_kind,
                }
            )
        return JSONResponse({"skills": skills})

    @app.get("/pinned-facts")
    async def pinned_facts() -> JSONResponse:
        rows = await scenario_store.list_top(limit=100)
        facts = []
        for row in rows:
            if not row.pinned and row.memory_state != "trusted":
                continue
            text = row.prepared_context.strip()
            if not text and row.coverage_gaps:
                text = row.coverage_gaps[0]
            if not text:
                text = " · ".join(row.entities[:3]) or row.id
            facts.append({"id": row.id, "text": text[:240]})
        return JSONResponse({"facts": facts})

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
    async def list_items(kind: str | None = None, limit: int = 10, visibility: str = "live") -> JSONResponse:
        visibility_mode = visibility if visibility in {"live", "history", "all"} else "live"
        rows = await _best_effort(
            scenario_store.list_top(kind=kind, limit=max(1, min(limit, 100)), visibility=visibility_mode),
            [],
            label="scenario list",
        )
        return JSONResponse({"count": len(rows), "visibility": visibility_mode, "scenarios": [_scenario_payload(row) for row in rows]})

    async def _heatmap_replay_payload(
        *,
        from_ts: float | None = None,
        to_ts: float | None = None,
        range_seconds: float | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        end_ts = float(to_ts or time.time())
        default_range = float(range_seconds or 15 * 60)
        start_ts = float(from_ts if from_ts is not None else end_ts - default_range)
        if start_ts > end_ts:
            start_ts, end_ts = end_ts, start_ts
        capped_limit = max(1, min(int(limit), 200))
        rows = await _best_effort(
            scenario_store.list_top(limit=capped_limit, visibility="all"),
            [],
            label="heatmap scenarios",
            timeout=2.0,
        )
        scenario_ids = [str(row.id) for row in rows]
        samples = await _best_effort(
            scenario_store.list_samples(scenario_ids=scenario_ids, start_ts=start_ts, end_ts=end_ts, limit=50_000),
            [],
            label="heatmap samples",
            timeout=2.0,
        )
        live_events = [
            row
            for row in read_live_work_events(config.repo_root, limit=1000)
            if start_ts <= float(row.get("ts") or 0.0) <= end_ts
        ]
        return {
            "from_ts": start_ts,
            "to_ts": end_ts,
            "scenarios": [_scenario_payload(row) for row in rows],
            "samples": [sample.__dict__ for sample in samples],
            "events": live_events,
            "metadata": {
                "sample_source": "scenario_samples",
                "event_source": "live_work_events",
                "synthetic": False,
                "sample_count": len(samples),
                "event_count": len(live_events),
                "scenario_count": len(rows),
                "complete": bool(samples),
            },
        }

    @app.get("/heatmap/replay/stream")
    async def heatmap_replay_stream(
        from_ts: float | None = None,
        to_ts: float | None = None,
        range_seconds: float | None = None,
        limit: int = 100,
    ) -> StreamingResponse:
        async def event_gen() -> AsyncIterator[str]:
            last_fingerprint = ""
            last_keepalive = time.monotonic()
            sent = 0
            while True:
                payload = await _heatmap_replay_payload(
                    from_ts=from_ts if to_ts is not None else None,
                    to_ts=to_ts,
                    range_seconds=range_seconds,
                    limit=limit,
                )
                serialized = json.dumps(payload, sort_keys=True, default=str)
                if serialized != last_fingerprint:
                    yield f"event: replay_snapshot\ndata: {serialized}\n\n"
                    last_fingerprint = serialized
                    last_keepalive = time.monotonic()
                    sent += 1
                    if limit is not None and sent >= max(1, int(limit)):
                        return
                now = time.monotonic()
                if now - last_keepalive >= 10.0:
                    yield ": keepalive\n\n"
                    last_keepalive = now
                await asyncio.sleep(1.0)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @app.get("/heatmap/replay")
    async def heatmap_replay(from_ts: float | None = None, to_ts: float | None = None, limit: int = 100) -> JSONResponse:
        return JSONResponse(await _heatmap_replay_payload(from_ts=from_ts, to_ts=to_ts, limit=limit))

    @app.get("/scenarios/{scenario_id}")
    async def fetch_item(scenario_id: str) -> JSONResponse:
        row = await scenario_store.get(scenario_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        body = _scenario_payload(row)
        # Surface the most recent invalidation signal so the Inspector can
        # render a 'stale because' line. Only attached when the scenario
        # is not fresh — fresh rows do not need a justification.
        if body.get("freshness") and body["freshness"] != "fresh":
            try:
                from vaner.store.artefacts import ArtefactStore

                store = ArtefactStore(config.repo_root / ".vaner" / "artefacts.db")
                await store.initialize()
                from vaner.models.signal import SignalEvent

                events: list[SignalEvent] = await store.list_signal_events(limit=1)
                if events:
                    evt = events[0]
                    body["latest_invalidation_signal"] = {
                        "kind": evt.kind,
                        "source": evt.source,
                        "timestamp": evt.timestamp,
                    }
            except Exception:
                # Don't break the route on a store hiccup.
                pass
        return JSONResponse(body)

    def _scenario_payload(row: Any) -> dict[str, Any]:
        body = row.model_dump(mode="json")
        body["lifecycle_components"] = [
            {
                "label": "relevance",
                "value": float(body.get("relevance") or 0.0),
                "description": "Current usefulness for the live workspace context.",
            },
            {
                "label": "confidence",
                "value": float(body.get("confidence") or 0.0),
                "description": "Belief that this scenario is valid.",
            },
            {
                "label": "freshness",
                "value": {"fresh": 1.0, "recent": 0.62, "stale": 0.18}.get(str(body.get("freshness") or ""), 0.0),
                "description": "How recently new signals reinforced this scenario.",
            },
            {
                "label": "readiness",
                "value": {"ready": 1.0, "warming": 0.68, "cooling": 0.35, "unprepared": 0.2}.get(str(body.get("readiness") or ""), 0.0),
                "description": "Whether Vaner has enough evidence or prepared context to act.",
            },
        ]
        return body

    @app.post("/scenarios/{scenario_id}/expand")
    async def expand_item(scenario_id: str) -> JSONResponse:
        row = await scenario_store.get(scenario_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        await scenario_store.record_expansion(scenario_id)
        refreshed = await scenario_store.get(scenario_id)
        if refreshed is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        return JSONResponse({"ok": True, "scenario": _scenario_payload(refreshed)})

    @app.post("/scenarios/{scenario_id}/outcome")
    async def record_feedback(scenario_id: str, payload: dict[str, Any]) -> JSONResponse:
        row = await scenario_store.get(scenario_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        if "pinned" in payload:
            await scenario_store.set_pinned(scenario_id, bool(payload.get("pinned")))
            refreshed_pin = await scenario_store.get(scenario_id)
            return JSONResponse({"ok": True, "scenario": _scenario_payload(refreshed_pin) if refreshed_pin else None})
        result = str(payload.get("result", payload.get("outcome", ""))).strip()
        note = str(payload.get("note", "")).strip()
        if result not in {"useful", "partial", "irrelevant"}:
            raise HTTPException(status_code=400, detail="result must be one of useful|partial|irrelevant")
        await scenario_store.record_outcome(scenario_id, result)
        await metrics_store.record_scenario_outcome(scenario_id=scenario_id, result=result, note=note)
        refreshed = await scenario_store.get(scenario_id)
        return JSONResponse({"ok": True, "scenario": _scenario_payload(refreshed) if refreshed else None})

    @app.post("/scenarios/{scenario_id}/pin")
    async def pin_scenario(scenario_id: str, payload: dict[str, Any]) -> JSONResponse:
        row = await scenario_store.get(scenario_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Scenario not found")
        await scenario_store.set_pinned(scenario_id, bool(payload.get("pinned", True)))
        refreshed = await scenario_store.get(scenario_id)
        return JSONResponse({"ok": True, "scenario": _scenario_payload(refreshed) if refreshed else None})

    # ------------------------------------------------------------------
    # Phase 4 / Phase C: predictions surface
    # ------------------------------------------------------------------

    def _serialize_prediction(prompt: Any) -> dict[str, Any]:
        """Render a PredictedPrompt into a JSON-safe dict."""
        from vaner.intent.prediction_serialization import serialize_prediction_nested

        return serialize_prediction_nested(prompt)

    async def _work_product_store() -> Any:
        nonlocal work_product_store
        if work_product_store is not None:
            return work_product_store
        async with work_product_store_lock:
            if work_product_store is not None:
                return work_product_store
            if engine is not None and getattr(engine, "store", None) is not None:
                store = engine.store
            else:
                from vaner.store.artefacts import ArtefactStore

                # Runtime worker state lives in config.store_path. Older
                # cockpit-only tests and pre-0.9 installs may still seed the
                # legacy artefacts DB directly, so keep that as a fallback
                # only when the configured engine store has not been created.
                legacy_path = config.repo_root / ".vaner" / "artefacts.db"
                store_path = config.store_path if config.store_path.exists() else legacy_path
                store = ArtefactStore(store_path)
            await store.initialize()
            work_product_store = store
            return store

    def _live_prompt_card(row: dict[str, Any]) -> dict[str, Any]:
        query_text = str(row.get("query_text") or "").strip()
        source = str(row.get("source") or "query")
        host_app = str(row.get("host_app") or "client")
        timestamp = float(row.get("timestamp") or time.time())
        stable = "|".join(
            [
                host_app,
                str(row.get("session_id") or ""),
                str(row.get("turn_id") or ""),
                str(row.get("prompt_hash") or ""),
                query_text,
            ]
        )
        card_id = f"live-signal-{hashlib.sha256(stable.encode()).hexdigest()[:16]}"
        label_text = query_text[:80] if query_text else f"{host_app} prompt signal"
        label = f"Preparing: {label_text}"
        description = "Recent client prompt signal queued for prediction and preparation."
        return {
            "id": card_id,
            "spec": {
                "label": label,
                "description": description,
                "source": "history",
                "anchor": query_text or card_id,
                "confidence": 0.0,
                "hypothesis_type": "possible_branch",
                "specificity": "concrete" if query_text else "anchor",
                "created_at": timestamp,
                "structured": {
                    "action_type": "plan",
                    "object": query_text or host_app,
                    "answer_shape": "short_answer",
                    "confidence": 0.0,
                    "evidence_targets": [],
                    "readiness_mode": "metadata_only",
                    "readiness_reason": "queued",
                    "reason_codes": ["live_signal", source],
                    "semantic_hint": query_text,
                    "abstain_reason": "",
                    "contradicted_by": [],
                    "evidence_fresh_at": timestamp,
                },
            },
            "run": {
                "weight": 0.0,
                "token_budget": 0,
                "tokens_used": 0,
                "model_calls": 0,
                "scenarios_spawned": 0,
                "scenarios_complete": 0,
                "readiness": "queued",
                "updated_at": timestamp,
            },
            "artifacts": {
                "scenario_ids": [],
                "evidence_score": 0.0,
                "has_draft": False,
                "has_briefing": False,
                "thinking_trace_count": 0,
            },
            "readiness_label": "Queued",
            "eta_bucket": "maturing",
            "eta_bucket_label": "Queued for preparation",
            "adoptable": False,
            "suppression_reason": "preparing",
            "source_label": "Live prompt signal",
            "ui_summary": description,
            "trust_status": "preparing",
            "freshness": "fresh",
            "invalidated_by": [],
            "watched_sources": [],
            "changed_sources": [],
            "diagnostic_status": "not_checked",
            "host_app": host_app,
            "source_event_id": row.get("source_event_id"),
        }

    def _plan_draft_prediction_card(draft: Any) -> dict[str, Any]:
        updated_at = float(getattr(draft, "updated_at", None) or getattr(draft, "created_at", None) or time.time())
        tasks = list(getattr(draft, "tasks", None) or [])
        title = str(getattr(draft, "title", None) or "Draft plan").strip() or "Draft plan"
        summary = str(getattr(draft, "summary", None) or "").strip()
        description = summary or f"{len(tasks)} planned step{'s' if len(tasks) != 1 else ''} ready for shadow preparation."
        draft_id = str(getattr(draft, "id", None) or hashlib.sha256(title.encode()).hexdigest()[:16])
        return {
            "id": f"plan-draft-{draft_id}",
            "label": f"Preparing active plan: {title}",
            "display_label": f"Preparing active plan: {title}",
            "source_label": "Active draft plan",
            "readiness": "queued",
            "readiness_label": "Preparing",
            "adoptable": False,
            "match_state": "weak_match",
            "match_reason": "Vaner is preparing against the active draft plan in its local runtime area.",
            "recommended_action": "inspect",
            "snapshot_freshness": "warming",
            "confidence": 0.0,
            "token_budget": 0,
            "tokens_used": 0,
            "ui_summary": description,
            "spec": {
                "label": f"Preparing active plan: {title}",
                "description": description,
                "source": "plan_draft",
                "anchor": title,
                "confidence": 0.0,
                "hypothesis_type": "likely_next",
                "specificity": "concrete",
                "created_at": updated_at,
                "structured": {
                    "action_type": "plan",
                    "object": title,
                    "answer_shape": "implementation_plan",
                    "confidence": 0.0,
                    "evidence_targets": tasks,
                    "readiness_mode": "metadata_only",
                    "readiness_reason": "queued",
                    "reason_codes": ["active_plan_draft"],
                    "semantic_hint": description,
                    "abstain_reason": "",
                    "contradicted_by": [],
                    "evidence_fresh_at": updated_at,
                },
            },
            "run": {
                "weight": 0.0,
                "token_budget": 0,
                "tokens_used": 0,
                "model_calls": 0,
                "scenarios_spawned": 0,
                "scenarios_complete": 0,
                "readiness": "queued",
                "updated_at": updated_at,
            },
            "artifacts": {
                "scenario_ids": [],
                "evidence_score": 0.0,
                "has_draft": False,
                "has_briefing": False,
                "thinking_trace_count": 0,
            },
            "eta_bucket": "maturing",
            "eta_bucket_label": "Queued for preparation",
            "suppression_reason": "plan draft is preparing",
            "trust_status": "preparing",
            "freshness": "fresh",
            "invalidated_by": [],
            "watched_sources": [],
            "changed_sources": [],
            "diagnostic_status": "not_checked",
            "plan_draft_id": draft_id,
        }

    def _worker_progress_prediction_card(worker: dict[str, Any]) -> dict[str, Any]:
        updated_at = float(worker.get("last_heartbeat_at") or time.time())
        phase = str(worker.get("phase") or "prediction_precompute")
        return {
            "id": f"worker-progress-{phase}",
            "label": "Exploring likely next work",
            "display_label": "Exploring likely next work",
            "source_label": "Background worker",
            "readiness": "queued",
            "readiness_label": "Exploring",
            "adoptable": False,
            "match_state": "unrelated",
            "match_reason": "The precompute worker is running, but no prepared context has been published yet.",
            "recommended_action": "ignore",
            "snapshot_freshness": "warming",
            "confidence": 0.0,
            "token_budget": 0,
            "tokens_used": 0,
            "ui_summary": "Vaner is exploring likely next prompts for this workspace.",
            "spec": {
                "label": "Exploring likely next work",
                "description": "Vaner is exploring likely next prompts for this workspace.",
                "source": "worker_progress",
                "anchor": "workspace",
                "confidence": 0.0,
                "hypothesis_type": "possible_branch",
                "specificity": "anchor",
                "created_at": updated_at,
                "structured": {
                    "action_type": "plan",
                    "object": "workspace",
                    "answer_shape": "short_answer",
                    "confidence": 0.0,
                    "evidence_targets": [],
                    "readiness_mode": "metadata_only",
                    "readiness_reason": "worker_running",
                    "reason_codes": ["worker_progress", phase],
                    "semantic_hint": "Vaner is exploring likely next prompts for this workspace.",
                    "abstain_reason": "",
                    "contradicted_by": [],
                    "evidence_fresh_at": updated_at,
                },
            },
            "run": {
                "weight": 0.0,
                "token_budget": 0,
                "tokens_used": 0,
                "model_calls": 0,
                "scenarios_spawned": 0,
                "scenarios_complete": 0,
                "readiness": "queued",
                "updated_at": updated_at,
            },
            "artifacts": {
                "scenario_ids": [],
                "evidence_score": 0.0,
                "has_draft": False,
                "has_briefing": False,
                "thinking_trace_count": 0,
            },
            "eta_bucket": "maturing",
            "eta_bucket_label": "Worker running",
            "suppression_reason": "worker has not published prepared context yet",
            "trust_status": "preparing",
            "freshness": "fresh",
            "invalidated_by": [],
            "watched_sources": [],
            "changed_sources": [],
            "diagnostic_status": "not_checked",
        }

    async def _live_prompt_overlay(existing: list[dict[str, Any]], *, generated_at: float | None) -> list[dict[str, Any]]:
        store = await _work_product_store()
        rows = await store.list_query_history(limit=20)
        existing_anchors = {
            str(item.get("spec", {}).get("anchor") or "")
            for item in existing
            if isinstance(item, dict) and isinstance(item.get("spec"), dict)
        }
        cutoff = float(generated_at or 0.0)
        cards: list[dict[str, Any]] = []
        seen: set[str] = set()
        recent_floor = time.time() - 120.0
        for row in rows:
            query_text = str(row.get("query_text") or "").strip()
            if not query_text or query_text in existing_anchors:
                continue
            row_ts = float(row.get("timestamp") or 0.0)
            if cutoff and row_ts <= cutoff and row_ts < recent_floor:
                continue
            source = str(row.get("source") or "")
            if source not in {"codex_prompt", "query", "vaner.resolve"} and not row.get("host_app"):
                continue
            card = _live_prompt_card(row)
            if card["id"] in seen:
                continue
            seen.add(card["id"])
            cards.append(card)
            if len(cards) >= 5:
                break
        return cards

    async def _refresh_work_product_staleness(*, force: bool = False) -> int:
        nonlocal work_product_staleness_checked_at
        now = time.monotonic()
        if not force and now - work_product_staleness_checked_at < _WORK_PRODUCT_STALENESS_REFRESH_INTERVAL_SECONDS:
            return 0
        if not force and work_product_staleness_lock.locked():
            return 0
        async with work_product_staleness_lock:
            now = time.monotonic()
            if not force and now - work_product_staleness_checked_at < _WORK_PRODUCT_STALENESS_REFRESH_INTERVAL_SECONDS:
                return 0
            store = await _work_product_store()
            await store.expire_due_work_products()
            changed = await store.refresh_work_product_staleness(config.repo_root)
            work_product_staleness_checked_at = time.monotonic()
            return int(changed)

    def _prediction_snapshot_rows() -> list[dict[str, Any]]:
        snapshot = read_prediction_snapshot(config.repo_root)
        if not isinstance(snapshot, dict):
            return []
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(item: Any) -> None:
            if not isinstance(item, dict):
                return
            pid = str(item.get("id") or item.get("prediction_id") or "")
            if not pid or pid in seen:
                return
            seen.add(pid)
            rows.append(item)

        for item in snapshot.get("predictions") or []:
            add(item)
        by_state = snapshot.get("by_state")
        if isinstance(by_state, dict):
            for state in ("ready", "drafting", "evidence_gathering", "grounding", "queued"):
                for item in by_state.get(state) or []:
                    add(item)
        return rows

    def _prediction_row_by_id(prediction_id: str) -> dict[str, Any] | None:
        if engine is not None and getattr(engine, "prediction_registry", None) is not None:
            prompt = engine.prediction_registry.get(prediction_id)
            if prompt is not None:
                return _serialize_prediction(prompt)
        for row in _prediction_snapshot_rows():
            pid = str(row.get("id") or row.get("prediction_id") or "")
            spec = row.get("spec") if isinstance(row.get("spec"), dict) else {}
            if pid == prediction_id or str(spec.get("id") or "") == prediction_id:
                return compact_serialized_prediction(row)
        return None

    def _live_status_from_events(events: list[dict[str, Any]], fallback: str) -> tuple[str, str, bool, float | None]:
        latest = events[-1] if events else None
        if latest is None:
            return fallback, "No live activity has been recorded for this item yet.", False, None
        status = str(latest.get("status") or fallback)
        summary = str(latest.get("summary") or "Background work updated.")
        active = status in {"queued", "running"} or str(latest.get("stage") or "") in {"model", "progress", "prediction_precompute"}
        updated_at = float(latest.get("ts") or 0.0) or None
        return status, summary, active, updated_at

    def _pid_is_running(pid: Any) -> bool:
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            return False
        if pid_int <= 0:
            return False
        try:
            os.kill(pid_int, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    def _worker_status_is_fresh(worker: dict[str, Any]) -> bool:
        heartbeat = float(worker.get("last_heartbeat_at") or 0.0)
        if heartbeat <= 0:
            return False
        if time.time() - heartbeat > 90.0:
            return False
        return _pid_is_running(worker.get("pid"))

    def _resource_utilization() -> dict[str, Any]:
        try:
            cpu_count = os.cpu_count() or 1
            cpu_load = max(0.0, min(1.0, os.getloadavg()[0] / cpu_count))
        except Exception:
            cpu_load = 0.0
        gpu: dict[str, Any] = {"available": False}
        try:
            output = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1.0,
            ).strip()
            if output:
                first = [part.strip() for part in output.splitlines()[0].split(",")]
                if len(first) >= 5:
                    gpu = {
                        "available": True,
                        "name": first[0],
                        "utilization": float(first[1]) / 100.0,
                        "memory_utilization": float(first[2]) / 100.0,
                        "memory_used_mb": int(float(first[3])),
                        "memory_total_mb": int(float(first[4])),
                    }
        except Exception:
            gpu = {"available": False}
        return {"cpu_load": cpu_load, "gpu": gpu}

    async def _prepared_work_payload(
        *,
        limit: int,
        include_advisory: bool,
        include_diagnostics: bool,
        context_id: str | None,
        surface: str,
    ) -> list[dict[str, Any]]:
        from vaner.intent.prepared_work import build_plan_draft_cards, build_prediction_payload_cards, build_prepared_work_cards

        store = await _work_product_store()
        await _refresh_work_product_staleness()
        products = await store.list_work_products(
            include_hidden=True,
            include_terminal=True,
            limit=200,
        )
        predictions = []
        snapshot_predictions: list[dict[str, Any]] = []
        if engine is not None and getattr(engine, "prediction_registry", None) is not None:
            predictions = list(engine.get_active_predictions())
        else:
            snapshot_predictions = _prediction_snapshot_rows()
        cards = build_prepared_work_cards(
            work_products=products,
            predictions=predictions,
            include_advisory=include_advisory,
            include_diagnostics=include_diagnostics,
            context_id=context_id,
            surface=surface,
            limit=max(1, min(100, int(limit))),
        )
        if snapshot_predictions:
            cards.extend(
                build_prediction_payload_cards(
                    predictions=snapshot_predictions,
                    include_diagnostics=include_diagnostics,
                    context_id=context_id,
                    limit=max(1, min(100, int(limit))),
                )
            )
        cards = [
            *build_plan_draft_cards(drafts=list_plan_drafts(config.repo_root, limit=6), limit=6),
            *cards,
        ]
        deduped: list[Any] = []
        seen: set[str] = set()
        for card in cards:
            if card.id in seen:
                continue
            seen.add(card.id)
            deduped.append(card)
            if len(deduped) >= max(1, min(100, int(limit))):
                break
        return [card.model_dump(mode="json") for card in deduped]

    async def _active_work_snapshot() -> dict[str, Any]:
        worker_status = read_worker_status(config.repo_root) or {}
        worker = worker_status.get("worker") if isinstance(worker_status.get("worker"), dict) else {}
        queue = worker_status.get("queue") if isinstance(worker_status.get("queue"), dict) else {}
        worker_fresh = _worker_status_is_fresh(worker) if worker else False
        if worker and not worker_fresh:
            stale_age = max(0.0, time.time() - float(worker.get("last_heartbeat_at") or 0.0))
            worker = {
                **worker,
                "state": "stale",
                "phase": "idle",
                "stale": True,
                "stale_age_seconds": stale_age,
                "explanation": "precompute worker status is stale; no live worker heartbeat is available",
            }
            queue = {}
        jobs_payload = focus_manager.jobs_state()
        if worker_status and worker_fresh:
            worker_jobs = worker_status.get("jobs") if isinstance(worker_status.get("jobs"), list) else []
            jobs_payload["jobs"] = list(worker_jobs) + list(jobs_payload.get("jobs") or [])
            jobs_payload["worker"] = worker
            jobs_payload["queue"] = queue
            jobs_payload["profile"] = worker_status.get("profile")
        resources = focus_manager.resources_state().model_dump(mode="json")
        predictions = _prediction_snapshot_rows()
        latest_plan = latest_plan_draft(config.repo_root)
        phase = str(worker.get("phase") or "idle")
        loop = "ponder" if phase in {"prediction_precompute", "source_refresh"} else "answer" if phase == "answer" else "idle"
        prepared_cards = await _prepared_work_payload(
            limit=8,
            include_advisory=False,
            include_diagnostics=False,
            context_id=None,
            surface="active_work",
        )
        compact_predictions = [compact_serialized_prediction(row) for row in predictions[:12]]
        if latest_plan is not None:
            summary = f"Vaner is preparing against draft plan: {latest_plan.title}"
        elif phase == "prediction_precompute":
            summary = "Vaner is exploring likely next prompts for this workspace."
        elif prepared_cards:
            summary = f"Vaner has {len(prepared_cards)} prepared item{'s' if len(prepared_cards) != 1 else ''} ready."
        else:
            summary = "Vaner is listening for workspace intent."
        return {
            "summary": summary,
            "loop": loop,
            "phase": phase,
            "worker": worker,
            "queue": queue,
            "jobs": jobs_payload.get("jobs") or [],
            "resources": resources,
            "utilization": _resource_utilization(),
            "plan_draft": plan_draft_to_public(latest_plan) if latest_plan is not None else None,
            "prediction_count": len(predictions),
            "predictions": compact_predictions,
            "prepared_work_count": len(prepared_cards),
            "prepared_work": prepared_cards,
            "updated_at": time.time(),
        }

    @app.get("/work/live")
    async def work_live(entity_type: str, entity_id: str, limit: int = 80) -> JSONResponse:
        if entity_type not in {"prediction", "scenario", "work_product", "worker"}:
            return JSONResponse({"code": "invalid_input", "message": "unsupported entity_type"}, status_code=400)
        normalized_id = entity_id.removeprefix("prediction:") if entity_type == "prediction" else entity_id
        events = read_live_work_events(
            config.repo_root,
            entity_type=entity_type,
            entity_id=normalized_id,
            limit=max(1, min(200, int(limit))),
        )
        prediction = _prediction_row_by_id(normalized_id) if entity_type == "prediction" else None
        fallback_status = str((prediction or {}).get("readiness") or ((prediction or {}).get("run") or {}).get("readiness") or "queued")
        status_value, summary, active, updated_at = _live_status_from_events(events, fallback_status)
        worker_status = read_worker_status(config.repo_root) or {}
        return JSONResponse(
            {
                "entity_type": entity_type,
                "entity_id": normalized_id,
                "status": status_value,
                "summary": summary,
                "active": active,
                "updated_at": updated_at,
                "events": events,
                "prediction": prediction,
                "worker": worker_status.get("worker") if isinstance(worker_status.get("worker"), dict) else None,
                "queue": worker_status.get("queue") if isinstance(worker_status.get("queue"), dict) else None,
            }
        )

    @app.get("/work/live/stream")
    async def work_live_stream(entity_type: str, entity_id: str, limit: int | None = None) -> StreamingResponse:
        if entity_type not in {"prediction", "scenario", "work_product", "worker"}:
            raise HTTPException(status_code=400, detail="unsupported entity_type")
        normalized_id = entity_id.removeprefix("prediction:") if entity_type == "prediction" else entity_id

        async def event_gen() -> AsyncIterator[str]:
            seen: set[str] = set()
            sent = 0
            last_keepalive = time.monotonic()
            while True:
                rows = read_live_work_events(config.repo_root, entity_type=entity_type, entity_id=normalized_id, limit=100)
                for row in rows:
                    event_id = str(row.get("event_id") or "")
                    if not event_id or event_id in seen:
                        continue
                    seen.add(event_id)
                    yield f"data: {json.dumps(row, sort_keys=True)}\n\n"
                    sent += 1
                    if limit is not None and sent >= max(1, int(limit)):
                        return
                now = time.monotonic()
                if now - last_keepalive >= 10.0:
                    yield ": keepalive\n\n"
                    last_keepalive = now
                await asyncio.sleep(1.0)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @app.get("/predictions/active")
    async def predictions_active(include_all: bool = False) -> JSONResponse:
        """Return ready predictions plus, optionally, all in-flight states.

        ``predictions`` keeps the existing shape for back-compat (only the
        ready tail). When ``include_all=true`` is passed, ``by_state``
        groups every prompt in the registry by its ``readiness`` so the
        cockpit can render the full pipeline (queued / grounding /
        evidence_gathering / drafting / ready).
        """
        if engine is None:
            snapshot = read_prediction_snapshot(config.repo_root)
            body: dict[str, Any] = {"predictions": []}
            if snapshot is not None:
                body["predictions"] = [
                    compact_serialized_prediction(row) for row in list(snapshot.get("predictions") or []) if isinstance(row, dict)
                ]
                body["cycle_id"] = snapshot.get("cycle_id")
                body["generated_at"] = snapshot.get("generated_at")
                body["source"] = snapshot.get("source") or "precompute_worker"
            if include_all:
                by_state = dict(snapshot.get("by_state") or {}) if snapshot is not None else {}
                by_state = {
                    str(state): [compact_serialized_prediction(row) for row in list(items or []) if isinstance(row, dict)]
                    for state, items in by_state.items()
                }
                existing = [item for items in by_state.values() if isinstance(items, list) for item in items if isinstance(item, dict)]
                live_cards = await _live_prompt_overlay(existing, generated_at=float(body.get("generated_at") or 0.0))
                if live_cards:
                    by_state["queued"] = [*live_cards, *list(by_state.get("queued") or [])]
                latest_plan = latest_plan_draft(config.repo_root)
                if latest_plan is not None:
                    plan_card = compact_serialized_prediction(_plan_draft_prediction_card(latest_plan))
                    known_ids = {
                        str(item.get("id") or "")
                        for items in by_state.values()
                        if isinstance(items, list)
                        for item in items
                        if isinstance(item, dict)
                    }
                    if plan_card["id"] not in known_ids:
                        by_state["queued"] = [plan_card, *list(by_state.get("queued") or [])]
                if not any(bool(items) for items in by_state.values()):
                    worker_status = read_worker_status(config.repo_root) or {}
                    worker = worker_status.get("worker") if isinstance(worker_status.get("worker"), dict) else {}
                    if worker and _worker_status_is_fresh(worker) and str(worker.get("state") or "") in {"running", "queued"}:
                        by_state["queued"] = [compact_serialized_prediction(_worker_progress_prediction_card(worker))]
                body["by_state"] = by_state
            return JSONResponse(body)
        active = engine.get_active_predictions()
        body = {
            "predictions": [_serialize_prediction(p) for p in active],
        }
        if include_all and getattr(engine, "prediction_registry", None) is not None:
            by_state: dict[str, list[dict[str, Any]]] = {}
            for prompt in engine.prediction_registry.all():
                state = str(getattr(prompt.run, "readiness", "") or "unknown")
                by_state.setdefault(state, []).append(_serialize_prediction(prompt))
            body["by_state"] = by_state
        return JSONResponse(body)

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

    @app.get("/work-products")
    async def work_products_list(
        include_hidden: bool = False,
        include_terminal: bool = False,
        type: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        store = await _work_product_store()
        await _refresh_work_product_staleness()
        product_type: WorkProductType | str | None = None
        if type:
            try:
                product_type = WorkProductType(type)
            except ValueError:
                return JSONResponse({"code": "invalid_type", "message": f"unknown work product type: {type}"}, status_code=400)
        products = await store.list_work_products(
            include_hidden=include_hidden,
            include_terminal=include_terminal,
            type=product_type,
            limit=max(1, min(200, int(limit))),
        )
        return JSONResponse({"work_products": [product.model_dump(mode="json") for product in products]})

    @app.get("/work-products/{product_id}")
    async def work_products_one(product_id: str) -> JSONResponse:
        store = await _work_product_store()
        await _refresh_work_product_staleness()
        product = await store.get_work_product(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="work product not found")
        return JSONResponse(product.model_dump(mode="json"))

    @app.get("/work-products/{product_id}/inspect")
    async def work_products_inspect(product_id: str) -> JSONResponse:
        from vaner.intent.prepared_work import build_work_product_inspection

        store = await _work_product_store()
        await _refresh_work_product_staleness()
        product = await store.get_work_product(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="work product not found")
        inspection = build_work_product_inspection(product)
        await store.record_work_product_event(product_id, "inspect", metadata={"surface": "http"})
        body = inspection.model_dump(mode="json")
        # Cockpit refresh: surface the per-product self-eval scores and the
        # lifecycle event log so the inspector can render confidence bars
        # plus a 'CANDIDATE -> SURFACED -> ...' strip without round-tripping.
        body["self_eval"] = product.self_eval.model_dump(mode="json")
        try:
            event_log = await store.list_work_product_events(product_id, limit=20)
        except Exception:
            event_log = []
        body["events"] = [
            {
                "event_type": str(evt.get("event_type") or ""),
                "timestamp": float(evt.get("timestamp") or 0.0),
            }
            for evt in event_log
        ]
        body["status"] = product.status.value
        body["adoptability"] = product.adoptability.value
        body["feedback_state"] = product.feedback_state.value
        return JSONResponse(body)

    @app.get("/prepared-work")
    async def prepared_work(
        limit: int = 3,
        include_advisory: bool = False,
        include_diagnostics: bool = False,
        context_id: str | None = None,
        surface: str = "api",
    ) -> JSONResponse:
        cards = await _prepared_work_payload(
            limit=max(1, min(100, int(limit))),
            include_advisory=include_advisory,
            include_diagnostics=include_diagnostics,
            context_id=context_id,
            surface=surface,
        )
        return JSONResponse({"prepared_work": cards})

    @app.post("/work-products/{product_id}/dismiss")
    async def work_products_dismiss(product_id: str) -> JSONResponse:
        store = await _work_product_store()
        ok = await store.dismiss_work_product(product_id)
        if not ok:
            raise HTTPException(status_code=404, detail="work product not found")
        await store.record_work_product_event(product_id, "dismiss", metadata={"surface": "http"})
        return JSONResponse({"ok": True})

    @app.post("/work-products/{product_id}/feedback")
    async def work_products_feedback(product_id: str, payload: dict[str, Any]) -> JSONResponse:
        raw = str(payload.get("feedback_state", payload.get("feedback", ""))).strip()
        if raw == "not-useful":
            raw = "not_useful"
        try:
            feedback = WorkProductFeedbackState(raw)
        except ValueError:
            return JSONResponse(
                {"code": "invalid_feedback", "message": "feedback_state must be one of none|useful|partial|irrelevant|not_useful"},
                status_code=400,
            )
        store = await _work_product_store()
        ok = await store.feedback_work_product(product_id, feedback)
        if not ok:
            raise HTTPException(status_code=404, detail="work product not found")
        await store.record_work_product_event(product_id, "feedback", metadata={"surface": "http", "feedback_state": feedback.value})
        return JSONResponse({"ok": True})

    @app.post("/work-products/{product_id}/export")
    async def work_products_export(product_id: str) -> JSONResponse:
        store = await _work_product_store()
        await _refresh_work_product_staleness(force=True)
        product = await store.get_work_product(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="work product not found")
        if product.freshness.value == "stale":
            return JSONResponse(
                {"code": "stale_work_product", "message": "work product is stale; regenerate it before export"},
                status_code=409,
            )
        try:
            exported = await store.export_work_product(product_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="work product not found") from None
        except PermissionError:
            return JSONResponse(
                {"code": "not_exportable", "message": "work product is not exportable"},
                status_code=409,
            )
        await store.record_work_product_event(product_id, "export", metadata={"surface": "http"})
        return JSONResponse(exported.model_dump(mode="json"))

    # ------------------------------------------------------------------
    # Cockpit refresh: goals, artefacts, learning
    # Read-only HTTP wrappers around the same store accessors used by
    # the matching MCP tools (vaner.goals.*, vaner.artefacts.*).
    # ------------------------------------------------------------------

    async def _artefact_store() -> Any:
        nonlocal artefact_store
        from vaner.store.artefacts import ArtefactStore

        if artefact_store is not None:
            return artefact_store
        async with artefact_store_lock:
            if artefact_store is not None:
                return artefact_store
            store = ArtefactStore(config.repo_root / ".vaner" / "artefacts.db")
            await store.initialize()
            artefact_store = store
            return store

    async def _artefact_counts_by_connector() -> dict[str, int]:
        store = await _artefact_store()
        rows = await store.list_intent_artefacts(limit=500)
        counts: dict[str, int] = {}
        for row in rows:
            connector_name = str(row.get("connector") or "unknown")
            counts[connector_name] = counts.get(connector_name, 0) + 1
        return counts

    @app.get("/goals")
    async def list_goals(status: str | None = None, limit: int = 50) -> JSONResponse:
        store = await _artefact_store()
        rows = await store.list_workspace_goals(
            status=status if isinstance(status, str) and status else None,
            limit=max(1, min(200, int(limit))),
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            entry = dict(row)
            try:
                entry["evidence"] = json.loads(str(row.get("evidence_json") or "[]"))
            except Exception:
                entry["evidence"] = []
            try:
                entry["related_files"] = json.loads(str(row.get("related_files_json") or "[]"))
            except Exception:
                entry["related_files"] = []
            try:
                entry["artefact_refs"] = json.loads(str(row.get("artefact_refs_json") or "[]"))
            except Exception:
                entry["artefact_refs"] = []
            entry.pop("evidence_json", None)
            entry.pop("related_files_json", None)
            entry.pop("artefact_refs_json", None)
            out.append(entry)
        return JSONResponse({"goals": out})

    @app.get("/artefacts")
    async def list_artefacts(
        status: str | None = None,
        connector: str | None = None,
        source_tier: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        store = await _artefact_store()
        try:
            from vaner.intent.source_refresh import refresh_intent_artefacts_from_sources

            await _best_effort(
                refresh_intent_artefacts_from_sources(config, store),
                0,
                label="intent artefact source refresh",
                timeout=1.0,
            )
        except Exception:
            logger.exception("Intent artefact source refresh failed")
        rows = await _best_effort(
            store.list_intent_artefacts(
                status=status if isinstance(status, str) and status else None,
                connector=connector if isinstance(connector, str) and connector else None,
                source_tier=source_tier if isinstance(source_tier, str) and source_tier else None,
                limit=max(1, min(200, int(limit))),
            ),
            [],
            label="intent artefact list",
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            entry = dict(row)
            try:
                entry["linked_goals"] = json.loads(str(row.get("linked_goals_json") or "[]"))
            except Exception:
                entry["linked_goals"] = []
            try:
                entry["linked_files"] = json.loads(str(row.get("linked_files_json") or "[]"))
            except Exception:
                entry["linked_files"] = []
            entry.pop("linked_goals_json", None)
            entry.pop("linked_files_json", None)
            out.append(entry)
        return JSONResponse({"artefacts": out})

    @app.get("/sources/permissions")
    async def sources_permissions() -> JSONResponse:
        from vaner.sources_permissions import build_sources_permissions

        counts = await _best_effort(
            _artefact_counts_by_connector(),
            {},
            label="sources permission artefact counts",
            timeout=1.0,
        )
        payload = await asyncio.to_thread(build_sources_permissions, config, artefact_counts=counts)
        return JSONResponse(payload)

    @app.post("/sources/permissions")
    async def sources_permissions_update(payload: dict[str, Any]) -> JSONResponse:
        from vaner.sources_permissions import apply_sources_permissions, build_sources_permissions

        try:
            apply_sources_permissions(config.repo_root, payload)
        except OSError:
            return JSONResponse({"code": "config_write_failed", "message": "failed to write source permissions"}, status_code=400)
        refreshed = load_config(config.repo_root)
        config.sources = refreshed.sources
        counts = await _best_effort(_artefact_counts_by_connector(), {}, label="sources permission artefact counts")
        payload = await asyncio.to_thread(build_sources_permissions, config, artefact_counts=counts)
        return JSONResponse({"ok": True, **payload})

    @app.post("/sources/refresh")
    async def sources_refresh() -> JSONResponse:
        from vaner.intent.source_refresh import refresh_intent_artefacts_from_sources

        store = await _artefact_store()
        accepted = await _best_effort(
            refresh_intent_artefacts_from_sources(config, store),
            0,
            label="intent source refresh",
            timeout=10.0,
        )
        return JSONResponse({"ok": True, "accepted": int(accepted or 0)})

    @app.get("/sources/status")
    async def sources_status() -> JSONResponse:
        from vaner.sources_permissions import build_sources_permissions

        counts = await _best_effort(_artefact_counts_by_connector(), {}, label="sources status counts")
        permissions = await asyncio.to_thread(build_sources_permissions, config, artefact_counts=counts)
        return JSONResponse(
            {
                "sources": permissions["sources"],
                "privacy_boundary": permissions["privacy_boundary"],
                "ingest_counts": {"by_connector": counts, "total": sum(int(value) for value in counts.values())},
            }
        )

    @app.get("/artefacts/{artefact_id}")
    async def fetch_artefact(artefact_id: str) -> JSONResponse:
        store = await _artefact_store()
        row = await store.get_intent_artefact(artefact_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"no such artefact: {artefact_id}")
        latest_snapshot_id = str(row.get("latest_snapshot") or "")
        item_rows = (
            await store.list_intent_artefact_items(
                artefact_id=artefact_id,
                snapshot_id=latest_snapshot_id or None,
            )
            if latest_snapshot_id
            else []
        )
        items: list[dict[str, Any]] = []
        for item_row in item_rows:
            entry = dict(item_row)
            for json_field, friendly in (
                ("related_files_json", "related_files"),
                ("related_entities_json", "related_entities"),
                ("evidence_refs_json", "evidence_refs"),
            ):
                try:
                    entry[friendly] = json.loads(str(item_row.get(json_field) or "[]"))
                except Exception:
                    entry[friendly] = []
                entry.pop(json_field, None)
            items.append(entry)
        artefact_payload = dict(row)
        try:
            artefact_payload["linked_goals"] = json.loads(str(row.get("linked_goals_json") or "[]"))
        except Exception:
            artefact_payload["linked_goals"] = []
        try:
            artefact_payload["linked_files"] = json.loads(str(row.get("linked_files_json") or "[]"))
        except Exception:
            artefact_payload["linked_files"] = []
        artefact_payload.pop("linked_goals_json", None)
        artefact_payload.pop("linked_files_json", None)
        outcomes = await store.list_reconciliation_outcomes(artefact_id=artefact_id, limit=5)
        return JSONResponse(
            {
                "artefact": artefact_payload,
                "items": items,
                "snapshot_id": latest_snapshot_id,
                "recent_outcomes": outcomes,
            }
        )

    @app.get("/learning/recent")
    async def learning_recent(limit: int = 5) -> JSONResponse:
        store = await _artefact_store()
        bounded = max(1, min(50, int(limit)))
        events = await store.list_feedback_events(limit=bounded)
        # Surface a small set of learning_state keys that downstream
        # cockpit code can render as one-line summaries. The keys here
        # are best-effort — missing keys simply don't appear.
        learning_keys = (
            "scenario_kind_priors",
            "skill_weights",
            "intent_priors",
            "frontier_priors",
        )
        learning: dict[str, Any] = {}
        for key in learning_keys:
            try:
                value = await store.get_learning_state(key)
            except Exception:
                value = None
            if value is not None:
                learning[key] = value
        return JSONResponse({"feedback_events": events, "learning_state": learning})

    @app.get("/integrations/guidance")
    async def integrations_guidance(variant: str = "canonical", format: str = "body") -> JSONResponse:
        """Serve the canonical Vaner guidance asset.

        Query params:
          variant — canonical | weak | strong (default canonical).
          format — body | markdown | json (default body).

        Clients (MCP hosts, proxy integrations, agent-primer installers) fetch
        this endpoint at startup to embed Vaner guidance in the agent's prompt.
        """
        from vaner.integrations.guidance import available_variants, load_guidance

        if variant not in available_variants():
            return JSONResponse(
                {"code": "invalid_variant", "message": f"unknown variant {variant!r}"},
                status_code=400,
            )
        doc = load_guidance(variant)
        if format == "body":
            return JSONResponse({"body": doc.as_text(), "variant": variant, "version": doc.version})
        if format == "markdown":
            return JSONResponse({"markdown": doc.as_markdown(), "variant": variant, "version": doc.version})
        if format == "json":
            return JSONResponse(doc.as_dict())
        return JSONResponse(
            {"code": "invalid_format", "message": f"unknown format {format!r}"},
            status_code=400,
        )

    @app.post("/integrations/handoff/check")
    async def integrations_handoff_check(request: Request) -> JSONResponse:
        """Probe the platform-canonical adopt-handoff path without consuming it.

        0.8.5 WS13: read-only HTTP companion to MCP's `vaner.resolve`
        handoff short-circuit. Lets non-MCP clients (the web cockpit, a
        custom proxy) check whether a fresh adopted package is waiting
        on disk before deciding whether to spend a fresh resolve. Unlike
        the MCP path this does NOT delete the file on read — callers
        that want one-shot semantics should hit the corresponding MCP
        tool, or delete the file themselves after consuming.

        Optional body: `{"ttl_seconds": int}` to override the default
        10-min freshness window. Returns `{adopted_package, fresh,
        age_seconds, path}` where `adopted_package` is the raw payload
        the desktop client stashed (or `null` when missing/stale).
        """
        from vaner.integrations.injection.handoff import (
            DEFAULT_TTL_SECONDS,
            handoff_path,
            read_handoff,
        )

        ttl_seconds: int = DEFAULT_TTL_SECONDS
        try:
            body = await request.json()
            if isinstance(body, dict) and "ttl_seconds" in body:
                raw = body["ttl_seconds"]
                if isinstance(raw, (int, float)) and raw >= 0:
                    ttl_seconds = int(raw)
        except Exception:
            # Empty body or invalid JSON is fine — caller just wants the default TTL.
            pass

        result = read_handoff(ttl_seconds=ttl_seconds)
        if result is None:
            return JSONResponse(
                {
                    "adopted_package": None,
                    "fresh": False,
                    "age_seconds": None,
                    "path": str(handoff_path()),
                }
            )
        return JSONResponse(
            {
                "adopted_package": result.raw,
                "fresh": True,
                "age_seconds": result.age_seconds,
                "path": str(result.path),
            }
        )

    @app.post("/predictions/{prediction_id}/adopt")
    async def predictions_adopt(prediction_id: str) -> JSONResponse:
        pid = prediction_id.strip()
        if not pid:
            return JSONResponse(
                {"code": "invalid_input", "message": "prediction_id is required"},
                status_code=400,
            )
        if engine is None or engine.prediction_registry is None:
            snapshot = read_prediction_snapshot(config.repo_root)
            for row in list((snapshot or {}).get("predictions") or []):
                if not isinstance(row, dict):
                    continue
                row_id = str(row.get("id") or (row.get("spec") if isinstance(row.get("spec"), dict) else {}).get("id") or "")
                if row_id != pid:
                    continue
                resolution = _snapshot_prediction_resolution(row)
                if resolution is None:
                    break
                return JSONResponse(resolution.model_dump(mode="json"))
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
        try:
            async with engine.prediction_registry.lock:
                engine.prediction_registry.record_adoption(pid)
        except Exception:
            logger.debug("Failed to record best-effort prediction adoption for %s", pid, exc_info=True)
        return JSONResponse(resolution.model_dump(mode="json"))

    @app.post("/signals/codex/prompt")
    async def signals_codex_prompt(request: Request) -> JSONResponse:
        """Ingest a locally-redacted Codex UserPromptSubmit observation."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"code": "invalid_input", "message": "invalid JSON body"},
                status_code=400,
            )

        import uuid as _uuid

        from pydantic import ValidationError

        from vaner.signals.codex import CodexPromptSignal

        try:
            signal = CodexPromptSignal.model_validate(body)
        except ValidationError as exc:
            return JSONResponse(
                {"code": "invalid_input", "message": _sanitize_validation_errors(exc)},
                status_code=400,
            )

        timestamp_epoch: float | None = None
        if signal.timestamp:
            try:
                timestamp_epoch = datetime.fromisoformat(signal.timestamp.replace("Z", "+00:00")).timestamp()
            except ValueError:
                timestamp_epoch = None
        source_event_id = signal.source_event_id or _uuid.uuid4().hex
        query_text = signal.prompt_text_redacted or f"codex prompt {signal.prompt_hash[:12]}"
        try:
            if engine is not None:
                query_id = await engine.record_intent_observation(
                    query_text=query_text,
                    session_id=signal.session_id,
                    selected_paths=[],
                    hit_precomputed=False,
                    token_used=0,
                    source="codex_prompt",
                    host_app=signal.host_app,
                    source_event_id=source_event_id,
                    prompt_hash=signal.prompt_hash,
                    turn_id=signal.turn_id,
                    capture_policy=signal.capture_policy,
                    timestamp=timestamp_epoch,
                )
            else:
                store = await _work_product_store()
                query_id = await store.insert_query_history(
                    session_id=signal.session_id,
                    query_text=query_text,
                    selected_paths=[],
                    hit_precomputed=False,
                    token_used=0,
                    corpus_id="repo",
                    timestamp=timestamp_epoch,
                    source="codex_prompt",
                    host_app=signal.host_app,
                    source_event_id=source_event_id,
                    prompt_hash=signal.prompt_hash,
                    turn_id=signal.turn_id,
                    capture_policy=signal.capture_policy,
                )
        except Exception as exc:  # pragma: no cover - defensive endpoint guard
            logger.warning("Codex prompt signal ingest failed: %s", exc)
            return JSONResponse(
                {"code": "engine_error", "message": "failed to record codex prompt signal"},
                status_code=500,
            )
        plan_drafts = [
            record_plan_draft(
                config.repo_root,
                text=block,
                source_client=signal.host_app,
                session_id=signal.session_id,
                turn_id=signal.turn_id or "",
                workspace_id=signal.workspace_id or "",
                source_event_id=source_event_id,
                now=timestamp_epoch,
            )
            for block in extract_proposed_plan_blocks(signal.prompt_text_redacted or "")
        ]
        wake = request_precompute_wake(config.repo_root, reason="intent_signal")
        return JSONResponse(
            {
                "ok": True,
                "query_id": query_id,
                "source_event_id": source_event_id,
                "wake_id": wake["id"],
                "plan_drafts": [plan_draft_to_public(draft) for draft in plan_drafts],
            }
        )

    @app.post("/signals/codex/activity")
    async def signals_codex_activity(request: Request) -> JSONResponse:
        """Best-effort Codex lifecycle/tool activity signal.

        This is L0 activity only. It does not imply pre-enter composer access.
        """
        import uuid as _uuid

        from vaner.models.signal import SignalEvent

        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        event_id = str(body.get("source_event_id") or _uuid.uuid4().hex)
        payload = {
            "event": str(body.get("event") or body.get("hook_event_name") or "activity")[:80],
            "tool": str(body.get("tool") or body.get("tool_name") or body.get("name") or "")[:160],
            "session_id": str(body.get("session_id") or body.get("sessionId") or "")[:256],
            "workspace_id": str(body.get("workspace_id") or body.get("cwd") or "")[:2048],
            "capability_level": "L0",
        }
        plan_drafts = [
            record_plan_draft(
                config.repo_root,
                text=block,
                source_client="codex-cli",
                session_id=str(payload.get("session_id") or ""),
                workspace_id=str(payload.get("workspace_id") or ""),
                source_event_id=event_id,
            )
            for block in extract_proposed_plan_blocks(body)
        ]
        store = await _work_product_store()
        await store.insert_signal_event(
            SignalEvent(
                id=event_id,
                source="codex",
                kind="activity",
                timestamp=time.time(),
                payload=payload,
                corpus_id="repo",
            )
        )
        if plan_drafts:
            request_precompute_wake(config.repo_root, reason="plan_draft")
        return JSONResponse(
            {"ok": True, "source_event_id": event_id, "plan_drafts": [plan_draft_to_public(draft) for draft in plan_drafts]}
        )

    @app.get("/signals/capabilities")
    async def signals_capabilities() -> JSONResponse:
        return JSONResponse(
            {
                "composer_adapters": [
                    {
                        "host_app": "codex-cli",
                        "level": "L0",
                        "emits": ["submitted"],
                        "official_pre_enter_available": False,
                        "reason": "Codex exposes submitted prompt/tool lifecycle hooks here; no official draft-change hook is configured.",
                    }
                ],
                "privacy_boundary": {
                    "raw_live_typing": "not captured",
                    "draft_text": "never accepted on the composer snapshot boundary",
                },
            }
        )

    @app.post("/signals/composer")
    async def signals_composer(request: Request) -> JSONResponse:
        """0.8.7 WS7 — ingest a composer-lifecycle event.

        Accepts a ``DraftIntentSnapshot`` payload from a registered
        :class:`ComposerAdapter` (e.g. the Claude Code UserPromptSubmit
        hook), envelopes it into a :class:`SignalEvent` of kind
        ``composer_lifecycle``, and forwards to ``engine.observe()``.
        Returns the assigned ``composer_event_id`` so adapters can
        thread it back to the host UI for adoption attribution.

        Returns 400 on malformed payload (pydantic ValidationError),
        409 when no engine is available (the daemon is running without
        an injected engine, e.g. cockpit-only mode).
        """
        if engine is None:
            return JSONResponse(
                {"code": "engine_unavailable", "message": "engine unavailable"},
                status_code=409,
            )
        try:
            body = await request.json()
        except Exception:
            # 0.8.7 hardening (CodeQL py/stack-trace-exposure): the
            # JSON-parse exception text can carry payload bytes and
            # internal parser state. Surface only a static "invalid
            # JSON" message — adapter authors don't need the raw error.
            return JSONResponse(
                {"code": "invalid_input", "message": "invalid JSON body"},
                status_code=400,
            )

        # Lazy imports keep the daemon module load light.
        import time as _time
        import uuid as _uuid

        from pydantic import ValidationError

        from vaner.models.signal import KIND_COMPOSER_LIFECYCLE, SignalEvent
        from vaner.signals.composer import DraftIntentSnapshot

        try:
            snapshot = DraftIntentSnapshot.model_validate(body)
        except ValidationError as exc:
            # 0.8.7 hardening C1: scrub the raw `input` payload from
            # pydantic error envelopes. Pydantic v2's exc.errors() echoes
            # the offending value under the `input` key — for an adapter
            # bug that put draft text into the wrong field, that would
            # reflect raw text back to the client. We surface only the
            # field path + error type + message, never the input value.
            return JSONResponse(
                {"code": "invalid_input", "message": _sanitize_validation_errors(exc)},
                status_code=400,
            )

        # The adapter MUST only emit lifecycle states declared in
        # capabilities.emits — otherwise an L0 adapter could fabricate a
        # higher state and corrupt downstream scoring.
        if snapshot.lifecycle_state not in snapshot.capabilities.emits:
            return JSONResponse(
                {
                    "code": "capability_violation",
                    "message": (
                        f"adapter declared emits={list(snapshot.capabilities.emits)} but sent lifecycle_state={snapshot.lifecycle_state!r}"
                    ),
                },
                status_code=400,
            )

        composer_event_id = _uuid.uuid4().hex
        event = SignalEvent(
            id=composer_event_id,
            source=f"composer-adapter:{snapshot.capabilities.host_app}",
            kind=KIND_COMPOSER_LIFECYCLE,
            timestamp=_time.time(),
            payload=snapshot.model_dump(mode="json"),
        )
        try:
            await engine.observe(event)
        except ValidationError as exc:
            # engine.observe re-validates; same sanitization applies.
            return JSONResponse(
                {"code": "invalid_input", "message": _sanitize_validation_errors(exc)},
                status_code=400,
            )

        # Telemetry: record the composer-lifecycle event into draft_events
        # for hit/miss attribution downstream. Best-effort: a metrics
        # failure must not block the response.
        #
        # 0.8.7 hardening: reuse the closure-captured ``metrics_store``
        # that the daemon's lifespan already initialized at startup.
        # An earlier draft created a fresh MetricsStore + initialized it
        # per request, which raced under concurrent composer events on
        # SQLite WAL setup (CI Python 3.11 / 3.13 surfaced this).
        try:
            await metrics_store.record_composer_lifecycle_event(
                session_id=snapshot.session_id,
                snapshot_id=snapshot.snapshot_id,
                lifecycle_state=snapshot.lifecycle_state,
                text_hash=snapshot.text_hash,
                length_chars=snapshot.length_chars,
                composer_event_id=composer_event_id,
                metadata={
                    "host_app": snapshot.capabilities.host_app,
                    "host_kind": snapshot.capabilities.host_kind,
                    "level": snapshot.capabilities.level,
                    # 0.8.7 hardening H4: propagate privacy_zone so dashboards
                    # that aggregate by zone can attribute composer rows.
                    "privacy_zone": getattr(getattr(engine, "adapter", None), "privacy_zone", "local"),
                },
            )
        except Exception:  # pragma: no cover - defensive metrics
            pass

        return JSONResponse(
            {"composer_event_id": composer_event_id},
            status_code=200,
        )

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
        from vaner.clients.daemon import (
            RESOLVE_INCLUDE_BRIEFING_DEFAULT,
            RESOLVE_INCLUDE_PREDICTED_RESPONSE_DEFAULT,
        )

        context_raw = body.get("context")
        context = context_raw if isinstance(context_raw, dict) else None
        include_briefing = bool(body.get("include_briefing", RESOLVE_INCLUDE_BRIEFING_DEFAULT))
        include_predicted_response = bool(body.get("include_predicted_response", RESOLVE_INCLUDE_PREDICTED_RESPONSE_DEFAULT))
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
                rows = await scenario_store.list_top(limit=10, visibility="live")
                if rows:
                    fingerprint = json.dumps(
                        [
                            {
                                "id": row.id,
                                "relevance": row.relevance,
                                "visible_priority": row.visible_priority,
                                "readiness": row.readiness,
                                "visibility": row.visibility,
                                "lifecycle_motion": row.lifecycle_motion,
                                "freshness": row.freshness,
                                "last_outcome": row.last_outcome,
                                "last_reinforced_at": row.last_reinforced_at,
                            }
                            for row in rows
                        ],
                        sort_keys=True,
                    )
                    if fingerprint != last_fingerprint:
                        counts = await scenario_store.freshness_counts()
                        top = _scenario_payload(rows[0])
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
                                        "relevance": row.relevance,
                                        "confidence": row.confidence,
                                        "readiness": row.readiness,
                                        "visibility": row.visibility,
                                        "lifecycle_motion": row.lifecycle_motion,
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
                selected = {"scenarios", "prediction", "calibration", "draft", "budget", "predictions", "work"}

        async def event_gen() -> AsyncIterator[str]:
            last_scenario_fingerprint = ""
            stage_fingerprints: dict[str, str] = {}
            sent = 0
            while True:
                if "scenarios" in selected:
                    rows = await scenario_store.list_top(limit=10, visibility="live")
                    scenario_payload = json.dumps(
                        [
                            {
                                "id": row.id,
                                "relevance": row.relevance,
                                "confidence": row.confidence,
                                "readiness": row.readiness,
                                "visibility": row.visibility,
                                "lifecycle_motion": row.lifecycle_motion,
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
                    elif read_prediction_snapshot(config.repo_root) is not None:
                        predictions_payload = [compact_serialized_prediction(row) for row in _prediction_snapshot_rows()]
                    else:
                        predictions_payload = []
                    serialized_p = json.dumps(predictions_payload, sort_keys=True, default=str)
                    if serialized_p != stage_fingerprints.get("predictions", ""):
                        yield f"data: {json.dumps({'stage': 'predictions', 'payload': predictions_payload})}\n\n"
                        stage_fingerprints["predictions"] = serialized_p
                        sent += 1
                if "work" in selected:
                    work_payload = read_live_work_events(config.repo_root, limit=20)
                    serialized_work = json.dumps(work_payload, sort_keys=True, default=str)
                    if serialized_work != stage_fingerprints.get("work", ""):
                        yield f"data: {json.dumps({'stage': 'work', 'kind': 'work.snapshot', 'payload': {'items': work_payload}})}\n\n"
                        stage_fingerprints["work"] = serialized_work
                        sent += 1
                if limit is not None and sent >= max(1, limit):
                    return
                yield ": keepalive\n\n"
                await asyncio.sleep(2.0)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @app.get("/", response_class=HTMLResponse)
    async def cockpit() -> HTMLResponse:
        return cockpit_response(cockpit_dist)

    @app.get("/ui")
    async def ui() -> RedirectResponse:
        return RedirectResponse(url="/", status_code=307)

    return app
