# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

from vaner.broker.assembler import assemble_context_package
from vaner.models.artefact import Artefact, ArtefactKind


def _artefact(key: str, source_path: str, content: str, generated_at: float | None = None) -> Artefact:
    now = time.time()
    return Artefact(
        key=key,
        kind=ArtefactKind.FILE_SUMMARY,
        source_path=source_path,
        source_mtime=now,
        generated_at=generated_at if generated_at is not None else now,
        model="test",
        content=content,
    )


def test_assembler_only_reports_kept_artefacts():
    artefacts = [
        _artefact("a", "a.py", "alpha one two three"),
        _artefact("b", "b.py", "beta " * 200),
    ]
    package = assemble_context_package("alpha", artefacts, max_tokens=120)

    assert package.token_used <= package.token_budget
    assert len(package.selections) == 1
    assert package.selections[0].artefact_key == "a"
    assert "a.py" in package.injected_context
    assert "b.py" not in package.injected_context


def test_assembler_sets_stale_flag_from_age():
    artefact = _artefact("old", "old.py", "content", generated_at=time.time() - 10_000)
    package = assemble_context_package("old", [artefact], max_tokens=400, max_age_seconds=60)
    assert package.selections[0].stale is True
