# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from typing import Any

import httpx

from vaner.models.config import BackendConfig


async def forward_chat_completion(backend: BackendConfig, payload: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv(backend.api_key_env, "")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{backend.base_url}/chat/completions",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


async def stream_chat_completion(backend: BackendConfig, payload: dict[str, Any]):
    api_key = os.getenv(backend.api_key_env, "")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream(
            "POST",
            f"{backend.base_url}/chat/completions",
            json=payload,
            headers=headers,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk
