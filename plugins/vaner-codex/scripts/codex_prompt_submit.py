#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Codex UserPromptSubmit hook for Vaner.

The hook redacts prompt text locally, hashes the original text for dedupe,
and posts only to the local Vaner daemon. It exits 0 on every failure path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime

DAEMON_URL = os.environ.get("VANER_DAEMON_URL", "http://127.0.0.1:8473")
HTTP_TIMEOUT_SECONDS = 1.0


def _read_payload() -> dict[str, object]:
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _string(payload: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _redact(text: str) -> str:
    redacted = re.sub(
        r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]\s*['\"]?[^\s'\"&]+",
        r"\1=[REDACTED]",
        text,
    )
    redacted = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]", redacted)
    redacted = re.sub(r"\b(?:sk|pk|ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{16,}\b", "[REDACTED_TOKEN]", redacted)
    return redacted


def _build_signal(payload: dict[str, object]) -> dict[str, object] | None:
    prompt = _string(payload, "prompt", "input", "message", "text")
    if not prompt:
        return None
    session_id = _string(payload, "session_id", "sessionId", "conversation_id", "conversationId") or f"codex-{uuid.uuid4().hex[:8]}"
    turn_id = _string(payload, "turn_id", "turnId", "submission_id", "submissionId")
    cwd = _string(payload, "cwd", "workspace", "workspace_id", "workspaceId")
    model = _string(payload, "model")
    return {
        "host_app": "codex-cli",
        "session_id": session_id,
        "turn_id": turn_id or None,
        "workspace_id": cwd or None,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "model": model or None,
        "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_text_redacted": _redact(prompt),
        "length_chars": len(prompt),
        "capture_policy": "local_raw_redacted",
        "source_event_id": uuid.uuid4().hex,
    }


def _post(signal: dict[str, object]) -> None:
    body = json.dumps(signal).encode("utf-8")
    request = urllib.request.Request(
        DAEMON_URL.rstrip("/") + "/signals/codex/prompt",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS)  # noqa: S310 - loopback by default
    except (urllib.error.URLError, TimeoutError, OSError):
        return


def main() -> int:
    signal = _build_signal(_read_payload())
    if signal is not None:
        _post(signal)
    sys.stdout.write("{}\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        raise SystemExit(0) from None
