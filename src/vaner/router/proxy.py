# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import random
import signal
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse

from vaner.api import aquery
from vaner.cli.commands.config import load_config, set_compute_value
from vaner.daemon.cockpit_html import build_cockpit_html
from vaner.models.config import VanerConfig
from vaner.models.decision import DecisionRecord
from vaner.router.backends import (
    forward_chat_completion_with_request,
    stream_chat_completion_with_request,
    validate_backend_config,
)
from vaner.store.artefacts import ArtefactStore
from vaner.telemetry.metrics import MetricsStore, RequestMetrics


def _inject_context(payload: dict[str, Any], context: str) -> dict[str, Any]:
    messages = payload.get("messages", [])
    system_content = "Use provided context when relevant.\n\n" + context
    system_message = {"role": "system", "content": system_content}
    return {**payload, "messages": [system_message, *messages]}


def _normalize_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _metrics_db_path(repo_root: Path) -> Path:
    return repo_root / ".vaner" / "metrics.db"


class _RateLimiter:
    def __init__(self, max_requests_per_minute: int) -> None:
        self.max_requests_per_minute = max(1, max_requests_per_minute)
        self._timestamps = deque()

    def allow(self) -> bool:
        now = time.monotonic()
        cutoff = now - 60.0
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_requests_per_minute:
            return False
        self._timestamps.append(now)
        return True


class _PrometheusCounters:
    """Minimal in-process Prometheus-compatible counters (no external deps)."""

    def __init__(self) -> None:
        self.requests_total: int = 0
        self.requests_by_tier: dict[str, int] = {}
        self.context_retrieval_sum_ms: float = 0.0
        self.llm_total_sum_ms: float = 0.0
        self.total_e2e_sum_ms: float = 0.0

    def record(self, m: RequestMetrics) -> None:
        self.requests_total += 1
        tier = m.cache_tier or "unknown"
        self.requests_by_tier[tier] = self.requests_by_tier.get(tier, 0) + 1
        self.context_retrieval_sum_ms += m.context_retrieval_ms
        self.llm_total_sum_ms += m.llm_total_ms
        self.total_e2e_sum_ms += m.total_e2e_ms

    def render(self) -> str:
        lines = [
            "# HELP vaner_requests_total Total requests handled by Vaner proxy",
            "# TYPE vaner_requests_total counter",
            f"vaner_requests_total {self.requests_total}",
        ]
        for tier, count in self.requests_by_tier.items():
            lines.append(f'vaner_requests_by_tier{{tier="{tier}"}} {count}')
        n = max(self.requests_total, 1)
        lines += [
            "# HELP vaner_context_retrieval_ms_avg Average context retrieval latency (ms)",
            "# TYPE vaner_context_retrieval_ms_avg gauge",
            f"vaner_context_retrieval_ms_avg {self.context_retrieval_sum_ms / n:.2f}",
            "# HELP vaner_llm_total_ms_avg Average LLM generation latency (ms)",
            "# TYPE vaner_llm_total_ms_avg gauge",
            f"vaner_llm_total_ms_avg {self.llm_total_sum_ms / n:.2f}",
            "# HELP vaner_total_e2e_ms_avg Average end-to-end latency (ms)",
            "# TYPE vaner_total_e2e_ms_avg gauge",
            f"vaner_total_e2e_ms_avg {self.total_e2e_sum_ms / n:.2f}",
        ]
        return "\n".join(lines) + "\n"


