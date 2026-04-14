# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from vaner.models.config import BackendConfig, VanerConfig


def _headers(backend: BackendConfig, *, use_fallback: bool = False) -> dict[str, str]:
    key_env = backend.fallback_api_key_env if use_fallback else backend.api_key_env
    api_key = os.getenv(key_env, "")
    headers = {"Content-Type": "application/json"}
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
    headers = _headers(backend, use_fallback=use_fallback)
    base_url = _base_url(backend, use_fallback=use_fallback)
    model_name = backend.fallback_model if use_fallback and backend.fallback_model else backend.model
    final_payload = payload if "model" in payload else {**payload, "model": model_name}
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{base_url}/chat/completions",
            json=final_payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


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
    headers = _headers(backend, use_fallback=use_fallback)
    base_url = _base_url(backend, use_fallback=use_fallback)
    model_name = backend.fallback_model if use_fallback and backend.fallback_model else backend.model
    final_payload = payload if "model" in payload else {**payload, "model": model_name}
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream(
            "POST",
            f"{base_url}/chat/completions",
            json=final_payload,
            headers=headers,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk


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
