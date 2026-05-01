# SPDX-License-Identifier: Apache-2.0

"""Tests for the per-client hook installer (Phase C4 of the IDE/agent
visibility plan).

Layer 4 of the leverage stack documented at
docs.vaner.ai/integrations/client-capabilities. Today this module
ships prompt-submit hooks for Cline and Windsurf — the two clients
with stable hook APIs that aren't already covered by an atomic plugin
bundle.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from vaner.cli.commands.hooks import (
    HOOK_SURFACES,
    canonical_script_path,
    hook_version,
    write_hook_for_client,
    write_hooks,
)

# ---------------------------------------------------------------------------
# Canonical script
# ---------------------------------------------------------------------------


def test_canonical_script_exists_and_is_python() -> None:
    """The composer-submit script must ship with the package; the
    installers point per-client config at this exact path."""

    script = canonical_script_path()
    assert script.exists()
    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env python3")
    # The script targets the daemon's /signals/composer endpoint.
    assert "/signals/composer" in text


def test_hook_version_is_non_empty() -> None:
    assert hook_version()


# ---------------------------------------------------------------------------
# Per-client writer
# ---------------------------------------------------------------------------


def test_write_hook_cline_drops_executable_at_user_prompt_submit(tmp_path: Path) -> None:
    """Cline scans ``.clinerules/hooks/`` for executable scripts; the
    filename maps to the event. We ship ``UserPromptSubmit`` and chmod
    +x so the OS can run it."""

    repo = tmp_path / "repo"
    repo.mkdir()
    result = write_hook_for_client("cline", repo)
    assert result.action == "added"
    assert result.path == repo / ".clinerules" / "hooks" / "UserPromptSubmit"
    assert result.path.exists()
    # Executable bit must be set so Cline can dispatch.
    assert os.access(result.path, os.X_OK), "Cline hook must be executable"
    text = result.path.read_text(encoding="utf-8")
    assert "/signals/composer" in text


def test_write_hook_windsurf_writes_hooks_json(tmp_path: Path) -> None:
    """Windsurf's hook surface is a JSON config that references a
    shell command. We invoke the canonical script via python3 so the
    file format stays portable."""

    repo = tmp_path / "repo"
    repo.mkdir()
    result = write_hook_for_client("windsurf", repo)
    assert result.action == "added"
    assert result.path == repo / ".windsurf" / "hooks.json"
    payload = json.loads(result.path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert "user_prompt" in payload["hooks"]
    command = payload["hooks"]["user_prompt"][0]["command"]
    # The command list invokes python3 against the canonical script.
    assert command[0] == "python3"
    assert command[1] == str(canonical_script_path())
    # Observational hook, never blocks.
    assert payload["hooks"]["user_prompt"][0]["fail_closed"] is False


def test_write_hook_unsupported_client_returns_unsupported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = write_hook_for_client("nope-some-client", repo)
    assert result.action == "unsupported"
    assert result.path is None


def test_write_hook_is_idempotent(tmp_path: Path) -> None:
    """Two runs in a row produce ``skipped`` — safe to call from
    re-runs of ``vaner init``."""

    repo = tmp_path / "repo"
    repo.mkdir()
    first = write_hook_for_client("cline", repo)
    second = write_hook_for_client("cline", repo)
    assert first.action == "added"
    assert second.action == "skipped"


def test_write_hook_dry_run_does_not_touch_disk(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = write_hook_for_client("cline", repo, dry_run=True)
    assert result.action == "added"
    assert result.path is not None
    assert not result.path.exists()


# ---------------------------------------------------------------------------
# Batch writer
# ---------------------------------------------------------------------------


def test_write_hooks_writes_both_cline_and_windsurf(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    results = write_hooks(sorted(HOOK_SURFACES.keys()), repo)
    by_id = {r.client_id: r for r in results}
    assert {"cline", "windsurf"} == set(by_id.keys())
    for client_id in ("cline", "windsurf"):
        assert by_id[client_id].action == "added"
        assert by_id[client_id].path is not None
        assert by_id[client_id].path.exists()


# ---------------------------------------------------------------------------
# End-to-end: the canonical script's stdin/stdout contract
# ---------------------------------------------------------------------------


def _run_script(stdin_payload: dict, env: dict | None = None) -> str:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    result = subprocess.run(
        [sys.executable, str(canonical_script_path())],
        input=json.dumps(stdin_payload),
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
        timeout=8,
    )
    assert result.returncode == 0, f"composer_submit.py exited {result.returncode}; stderr: {result.stderr}"
    return result.stdout


def test_canonical_script_returns_empty_object_on_no_daemon() -> None:
    """The hook is observational — must never block. Even when the
    daemon is unreachable, it exits 0 with ``{}`` so neither Cline
    nor Windsurf reject the user's prompt."""

    out = _run_script(
        {"prompt": "test prompt", "session_id": "abc"},
        env={"VANER_DAEMON_URL": "http://127.0.0.1:9"},
    )
    assert json.loads(out.strip()) == {}


def test_canonical_script_handles_empty_stdin() -> None:
    out = _run_script({}, env={"VANER_DAEMON_URL": "http://127.0.0.1:9"})
    assert json.loads(out.strip()) == {}


def test_canonical_script_accepts_user_prompt_key_alias() -> None:
    """Windsurf uses ``user_prompt``; Cline uses ``prompt``. The script
    must accept either so a single canonical file works for both."""

    out = _run_script(
        {"user_prompt": "windsurf-style payload", "task_id": "windsurf-123"},
        env={"VANER_DAEMON_URL": "http://127.0.0.1:9"},
    )
    assert json.loads(out.strip()) == {}


# ---------------------------------------------------------------------------
# Verify-layer integration
# ---------------------------------------------------------------------------


def test_verify_reports_plugin_applicable_for_cline_and_windsurf(tmp_path: Path) -> None:
    """After Phase C4, ``vaner clients verify`` reports the plugin
    layer as ``applicable`` for Cline and Windsurf (whose hook
    surfaces Vaner now writes), in addition to Claude Code and Cursor
    (which have full plugin bundles)."""

    from vaner.cli.commands.mcp_clients import verify_all

    repo = tmp_path / "repo"
    repo.mkdir()
    results = verify_all(repo)
    by_id = {r.client_id: r for r in results}
    assert by_id["cline"].layers["plugin"].applicable is True
    assert by_id["windsurf"].layers["plugin"].applicable is True
    assert by_id["claude-code"].layers["plugin"].applicable is True
    assert by_id["cursor"].layers["plugin"].applicable is True
