# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from vaner.models.config import BackendConfig, VanerConfig
from vaner.router.translate import detect_format, translate_request, translate_response, translate_sse_chunk


@dataclass(frozen=True)
class RoutingTarget:
    base_url: str
    model: str
    api_key_env: str


def validate_backend_config(config: VanerConfig) -> None:
    """Raise a clear ``ValueError`` if the backend is not configured.

    Called at proxy startup so users get an actionable error instead of a
    confusing network failure when they forget to fill in ``vaner.toml``.
    """
    backend = config.backend
    if config.gateway.passthrough_enabled and config.gateway.routes:
        return
    if not backend.base_url.strip():
        raise ValueError(
            "Vaner proxy requires [backend] base_url to be set in .vaner/config.toml\n"
            '  Example (OpenAI):    base_url = "https://api.openai.com/v1"\n'
            '  Example (local):     base_url = "http://127.0.0.1:11434/v1"\n'
            "Run `vaner init` to regenerate a config template."
        )
    if not backend.model.strip():
        raise ValueError(
            "Vaner proxy requires [backend] model to be set in .vaner/config.toml\n"
            '  Example:  model = "gpt-4o"\n'
            "Run `vaner init` to regenerate a config template."
        )


def _headers(
    backend: BackendConfig,
    *,
    use_fallback: bool = False,
    backend_format: str = "openai",
    passthrough_authorization: str | None = None,
) -> dict[str, str]:
    if passthrough_authorization:
        return {
            "Content-Type": "application/json",
            "Authorization": passthrough_authorization,
        }
    key_env = backend.fallback_api_key_env if use_fallback else backend.api_key_env
    api_key = os.getenv(key_env, "")
    headers = {"Content-Type": "application/json"}
    if backend_format == "anthropic":
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    elif backend_format == "google":
        if api_key:
            headers["x-goog-api-key"] = api_key
    else:
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _base_url(backend: BackendConfig, *, use_fallback: bool = False) -> str:
    if use_fallback and backend.fallback_base_url:
        return backend.fallback_base_url.rstrip("/")
    return backend.base_url.rstrip("/")


def _is_local_backend(base_url: str) -> bool:
    lowered = base_url.lower()
    return "localhost" in lowered or "127.0.0.1" in lowered or "0.0.0.0" in lowered


# 0.8.3 WS2 — Deep-Run remote-call gate. Pre-call check that consults
# the active Deep-Run session (if any) and blocks remote calls that
# violate locality or cost constraints. Local-URL calls are never
# gated. When no Deep-Run session is active, this function is a
# zero-cost no-op — router behaviour is unchanged.
_DEEP_RUN_DEFAULT_REMOTE_CALL_ESTIMATE_USD = 0.01


class DeepRunRemoteCallBlockedError(RuntimeError):
    """Raised when a remote backend call is blocked by an active
    Deep-Run session's locality or cost gate. The error message names
    the session id and the constraint so surfaces can render a clear
    "Deep-Run blocked this call" notice."""


def _enforce_deep_run_gates_for_remote(
    base_url: str,
    *,
    estimated_usd: float = _DEEP_RUN_DEFAULT_REMOTE_CALL_ESTIMATE_USD,
) -> None:
    """Raise :class:`DeepRunRemoteCallBlockedError` if the active Deep-Run
    session blocks this remote call. No-op for local URLs and for the
    case where no session is active."""

    if _is_local_backend(base_url):
        return
    # Imports are deferred so the router does not pull in the intent
    # layer at module load (keeps the import graph cycle-safe).
    from vaner.intent.deep_run_gates import (
        get_active_session_for_routing,
        is_remote_call_allowed,
        try_consume_cost,
    )

    session = get_active_session_for_routing()
    if session is None:
        return
    if not is_remote_call_allowed(base_url):
        raise DeepRunRemoteCallBlockedError(f"Deep-Run session {session.id} is local_only; remote call to {base_url} blocked.")
    if not try_consume_cost(estimated_usd):
        raise DeepRunRemoteCallBlockedError(
            f"Deep-Run session {session.id} cost cap (${session.cost_cap_usd:.2f}) would be exceeded by this call."
        )


def _budget_state_path(repo_root: Path) -> Path:
    return repo_root / ".vaner" / "runtime" / "remote_budget.json"


