from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from vaner.cli.commands.config import load_config, set_compute_value
from vaner.daemon.cockpit_html import build_cockpit_html
from vaner.events.bus import build_stage_payloads
from vaner.models.config import VanerConfig
from vaner.models.work_product import WorkProductFeedbackState, WorkProductType
from vaner.store.scenarios import ScenarioStore
from vaner.telemetry.metrics import MetricsStore

logger = logging.getLogger(__name__)


def _metrics_path(repo_root: Path) -> Path:
    return repo_root / ".vaner" / "metrics.db"


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


def _cockpit_dist_dir() -> Path | None:
    candidate = Path(__file__).resolve().parents[3] / "ui" / "cockpit" / "dist"
    if (candidate / "index.html").exists():
        return candidate
    return None


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
    cockpit_dist = _cockpit_dist_dir()
    if cockpit_dist is not None and (cockpit_dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=cockpit_dist / "assets"), name="cockpit-assets")

    def _prediction_health() -> dict[str, Any]:
        readiness_counts = {state: 0 for state in ["queued", "grounding", "evidence_gathering", "drafting", "ready", "stale"]}
        if engine is None or getattr(engine, "prediction_registry", None) is None:
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
    async def bootstrap() -> dict[str, str]:
        return {"mode": "daemon", "version": app.version, "cockpit_sha": os.environ.get("VANER_COCKPIT_SHA", "")}

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
                "prediction_health": _prediction_health(),
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
        from vaner.setup.config_io import persist_setup_and_policy
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
                        "default_model": config.backend.model or "qwen3.5:35b",
                        "api_key_env": "OPENAI_API_KEY",
                    },
                    {
                        "name": "vLLM OpenAI-compatible",
                        "base_url": "http://127.0.0.1:8000/v1",
                        "default_model": config.backend.model or "Qwen/Qwen3.5-32B",
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
        from vaner.intent.prediction_serialization import serialize_prediction_nested

        return serialize_prediction_nested(prompt)

    async def _work_product_store() -> Any:
        if engine is not None and getattr(engine, "store", None) is not None:
            await engine.store.initialize()
            return engine.store
        from vaner.store.artefacts import ArtefactStore

        store = ArtefactStore(config.repo_root / ".vaner" / "artefacts.db")
        await store.initialize()
        return store

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

    @app.get("/work-products")
    async def work_products_list(
        include_hidden: bool = False,
        include_terminal: bool = False,
        type: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        store = await _work_product_store()
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
        product = await store.get_work_product(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="work product not found")
        return JSONResponse(product.model_dump(mode="json"))

    @app.get("/prepared-work")
    async def prepared_work(
        limit: int = 20,
        include_advisory: bool = False,
        include_diagnostics: bool = False,
        context_id: str | None = None,
        surface: str = "api",
    ) -> JSONResponse:
        from vaner.intent.prepared_work import build_prepared_work_cards

        store = await _work_product_store()
        products = await store.list_work_products(
            include_hidden=True,
            include_terminal=True,
            limit=200,
        )
        predictions = []
        if engine is not None and getattr(engine, "prediction_registry", None) is not None:
            predictions = list(engine.get_active_predictions())
        cards = build_prepared_work_cards(
            work_products=products,
            predictions=predictions,
            include_advisory=include_advisory,
            include_diagnostics=include_diagnostics,
            context_id=context_id,
            surface=surface,
            limit=max(1, min(100, int(limit))),
        )
        return JSONResponse({"prepared_work": [card.model_dump(mode="json") for card in cards]})

    @app.post("/work-products/{product_id}/dismiss")
    async def work_products_dismiss(product_id: str) -> JSONResponse:
        store = await _work_product_store()
        ok = await store.dismiss_work_product(product_id)
        if not ok:
            raise HTTPException(status_code=404, detail="work product not found")
        return JSONResponse({"ok": True})

    @app.post("/work-products/{product_id}/feedback")
    async def work_products_feedback(product_id: str, payload: dict[str, Any]) -> JSONResponse:
        raw = str(payload.get("feedback_state", payload.get("feedback", ""))).strip()
        try:
            feedback = WorkProductFeedbackState(raw)
        except ValueError:
            return JSONResponse(
                {"code": "invalid_feedback", "message": "feedback_state must be one of none|useful|partial|irrelevant"},
                status_code=400,
            )
        store = await _work_product_store()
        ok = await store.feedback_work_product(product_id, feedback)
        if not ok:
            raise HTTPException(status_code=404, detail="work product not found")
        return JSONResponse({"ok": True})

    @app.post("/work-products/{product_id}/export")
    async def work_products_export(product_id: str) -> JSONResponse:
        store = await _work_product_store()
        try:
            exported = await store.export_work_product(product_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="work product not found") from None
        except PermissionError:
            return JSONResponse(
                {"code": "not_exportable", "message": "work product is not exportable"},
                status_code=409,
            )
        return JSONResponse(exported.model_dump(mode="json"))

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
        if cockpit_dist is not None:
            return (cockpit_dist / "index.html").read_text(encoding="utf-8")
        return build_cockpit_html("daemon")

    @app.get("/ui")
    async def ui() -> RedirectResponse:
        return RedirectResponse(url="/", status_code=307)

    return app
