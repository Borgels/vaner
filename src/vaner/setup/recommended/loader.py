# SPDX-License-Identifier: Apache-2.0
"""Disk loader for the recommended-models registry.

The registry data file (``data.json``) is generated at release time
by ``scripts/refresh_recommended_models.py`` and is *gitignored* —
development checkouts will not have it until the script is run. The
loader handles the missing-file case gracefully so the rest of the
daemon can boot regardless.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from vaner.setup.recommended.schema import Registry

logger = logging.getLogger(__name__)


_REGISTRY_FILENAME = "data.json"
_PACKAGE_DIR = Path(__file__).resolve().parent


def _default_path() -> Path:
    """Return the standard registry path (next to this module)."""
    return _PACKAGE_DIR / _REGISTRY_FILENAME


def _empty_registry() -> Registry:
    """Build a registry with no models — the safe fallback."""
    return Registry(
        schema_version=1,
        generated_at=datetime.now(tz=UTC),
        generator="loader.py:empty-fallback",
        sources=(),
        models=(),
    )


def load_registry(path: Path | None = None) -> Registry:
    """Read the registry from disk; return an empty registry on miss.

    The function is fail-safe by design — a missing file, malformed
    JSON, or a schema mismatch all log at WARNING and yield an empty
    :class:`Registry` rather than raising. The desktop wizard's
    Recommended-preset card reads ``len(registry.models) == 0`` as
    "no recommendation; show fallback copy."

    Pass ``path`` to load from a non-default location (used by tests
    and the refresh script for `--source-snapshot` reproducibility).
    """
    target = path or _default_path()
    if not target.exists():
        logger.info(
            "recommended-models registry missing at %s; returning empty registry",
            target,
        )
        return _empty_registry()
    try:
        raw = target.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("failed to read registry %s: %s", target, exc)
        return _empty_registry()
    try:
        return Registry.model_validate(payload)
    except ValidationError as exc:
        logger.warning("registry %s failed schema validation: %s", target, exc)
        return _empty_registry()


__all__ = ["load_registry"]
