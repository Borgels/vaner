# SPDX-License-Identifier: Apache-2.0
"""0.8.2 WS1 — connector integration tests.

Exercises :class:`LocalPlanAdapter`, :class:`MarkdownOutlineAdapter`, and
:class:`GitHubIssuesAdapter` (in its no-config no-op mode — network
paths are out of scope for the unit suite).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from vaner.intent.connectors import (
    GitHubIssuesAdapter,
    LocalPlanAdapter,
    MarkdownOutlineAdapter,
)

pytestmark = pytest.mark.asyncio


async def test_local_plan_discovers_configured_folders_only() -> None:
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / ".claude" / "plans").mkdir(parents=True)
        (ws / ".claude" / "plans" / "release.md").write_text("# Plan\n\n- [ ] x\n")
        (ws / "docs" / "plans").mkdir(parents=True)
        (ws / "docs" / "plans" / "roadmap.md").write_text("# Road\n\n- [ ] y\n")
        # Outside the allowlist — must be ignored.
        (ws / "src").mkdir()
        (ws / "src" / "plans.md").write_text("# Not a plan folder\n")

        adapter = LocalPlanAdapter(workspace_root=ws)
        candidates = list(await adapter.discover())
        rel_paths = {c.metadata["workspace_rel_path"] for c in candidates}
        assert rel_paths == {
            ".claude/plans/release.md",
            "docs/plans/roadmap.md",
        }


async def test_local_plan_excludes_secrets() -> None:
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / ".claude" / "plans").mkdir(parents=True)
        (ws / ".claude" / "plans" / "real.md").write_text("# Plan\n\n- [ ] real task one\n- [ ] real task two\n")
        (ws / ".claude" / "plans" / "credentials.md").write_text("# secret content here\n\nLong enough to clear the size floor\n")
        (ws / ".claude" / "plans" / "foo.secret").write_text("should not appear and is long enough")

        adapter = LocalPlanAdapter(workspace_root=ws)
        candidates = list(await adapter.discover())
        names = {Path(c.source_uri.replace("file://", "")).name for c in candidates}
        assert "real.md" in names
        assert "credentials.md" not in names
        assert "foo.secret" not in names


async def test_local_plan_fetch_reads_content() -> None:
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / ".claude" / "plans").mkdir(parents=True)
        (ws / ".claude" / "plans" / "t.md").write_text("hello world " * 5)
        adapter = LocalPlanAdapter(workspace_root=ws)
        candidates = list(await adapter.discover())
        assert len(candidates) == 1
        raw = await adapter.fetch(candidates[0])
        assert "hello world" in raw.text
        assert raw.connector == "local_plan"
        assert raw.tier == "T1"


async def test_local_plan_rejects_path_escape() -> None:
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "inner"
        ws.mkdir()
        # A symlink escape via the configured allowlist would be an escape
        # — we assert the resolve() + relative_to() guard. Use an absolute
        # path as an allowlist entry pointing outside ``ws`` and verify
        # the adapter does not yield it.
        outside = Path(td) / "outside.md"
        outside.write_text("# not allowed\n")
        adapter = LocalPlanAdapter(
            workspace_root=ws,
            allowlist=("../outside.md",),
            excludelist=(),
        )
        candidates = list(await adapter.discover())
        assert candidates == []


async def test_markdown_outline_skips_noise_dirs() -> None:
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "node_modules" / "lib").mkdir(parents=True)
        (ws / "node_modules" / "lib" / "plan.md").write_text("# Noise\n- [ ] x\n")
        (ws / ".git" / "plan.md").parent.mkdir(parents=True)
        (ws / ".git" / "plan.md").write_text("# Noise\n- [ ] x\n")
        (ws / "README.md").write_text("# readme\n\nhello world\n")

        adapter = MarkdownOutlineAdapter(workspace_root=ws)
        candidates = list(await adapter.discover())
        paths = {Path(c.source_uri.replace("file://", "")).name for c in candidates}
        assert "README.md" in paths
        # node_modules / .git must be pruned.
        assert not any("node_modules" in c.source_uri for c in candidates)
        assert not any(".git" in c.source_uri for c in candidates)


async def test_markdown_outline_respects_max_candidates() -> None:
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        for i in range(30):
            (ws / f"f{i}.md").write_text("# doc\n\nsome content here\n")
        adapter = MarkdownOutlineAdapter(workspace_root=ws, max_candidates=10)
        candidates = list(await adapter.discover())
        assert len(candidates) == 10


async def test_github_adapter_noop_without_repos() -> None:
    adapter = GitHubIssuesAdapter(repos=())
    result = list(await adapter.discover())
    assert result == []


async def test_github_adapter_noop_when_gh_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import vaner.intent.connectors.github_issues as gh_module

    # Force the ``gh`` lookup to fail so the adapter takes the missing-
    # binary path. The adapter must not raise, just log and return empty.
    monkeypatch.setattr(gh_module.shutil, "which", lambda name: None)

    adapter = GitHubIssuesAdapter(repos=("acme/repo",))
    result = list(await adapter.discover())
    assert result == []
