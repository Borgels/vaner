# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from vaner import api
from vaner.cli.commands.explain import render_human, render_json


def inspect_cache(repo_root: Path) -> str:
    return api.inspect(repo_root)


def inspect_last(repo_root: Path, *, verbose: bool = False, as_json: bool = False) -> str:
    record = api.inspect_last_decision(repo_root)
    if record is not None:
        if as_json:
            return render_json(record)
        return render_human(record, verbose=verbose)
    return api.inspect_last(repo_root)


def inspect_decision(
    repo_root: Path,
    decision_id: str | None = None,
    *,
    verbose: bool = False,
    as_json: bool = False,
) -> str:
    record = api.inspect_decision(repo_root, decision_id)
    if record is None:
        return "No context decisions recorded yet."
    if as_json:
        return render_json(record)
    return render_human(record, verbose=verbose)


def list_decisions(repo_root: Path, *, limit: int = 20) -> str:
    ids = api.list_decisions(repo_root, limit=limit)
    if not ids:
        return "No context decisions recorded yet."
    return "\n".join(ids)
