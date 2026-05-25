# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vaner.engine import VanerEngine
from vaner.external_state import ExternalStateFreshnessClass, ExternalStateSensitivity, ExternalStateSnapshot
from vaner.intent.adapter import CodeRepoAdapter
from vaner.models.artefact import Artefact, ArtefactKind
from vaner.models.work_product import WorkProductType


class _FakeExternalStateManager:
    def __init__(self, snapshot: ExternalStateSnapshot) -> None:
        self.snapshot = snapshot
        self.reset = False

    def reset_cycle_budget(self) -> None:
        self.reset = True

    async def collect_finance_snapshots(self, recent_queries: list[str]) -> list[ExternalStateSnapshot]:
        return [self.snapshot]


def _git_status(repo: Path) -> str:
    return subprocess.run(
        ["git", "status", "--short", "--untracked-files=no"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.mark.asyncio
async def test_engine_work_product_pass_does_not_mutate_tracked_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "sample.py").write_text("def prepared():\n    return 1", encoding="utf-8")

    engine = VanerEngine(adapter=CodeRepoAdapter(repo))
    await engine.store.initialize()
    await engine.store.upsert(
        Artefact(
            key="sample",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="sample.py",
            source_mtime=0.0,
            generated_at=1.0,
            model="test",
            content="Sample module summary.",
        )
    )

    before = _git_status(repo)
    written = await engine._run_work_product_pass(cycle_deadline=None)
    after = _git_status(repo)

    assert before == after
    assert written >= 1
    products = await engine.store.list_work_products(include_hidden=True)
    assert any(product.type == WorkProductType.VIRTUAL_DIFF for product in products)
    assert (repo / "sample.py").read_text(encoding="utf-8") == "def prepared():\n    return 1"


@pytest.mark.asyncio
async def test_engine_work_product_pass_persists_external_finance_products(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "notes.md").write_text("# Finance\nOption screen follow-up.\n", encoding="utf-8")

    engine = VanerEngine(adapter=CodeRepoAdapter(repo))
    await engine.store.initialize()
    engine.config.external_state.enabled = True
    engine.config.external_state.finance.enabled = True
    engine.config.external_state.finance.market_data_enabled = True
    snapshot = ExternalStateSnapshot.build(
        provider_id="provider",
        capability="screen_market",
        freshness_class=ExternalStateFreshnessClass.MARKET_SNAPSHOT,
        sensitivity_class=ExternalStateSensitivity.PUBLIC_MARKET_ONLY,
        payload={"result": "redacted"},
        captured_at=10.0,
        ttl_seconds=300.0,
    )
    manager = _FakeExternalStateManager(snapshot)
    engine.set_external_state_manager(manager)

    written = await engine._run_work_product_pass(cycle_deadline=None)

    assert written >= 1
    assert manager.reset is True
    saved_snapshot = await engine.store.get_external_state_snapshot(snapshot.id)
    assert saved_snapshot is not None
    products = await engine.store.list_work_products(include_hidden=True)
    finance = next(product for product in products if product.type == WorkProductType.FINANCE_SCREENING_RESULT)
    assert finance.fresh_precheck_required is True
    assert finance.external_inputs[0].snapshot_id == snapshot.id
    assert finance.prohibited_actions == ["execution"]
