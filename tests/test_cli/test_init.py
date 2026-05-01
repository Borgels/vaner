# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from vaner.cli.commands.app import app
from vaner.cli.commands.init import init_repo, write_mcp_configs


def test_init_creates_config(temp_repo):
    config_path = init_repo(temp_repo)
    assert config_path.exists()
    content = config_path.read_text(encoding="utf-8")
    assert "[gateway.passthrough]" in content
    assert "enabled = false" in content
    assert "[mcp]" in content
    assert "[compute]" in content
    assert "idle_only = true" in content
    assert "[intent]" in content
    assert "[intent.skills_loop]" in content


def test_init_writes_managed_feedback_skill(temp_repo, monkeypatch):
    """Phase C: skill writes moved out of ``write_mcp_configs`` into the
    dedicated :mod:`vaner.cli.commands.skills` module. The skills are
    now installed by ``write_skills`` (called from ``vaner init``)
    rather than the MCP-config writer.
    """

    from vaner.cli.commands.skills import SKILL_SURFACES, write_skills

    fake_home = temp_repo / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    results = write_skills(sorted(SKILL_SURFACES.keys()), temp_repo)
    by_id = {r.client_id: r for r in results}
    # The two surfaces this test originally guarded — Cursor (repo) and
    # Claude Code (user) — must still get written.
    assert (temp_repo / ".cursor" / "skills" / "vaner" / "vaner-feedback" / "SKILL.md").exists()
    assert (fake_home / ".claude" / "skills" / "vaner" / "vaner-feedback" / "SKILL.md").exists()
    assert by_id["cursor"].action == "added"
    assert by_id["claude-code"].action == "added"


def test_write_mcp_configs_prefers_absolute_vaner_binary(temp_repo, monkeypatch):
    fake_home = temp_repo / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setattr("vaner.cli.commands.init.shutil.which", lambda name: "/usr/local/bin/vaner" if name == "vaner" else None)
    written, launcher = write_mcp_configs(temp_repo)
    assert written
    assert launcher == "/usr/local/bin/vaner"
    cursor_payload = (temp_repo / ".cursor" / "mcp.json").read_text(encoding="utf-8")
    assert "/usr/local/bin/vaner" in cursor_payload


def test_write_mcp_configs_writes_single_current_feedback_skill(temp_repo, monkeypatch):
    """Phase C: the skill content is now produced by the dedicated
    skills module. This test still guards the *content* of the skill
    that lands at the Cursor location — Agent Skills frontmatter
    delimiters, current MCP tool names, no legacy tool references.
    """

    from vaner.cli.commands.skills import write_skill_for_client

    fake_home = temp_repo / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    write_skill_for_client("cursor", temp_repo)
    repo_skill = (temp_repo / ".cursor" / "skills" / "vaner" / "vaner-feedback" / "SKILL.md").read_text(encoding="utf-8")

    assert repo_skill.count("---") == 2
    assert "vaner.feedback" in repo_skill
    assert "report_outcome" not in repo_skill
    assert "list_scenarios" not in repo_skill


def test_uninstall_removes_repo_cursor_wiring_and_skills(temp_repo, monkeypatch):
    fake_home = temp_repo / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    write_mcp_configs(temp_repo)
    runner = CliRunner()
    result = runner.invoke(app, ["uninstall", "--path", str(temp_repo)])

    assert result.exit_code == 0

    repo_mcp = temp_repo / ".cursor" / "mcp.json"
    assert not repo_mcp.exists() or "vaner" not in json.loads(repo_mcp.read_text(encoding="utf-8")).get("mcpServers", {})
    assert not (temp_repo / ".cursor" / "skills" / "vaner" / "vaner-feedback" / "SKILL.md").exists()
    assert not (fake_home / ".claude" / "skills" / "vaner" / "vaner-feedback" / "SKILL.md").exists()
