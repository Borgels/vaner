# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from vaner.api import aquery
from vaner.models.config import VanerConfig
from vaner.router.backends import forward_chat_completion, stream_chat_completion
from vaner.store.artefacts import ArtefactStore


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


def create_app(config: VanerConfig, store: ArtefactStore) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await store.initialize()
        yield

    app = FastAPI(title="Vaner Proxy", version="0.1.0", lifespan=lifespan)

    @app.post("/v1/chat/completions")
    async def chat_completions(payload: dict[str, Any]) -> Any:
        user_messages = [msg for msg in payload.get("messages", []) if msg.get("role") == "user"]
        prompt = _normalize_message_content(user_messages[-1].get("content")) if user_messages else ""
        context_package = await aquery(prompt, config.repo_root, config=config, top_n=6)
        enriched = _inject_context(payload, context_package.injected_context)
        try:
            if payload.get("stream") is True:
                return StreamingResponse(stream_chat_completion(config.backend, enriched), media_type="text/event-stream")
            return await forward_chat_completion(config.backend, enriched)
        except Exception as exc:  # pragma: no cover - network errors
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app
