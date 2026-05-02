# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import json
import platform
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from vaner.daemon.http import create_daemon_http_app
from vaner.daemon.signals.git_reader import read_content_hashes
from vaner.models.config import VanerConfig
from vaner.models.work_product import (
    WorkProduct,
    WorkProductAdoptability,
    WorkProductEvidenceRef,
    WorkProductFreshness,
    WorkProductSelfEval,
    WorkProductSourceSnapshot,
    WorkProductStatus,
    WorkProductType,
)
from vaner.store.artefacts import ArtefactStore

if platform.system().lower().startswith("win"):
    pytest.skip("daemon http TestClient is flaky on Windows runners", allow_module_level=True)


def _run_git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def _make_public_fixture_repo(repo: Path) -> None:
    (repo / "src").mkdir()
    (repo / "docs").mkdir()
    (repo / "src" / "calculator.py").write_text(
        "def divide(left, right):\n    return left / right\n",
        encoding="utf-8",
    )
    (repo / "docs" / "calculator.md").write_text(
        "# Calculator\n\nThe divide helper does not describe zero handling yet.\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# Public Prepared Work Fixture\n", encoding="utf-8")
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Prepared Work Test")
    _run_git(repo, "config", "commit.gpgsign", "false")
    _run_git(repo, "add", "README.md", "src/calculator.py", "docs/calculator.md")
    _run_git(repo, "commit", "-m", "seed public fixture")


def _config(repo: Path) -> VanerConfig:
    return VanerConfig(
        repo_root=repo,
        store_path=repo / ".vaner" / "store.db",
        telemetry_path=repo / ".vaner" / "telemetry.db",
    )


async def _seed_products(repo: Path) -> None:
    now = time.time()
    store = ArtefactStore(repo / ".vaner" / "artefacts.db")
    await store.initialize()
    hashes = read_content_hashes(repo, ["src/calculator.py", "docs/calculator.md"])
    products = [
        WorkProduct(
            id="wp-zero-guard-diff",
            type=WorkProductType.VIRTUAL_DIFF,
            title="Guard divide by zero",
            summary="A one-file diff adds an explicit zero-division guard.",
            body="```diff\n"
            "--- a/src/calculator.py\n"
            "+++ b/src/calculator.py\n"
            "@@\n"
            " def divide(left, right):\n"
            "+    if right == 0:\n"
            '+        raise ValueError("right must not be zero")\n'
            "     return left / right\n"
            "```",
            evidence_refs=[
                WorkProductEvidenceRef(kind="file", path="src/calculator.py", reason="target function source"),
                WorkProductEvidenceRef(kind="file", path="docs/calculator.md", reason="docs mention missing zero handling"),
            ],
            source_snapshot=WorkProductSourceSnapshot(
                project_id="public-prepared-work-fixture",
                relative_paths=["src/calculator.py", "docs/calculator.md"],
                file_hashes=hashes,
                base_commit=_run_git(repo, "rev-parse", "HEAD").strip(),
                generated_at=now,
                generator_version="product-proof.v1",
            ),
            confidence=0.88,
            freshness=WorkProductFreshness.FRESH,
            status=WorkProductStatus.SURFACED,
            adoptability=WorkProductAdoptability.EXPORTABLE,
            self_eval=WorkProductSelfEval(
                evidence_coverage=0.9,
                groundedness=0.86,
                stale_risk=0.05,
                reason="You are working near src/calculator.py; Vaner found direct source and docs evidence for zero handling.",
            ),
            created_at=now,
            updated_at=now,
            target_key="src/calculator.py",
        ),
        WorkProduct(
            id="wp-docs-note",
            type=WorkProductType.DOCS_DRIFT,
            title="Document divide zero behavior",
            summary="The docs do not explain what divide should do when the denominator is zero.",
            body="The calculator docs should describe whether divide raises or returns a sentinel for zero denominators.",
            evidence_refs=[WorkProductEvidenceRef(kind="file", path="docs/calculator.md", reason="documentation gap")],
            source_snapshot=WorkProductSourceSnapshot(
                project_id="public-prepared-work-fixture",
                relative_paths=["docs/calculator.md"],
                file_hashes={"docs/calculator.md": hashes["docs/calculator.md"]},
                base_commit=_run_git(repo, "rev-parse", "HEAD").strip(),
                generated_at=now,
                generator_version="product-proof.v1",
            ),
            confidence=0.72,
            freshness=WorkProductFreshness.FRESH,
            status=WorkProductStatus.SURFACED,
            adoptability=WorkProductAdoptability.ADVISORY,
            self_eval=WorkProductSelfEval(
                evidence_coverage=0.65,
                groundedness=0.65,
                stale_risk=0.15,
                reason="The source and docs disagree on whether zero handling is intentional.",
            ),
            created_at=now,
            updated_at=now,
            target_key="docs/calculator.md",
        ),
        WorkProduct(
            id="wp-weak-generic",
            type=WorkProductType.REVIEW_NOTE,
            title="Maybe review the repo",
            summary="There may be something to review.",
            body="Generic note with no usable evidence.",
            evidence_refs=[],
            source_snapshot=WorkProductSourceSnapshot(
                project_id="public-prepared-work-fixture",
                relative_paths=[],
                file_hashes={},
                generated_at=now,
                generator_version="product-proof.v1",
            ),
            confidence=0.41,
            freshness=WorkProductFreshness.UNKNOWN,
            status=WorkProductStatus.SURFACED,
            adoptability=WorkProductAdoptability.ADVISORY,
            self_eval=WorkProductSelfEval(evidence_coverage=0.0, groundedness=0.0, stale_risk=0.3),
            created_at=now,
            updated_at=now,
            target_key="repo",
        ),
    ]
    for product in products:
        await store.upsert_work_product(product)


