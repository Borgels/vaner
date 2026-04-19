# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio

from vaner.models.config import BackendConfig, VanerConfig
from vaner.router import backends


def _config(temp_repo, **backend_overrides) -> VanerConfig:
    backend = BackendConfig(**backend_overrides)
    return VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
        backend=backend,
    )


def test_is_local_backend():
    assert backends._is_local_backend("http://localhost:11434/v1")
    assert backends._is_local_backend("http://127.0.0.1:8000/v1")
    assert not backends._is_local_backend("https://api.openai.com/v1")


def test_remote_budget_is_enforced(temp_repo):
    assert backends._consume_remote_budget(temp_repo, max_per_hour=2)
    assert backends._consume_remote_budget(temp_repo, max_per_hour=2)
    assert not backends._consume_remote_budget(temp_repo, max_per_hour=2)


def test_forward_chat_completion_uses_fallback_when_primary_fails(temp_repo, monkeypatch):
    config = _config(
        temp_repo,
        base_url="http://localhost:11434/v1",
        model="test-model",
        fallback_enabled=True,
        fallback_base_url="https://api.openai.com/v1",
        remote_budget_per_hour=3,
    )

    calls: list[bool] = []

    async def _fake_post_chat(backend, payload, *, use_fallback=False):
        calls.append(use_fallback)
        if not use_fallback:
            raise RuntimeError("primary unavailable")
        return {"id": "ok"}

    monkeypatch.setattr(backends, "_post_chat", _fake_post_chat)
    result = asyncio.run(backends.forward_chat_completion(config, {"messages": []}))
    assert result["id"] == "ok"
    assert calls == [False, True]
