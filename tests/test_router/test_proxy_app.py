# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.models.config import VanerConfig
from vaner.router.proxy import create_app
from vaner.store.artefacts import ArtefactStore


def test_create_proxy_app(temp_repo):
    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )
    app = create_app(config, ArtefactStore(config.store_path))
    assert app.title == "Vaner Proxy"