def _consume_remote_budget(repo_root: Path, max_per_hour: int) -> bool:
    """Reserve one remote call against the hourly budget.

    0.8.4 hardening (HIGH-5): the previous implementation did
    ``read_text`` → parse → ``write_text`` with no lock and treated
    parse failure as "reset to 0". Two concurrent callers could both
    read the same ``used`` value and both increment to ``used+1``,
    overshooting ``max_per_hour``. A crash mid-write would leave a
    malformed file and subsequent callers would silently reset the
    counter, unblocking the budget. Current version:

    1. Uses ``fcntl.flock`` on the state file to serialise access
       across processes sharing the same ``repo_root``.
    2. Writes via a temp file + ``os.replace`` so the swap is atomic
       and a crash mid-write leaves either the old state or the new
       state on disk, never a truncated file.
    3. Treats parse failure as cap-hit (return False) rather than
       resetting to 0 — fail-closed under state corruption.
    """

    now = int(time.time())
    hour = now // 3600
    state_path = _budget_state_path(repo_root)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(".lock")

    # Open (and create if needed) the lock file. Cross-process
    # serialisation via fcntl.flock. Threads within a single process
    # also serialise on the same file descriptor.
    import fcntl
    import os
    import tempfile

    with open(lock_path, "a+b") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            state = {"hour": hour, "used": 0}
            if state_path.exists():
                try:
                    parsed = json.loads(state_path.read_text(encoding="utf-8"))
                    if not isinstance(parsed, dict):
                        return False  # malformed → fail-closed
                    state["hour"] = int(parsed.get("hour", hour))
                    state["used"] = int(parsed.get("used", 0))
                except (ValueError, OSError, TypeError):
                    # Corrupt state file → fail-closed. The operator
                    # can manually delete the file to reset.
                    return False

            if state["hour"] != hour:
                # Fresh hour — reset counter.
                state = {"hour": hour, "used": 0}
            if state["used"] >= max_per_hour:
                return False

            state["used"] += 1

            # Atomic write: temp file in the same directory + os.replace.
            tmp_fd, tmp_name = tempfile.mkstemp(
                prefix=".remote_budget.",
                suffix=".tmp",
                dir=str(state_path.parent),
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_fh:
                    json.dump(state, tmp_fh)
                os.replace(tmp_name, state_path)
            except Exception:
                # Best-effort cleanup of the temp file on failure.
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
            return True
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


async def _post_chat(backend: BackendConfig, payload: dict[str, Any], *, use_fallback: bool = False) -> dict[str, Any]:
    base_url = _base_url(backend, use_fallback=use_fallback)
    model_name = backend.fallback_model if use_fallback and backend.fallback_model else backend.model
    backend_format = detect_format(base_url)
    full_payload = payload if "model" in payload else {**payload, "model": model_name}
    endpoint, translated = translate_request(full_payload, backend_format=backend_format, model=model_name)
    headers = _headers(backend, use_fallback=use_fallback, backend_format=backend_format)
    timeout_seconds = max(1.0, float(getattr(backend, "request_timeout_seconds", 30.0)))
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            f"{base_url}{endpoint}",
            json=translated,
            headers=headers,
        )
        response.raise_for_status()
        raw = response.json()
        return translate_response(raw, backend_format=backend_format, model=model_name)


def _resolve_route_base_url(config: VanerConfig, model_name: str | None) -> str | None:
    if not model_name:
        return None
    match_prefix = ""
    match_url: str | None = None
    for prefix, base_url in config.gateway.routes.items():
        if model_name.startswith(prefix) and len(prefix) > len(match_prefix):
            match_prefix = prefix
            match_url = base_url
    if match_url:
        return match_url.rstrip("/")
    return None


def _resolve_target(config: VanerConfig, payload: dict[str, Any]) -> RoutingTarget:
    model_name = payload.get("model")
    if isinstance(model_name, str):
        routed = _resolve_route_base_url(config, model_name)
        if routed:
            return RoutingTarget(base_url=routed, model=model_name, api_key_env=config.backend.api_key_env)
    return RoutingTarget(
        base_url=config.backend.base_url.rstrip("/"),
        model=model_name if isinstance(model_name, str) and model_name.strip() else config.backend.model,
        api_key_env=config.backend.api_key_env,
    )


async def _post_chat_passthrough(
    config: VanerConfig,
    payload: dict[str, Any],
    *,
    authorization_header: str | None = None,
) -> dict[str, Any]:
    target = _resolve_target(config, payload)
    backend_format = detect_format(target.base_url)
    endpoint, translated = translate_request(payload, backend_format=backend_format, model=target.model)
    temp_backend = config.backend.model_copy(
        update={
            "base_url": target.base_url,
            "model": target.model,
            "api_key_env": target.api_key_env,
        }
    )
    headers = _headers(
        temp_backend,
        use_fallback=False,
        backend_format=backend_format,
        passthrough_authorization=authorization_header,
    )
    timeout_seconds = max(1.0, float(getattr(temp_backend, "request_timeout_seconds", 30.0)))
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            f"{target.base_url}{endpoint}",
            json=translated,
            headers=headers,
        )
        response.raise_for_status()
        raw = response.json()
        return translate_response(raw, backend_format=backend_format, model=target.model)


