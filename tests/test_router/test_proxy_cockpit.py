# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from fastapi.testclient import TestClient

from vaner.models.config import BackendConfig, VanerConfig
from vaner.router.proxy import create_app
from vaner.store.artefacts import ArtefactStore


def test_proxy_cockpit_root_and_ui_redirect(temp_repo, monkeypatch) -> None:
    cockpit_dist = temp_repo / "cockpit-dist"
    (cockpit_dist / "assets").mkdir(parents=True)
    (cockpit_dist / "index.html").write_text(
        '<!doctype html><title>Vaner Cockpit</title><div id="root"></div><script src="/assets/index.js"></script>',
        encoding="utf-8",
    )
    monkeypatch.setattr("vaner.router.proxy.cockpit_dist_dir", lambda: cockpit_dist)
    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
        backend=BackendConfig(base_url="http://127.0.0.1:11434/v1", model="test-model"),
    )
    app = create_app(config, ArtefactStore(config.store_path))
    with TestClient(app) as client:
        root = client.get("/")
        ui_redirect = client.get("/ui", follow_redirects=False)
    assert root.status_code == 200
    assert root.headers["content-type"].startswith("text/html")
    assert '<div id="root"></div>' in root.text
    assert 'data-mode="proxy"' not in root.text
    assert ui_redirect.status_code == 307
    assert ui_redirect.headers["location"] == "/"
