# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Awaitable, Callable


def ollama_llm(
    *,
    model: str,
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 120.0,
) -> Callable[[str], Awaitable[str]]:
    """Ollama inference client using the native ``/api/generate`` endpoint.

    ``base_url`` defaults to a local Ollama instance but can point at any
    remote Ollama server (e.g. ``"http://192.168.1.10:11434"``).
    """
    _base = base_url.rstrip("/")

    async def _call(prompt: str) -> str:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{_base}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            payload = response.json()
            return str(payload.get("response", "[]"))

    return _call
