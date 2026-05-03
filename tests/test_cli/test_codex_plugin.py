# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

if platform.system().lower().startswith("win"):
    pytest.skip("hook subprocess tests are flaky on Windows runners", allow_module_level=True)

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "plugins" / "vaner-codex"


def test_codex_plugin_manifest_and_hooks_parse() -> None:
    manifest = json.loads((PLUGIN_DIR / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "vaner-codex"
    assert manifest["hooks"] == "./hooks.json"
    assert manifest["mcpServers"] == "./.mcp.json"

    hooks = json.loads((PLUGIN_DIR / "hooks.json").read_text(encoding="utf-8"))
    for event in ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"):
        assert event in hooks["hooks"]

    mcp = json.loads((PLUGIN_DIR / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["vaner"]["command"] == "vaner"


def test_codex_plugin_version_matches_package() -> None:
    import tomllib

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = json.loads((PLUGIN_DIR / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == pyproject["project"]["version"]


def test_codex_plugin_install_refreshes_existing_bundle_and_global_hooks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    target = home / ".codex" / "plugins" / "vaner-codex"
    (target / ".codex-plugin").mkdir(parents=True)
    (target / ".codex-plugin" / "plugin.json").write_text(
        (PLUGIN_DIR / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (target / "scripts").mkdir()
    (target / "scripts" / "codex_tool_event.py").write_text("# stale\n", encoding="utf-8")
    (home / ".codex" / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"matcher": "*", "hooks": [{"type": "command", "command": "echo user hook", "timeout": 1}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    from vaner.cli.commands.plugins import write_plugin_for_client

    result = write_plugin_for_client("codex-cli")

    assert result.action == "updated"
    assert (target / "scripts" / "codex_tool_event.py").read_text(encoding="utf-8") == (
        PLUGIN_DIR / "scripts" / "codex_tool_event.py"
    ).read_text(encoding="utf-8")
    config = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert '[plugins."vaner-codex@vaner-local"]' in config
    assert "codex_hooks = true" in config
    hooks = json.loads((home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entry in hooks["hooks"]["UserPromptSubmit"]
        for hook in entry["hooks"]
    ]
    assert "echo user hook" in commands
    assert any("codex_prompt_submit.py" in command for command in commands)


@dataclass
class _Received:
    body: dict[str, object]


class _SilentHandler(WSGIRequestHandler):
    def log_message(self, format, *args):  # noqa: A002, ARG002
        pass


class _StubDaemon:
    def __init__(self) -> None:
        self.received: list[_Received] = []
        self._server = make_server("127.0.0.1", 0, self._handler, handler_class=_SilentHandler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _StubDaemon:
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _handler(self, environ, start_response):
        length = int(environ.get("CONTENT_LENGTH") or 0)
        body = json.loads(environ["wsgi.input"].read(length).decode("utf-8"))
        self.received.append(_Received(body=body))
        start_response("200 OK", [("Content-Type", "application/json")])
        return [b'{"ok": true}']


def test_codex_prompt_hook_redacts_and_posts_to_loopback() -> None:
    prompt = "Implement auth with api_key=secret123 and email ops@example.com"
    with _StubDaemon() as daemon:
        result = subprocess.run(
            [sys.executable, str(PLUGIN_DIR / "scripts" / "codex_prompt_submit.py")],
            input=json.dumps({"prompt": prompt, "session_id": "s1", "turn_id": "t1", "cwd": "/repo"}),
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
            env={"VANER_DAEMON_URL": f"http://127.0.0.1:{daemon.port}"},
        )

    assert result.returncode == 0
    assert json.loads(result.stdout or "{}") == {}
    assert len(daemon.received) == 1
    body = daemon.received[0].body
    assert body["session_id"] == "s1"
    assert body["turn_id"] == "t1"
    assert body["prompt_hash"] == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert "secret123" not in json.dumps(body)
    assert "ops@example.com" not in json.dumps(body)
    assert body["capture_policy"] == "local_raw_redacted"


def test_codex_prompt_hook_noops_when_daemon_down() -> None:
    result = subprocess.run(
        [sys.executable, str(PLUGIN_DIR / "scripts" / "codex_prompt_submit.py")],
        input=json.dumps({"prompt": "test", "session_id": "s1"}),
        text=True,
        capture_output=True,
        timeout=8,
        check=False,
        env={"VANER_DAEMON_URL": "http://127.0.0.1:9"},
    )
    assert result.returncode == 0
    assert json.loads(result.stdout or "{}") == {}
