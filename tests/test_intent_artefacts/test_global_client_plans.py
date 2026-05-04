from __future__ import annotations

import pytest

from vaner.intent.connectors.global_client_plans import GlobalClientPlansAdapter


@pytest.mark.asyncio
async def test_global_client_plans_requires_workspace_match(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "work" / "vaner-demo"
    workspace.mkdir(parents=True)
    home = tmp_path / "home"
    plans = home / ".claude" / "plans"
    plans.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    matched = plans / "current.md"
    matched.write_text(f"# Plan\n\nWork in {workspace}\n", encoding="utf-8")
    unrelated = plans / "other.md"
    unrelated.write_text("# Other\n\nWork in another-repo\n", encoding="utf-8")

    adapter = GlobalClientPlansAdapter(workspace_root=workspace, selected_clients=("claude-code",))
    decisions = adapter.inspect_candidates()

    by_name = {decision.path.rsplit("/", 1)[-1]: decision for decision in decisions}
    assert by_name["current.md"].status == "accepted"
    assert by_name["current.md"].matched_by == "workspace_path"
    assert by_name["other.md"].status == "skipped"
    assert by_name["other.md"].reason == "no_workspace_match"

    candidates = list(await adapter.discover())
    assert [candidate.title_hint for candidate in candidates] == ["current.md"]
