#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Best-effort Codex tool/stop lifecycle event hook for Vaner."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

DAEMON_URL = os.environ.get("VANER_DAEMON_URL", "http://127.0.0.1:8473")
HTTP_TIMEOUT_SECONDS = 0.8


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


def _post(payload: dict[str, object]) -> None:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        DAEMON_URL.rstrip("/") + "/signals/codex/activity",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS)  # noqa: S310 - loopback by default
    except (urllib.error.URLError, TimeoutError, OSError):
        return


def main() -> int:
    payload = _read_payload()
    payload.setdefault("source_event_id", uuid.uuid4().hex)
    _post(payload)
    sys.stdout.write("{}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
