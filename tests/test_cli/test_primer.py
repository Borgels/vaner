# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from vaner.cli.commands.app import app
from vaner.cli.commands.primer import (
    PRIMER_BLOCK_END,
    PRIMER_SURFACES,
    PrimerScope,
    apply_primer_block,
    load_canonical_primer,
    primer_version,
    write_primer_for_client,
    write_primers,
)

# ---------------------------------------------------------------------------
# Merge primitive
# ---------------------------------------------------------------------------


def test_apply_primer_block_inserts_into_empty_content():
    result, action = apply_primer_block("", "primer body", version="1.0")
    assert action == "added"
    assert result.startswith("<!-- vaner-primer:start v=1.0 -->")
    assert result.rstrip().endswith(PRIMER_BLOCK_END)
    assert "primer body" in result


def test_apply_primer_block_appends_to_existing_content():
    existing = "# Project Notes\n\nexisting text\n"
    result, action = apply_primer_block(existing, "primer body", version="1.0")
    assert action == "added"
    # Existing content preserved verbatim at the top.
    assert result.startswith("# Project Notes\n\nexisting text\n")
    assert "<!-- vaner-primer:start" in result
    assert "primer body" in result


def test_apply_primer_block_replaces_existing_block():
    first, _ = apply_primer_block("# Notes\n", "first body", version="1.0")
    second, action = apply_primer_block(first, "second body", version="2.0")
    assert action == "updated"
    # Only one block present.
    assert second.count("vaner-primer:start") == 1
    assert "second body" in second
    assert "first body" not in second
    assert "v=2.0" in second
    # Surrounding content still intact.
    assert second.startswith("# Notes\n")


def test_apply_primer_block_idempotent_on_identical_rewrite():
    first, _ = apply_primer_block("# Notes\n", "same body", version="1.0")
    second, action = apply_primer_block(first, "same body", version="1.0")
    assert action == "skipped"
    assert second == first


def test_apply_primer_block_preserves_content_outside_block():
    before = "top\n"
    after_tail = "bottom\n"
    seeded, _ = apply_primer_block(before, "BODY_V1", version="1.0")
    user_edit = seeded + after_tail
    rewritten, action = apply_primer_block(user_edit, "BODY_V2", version="2.0")
    assert action == "updated"
    # Both the top and the bottom user content survive the rewrite.
    assert rewritten.startswith("top\n")
    assert rewritten.rstrip().endswith("bottom")
    # Exactly one block, with new body and new version marker.
    assert rewritten.count("vaner-primer:start") == 1
    assert "BODY_V2" in rewritten
    assert "BODY_V1" not in rewritten
    assert "v=2.0" in rewritten


# ---------------------------------------------------------------------------
# Per-client writer
# ---------------------------------------------------------------------------


def test_write_primer_for_client_claude_code_repo_scope(temp_repo):
    result = write_primer_for_client("claude-code", temp_repo, scope=PrimerScope.REPO)
    assert result.action == "added"
    assert result.path == temp_repo / ".claude" / "CLAUDE.md"
    content = result.path.read_text(encoding="utf-8")
    assert "vaner-primer:start" in content
    assert "Using Vaner" in content


