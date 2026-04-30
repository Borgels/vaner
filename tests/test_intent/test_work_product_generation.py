# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess
from pathlib import Path

from vaner.intent.work_products import generate_work_products
from vaner.models.artefact import Artefact, ArtefactKind
from vaner.models.work_product import WorkProductAdoptability, WorkProductType


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
