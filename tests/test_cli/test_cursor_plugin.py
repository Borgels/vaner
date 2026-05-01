"""Tests for the Cursor plugin bundle at ``cursor-plugins/vaner/``.

Mirrors the spirit of the Claude Code plugin parity tests:

* Manifest exists, parses, and matches the package version.
* hooks.json declares both hooks and references the right scripts.
* Each hook script's stdin → stdout contract emits valid JSON in
  Cursor's hook output format (``additional_context`` /
  ``user_message`` keys).
* The bundled SKILL.md is byte-identical to the canonical copy
  (enforced by ``scripts/sync-plugin-skill.sh --check`` in CI; this
  test catches the drift earlier in dev).

Cursor 1.7+ docs: https://cursor.com/docs/hooks
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CURSOR_PLUGIN_DIR = REPO_ROOT / "cursor-plugins" / "vaner"
CANONICAL_SKILL = REPO_ROOT / "src" / "vaner" / "defaults" / "skills" / "vaner-feedback" / "SKILL.md"


# ---------------------------------------------------------------------------
# Manifest + bundled assets
# ---------------------------------------------------------------------------


def test_manifest_parses_with_required_keys() -> None:
    manifest = json.loads((CURSOR_PLUGIN_DIR / ".cursor-plugin" / "plugin.json").read_text())
    for key in ("name", "version", "description", "author", "license"):
        assert key in manifest, f"manifest missing required key: {key}"
    assert manifest["name"] == "vaner"


def test_manifest_version_matches_package_version() -> None:
    """Version parity gate (also enforced by scripts/bump-plugin-version.sh).

    The plugin's version must match ``pyproject.toml``'s; otherwise users
    end up on the wrong tool surface."""

    import tomllib

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    expected = pyproject["project"]["version"]
    manifest = json.loads((CURSOR_PLUGIN_DIR / ".cursor-plugin" / "plugin.json").read_text())
    assert manifest["version"] == expected, "Cursor plugin version drift; run scripts/bump-plugin-version.sh --write"


def test_skill_is_byte_identical_to_canonical() -> None:
    """The bundled vaner-feedback SKILL.md must match the canonical
    copy under src/vaner/defaults/skills/. Drift means CI's
    sync-plugin-skill.sh check fails — catching it here keeps the
    feedback loop short."""

    bundled = (CURSOR_PLUGIN_DIR / "skills" / "vaner-feedback" / "SKILL.md").read_bytes()
    canonical = CANONICAL_SKILL.read_bytes()
    assert bundled == canonical, "Cursor plugin's SKILL.md is out of sync; run scripts/sync-plugin-skill.sh --write"


def test_primer_rule_uses_always_apply_frontmatter() -> None:
    """The bundled primer rule must declare ``alwaysApply: true`` so
    Cursor injects it on every chat — without it, the rule wouldn't
    fire and the model would see no Vaner instruction."""

    text = (CURSOR_PLUGIN_DIR / "rules" / "vaner-primer.mdc").read_text(encoding="utf-8")
    assert text.startswith("---\n"), "rule file must start with YAML frontmatter"
    assert "alwaysApply: true" in text


# ---------------------------------------------------------------------------
# hooks.json contract
# ---------------------------------------------------------------------------


def test_hooks_json_declares_both_session_start_and_before_submit() -> None:
    hooks = json.loads((CURSOR_PLUGIN_DIR / "hooks.json").read_text())
    assert hooks["version"] == 1
    assert "sessionStart" in hooks["hooks"]
    assert "beforeSubmitPrompt" in hooks["hooks"]


def test_hooks_json_commands_reference_existing_scripts() -> None:
    hooks = json.loads((CURSOR_PLUGIN_DIR / "hooks.json").read_text())
    for event_name, entries in hooks["hooks"].items():
        for entry in entries:
            cmd = entry["command"]
            # The command list templates ``${CURSOR_PLUGIN_ROOT}`` —
            # resolve it locally for the existence check.
            resolved = [arg.replace("${CURSOR_PLUGIN_ROOT}", str(CURSOR_PLUGIN_DIR)) for arg in cmd]
            assert len(resolved) >= 2, f"{event_name} hook command shape unexpected: {cmd}"
            script_path = Path(resolved[1])
            assert script_path.exists(), f"{event_name} references non-existent script: {script_path}"


# ---------------------------------------------------------------------------
# Hook scripts — stdin/stdout contract
# ---------------------------------------------------------------------------


def _run_hook(script: Path, stdin_payload: dict, env: dict | None = None) -> dict:
    """Invoke a hook script and parse its stdout as JSON."""

    full_env = os.environ.copy()
    full_env["CURSOR_PLUGIN_ROOT"] = str(CURSOR_PLUGIN_DIR)
    if env:
        full_env.update(env)
    result = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(stdin_payload),
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
        timeout=8,
    )
    assert result.returncode == 0, f"{script.name} exited {result.returncode}; stderr: {result.stderr}"
    stdout = result.stdout.strip() or "{}"
    return json.loads(stdout)


def test_session_start_emits_additional_context_when_vaner_on_path(monkeypatch, tmp_path) -> None:
    """When ``vaner`` is on PATH, the hook injects the canonical primer
    so the model uses Vaner correctly. Cursor reads ``additional_context``
    from the hook's stdout."""

    # Stub a fake ``vaner`` so shutil.which finds it.
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "vaner").write_text("#!/bin/sh\nexit 0\n")
    (fake_bin / "vaner").chmod(0o755)
    env = {"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}

    out = _run_hook(
        CURSOR_PLUGIN_DIR / "scripts" / "session_start.py",
        stdin_payload={"event": "sessionStart"},
        env=env,
    )
    assert "additional_context" in out
    assert "Vaner" in out["additional_context"]
    assert "vaner.resolve" in out["additional_context"]


def test_session_start_emits_user_message_when_vaner_missing(tmp_path) -> None:
    """Without ``vaner`` on PATH, the hook surfaces installer pointers as
    ``user_message`` so the user sees why the bundled MCP isn't running."""

    # Empty PATH so shutil.which can't find vaner.
    env = {"PATH": str(tmp_path / "empty")}
    out = _run_hook(
        CURSOR_PLUGIN_DIR / "scripts" / "session_start.py",
        stdin_payload={"event": "sessionStart"},
        env=env,
    )
    assert "user_message" in out
    assert "vaner.ai" in out["user_message"]


def test_before_submit_prompt_returns_empty_object_on_no_daemon() -> None:
    """The composer hook is observational — it never blocks. Even when
    the daemon is unreachable, it must exit 0 and emit a valid JSON
    object so Cursor doesn't reject the user's prompt."""

    out = _run_hook(
        CURSOR_PLUGIN_DIR / "scripts" / "before_submit_prompt.py",
        stdin_payload={"prompt": "test prompt", "session_id": "abc"},
        # Force the daemon URL to a port nothing's listening on.
        env={"VANER_DAEMON_URL": "http://127.0.0.1:9"},
    )
    assert out == {}


def test_before_submit_prompt_handles_empty_stdin() -> None:
    """Cursor may invoke the hook with an empty payload during init or
    edge cases. The script must still produce a valid response."""

    out = _run_hook(
        CURSOR_PLUGIN_DIR / "scripts" / "before_submit_prompt.py",
        stdin_payload={},
        env={"VANER_DAEMON_URL": "http://127.0.0.1:9"},
    )
    assert out == {}


# ---------------------------------------------------------------------------
# MCP server entry
# ---------------------------------------------------------------------------


def test_mcp_json_declares_vaner_server() -> None:
    """The plugin bundles its MCP server entry so users get the full
    leverage stack (MCP + skill + primer + hooks) on a single install,
    matching the Claude Code plugin's atomic-bundle behaviour."""

    mcp = json.loads((CURSOR_PLUGIN_DIR / "mcp" / "mcp.json").read_text())
    assert "vaner" in mcp["mcpServers"]
    assert mcp["mcpServers"]["vaner"]["command"] == "vaner"


@pytest.mark.parametrize(
    "filename",
    [
        ".cursor-plugin/plugin.json",
        "hooks.json",
        "mcp/mcp.json",
        "rules/vaner-primer.mdc",
        "skills/vaner-feedback/SKILL.md",
        "scripts/session_start.py",
        "scripts/before_submit_prompt.py",
    ],
)
def test_required_files_exist(filename: str) -> None:
    """Catch accidental deletion or rename of any required plugin asset."""

    assert (CURSOR_PLUGIN_DIR / filename).exists(), f"missing required file: {filename}"
