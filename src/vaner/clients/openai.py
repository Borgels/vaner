# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

from vaner.clients.llm_response import LLMResponse, approx_tokens, split_thinking_and_content
from vaner.defaults.loader import reasoning_defaults_for_model

ReasoningMode = Literal["off", "allowed", "required", "provider_default"]


def openai_llm(
    *,
    model: str,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    timeout: float = 120.0,
) -> Callable[[str], Awaitable[str]]:
    """OpenAI-compatible LLM client (bare-string return — legacy contract).

    Works with OpenAI, vLLM, and any server that implements the
    ``/v1/chat/completions`` endpoint.  Pass ``api_key="EMPTY"`` for local
    vLLM instances that require a non-empty but unauthenticated token.

    For richer returns (thinking trace separation + structured output), use
    :func:`openai_llm_structured` instead.
    """
    structured = openai_llm_structured(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
    )

    async def _call(prompt: str) -> str:
        response = await structured(prompt)
        return response.content

    return _call


def openai_llm_structured(
    *,
    model: str,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    timeout: float = 120.0,
    max_tokens: int | None = None,
    response_format: dict | None = None,
    extra_body: dict | None = None,
    reasoning_mode: ReasoningMode = "provider_default",
) -> Callable[..., Awaitable[LLMResponse]]:
    """OpenAI-compatible LLM client that returns a structured LLMResponse.

    Parameters beyond the basic client:

    - ``max_tokens``: if set, forwarded as ``max_tokens`` on the request.
    - ``response_format``: forwarded verbatim (e.g. ``{"type": "json_object"}``).
      Providers that don't support it will ignore or error depending on their
      implementation; see Phase B's tolerant parsing.
    - ``extra_body``: merged into the POST body. Covers provider-specific
      extensions like Qwen3's ``chat_template_kwargs``.
    - ``reasoning_mode``:
        * ``off`` — adapter sets ``extra_body.chat_template_kwargs.enable_thinking=false``
          when the caller hasn't already set it, and rejects responses that
          still contain a thinking preamble.
        * ``allowed`` — thinking permitted; strip preamble into ``.thinking``.
        * ``required`` — thinking must be present; error if content arrives
          without one.
        * ``provider_default`` — pass through without opinion. When the
          model name matches a known reasoning model in the defaults
          manifest, the recommended mode is applied automatically.
    """
    # Phase 4 / WS2.d: consult the reasoning-model manifest to auto-upgrade
    # ``provider_default`` for known reasoning models. User-supplied values
    # always win; we only overwrite the untouched default.
    if reasoning_mode == "provider_default":
        manifest_entry = reasoning_defaults_for_model(model)
        if manifest_entry is not None:
            reasoning_mode = manifest_entry["reasoning_mode"]  # type: ignore[assignment]
            if not extra_body:
                extra_body = dict(manifest_entry.get("extra_body") or {})
    _base = base_url.rstrip("/")

    async def _call(prompt: str, *, max_tokens: int | None = max_tokens) -> LLMResponse:
        # Per-call override: when an engine caller passes its own max_tokens
        # (typically derived from the parent prediction's remaining budget),
        # that value wins over the factory default.
        import httpx

        body: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Return only JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        if max_tokens is not None and max_tokens > 0:
            body["max_tokens"] = int(max_tokens)
        if response_format is not None:
            body["response_format"] = response_format
        merged_extra = dict(extra_body or {})
        if reasoning_mode == "off":
            # Opt reasoning models out of thinking when the caller didn't.
            chat_tpl = dict(merged_extra.get("chat_template_kwargs") or {})
            chat_tpl.setdefault("enable_thinking", False)
            merged_extra["chat_template_kwargs"] = chat_tpl
        body.update(merged_extra)

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{_base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
            choices = payload.get("choices", [])
            if not choices:
                return LLMResponse(thinking="", content="[]", raw="")
            raw = str(choices[0]["message"].get("content", ""))

        split = split_thinking_and_content(raw)

        if reasoning_mode == "required" and not split.thinking:
            raise ValueError(
                f"reasoning_mode='required' but response contained no detected thinking preamble (raw len={approx_tokens(raw)} tokens)"
            )
        if reasoning_mode == "off" and split.thinking:
            raise ValueError(
                "reasoning_mode='off' but response still contained a thinking preamble — provider did not honour enable_thinking=false"
            )
        return split

    return _call
