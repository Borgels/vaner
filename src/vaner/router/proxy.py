# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import signal
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from vaner.api import aquery
from vaner.models.config import VanerConfig
from vaner.router.backends import forward_chat_completion, stream_chat_completion, validate_backend_config
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

    app = FastAPI(title="Vaner Proxy", version="0.1.0", lifespan=lifespan)
    limiter = _RateLimiter(config.proxy.max_requests_per_minute)
    required_token = (config.proxy.proxy_token or "").strip()

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
            if auth_header != f"Bearer {required_token}":
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

        context_package = await aquery(prompt, repo_root, config=config, top_n=6)

        metrics.t1_context_ready = time.monotonic()
        metrics.cache_tier = context_package.cache_tier
        metrics.partial_similarity = context_package.partial_similarity
        metrics.context_tokens = context_package.token_used

        enriched = _inject_context(payload, context_package.injected_context)
        metrics.t2_forwarded = time.monotonic()

        async def _finalize_metrics() -> None:
            metrics.finalize()
            prom.record(metrics)
            try:
                await metrics_store.record(metrics)
            except Exception:
                pass

        try:
            if metrics.is_stream:

                async def _instrumented_stream():
                    first_token_set = False
                    try:
                        async for chunk in stream_chat_completion(config, enriched):
                            if not first_token_set and chunk:
                                metrics.t3_first_token = time.monotonic()
                                first_token_set = True
                            yield chunk
                    finally:
                        metrics.t4_complete = time.monotonic()
                        await _finalize_metrics()

                return StreamingResponse(_instrumented_stream(), media_type="text/event-stream")
            else:
                result = await forward_chat_completion(config, enriched)
                metrics.t4_complete = time.monotonic()
                await _finalize_metrics()
                return result
        except Exception as exc:  # pragma: no cover - network errors
            metrics.t4_complete = time.monotonic()
            await _finalize_metrics()
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app
