from __future__ import annotations

from fastapi.testclient import TestClient

from vaner.daemon.http import create_daemon_http_app
from vaner.models.config import VanerConfig


def test_sources_permissions_reports_global_plan_candidates(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    home = tmp_path / "home"
    plans = home / ".claude" / "plans"
    plans.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (plans / "repo-plan.md").write_text(f"# Plan\n\nWorkspace {workspace}\n", encoding="utf-8")

    config = VanerConfig(
        repo_root=workspace,
        store_path=workspace / ".vaner" / "store.db",
        telemetry_path=workspace / ".vaner" / "telemetry.db",
    )
    app = create_daemon_http_app(config, engine=None)

    with TestClient(app) as client:
        response = client.get("/sources/permissions")

    assert response.status_code == 200
    body = response.json()
    global_plans = body["sources"]["global_client_plans"]
    assert global_plans["enabled"] is False
    assert global_plans["accepted_count"] == 1
    assert global_plans["accepted_examples"][0]["matched_by"] == "workspace_path"
    assert body["privacy_boundary"]["global_plans"] == "opt_in_workspace_matched"
