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

    async def _fake_forward_chat_completion(config, payload, *, authorization_header=None):
        return {"id": "ok"}

    monkeypatch.setattr(proxy_module, "aquery", _fake_aquery)
    monkeypatch.setattr(proxy_module, "forward_chat_completion_with_request", _fake_forward_chat_completion)
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

    async def _fake_forward_chat_completion(config, payload, *, authorization_header=None):
        return {"id": "ok"}

    monkeypatch.setattr(proxy_module, "aquery", _fake_aquery)
    monkeypatch.setattr(proxy_module, "forward_chat_completion_with_request", _fake_forward_chat_completion)
    config = _make_config(temp_repo, proxy=ProxyConfig(max_requests_per_minute=1))
    app = create_app(config, ArtefactStore(config.store_path))
    client = TestClient(app)

    first = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    second = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert first.status_code == 200
    assert second.status_code == 429


def test_proxy_preserves_authorization_header_in_passthrough(temp_repo, monkeypatch):
    class _Package:
        injected_context = "context"
        cache_tier = "miss"
        partial_similarity = 0.0
        token_used = 5

    captured_authorization = {}

    async def _fake_aquery(prompt, repo_root, config=None, top_n=6):
        return _Package()

    async def _fake_forward_chat_completion(config, payload, *, authorization_header=None):
        captured_authorization["value"] = authorization_header
        return {"id": "ok"}

    monkeypatch.setattr(proxy_module, "aquery", _fake_aquery)
    monkeypatch.setattr(proxy_module, "forward_chat_completion_with_request", _fake_forward_chat_completion)
    config = _make_config(temp_repo)
    app = create_app(config, ArtefactStore(config.store_path))
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer upstream-key"},
    )

    assert response.status_code == 200
    assert captured_authorization["value"] == "Bearer upstream-key"
    assert response.headers["X-Vaner-Context-Tokens"] == "5"
    assert "X-Vaner-Decision" in response.headers


def test_cockpit_endpoints(temp_repo, monkeypatch):
    vaner_dir = temp_repo / ".vaner"
    vaner_dir.mkdir(parents=True, exist_ok=True)
    (vaner_dir / "config.toml").write_text(
        """
[backend]
base_url = "http://127.0.0.1:11434/v1"
model = "test-model"
""".strip(),
        encoding="utf-8",
    )

    class _Package:
        injected_context = "context"
        cache_tier = "miss"
        partial_similarity = 0.0
        token_used = 1

    async def _fake_aquery(prompt, repo_root, config=None, top_n=6):
        return _Package()

    async def _fake_forward_chat_completion(config, payload, *, authorization_header=None):
        return {"id": "ok", "choices": [{"message": {"content": "done"}}]}

    monkeypatch.setattr(proxy_module, "aquery", _fake_aquery)
    monkeypatch.setattr(proxy_module, "forward_chat_completion_with_request", _fake_forward_chat_completion)
    config = _make_config(temp_repo)
    app = create_app(config, ArtefactStore(config.store_path))
    client = TestClient(app)

    status = client.get("/status")
    assert status.status_code == 200
    assert status.json()["health"] == "ok"

    html = client.get("/ui")
    assert html.status_code == 200
    assert "Vaner Cockpit" in html.text

    updated = client.post("/compute", json={"cpu_fraction": 0.3})
    assert updated.status_code == 200
    assert updated.json()["compute"]["cpu_fraction"] == 0.3

    toggled = client.post("/gateway/toggle", json={"enabled": False})
    assert toggled.status_code == 200
    assert toggled.json()["gateway_enabled"] is False
