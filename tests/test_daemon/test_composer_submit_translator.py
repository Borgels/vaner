# SPDX-License-Identifier: Apache-2.0
"""Tests for the Claude Code UserPromptSubmit translator script — 0.8.7 WS7.

The translator (plugins/vaner/scripts/composer_submit.py) reads a
Claude Code hook payload from stdin, builds a DraftIntentSnapshot,
and POSTs it to the daemon. We exercise the translator end-to-end by:

1. Spinning up an in-process daemon HTTP app via TestClient.
2. Invoking the translator as a subprocess with a synthetic stdin.
3. Asserting the daemon received the right snapshot.

The translator's failure modes (no prompt field, daemon down, bad
JSON) MUST exit 0 so a misconfigured daemon never blocks the user's
prompt.
"""

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
    pytest.skip("translator subprocess is flaky on Windows runners", allow_module_level=True)


REPO_ROOT = Path(__file__).resolve().parents[2]
TRANSLATOR = REPO_ROOT / "plugins" / "vaner" / "scripts" / "composer_submit.py"


@dataclass
class _ReceivedRequest:
    method: str
    path: str
    body_json: dict | None
    response_status: int = 200


class _SilentHandler(WSGIRequestHandler):
    def log_message(self, format, *args):  # noqa: A002, ARG002
        pass


class _StubDaemon:
    """Minimal HTTP server that captures POSTs to /signals/composer.

    Avoids dragging FastAPI/TestClient into a subprocess scenario — the
    translator hits a real port via urllib.
    """

    def __init__(self) -> None:
        self.received: list[_ReceivedRequest] = []
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
        method = environ["REQUEST_METHOD"]
        path = environ["PATH_INFO"]
        body_bytes = b""
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length:
            body_bytes = environ["wsgi.input"].read(length)
        try:
            body_json = json.loads(body_bytes.decode("utf-8")) if body_bytes else None
        except json.JSONDecodeError:
            body_json = None
        self.received.append(_ReceivedRequest(method=method, path=path, body_json=body_json))
        start_response("200 OK", [("Content-Type", "application/json")])
        return [b'{"composer_event_id": "evt-stub"}']


def _run_translator(*, port: int, stdin_payload: dict | str) -> tuple[int, str, str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "VANER_DAEMON_PORT": str(port),
    }
    stdin_data = (stdin_payload if isinstance(stdin_payload, str) else json.dumps(stdin_payload)).encode("utf-8")
    result = subprocess.run(
        [sys.executable, str(TRANSLATOR)],
        input=stdin_data,
        env=env,
        capture_output=True,
        timeout=10,
        check=False,
    )
    return result.returncode, result.stdout.decode(), result.stderr.decode()


def test_translator_posts_snapshot_with_text_hash(tmp_path):
    prompt = "rewrite this scene so the tension rises more slowly"
    expected_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    with _StubDaemon() as daemon:
        rc, _stdout, _stderr = _run_translator(
            port=daemon.port,
            stdin_payload={
                "prompt": prompt,
                "session_id": "session-abc",
                "cwd": str(tmp_path),
            },
        )

    assert rc == 0
    assert len(daemon.received) == 1
    req = daemon.received[0]
    assert req.method == "POST"
    assert req.path == "/signals/composer"
    body = req.body_json
    assert body is not None
    assert body["session_id"] == "session-abc"
    assert body["lifecycle_state"] == "submitted"
    assert body["text_hash"] == expected_hash
    assert body["length_chars"] == len(prompt)
    assert body["capabilities"]["level"] == "L0"
    assert body["capabilities"]["host_app"] == "claude-code"
    assert body["capabilities"]["emits"] == ["submitted"]
    # Privacy invariant: raw prompt MUST NOT be in the payload.
    assert prompt not in json.dumps(body)


def test_translator_handles_missing_prompt_silently(tmp_path):
    with _StubDaemon() as daemon:
        rc, _stdout, _stderr = _run_translator(
            port=daemon.port,
            stdin_payload={"session_id": "session-abc"},
        )

    assert rc == 0
    # No POST should have happened.
    assert daemon.received == []


def test_translator_handles_empty_stdin_silently(tmp_path):
    with _StubDaemon() as daemon:
        rc, _stdout, _stderr = _run_translator(
            port=daemon.port,
            stdin_payload="",
        )

    assert rc == 0
    assert daemon.received == []


def test_translator_handles_malformed_stdin_silently(tmp_path):
    with _StubDaemon() as daemon:
        rc, _stdout, _stderr = _run_translator(
            port=daemon.port,
            stdin_payload="not-json{",
        )

    assert rc == 0
    assert daemon.received == []


def test_translator_silent_when_daemon_unreachable(tmp_path):
    # No stub daemon on this port — POST will fail.
    rc, _stdout, _stderr = _run_translator(
        port=1,  # privileged, refused
        stdin_payload={"prompt": "test", "session_id": "s"},
    )
    # MUST exit 0 so the user's prompt isn't blocked. May log to stderr.
    assert rc == 0
