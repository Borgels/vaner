# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from vaner.models.config import BackendConfig, VanerConfig
from vaner.router.translate import detect_format, translate_request, translate_response, translate_sse_chunk


def validate_backend_config(config: VanerConfig) -> None:
    """Raise a clear ``ValueError`` if the backend is not configured.

    Called at proxy startup so users get an actionable error instead of a
    confusing network failure when they forget to fill in ``vaner.toml``.
    """
    backend = config.backend
    if not backend.base_url.strip():
        raise ValueError(
            "Vaner proxy requires [backend] base_url to be set in .vaner/config.toml\n"
            "  Example (OpenAI):    base_url = \"https://api.openai.com/v1\"\n"
            "  Example (local):     base_url = \"http://127.0.0.1:11434/v1\"\n"
            "Run `vaner init` to regenerate a config template."
        )
    if not backend.model.strip():
        raise ValueError(
            "Vaner proxy requires [backend] model to be set in .vaner/config.toml\n"
            "  Example:  model = \"gpt-4o\"\n"
            "Run `vaner init` to regenerate a config template."
        )


def _headers(backend: BackendConfig, *, use_fallback: bool = False, backend_format: str = "openai") -> dict[str, str]:
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


def _budget_state_path(repo_root: Path) -> Path:
    return repo_root / ".vaner" / "runtime" / "remote_budget.json"


def _consume_remote_budget(repo_root: Path, max_per_hour: int) -> bool:
    now = int(time.time())
    hour = now // 3600
    state_path = _budget_state_path(repo_root)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    state = {"hour": hour, "used": 0}
    if state_path.exists():
        try:
            parsed = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                state["hour"] = int(parsed.get("hour", hour))
                state["used"] = int(parsed.get("used", 0))
        except Exception:
            state = {"hour": hour, "used": 0}

    if state["hour"] != hour:
        state = {"hour": hour, "used": 0}
    if state["used"] >= max_per_hour:
        return False

    state["used"] += 1
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return True


async def _post_chat(backend: BackendConfig, payload: dict[str, Any], *, use_fallback: bool = False) -> dict[str, Any]:
    base_url = _base_url(backend, use_fallback=use_fallback)
    model_name = backend.fallback_model if use_fallback and backend.fallback_model else backend.model
    backend_format = detect_format(base_url)
    full_payload = payload if "model" in payload else {**payload, "model": model_name}
    endpoint, translated = translate_request(full_payload, backend_format=backend_format, model=model_name)
    headers = _headers(backend, use_fallback=use_fallback, backend_format=backend_format)
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{base_url}{endpoint}",
            json=translated,
            headers=headers,
        )
        response.raise_for_status()
        raw = response.json()
        return translate_response(raw, backend_format=backend_format, model=model_name)


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
    backend = config.backend
    primary_error: Exception | None = None

    api_key = os.getenv(backend.api_key_env, "")
    try:
        # If no primary credentials exist and fallback is configured, skip straight to fallback.
        if api_key or _is_local_backend(backend.base_url):
            return await _post_chat(backend, payload, use_fallback=False)
        primary_error = RuntimeError("Primary backend credentials are missing.")
    except Exception as exc:
        primary_error = exc

    if _should_use_fallback(config, primary_failed=True):
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
    async with httpx.AsyncClient(timeout=60) as client:
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


async def stream_chat_completion(config: VanerConfig, payload: dict[str, Any]):
    backend = config.backend
    api_key = os.getenv(backend.api_key_env, "")
    try:
        if api_key or _is_local_backend(backend.base_url):
            async for chunk in _stream_chat(backend, payload, use_fallback=False):
                yield chunk
            return
        raise RuntimeError("Primary backend credentials are missing.")
    except Exception as exc:
        if _should_use_fallback(config, primary_failed=True):
            if not _consume_remote_budget(config.repo_root, backend.remote_budget_per_hour):
                raise RuntimeError("Remote fallback budget exhausted for this hour.") from exc
            async for chunk in _stream_chat(backend, payload, use_fallback=True):
                yield chunk
            return
        raise