def create_app(config: VanerConfig, store: ArtefactStore) -> FastAPI:
    validate_backend_config(config)

    metrics_store = MetricsStore(_metrics_db_path(config.repo_root))
    prom = _PrometheusCounters()

    # Track in-flight requests for graceful shutdown
    _inflight: set[asyncio.Task] = set()
    _shutting_down = False

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.initialize()
        await metrics_store.initialize()

        loop = asyncio.get_running_loop()

        def _handle_sigterm():
            nonlocal _shutting_down
            _shutting_down = True

            # Wait for in-flight requests to drain, then stop
            async def _drain():
                if _inflight:
                    await asyncio.gather(*_inflight, return_exceptions=True)

            asyncio.ensure_future(_drain())

        try:
            loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)
        except (NotImplementedError, RuntimeError):
            pass  # Windows or non-main thread

        yield

        try:
            loop.remove_signal_handler(signal.SIGTERM)
        except (NotImplementedError, RuntimeError):
            pass

    app = FastAPI(title="Vaner Proxy", version="0.2.0", lifespan=lifespan)
    limiter = _RateLimiter(config.proxy.max_requests_per_minute)
    required_token = (config.proxy.proxy_token or "").strip()
    shadow_rate = max(0.0, min(config.gateway.shadow_rate, 1.0))
    decision_stream_subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
    gateway_enabled = bool(config.gateway.passthrough_enabled)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe -- always returns 200 when the server is running."""
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        """Readiness probe -- returns 200 when the store is initialized and available."""
        if _shutting_down:
            raise HTTPException(status_code=503, detail="Shutting down")
        try:
            await store.initialize()
            return {"status": "ready"}
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Store unavailable: {exc}") from exc

    @app.get("/metrics", response_class=PlainTextResponse)
    async def prometheus_metrics() -> str:
        """Prometheus-compatible metrics endpoint."""
        return prom.render()

    @app.get("/compute/devices")
    async def compute_devices() -> JSONResponse:
        devices: list[dict[str, Any]] = [{"id": "cpu", "label": "CPU", "kind": "cpu"}]
        try:  # pragma: no cover - GPU visibility depends on environment
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
        except Exception:
            pass
        return JSONResponse({"devices": devices, "selected": config.compute.device})

    @app.get("/impact/summary")
    async def impact_summary(last: int = 500) -> JSONResponse:
        summary = await metrics_store.shadow_summary(last_n=max(1, min(last, 2000)))
        idle_path = config.repo_root / ".vaner" / "runtime" / "idle_usage.json"
        idle_seconds = 0.0
        if idle_path.exists():
            try:
                parsed = json.loads(idle_path.read_text(encoding="utf-8"))
                idle_seconds = float(parsed.get("idle_seconds_used", 0.0))
            except Exception:
                idle_seconds = 0.0
        summary["idle_seconds_used"] = round(idle_seconds, 3)
        return JSONResponse(summary)

    @app.get("/status")
    async def cockpit_status() -> JSONResponse:
        return JSONResponse(
            {
                "health": "ok",
                "gateway_enabled": gateway_enabled,
                "compute": config.compute.model_dump(mode="json"),
                "backend": config.backend.model_dump(mode="json"),
            }
        )

    @app.post("/compute")
    async def update_compute(payload: dict[str, Any]) -> JSONResponse:
        allowed_keys = {
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
            if key not in allowed_keys:
                raise HTTPException(status_code=400, detail=f"Unsupported compute key: {key}")
            set_compute_value(config.repo_root, key, value)
        refreshed = load_config(config.repo_root)
        config.compute = refreshed.compute
        return JSONResponse({"ok": True, "compute": config.compute.model_dump(mode="json")})

    @app.post("/gateway/toggle")
    async def toggle_gateway(payload: dict[str, Any]) -> JSONResponse:
        nonlocal gateway_enabled
        gateway_enabled = bool(payload.get("enabled", True))
        return JSONResponse({"ok": True, "gateway_enabled": gateway_enabled})

    @app.get("/", response_class=HTMLResponse)
    async def cockpit() -> str:
        return build_cockpit_html("proxy")

    @app.get("/ui")
    async def cockpit_ui() -> RedirectResponse:
        return RedirectResponse(url="/", status_code=307)

    async def _publish_decision_event(event: dict[str, Any]) -> None:
        if not decision_stream_subscribers:
            return
        stale_subscribers: list[asyncio.Queue[dict[str, Any] | None]] = []
        for subscriber in decision_stream_subscribers:
            try:
                subscriber.put_nowait(event)
            except asyncio.QueueFull:
                stale_subscribers.append(subscriber)
        for stale in stale_subscribers:
            decision_stream_subscribers.discard(stale)

    def _serialize_decision(record: DecisionRecord) -> dict[str, Any]:
        payload = record.model_dump(mode="json")
        payload["selection_count"] = len(record.selections)
        return payload

    def _load_recent_decisions(repo_root: Path, limit: int) -> list[dict[str, Any]]:
        ids = DecisionRecord.list_recent_ids(repo_root, limit=limit)
        items: list[dict[str, Any]] = []
        for decision_id in ids:
            record = DecisionRecord.read_by_id(repo_root, decision_id)
            if record is None:
                continue
            items.append(_serialize_decision(record))
        return items

    @app.get("/decisions")
    async def list_decisions(request: Request, limit: int = 200) -> JSONResponse:
        repo_root = _resolve_repo_root(request)
        bounded_limit = max(1, min(limit, 500))
        return JSONResponse({"items": _load_recent_decisions(repo_root, bounded_limit)})

    @app.get("/decisions/stream")
    async def stream_decisions() -> StreamingResponse:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=200)
        decision_stream_subscribers.add(queue)

        async def _event_stream():
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                decision_stream_subscribers.discard(queue)

        return StreamingResponse(_event_stream(), media_type="text/event-stream")

    @app.websocket("/decisions")
    async def websocket_decisions(websocket: WebSocket) -> None:
        await websocket.accept()
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=200)
        decision_stream_subscribers.add(queue)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            decision_stream_subscribers.discard(queue)

    def _resolve_repo_root(request: Request) -> Path:
        """Determine which repo root to use for a request.

        Clients can override the default via ``X-Vaner-Repo`` header (absolute
        path) or by mounting the proxy at a sub-path like ``/repos/myproject/v1/...``.
        Falls back to the config default.
        """
        override = request.headers.get("X-Vaner-Repo", "").strip()
        if override:
            p = Path(override)
            if p.is_dir():
                return p
        return config.repo_root

    @app.post("/v1/chat/completions")
    async def chat_completions(payload: dict[str, Any], request: Request) -> Any:
        if _shutting_down:
            raise HTTPException(status_code=503, detail="Shutting down, please retry.")
        if required_token:
            auth_header = request.headers.get("Authorization", "")
            token_header = request.headers.get("X-Vaner-Proxy-Token", "")
            if token_header != required_token and auth_header != f"Bearer {required_token}":
                raise HTTPException(status_code=401, detail="Missing or invalid proxy token.")
        if not limiter.allow():
            raise HTTPException(status_code=429, detail="Rate limit exceeded.")

        metrics = RequestMetrics()
        metrics.t0_received = time.monotonic()
        metrics.is_stream = payload.get("stream") is True

        repo_root = _resolve_repo_root(request)
        user_messages = [msg for msg in payload.get("messages", []) if msg.get("role") == "user"]
        prompt = _normalize_message_content(user_messages[-1].get("content")) if user_messages else ""
        metrics.prompt_tokens = len(prompt.split())
        if gateway_enabled:
            context_package = await aquery(prompt, repo_root, config=config, top_n=6)
            metrics.t1_context_ready = time.monotonic()
            metrics.cache_tier = context_package.cache_tier
            metrics.partial_similarity = context_package.partial_similarity
            metrics.context_tokens = context_package.token_used
            enriched = _inject_context(payload, context_package.injected_context)
        else:
            context_package = type("Package", (), {"cache_tier": "disabled", "partial_similarity": 0.0, "token_used": 0})()
            metrics.t1_context_ready = time.monotonic()
            metrics.cache_tier = "disabled"
            metrics.partial_similarity = 0.0
            metrics.context_tokens = 0
            enriched = payload
        metrics.t2_forwarded = time.monotonic()
        decision_record = DecisionRecord.read_latest(repo_root)
        decision_id = decision_record.id if decision_record is not None else "unknown"
        request_authorization = request.headers.get("Authorization")

        async def _finalize_metrics() -> None:
            metrics.finalize()
            prom.record(metrics)
            try:
                await metrics_store.record(metrics)
                await metrics_store.increment_mode_usage("proxy")
            except Exception:
                pass

        response_headers = {
            "X-Vaner-Decision": decision_id,
            "X-Vaner-Context-Tokens": str(context_package.token_used),
            "X-Vaner-Hit-Tier": context_package.cache_tier,
        }

        if decision_record is not None:
            await _publish_decision_event(_serialize_decision(decision_record))

        async def _run_shadow_sample(with_context_ms: float) -> None:
            if shadow_rate <= 0.0:
                return
            if metrics.is_stream:
                return
            if random.random() > shadow_rate:
                return
            started = time.monotonic()
            try:
                await forward_chat_completion_with_request(
                    config,
                    payload,
                    authorization_header=request_authorization,
                )
            except Exception:
                return
            without_context_ms = (time.monotonic() - started) * 1000.0
            await metrics_store.record_shadow_pair(
                request_id=metrics.request_id,
                with_context_total_ms=with_context_ms,
                without_context_total_ms=without_context_ms,
                with_context_tokens=context_package.token_used,
                without_context_tokens=0,
            )

        try:
            if metrics.is_stream:

                async def _instrumented_stream():
                    first_token_set = False
                    try:
                        async for chunk in stream_chat_completion_with_request(
                            config,
                            enriched,
                            authorization_header=request.headers.get("Authorization"),
                        ):
                            if not first_token_set and chunk:
                                metrics.t3_first_token = time.monotonic()
                                first_token_set = True
                            yield chunk
                    finally:
                        metrics.t4_complete = time.monotonic()
                        await _finalize_metrics()

                return StreamingResponse(
                    _instrumented_stream(),
                    media_type="text/event-stream",
                    headers=response_headers,
                )
            else:
                result = await forward_chat_completion_with_request(
                    config,
                    enriched,
                    authorization_header=request_authorization,
                )
                metrics.t4_complete = time.monotonic()
                await _finalize_metrics()
                await _run_shadow_sample(metrics.total_e2e_ms)
                response_headers["X-Vaner-Latency-Ms"] = f"{metrics.total_e2e_ms:.2f}"
                return JSONResponse(result, headers=response_headers)
        except Exception as exc:  # pragma: no cover - network errors
            metrics.t4_complete = time.monotonic()
            await _finalize_metrics()
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app
