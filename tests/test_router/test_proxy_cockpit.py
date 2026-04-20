# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from fastapi.testclient import TestClient

from vaner.models.config import BackendConfig, VanerConfig
from vaner.router.proxy import create_app
from vaner.store.artefacts import ArtefactStore


def test_proxy_cockpit_root_and_ui_redirect(temp_repo) -> None:
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
    assert 'data-mode="proxy"' in root.text
    assert ui_redirect.status_code == 307
    assert ui_redirect.headers["location"] == "/"
