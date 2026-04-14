# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from vaner.daemon.runner import VanerDaemon
from vaner.models.config import VanerConfig


@pytest.mark.asyncio
async def test_daemon_run_once_writes_artefacts(temp_repo):
    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )
    daemon = VanerDaemon(config)
    await daemon.initialize()
    written = await daemon.run_once()
    assert written >= 1
