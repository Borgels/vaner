"""Backward-compatible API module alias to ``vaner.server``."""

from __future__ import annotations

from vaner import server as _server


def __getattr__(name: str) -> object:
    return getattr(_server, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_server)))
