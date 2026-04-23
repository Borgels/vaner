# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

from vaner.clients.llm_response import LLMResponse, approx_tokens, split_thinking_and_content
from vaner.defaults.loader import reasoning_defaults_for_model

ReasoningMode = Literal["off", "allowed", "required", "provider_default"]


def ollama_llm(
    *,
    model: str,
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 120.0,
) -> Callable[[str], Awaitable[str]]:
    """Ollama inference client (bare-string return — legacy contract).

    For richer returns (thinking trace separation + structured output), use
    :func:`ollama_llm_structured` instead.
    """
    structured = ollama_llm_structured(
        model=model,
        base_url=base_url,
        timeout=timeout,
    )

    async def _call(prompt: str) -> str:
        response = await structured(prompt)
        return response.content

    return _call


def ollama_llm_structured(
    *,
    model: str,
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 120.0,
    max_tokens: int | None = None,
    response_format: dict | None = None,
    extra_body: dict | None = None,
    reasoning_mode: ReasoningMode = "provider_default",
) -> Callable[..., Awaitable[LLMResponse]]:
    """Ollama client that returns a structured LLMResponse.

    Ollama's ``/api/generate`` supports ``options`` (e.g. ``num_predict``)
    and ``format: "json"``. We translate Vaner's provider-neutral parameters
    to Ollama's idiom:

    - ``max_tokens`` → ``options.num_predict``
    - ``response_format={"type": "json_object"}`` → ``format: "json"``
    - ``extra_body`` is merged into the body (useful for ``options`` overrides
      or chat-template toggles).
    - ``reasoning_mode`` applies the same tolerant parsing semantics as
      :func:`vaner.clients.openai.openai_llm_structured`. When
      ``provider_default`` is left and ``model`` matches a known reasoning
      model in the defaults manifest, the recommended mode is applied.
    """
    if reasoning_mode == "provider_default":
        manifest_entry = reasoning_defaults_for_model(model)
        if manifest_entry is not None:
            reasoning_mode = manifest_entry["reasoning_mode"]  # type: ignore[assignment]
            if not extra_body:
                extra_body = dict(manifest_entry.get("extra_body") or {})
    _base = base_url.rstrip("/")

    async def _call(prompt: str, *, max_tokens: int | None = max_tokens) -> LLMResponse:
        import httpx

        body: dict = {"model": model, "prompt": prompt, "stream": False}
        options: dict = {}
        if max_tokens is not None and max_tokens > 0:
            options["num_predict"] = int(max_tokens)
        if options:
            body["options"] = options
        if response_format is not None:
            rf_type = response_format.get("type")
            if rf_type in ("json_object", "json"):
                body["format"] = "json"
        merged_extra = dict(extra_body or {})
        if reasoning_mode == "off":
            body["prompt"] = prompt + "\n/no_think"
        body.update(merged_extra)

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{_base}/api/generate", json=body)
            response.raise_for_status()
            payload = response.json()
            raw = str(payload.get("response", ""))

        split = split_thinking_and_content(raw)

        if reasoning_mode == "required" and not split.thinking:
            raise ValueError(
                f"reasoning_mode='required' but response contained no detected thinking preamble (raw len={approx_tokens(raw)} tokens)"
            )
        if reasoning_mode == "off" and split.thinking:
            raise ValueError("reasoning_mode='off' but response still contained a thinking preamble — provider did not honour /no_think")
        return split

    return _call