def test_write_primer_for_client_claude_code_user_scope(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    repo = tmp_path / "repo"
    repo.mkdir()
    result = write_primer_for_client("claude-code", repo, scope=PrimerScope.USER)
    assert result.action == "added"
    assert result.path == fake_home / ".claude" / "CLAUDE.md"


def test_write_primer_for_client_cursor_uses_mdc_with_frontmatter(temp_repo):
    result = write_primer_for_client("cursor", temp_repo)
    assert result.action == "added"
    assert result.path == temp_repo / ".cursor" / "rules" / "vaner.mdc"
    content = result.path.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "alwaysApply: true" in content
    assert "Using Vaner" in content


def test_write_primer_for_client_is_idempotent(temp_repo):
    first = write_primer_for_client("codex-cli", temp_repo)
    second = write_primer_for_client("codex-cli", temp_repo)
    assert first.action == "added"
    assert second.action == "skipped"
    assert first.path == second.path == temp_repo / "AGENTS.md"


def test_write_primer_preserves_existing_file_content(temp_repo):
    copilot = temp_repo / ".github" / "copilot-instructions.md"
    copilot.parent.mkdir(parents=True)
    copilot.write_text("# Repo rules\n\nCustom instruction 1.\n", encoding="utf-8")
    result = write_primer_for_client("vscode-copilot", temp_repo)
    assert result.action == "added"
    final = copilot.read_text(encoding="utf-8")
    # Existing content intact at the top.
    assert final.startswith("# Repo rules\n\nCustom instruction 1.\n")
    # Primer appended after.
    assert "vaner-primer:start" in final


def test_write_primer_rewrites_existing_block_without_duplicating(temp_repo):
    first = write_primer_for_client("cline", temp_repo)
    assert first.action == "added"
    # Re-run with a different version — should replace in place.
    second = write_primer_for_client(
        "cline",
        temp_repo,
        body="CUSTOM PRIMER",
        version="bumped",
    )
    assert second.action == "updated"
    content = (temp_repo / ".clinerules").read_text(encoding="utf-8")
    assert content.count("vaner-primer:start") == 1
    assert "v=bumped" in content
    assert "CUSTOM PRIMER" in content
    assert "Using Vaner" not in content  # canonical body replaced


def test_write_primer_user_content_above_and_below_block_survives_rewrite(temp_repo):
    agents = temp_repo / "AGENTS.md"
    agents.write_text("# Project Agents\n\nabove-rule\n", encoding="utf-8")
    first = write_primer_for_client("codex-cli", temp_repo, body="v1", version="1")
    assert first.action == "added"
    # Simulate user adding content after the block.
    content = agents.read_text(encoding="utf-8") + "\nbelow-rule\n"
    agents.write_text(content, encoding="utf-8")
    # Rewrite with new body + version.
    second = write_primer_for_client("codex-cli", temp_repo, body="v2", version="2")
    assert second.action == "updated"
    rewritten = agents.read_text(encoding="utf-8")
    assert rewritten.startswith("# Project Agents\n\nabove-rule\n")
    assert "below-rule" in rewritten
    assert "v2" in rewritten
    assert rewritten.count("vaner-primer:start") == 1


def test_write_primer_unsupported_client_returns_unsupported(temp_repo):
    result = write_primer_for_client("claude-desktop", temp_repo)
    assert result.action == "unsupported"
    assert result.path is None


def test_write_primer_user_scope_unsupported_for_non_claude_code(temp_repo):
    # Cursor only has a repo-scope surface.
    result = write_primer_for_client("cursor", temp_repo, scope=PrimerScope.USER)
    assert result.action == "unsupported"


# ---------------------------------------------------------------------------
# Batch write_primers + init integration
# ---------------------------------------------------------------------------


def test_write_primers_writes_all_supported_clients(temp_repo):
    results = write_primers(sorted(PRIMER_SURFACES.keys()), temp_repo)
    written = {r.client_id: r for r in results}
    assert written["claude-code"].action == "added"
    assert written["cursor"].action == "added"
    assert written["vscode-copilot"].action == "added"
    assert written["codex-cli"].action == "added"
    assert written["cline"].action == "added"
    assert written["continue"].action == "added"


def test_write_primers_user_scope_only_touches_claude_code(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    repo = tmp_path / "repo"
    repo.mkdir()
    results = write_primers(sorted(PRIMER_SURFACES.keys()), repo, include_user_scope=True)
    user_results = [r for r in results if r.scope == PrimerScope.USER]
    # Only claude-code has a user-scope surface.
    assert len(user_results) == 1
    assert user_results[0].client_id == "claude-code"
    assert user_results[0].path == fake_home / ".claude" / "CLAUDE.md"
    assert user_results[0].action == "added"


def test_init_writes_primers_by_default(temp_repo, monkeypatch):
    fake_home = temp_repo / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["init", "--path", str(temp_repo), "--no-interactive", "--no-mcp"],
    )
    assert result.exit_code == 0, result.output
    assert (temp_repo / ".claude" / "CLAUDE.md").exists()
    assert (temp_repo / "AGENTS.md").exists()
    assert (temp_repo / ".cursor" / "rules" / "vaner.mdc").exists()
    # User-scope Claude file should NOT be created by default.
    assert not (fake_home / ".claude" / "CLAUDE.md").exists()


def test_init_no_primer_flag_skips_primer_writes(temp_repo):
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["init", "--path", str(temp_repo), "--no-interactive", "--no-mcp", "--no-primer"],
    )
    assert result.exit_code == 0, result.output
    assert not (temp_repo / ".claude" / "CLAUDE.md").exists()
    assert not (temp_repo / "AGENTS.md").exists()
    assert not (temp_repo / ".cursor" / "rules" / "vaner.mdc").exists()


def test_init_user_primer_flag_also_writes_user_scope_claude_code(temp_repo, monkeypatch):
    fake_home = temp_repo / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "init",
            "--path",
            str(temp_repo),
            "--no-interactive",
            "--no-mcp",
            "--user-primer",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (temp_repo / ".claude" / "CLAUDE.md").exists()
    assert (fake_home / ".claude" / "CLAUDE.md").exists()


# ---------------------------------------------------------------------------
# Canonical loader
# ---------------------------------------------------------------------------


def test_load_canonical_primer_reads_shipped_file():
    body = load_canonical_primer()
    assert body.strip().startswith("# Using Vaner")
    # Anchored on the user-provided principle.
    assert "reduce uncertainty" in body
    assert "do not call it mechanically" in body.lower()


def test_primer_version_tracks_package_version():
    assert primer_version() == __import__("vaner").__version__
