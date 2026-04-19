# SPDX-License-Identifier: Apache-2.0
"""API format translation between OpenAI and other provider formats.

Vaner's proxy speaks OpenAI ``/v1/chat/completions`` on the inbound side.
For backends that don't natively speak OpenAI format, this module translates
requests and responses transparently so clients never need to know which
backend they're talking to.

Supported backend formats
--------------------------
- ``openai``  -- pass-through (no translation needed)
- ``anthropic`` -- Claude Messages API (``/v1/messages``)
- ``google``  -- Gemini generateContent API (``/v1beta/models/{model}:generateContent``)
"""

from __future__ import annotations

import json
from typing import Any


def detect_format(base_url: str) -> str:
    """Infer the backend API format from *base_url*.

    Returns one of ``"openai"``, ``"anthropic"``, or ``"google"``.
    Defaults to ``"openai"`` for unknown URLs (covers vLLM, LM Studio, Ollama /v1, etc.).
    """
    url = base_url.lower()
    if "anthropic.com" in url:
        return "anthropic"
    if "googleapis.com" in url or "generativelanguage" in url or "aiplatform.google" in url:
        return "google"
    return "openai"


# ---------------------------------------------------------------------------
# Anthropic translation
# ---------------------------------------------------------------------------


def _openai_to_anthropic(payload: dict[str, Any], model: str) -> tuple[str, dict[str, Any]]:
    """Convert an OpenAI chat payload to Anthropic Messages API format.

    Returns ``(endpoint_path, translated_payload)``.
    """
    messages = payload.get("messages", [])
    system_parts: list[str] = []
    anthropic_messages: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        if role == "system":
            system_parts.append(str(content))
        elif role in ("user", "assistant"):
            anthropic_messages.append({"role": role, "content": str(content)})

    translated: dict[str, Any] = {
        "model": payload.get("model") or model,
        "messages": anthropic_messages,
        "max_tokens": payload.get("max_tokens", 4096),
    }
    if system_parts:
        translated["system"] = "\n\n".join(system_parts)
    if "temperature" in payload:
        translated["temperature"] = payload["temperature"]
    if payload.get("stream"):
        translated["stream"] = True

    return "/v1/messages", translated


def _anthropic_to_openai(response: dict[str, Any]) -> dict[str, Any]:
    """Convert an Anthropic Messages response to OpenAI chat completion format."""
    content_blocks = response.get("content", [])
    text = " ".join(b.get("text", "") for b in content_blocks if isinstance(b, dict) and b.get("type") == "text")
    usage = response.get("usage", {})
    return {
        "id": response.get("id", ""),
        "object": "chat.completion",
        "model": response.get("model", ""),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": response.get("stop_reason", "stop"),
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
    }


def _anthropic_sse_to_openai_sse(chunk: bytes) -> bytes:
    """Best-effort conversion of a single Anthropic SSE chunk to OpenAI SSE format."""
    try:
        text = chunk.decode("utf-8").strip()
        if not text.startswith("data:"):
            return chunk
        data_str = text[5:].strip()
        if data_str == "[DONE]":
            return b"data: [DONE]\n\n"
        event = json.loads(data_str)
        etype = event.get("type", "")
        if etype == "content_block_delta":
            delta_text = event.get("delta", {}).get("text", "")
            openai_chunk = {
                "object": "chat.completion.chunk",
                "choices": [{"delta": {"content": delta_text}, "index": 0, "finish_reason": None}],
            }
            return f"data: {json.dumps(openai_chunk)}\n\n".encode()
        if etype == "message_stop":
            return b"data: [DONE]\n\n"
    except Exception:
        pass
    return chunk


# ---------------------------------------------------------------------------
# Google Gemini translation
# ---------------------------------------------------------------------------


def _openai_to_google(payload: dict[str, Any], model: str) -> tuple[str, dict[str, Any]]:
    """Convert an OpenAI chat payload to Google Gemini generateContent format."""
    messages = payload.get("messages", [])
    contents: list[dict[str, Any]] = []
    system_parts: list[str] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        if role == "system":
            system_parts.append(str(content))
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": str(content)}]})
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": str(content)}]})

    model_name = (payload.get("model") or model).replace("models/", "")
    endpoint = f"/v1beta/models/{model_name}:generateContent"

    translated: dict[str, Any] = {"contents": contents}
    if system_parts:
        translated["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
    if "temperature" in payload or "max_tokens" in payload:
        gen_config: dict[str, Any] = {}
        if "temperature" in payload:
            gen_config["temperature"] = payload["temperature"]
        if "max_tokens" in payload:
            gen_config["maxOutputTokens"] = payload["max_tokens"]
        translated["generationConfig"] = gen_config

    return endpoint, translated


def _google_to_openai(response: dict[str, Any], model: str) -> dict[str, Any]:
    """Convert a Google Gemini response to OpenAI chat completion format."""
    candidates = response.get("candidates", [])
    text = ""
    finish_reason = "stop"
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        finish_reason = candidates[0].get("finishReason", "stop").lower()

    usage_meta = response.get("usageMetadata", {})
    return {
        "id": "",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": usage_meta.get("promptTokenCount", 0),
            "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
            "total_tokens": usage_meta.get("totalTokenCount", 0),
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def translate_request(
    payload: dict[str, Any],
    *,
    backend_format: str,
    model: str,
) -> tuple[str, dict[str, Any]]:
    """Translate an OpenAI-format payload to the target backend format.

    Returns ``(endpoint_path, translated_payload)``.
    ``endpoint_path`` is appended to ``base_url`` when making the upstream call.
    """
    if backend_format == "anthropic":
        return _openai_to_anthropic(payload, model)
    if backend_format == "google":
        return _openai_to_google(payload, model)
    return "/chat/completions", payload


def translate_response(
    response: dict[str, Any],
    *,
    backend_format: str,
    model: str,
) -> dict[str, Any]:
    """Translate a backend response back to OpenAI format."""
    if backend_format == "anthropic":
        return _anthropic_to_openai(response)
    if backend_format == "google":
        return _google_to_openai(response, model)
    return response


def translate_sse_chunk(chunk: bytes, *, backend_format: str) -> bytes:
    """Translate a single SSE chunk from the backend format to OpenAI SSE format."""
    if backend_format == "anthropic":
        return _anthropic_sse_to_openai_sse(chunk)
    return chunk
