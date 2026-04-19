# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from pathlib import Path

from vaner.cli.commands.config import load_config
from vaner.engine import build_default_engine

_SUPPORTED_PIN_HINTS = {"prefer_source", "focus_paths", "avoid_paths"}


def _to_scoring_hint(key: str, value: str) -> dict[str, object] | None:
    if key in _SUPPORTED_PIN_HINTS:
        return {"kind": key, "target": value}
    return None


def _format_pin_line(row: dict[str, object]) -> str:
    scope = str(row.get("scope", "user"))
    key = str(row.get("key", ""))
    value = str(row.get("value", ""))
    if scope == "user":
        return f"{key}={value}"
    return f"{scope}:{key}={value}"


def _parse_pin_line(line: str) -> dict[str, object] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if "=" not in stripped:
        raise ValueError(f"Invalid pin line (missing '='): {line}")
    left, value = stripped.split("=", 1)
    left = left.strip()
    value = value.strip()
    scope = "user"
    key = left
    if ":" in left:
        scope_candidate, key_candidate = left.split(":", 1)
        scope = scope_candidate.strip().lower() or "user"
        key = key_candidate.strip()
    key = key.strip()
    if not key:
        raise ValueError(f"Invalid pin line (empty key): {line}")
    return {
        "key": key,
        "value": value,
        "scope": scope,
        "scoring_hint": _to_scoring_hint(key, value),
    }


async def aprofile_show(repo_root: Path) -> dict[str, object]:
    config = load_config(repo_root)
    engine = build_default_engine(repo_root, config)
    await engine.initialize()
    store = engine.store
    pins = await store.list_pinned_facts()
    macros = await store.list_prompt_macros(limit=10)
    transitions = await store.list_habit_transitions(limit=10)
    phase = await store.get_workflow_phase_summary()
    explored = [
        {
            "source": item.source,
            "anchor": item.anchor,
            "reason": item.reason,
            "priority": item.priority,
            "depth": item.depth,
            "unit_ids": item.unit_ids,
            "cached": item.cached,
        }
        for item in engine.get_explored_scenarios()
    ]
    return {
        "pins": pins,
        "prompt_macros": macros,
        "habit_transitions": transitions,
        "workflow_phase": phase,
        "explored_scenarios": explored,
    }


async def apin_fact(repo_root: Path, assignment: str, *, scope: str = "user") -> dict[str, object]:
    if "=" not in assignment:
        raise ValueError("Pin must be KEY=VALUE")
    key, value = assignment.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        raise ValueError("Pin key must not be empty")
    config = load_config(repo_root)
    engine = build_default_engine(repo_root, config)
    await engine.initialize()
    await engine.store.upsert_pinned_fact(
        key=key,
        value=value,
        scope=scope,
        scoring_hint=_to_scoring_hint(key, value),
    )
    engine.invalidate_pinned_facts()
    return {"key": key, "value": value, "scope": scope}


async def aunpin_fact(repo_root: Path, key: str) -> bool:
    config = load_config(repo_root)
    engine = build_default_engine(repo_root, config)
    await engine.initialize()
    removed = await engine.store.remove_pinned_fact(key)
    engine.invalidate_pinned_facts()
    return removed


async def aexport_pins(repo_root: Path, out_path: Path | None = None) -> Path:
    config = load_config(repo_root)
    engine = build_default_engine(repo_root, config)
    await engine.initialize()
    pins = await engine.store.list_pinned_facts()
    output = out_path if out_path is not None else (repo_root / ".vaner" / "pinned_facts.env")
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Vaner pinned profile facts"]
    lines.extend(_format_pin_line(row) for row in sorted(pins, key=lambda row: str(row.get("key", ""))))
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


async def aimport_pins(repo_root: Path, import_path: Path) -> int:
    if not import_path.exists():
        raise FileNotFoundError(import_path)
    config = load_config(repo_root)
    engine = build_default_engine(repo_root, config)
    await engine.initialize()
    rows: list[dict[str, object]] = []
    for line in import_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_pin_line(line)
        if parsed is not None:
            rows.append(parsed)
    await engine.store.replace_pinned_facts(rows)
    engine.invalidate_pinned_facts()
    return len(rows)


def profile_show(repo_root: Path) -> dict[str, object]:
    return asyncio.run(aprofile_show(repo_root))


def pin_fact(repo_root: Path, assignment: str, *, scope: str = "user") -> dict[str, object]:
    return asyncio.run(apin_fact(repo_root, assignment, scope=scope))


def unpin_fact(repo_root: Path, key: str) -> bool:
    return asyncio.run(aunpin_fact(repo_root, key))


def export_pins(repo_root: Path, out_path: Path | None = None) -> Path:
    return asyncio.run(aexport_pins(repo_root, out_path=out_path))


def import_pins(repo_root: Path, import_path: Path) -> int:
    return asyncio.run(aimport_pins(repo_root, import_path))