def _should_use_fallback(config: VanerConfig, primary_failed: bool) -> bool:
    backend = config.backend
    has_fallback = bool(backend.fallback_base_url)
    if not backend.fallback_enabled or not has_fallback:
        return False
    if primary_failed:
        return True
    # Local-first routing: use fallback only when primary isn't local.
    return backend.prefer_local and not _is_local_backend(backend.base_url)


async def forward_chat_completion(config: VanerConfig, payload: dict[str, Any]) -> dict[str, Any]:
    return await forward_chat_completion_with_request(config, payload, authorization_header=None)


async def forward_chat_completion_with_request(
    config: VanerConfig,
    payload: dict[str, Any],
    *,
    authorization_header: str | None,
) -> dict[str, Any]:
    if config.gateway.passthrough_enabled and authorization_header:
        return await _post_chat_passthrough(config, payload, authorization_header=authorization_header)
    backend = config.backend
    primary_error: Exception | None = None

    api_key = os.getenv(backend.api_key_env, "")
    try:
        # If no primary credentials exist and fallback is configured, skip straight to fallback.
        if api_key or _is_local_backend(backend.base_url):
            _enforce_deep_run_gates_for_remote(backend.base_url)
            return await _post_chat(backend, payload, use_fallback=False)
        primary_error = RuntimeError("Primary backend credentials are missing.")
    except Exception as exc:
        primary_error = exc

    if _should_use_fallback(config, primary_failed=True):
        fallback_url = backend.fallback_base_url or backend.base_url
        _enforce_deep_run_gates_for_remote(fallback_url)
        if not _consume_remote_budget(config.repo_root, backend.remote_budget_per_hour):
            raise RuntimeError("Remote fallback budget exhausted for this hour.") from primary_error
        return await _post_chat(backend, payload, use_fallback=True)

    assert primary_error is not None
    raise primary_error


async def _stream_chat(backend: BackendConfig, payload: dict[str, Any], *, use_fallback: bool = False):
    base_url = _base_url(backend, use_fallback=use_fallback)
    model_name = backend.fallback_model if use_fallback and backend.fallback_model else backend.model
    backend_format = detect_format(base_url)
    full_payload = payload if "model" in payload else {**payload, "model": model_name}
    endpoint, translated = translate_request(full_payload, backend_format=backend_format, model=model_name)
    headers = _headers(backend, use_fallback=use_fallback, backend_format=backend_format)
    timeout_seconds = max(1.0, float(getattr(backend, "request_timeout_seconds", 30.0)))
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        async with client.stream(
            "POST",
            f"{base_url}{endpoint}",
            json=translated,
            headers=headers,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield translate_sse_chunk(chunk, backend_format=backend_format)


async def _stream_chat_passthrough(
    config: VanerConfig,
    payload: dict[str, Any],
    *,
    authorization_header: str | None = None,
):
    target = _resolve_target(config, payload)
    backend_format = detect_format(target.base_url)
    endpoint, translated = translate_request(payload, backend_format=backend_format, model=target.model)
    temp_backend = config.backend.model_copy(
        update={
            "base_url": target.base_url,
            "model": target.model,
            "api_key_env": target.api_key_env,
        }
    )
    headers = _headers(
        temp_backend,
        use_fallback=False,
        backend_format=backend_format,
        passthrough_authorization=authorization_header,
    )
    timeout_seconds = max(1.0, float(getattr(temp_backend, "request_timeout_seconds", 30.0)))
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        async with client.stream(
            "POST",
            f"{target.base_url}{endpoint}",
            json=translated,
            headers=headers,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield translate_sse_chunk(chunk, backend_format=backend_format)


async def stream_chat_completion(config: VanerConfig, payload: dict[str, Any]):
    async for chunk in stream_chat_completion_with_request(config, payload, authorization_header=None):
        yield chunk


async def stream_chat_completion_with_request(
    config: VanerConfig,
    payload: dict[str, Any],
    *,
    authorization_header: str | None,
):
    if config.gateway.passthrough_enabled and authorization_header:
        async for chunk in _stream_chat_passthrough(config, payload, authorization_header=authorization_header):
            yield chunk
        return
    backend = config.backend
    api_key = os.getenv(backend.api_key_env, "")
    try:
        if api_key or _is_local_backend(backend.base_url):
            _enforce_deep_run_gates_for_remote(backend.base_url)
            async for chunk in _stream_chat(backend, payload, use_fallback=False):
                yield chunk
            return
        raise RuntimeError("Primary backend credentials are missing.")
    except Exception as exc:
        if _should_use_fallback(config, primary_failed=True):
            fallback_url = backend.fallback_base_url or backend.base_url
            _enforce_deep_run_gates_for_remote(fallback_url)
            if not _consume_remote_budget(config.repo_root, backend.remote_budget_per_hour):
                raise RuntimeError("Remote fallback budget exhausted for this hour.") from exc
            async for chunk in _stream_chat(backend, payload, use_fallback=True):
                yield chunk
            return
        raise
