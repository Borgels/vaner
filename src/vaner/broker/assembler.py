# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from vaner.broker.compressor import compress_context
from vaner.broker.selector import score_artefact
from vaner.models.artefact import Artefact
from vaner.models.context import ContextPackage, ContextSelection
from vaner.policy.staleness import is_stale_timestamp


def _is_stale(artefact: Artefact, repo_root: Path | None, max_age_seconds: int) -> bool:
    if is_stale_timestamp(artefact.generated_at, max_age_seconds):
        return True
    if repo_root is None:
        return False
    source_abs = repo_root / artefact.source_path
    if source_abs.exists() and source_abs.stat().st_mtime > artefact.source_mtime:
        return True
    return False


def assemble_context_package(
    prompt: str,
    artefacts: list[Artefact],
    max_tokens: int,
    repo_root: Path | None = None,
    max_age_seconds: int = 3600,
) -> ContextPackage:
    injected_context, token_map, used, kept_keys = compress_context(artefacts, max_tokens=max_tokens)
    selections = [
        ContextSelection(
            artefact_key=artefact.key,
            source_path=artefact.source_path,
            score=score_artefact(prompt, artefact),
            stale=_is_stale(artefact, repo_root, max_age_seconds),
            token_count=token_map.get(artefact.key, 0),
            rationale="keyword_overlap",
        )
        for artefact in artefacts
        if artefact.key in kept_keys
    ]
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return ContextPackage(
        id=f"ctx_{prompt_hash[:12]}",
        prompt_hash=prompt_hash,
        assembled_at=time.time(),
        token_budget=max_tokens,
        token_used=used,
        selections=selections,
        injected_context=injected_context,
    )
