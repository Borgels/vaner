# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from fastapi.testclient import TestClient

from vaner.models.config import BackendConfig, ProxyConfig, VanerConfig
from vaner.router import proxy as proxy_module
from vaner.router.proxy import create_app
from vaner.store.artefacts import ArtefactStore

_TEST_BACKEND = BackendConfig(base_url="http://127.0.0.1:11434/v1", model="test-model")


def _make_config(temp_repo, **kwargs) -> VanerConfig:
    kwargs.setdefault("backend", _TEST_BACKEND)
    return VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
        **kwargs,
    )


def test_create_proxy_app(temp_repo):
    config = _make_config(temp_repo)
    app = create_app(config, ArtefactStore(config.store_path))
    assert app.title == "Vaner Proxy"


def test_proxy_requires_token_when_configured(temp_repo, monkeypatch):
    class _Package:
        injected_context = "context"
        cache_tier = "miss"
        partial_similarity = 0.0
        token_used = 0

    async def _fake_aquery(prompt, repo_root, config=None, top_n=6):
        return _Package()

    async def _fake_forward_chat_completion(config, payload):
        return {"id": "ok"}

    monkeypatch.setattr(proxy_module, "aquery", _fake_aquery)
    monkeypatch.setattr(proxy_module, "forward_chat_completion", _fake_forward_chat_completion)
    config = _make_config(temp_repo, proxy=ProxyConfig(proxy_token="abc123", max_requests_per_minute=10))
    app = create_app(config, ArtefactStore(config.store_path))
    client = TestClient(app)

    no_auth = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert no_auth.status_code == 401

    with_auth = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer abc123"},
    )
    assert with_auth.status_code == 200


def test_proxy_rate_limits_requests(temp_repo, monkeypatch):
    class _Package:
        injected_context = "context"
        cache_tier = "miss"
        partial_similarity = 0.0
        token_used = 0

    async def _fake_aquery(prompt, repo_root, config=None, top_n=6):
        return _Package()

    async def _fake_forward_chat_completion(config, payload):
        return {"id": "ok"}

    monkeypatch.setattr(proxy_module, "aquery", _fake_aquery)
    monkeypatch.setattr(proxy_module, "forward_chat_completion", _fake_forward_chat_completion)
    config = _make_config(temp_repo, proxy=ProxyConfig(max_requests_per_minute=1))
    app = create_app(config, ArtefactStore(config.store_path))
    client = TestClient(app)

    first = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    second = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert first.status_code == 200
    assert second.status_code == 429
