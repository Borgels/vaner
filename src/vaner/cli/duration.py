# SPDX-License-Identifier: Apache-2.0
"""WS9 — Shared duration / "tonight" parsing helper (0.8.6).

Extracted from :mod:`vaner.cli.commands.deep_run` so desktop apps and
other CLI surfaces can reuse the same syntax. Behaviour is byte-
identical to the original :func:`_parse_until` — same accepted forms,
same error messages, same edge cases. Tests in
:mod:`tests.test_cli.test_deep_run` pin both signature and behaviour.

Accepted forms:
- duration: ``30s`` / ``45m`` / ``8h`` / ``2d``
- time of day: ``07:00`` (next occurrence; tomorrow if already past today)
- ISO-8601: ``2026-04-25T07:00:00`` (naive ISO-8601 is interpreted in UTC)

Raises :class:`typer.BadParameter` on parse error so the CLI shows a
clean error message rather than a stack trace. Non-CLI consumers can
catch the same exception or run the same regexes locally.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime

import typer

__all__ = ["parse_until"]


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_TIMEOFDAY_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def parse_until(spec: str, *, now: float | None = None) -> float:
    """Parse a window-end specifier into an absolute epoch timestamp.

    See module docstring for the accepted syntax. ``now`` is injectable
    for deterministic tests; in production callers always omit it and
    the helper falls back to :func:`time.time`.
    """

    base_ts = now if now is not None else time.time()
    text = spec.strip()
    if not text:
        raise typer.BadParameter("--until cannot be empty")

    m = _DURATION_RE.match(text)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * amount
        if seconds <= 0:
            raise typer.BadParameter(f"--until {spec!r} is non-positive")
        return base_ts + float(seconds)

    m = _TIMEOFDAY_RE.match(text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise typer.BadParameter(f"--until {spec!r} is not a valid time")
        now_dt = datetime.fromtimestamp(base_ts).astimezone()
        target = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target.timestamp() <= base_ts:
            target = target.replace(day=target.day + 1)
        return target.timestamp()

    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise typer.BadParameter(f"--until {spec!r} is not a recognised duration / time / ISO-8601") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC).astimezone()
    return dt.timestamp()
