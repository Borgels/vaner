# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Awaitable, Callable


def openai_llm(
    *,
    model: str,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    timeout: float = 120.0,
) -> Callable[[str], Awaitable[str]]:
    """OpenAI-compatible LLM client.

    Works with OpenAI, vLLM, and any server that implements the
    ``/v1/chat/completions`` endpoint.  Pass ``api_key="EMPTY"`` for local
    vLLM instances that require a non-empty but unauthenticated token.
    """
    # Strip trailing slash so we can safely append /chat/completions
    _base = base_url.rstrip("/")

    async def _call(prompt: str) -> str:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{_base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "Return only JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                },
            )
            response.raise_for_status()
            payload = response.json()
            choices = payload.get("choices", [])
            if not choices:
                return "[]"
            return str(choices[0]["message"]["content"])

    return _call
