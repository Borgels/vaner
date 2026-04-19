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


@pytest.mark.asyncio
async def test_daemon_respects_max_generations_per_cycle(temp_repo):
    for idx in range(5):
        (temp_repo / f"mod_{idx}.py").write_text(f"def fn_{idx}():\n    return {idx}\n", encoding="utf-8")

    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )
    config.generation.max_generations_per_cycle = 2

    daemon = VanerDaemon(config)
    await daemon.initialize()
    written = await daemon.run_once(changed_files=sorted(temp_repo.glob("*.py")))

    # Includes capped file summaries plus a repo index artefact.
    assert written <= config.generation.max_generations_per_cycle + 2