def _assert_no_project_mutation(repo: Path) -> None:
    assert _run_git(repo, "status", "--short", "--untracked-files=no") == ""
    assert _run_git(repo, "diff", "--", "README.md", "src/calculator.py", "docs/calculator.md") == ""


def _assert_public_payload(value: Any, repo: Path, *, allow_inspect_internals: bool = False) -> None:
    """Verify the public payload doesn't leak filesystem paths or
    internal state. The four cockpit-refresh fields (`adoptability`,
    `self_eval`, plus the lifecycle/feedback labels they pair with)
    are deliberately exposed on `/work-products/{id}/inspect` so the
    cockpit Inspector can render score-factor bars and lifecycle
    strips. Pass `allow_inspect_internals=True` for the inspect
    endpoint; everywhere else those fields stay forbidden."""
    text = json.dumps(value, sort_keys=True)
    forbidden = [
        str(repo),
        str(repo.parent),
        "/home/",
        "\\Users\\",
        "WorkProductStatus",
        "chain-of-thought",
        "PRIVATE_KEY_MARKER",
    ]
    if not allow_inspect_internals:
        forbidden.extend(["adoptability", "self_eval"])
    for needle in forbidden:
        assert needle not in text


def _mcp_payload_from_result(result: Any) -> dict[str, Any]:
    for item in result.root.content:
        text = getattr(item, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError("MCP result did not include JSON text")


async def _call_mcp_tool(server: Any, name: str, arguments: dict[str, Any] | None = None) -> Any:
    from mcp.types import CallToolRequest

    handler = server.request_handlers[CallToolRequest]
    return await handler(CallToolRequest(method="tools/call", params={"name": name, "arguments": arguments or {}}))


@pytest.mark.asyncio
async def test_prepared_work_product_proof_http_and_mcp_path(tmp_path: Path) -> None:
    repo = tmp_path / "public-fixture"
    repo.mkdir()
    _make_public_fixture_repo(repo)
    await _seed_products(repo)
    _assert_no_project_mutation(repo)

    app = create_daemon_http_app(_config(repo))
    with TestClient(app) as client:
        dashboard = client.get("/prepared-work?surface=cockpit&limit=5")
        assert dashboard.status_code == 200
        payload = dashboard.json()
        cards = payload["prepared_work"]
        assert 1 <= len(cards) <= 5
        assert cards[0]["id"] == "work_product:wp-zero-guard-diff"
        assert cards[0]["primary_action"]["kind"] == "export"
        assert cards[0]["action_note"].endswith("Vaner will not apply it automatically.")
        assert all(card["id"] != "work_product:wp-weak-generic" for card in cards)
        _assert_public_payload(payload, repo)

        inspected = client.get("/work-products/wp-zero-guard-diff/inspect")
        assert inspected.status_code == 200
        inspection = inspected.json()
        assert inspection["evidence_count"] == 2
        assert inspection["export_preview"]
        assert any(ref["path"] == "src/calculator.py" for ref in inspection["evidence_refs"])
        assert any(action["kind"] == "export" for action in inspection["allowed_actions"])
        _assert_public_payload(inspection, repo, allow_inspect_internals=True)

        exported = client.post("/work-products/wp-zero-guard-diff/export")
        assert exported.status_code == 200
        assert "right must not be zero" in exported.json()["body"]
        _assert_no_project_mutation(repo)

        feedback = client.post("/work-products/wp-docs-note/feedback", json={"feedback_state": "not_useful"})
        assert feedback.status_code == 200
        dismissed = client.post("/work-products/wp-docs-note/dismiss")
        assert dismissed.status_code == 200
        _assert_no_project_mutation(repo)

    if importlib.util.find_spec("mcp") is None:
        pytest.skip("mcp package is unavailable in this test environment")

    from vaner.mcp.server import build_server

    server = build_server(repo)
    mcp_dashboard = _mcp_payload_from_result(
        await _call_mcp_tool(server, "vaner.prepared_work.dashboard", {"limit": 5, "surface": "mcp_app"})
    )
    mcp_cards = mcp_dashboard["prepared_work"]
    assert mcp_cards[0]["id"] == "work_product:wp-zero-guard-diff"
    assert mcp_cards[0]["primary_action"]["tool"] == "vaner.work_products.export"
    _assert_public_payload(mcp_dashboard, repo)

    mcp_inspect = _mcp_payload_from_result(
        await _call_mcp_tool(server, "vaner.work_products.inspect", {"work_product_id": "wp-zero-guard-diff"})
    )
    assert mcp_inspect["source_id"] == "wp-zero-guard-diff"
    assert any(action["kind"] == "export" for action in mcp_inspect["allowed_actions"])

    mcp_export = _mcp_payload_from_result(
        await _call_mcp_tool(server, "vaner.work_products.export", {"work_product_id": "wp-zero-guard-diff"})
    )
    assert "right must not be zero" in mcp_export["body"]
    _assert_no_project_mutation(repo)
