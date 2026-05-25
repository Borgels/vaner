# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess
from pathlib import Path

from vaner.external_state.models import ExternalStateFreshnessClass, ExternalStateSensitivity, ExternalStateSnapshot
from vaner.intent.work_products import generate_external_finance_work_products, generate_work_products
from vaner.models.artefact import Artefact, ArtefactKind
from vaner.models.work_product import WorkProductAdoptability, WorkProductSensitivity, WorkProductType


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


def test_generate_virtual_diff_without_mutating_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sample.py").write_text("def f():\n    return 1", encoding="utf-8")
    _init_repo(repo)
    before = subprocess.run(["git", "status", "--short"], cwd=repo, check=True, capture_output=True, text=True).stdout

    products = generate_work_products(
        repo_root=repo,
        recent_queries=["please review the latest change"],
        artefacts=[
            Artefact(
                key="a",
                kind=ArtefactKind.FILE_SUMMARY,
                source_path="sample.py",
                source_mtime=0.0,
                generated_at=1.0,
                model="test",
                content="sample summary",
            )
        ],
    )

    after = subprocess.run(["git", "status", "--short"], cwd=repo, check=True, capture_output=True, text=True).stdout
    assert before == after
    virtual_diff = next(product for product in products if product.type == WorkProductType.VIRTUAL_DIFF)
    assert virtual_diff.adoptability == WorkProductAdoptability.EXPORTABLE
    assert "```diff" in virtual_diff.body
    assert str(repo) not in virtual_diff.model_dump_json()


def test_research_brief_requires_research_intent_and_doc_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "notes.md").write_text("# Notes\nPublic source summary.\n", encoding="utf-8")
    _init_repo(repo)
    artefact = Artefact(
        key="doc",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path="notes.md",
        source_mtime=0.0,
        generated_at=1.0,
        model="test",
        content="Benchmark notes with grounded source evidence.",
    )

    products = generate_work_products(
        repo_root=repo,
        recent_queries=["research benchmark evidence for this approach"],
        artefacts=[artefact],
    )

    assert any(product.type == WorkProductType.RESEARCH_BRIEF for product in products)


def test_finance_brief_from_local_notes_without_external_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "trading-notes.md").write_text(
        "# Watchlist\nReview option expiry, IV, delta, and position drift before considering any hedge.\n",
        encoding="utf-8",
    )
    _init_repo(repo)
    artefact = Artefact(
        key="finance",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path="trading-notes.md",
        source_mtime=0.0,
        generated_at=1.0,
        model="test",
        content="Watchlist option expiry IV delta and position drift notes.",
    )

    products = generate_work_products(
        repo_root=repo,
        recent_queries=["prepare an option expiry position brief"],
        artefacts=[artefact],
    )

    finance = next(product for product in products if product.type.value.startswith("finance_"))
    assert finance.adoptability == WorkProductAdoptability.ADVISORY
    assert finance.sensitivity_class in {
        WorkProductSensitivity.POSITION_SPECIFIC,
        WorkProductSensitivity.USER_WATCHLIST,
    }
    assert finance.fresh_precheck_required is False
    assert finance.prohibited_actions == ["execution"]
    assert finance.external_inputs == []
    assert "no external market/account snapshot was used" in finance.body


def test_external_finance_work_product_records_exploration_pruning(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Finance fixture\n", encoding="utf-8")
    _init_repo(repo)
    now_payload = {
        "candidates": [
            {"symbol": "AAA", "lastPrice": 10.0, "changePercent": 5.0, "volume": 100000, "rank": 1},
            {"symbol": "BBB", "lastPrice": 11.0, "changePercent": 4.0, "volume": 90000, "rank": 2},
            {"symbol": "CCC", "lastPrice": 12.0, "changePercent": 3.0, "volume": 80000, "rank": 3},
            {"symbol": "DDD", "lastPrice": 13.0, "changePercent": 2.0, "volume": 70000, "rank": 4},
            {"symbol": "EEE", "lastPrice": 14.0, "changePercent": 1.0, "volume": 60000, "rank": 5},
            {"symbol": "FFF", "lastPrice": 15.0, "rank": 6},
            {"symbol": "GGG", "rank": 7},
        ]
    }
    snapshot = ExternalStateSnapshot.build(
        provider_id="provider",
        capability="screen_market",
        source_tool="provider_screen_market",
        freshness_class=ExternalStateFreshnessClass.MARKET_SNAPSHOT,
        sensitivity_class=ExternalStateSensitivity.PUBLIC_MARKET_ONLY,
        payload=now_payload,
        query_key="test",
        captured_at=1_700_000_000.0,
        ttl_seconds=300,
    )

    products = generate_external_finance_work_products(
        repo_root=repo,
        recent_queries=["screen market candidates for a finance brief"],
        snapshots=[snapshot],
    )

    finance = products[0]
    exploration = finance.provenance["finance"]["exploration"]
    assert finance.type == WorkProductType.FINANCE_SCREENING_RESULT
    assert finance.fresh_precheck_required is True
    assert finance.prohibited_actions == ["execution"]
    assert finance.external_inputs[0].capability == "screen_market"
    assert exploration["candidate_count"] == 7
    assert exploration["kept_candidate_count"] == 5
    assert exploration["pruned_candidate_count"] == 2
    assert "evaluated 7 candidate records, kept 5, pruned 2" in finance.body
    assert "risk-adjusted preparation" in finance.body
    assert "execution guidance" in finance.body
