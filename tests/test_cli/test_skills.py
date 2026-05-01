# SPDX-License-Identifier: Apache-2.0

"""Tests for the per-client skill installer (Phase C of the IDE/agent
visibility plan).

The canonical skill body lives at
``src/vaner/defaults/skills/vaner-feedback/SKILL.md``; per-client
adapters in ``vaner.cli.commands.skills`` ship it into the right place
in the right format for each client. This file exercises:

* every supported client's path resolver returns the documented location
* render output is non-empty, idempotent, and stable across reruns
* clients with frontmatter (continue, roo) emit the right keys
* the writer is non-destructive when content is identical (skipped)
* the user-scope surfaces (claude-code, codex-cli) honor a stubbed home
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vaner.cli.commands.skills import (
    SKILL_SURFACES,
    SkillScope,
    load_canonical_skill_body,
    skill_version,
    write_skill_for_client,
    write_skills,
)

# ---------------------------------------------------------------------------
# Canonical body loader
# ---------------------------------------------------------------------------


def test_load_canonical_skill_body_strips_frontmatter() -> None:
    body = load_canonical_skill_body()
    # The shipped SKILL.md starts with YAML frontmatter; the body
    # loader must strip it.
    assert not body.startswith("---")
    # The body keeps the actual instruction text.
    assert "vaner.feedback" in body
    assert "resolution_id" in body


def test_skill_version_is_non_empty() -> None:
    assert skill_version()


# ---------------------------------------------------------------------------
# Per-client writer
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin Path.home() to tmp_path/home for user-scope skill tests."""

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


def test_write_skill_claude_code_user_scope(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = write_skill_for_client("claude-code", repo)
    assert result.action == "added"
    assert result.scope == SkillScope.USER
    assert result.path == fake_home / ".claude" / "skills" / "vaner" / "vaner-feedback" / "SKILL.md"
    content = result.path.read_text(encoding="utf-8")
    # Agent Skills frontmatter is preserved (claude-code uses the
    # canonical SKILL.md unchanged).
    assert content.startswith("---\n")
    assert "name: vaner-feedback" in content


def test_write_skill_cursor_repo_scope(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = write_skill_for_client("cursor", repo)
    assert result.action == "added"
    assert result.scope == SkillScope.REPO
    assert result.path == repo / ".cursor" / "skills" / "vaner" / "vaner-feedback" / "SKILL.md"
    content = result.path.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "name: vaner-feedback" in content


def test_write_skill_codex_cli_user_scope(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = write_skill_for_client("codex-cli", repo)
    assert result.action == "added"
    assert result.scope == SkillScope.USER
    # Codex CLI uses ``~/.codex/skills/<name>/SKILL.md`` (no extra
    # vaner-namespace dir).
    assert result.path == fake_home / ".codex" / "skills" / "vaner-feedback" / "SKILL.md"
    content = result.path.read_text(encoding="utf-8")
    # Same Agent Skills standard body as Claude Code / Cursor.
    assert "name: vaner-feedback" in content


def test_write_skill_continue_uses_invokable_prompt_format(tmp_path: Path) -> None:
    """Continue prompts are user-invoked via ``/<name>``; the
    ``invokable: true`` flag in frontmatter is what makes the file
    appear as a slash command."""

    repo = tmp_path / "repo"
    repo.mkdir()
    result = write_skill_for_client("continue", repo)
    assert result.action == "added"
    assert result.path == repo / ".continue" / "prompts" / "vaner-feedback.md"
    content = result.path.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "invokable: true" in content
    assert "name: vaner-feedback" in content
    # Body content carries through.
    assert "resolution_id" in content


def test_write_skill_cline_workflow_uses_filename_as_command(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = write_skill_for_client("cline", repo)
    assert result.action == "added"
    # Filename is the slash command — ``/vaner-feedback`` triggers it.
    assert result.path == repo / ".clinerules" / "workflows" / "vaner-feedback.md"
    content = result.path.read_text(encoding="utf-8")
    # Cline workflows don't require frontmatter; we add a managed-marker
    # comment so re-runs can detect ownership.
    assert "x-vaner-managed" in content
    assert "Vaner feedback" in content


def test_write_skill_windsurf_workflow_with_title(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = write_skill_for_client("windsurf", repo)
    assert result.action == "added"
    assert result.path == repo / ".windsurf" / "workflows" / "vaner-feedback.md"
    content = result.path.read_text(encoding="utf-8")
    # Windsurf renders the first heading as the workflow title.
    assert "# Vaner feedback" in content
    assert "x-vaner-managed" in content


def test_write_skill_roo_slash_command_with_description(tmp_path: Path) -> None:
    """Roo slash commands carry a ``description`` frontmatter field
    that surfaces in the command palette. ``run_slash_command`` lets
    the agent invoke this programmatically."""

    repo = tmp_path / "repo"
    repo.mkdir()
    result = write_skill_for_client("roo", repo)
    assert result.action == "added"
    assert result.path == repo / ".roo" / "commands" / "vaner-feedback.md"
    content = result.path.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "description:" in content
    # Body carries through.
    assert "vaner.feedback" in content


def test_write_skill_is_idempotent(tmp_path: Path) -> None:
    """Two runs in a row with identical content produce ``skipped``."""

    repo = tmp_path / "repo"
    repo.mkdir()
    first = write_skill_for_client("cursor", repo)
    second = write_skill_for_client("cursor", repo)
    assert first.action == "added"
    assert second.action == "skipped"
    assert first.path == second.path


def test_write_skill_unsupported_client_returns_unsupported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = write_skill_for_client("nope-some-client", repo)
    assert result.action == "unsupported"
    assert result.path is None


def test_write_skill_dry_run_does_not_touch_disk(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = write_skill_for_client("cursor", repo, dry_run=True)
    assert result.action == "added"
    assert result.path is not None
    assert not result.path.exists()


# ---------------------------------------------------------------------------
# Batch writer
# ---------------------------------------------------------------------------


def test_write_skills_writes_all_seven_clients(fake_home: Path, tmp_path: Path) -> None:
    """All seven supported clients (claude-code + cursor + codex-cli +
    continue + cline + windsurf + roo) get a skill on a single
    ``write_skills`` call."""

    repo = tmp_path / "repo"
    repo.mkdir()
    results = write_skills(sorted(SKILL_SURFACES.keys()), repo)
    by_id = {r.client_id: r for r in results}
    expected = {"claude-code", "cursor", "codex-cli", "continue", "cline", "windsurf", "roo"}
    assert expected == set(by_id.keys())
    for client_id in expected:
        assert by_id[client_id].action == "added", f"{client_id} should write on a fresh tree; got {by_id[client_id].action}"
        assert by_id[client_id].path is not None
        assert by_id[client_id].path.exists()


def test_write_skills_idempotent_on_rerun(fake_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    write_skills(sorted(SKILL_SURFACES.keys()), repo)
    second = write_skills(sorted(SKILL_SURFACES.keys()), repo)
    for r in second:
        assert r.action == "skipped", f"{r.client_id} should be skipped on re-run"
