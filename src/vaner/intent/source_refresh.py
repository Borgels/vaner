# SPDX-License-Identifier: Apache-2.0
"""Refresh intent artefacts from configured local/client sources."""

from __future__ import annotations

import logging
from typing import Any

from vaner.intent.connectors import GitHubIssuesAdapter, GlobalClientPlansAdapter, MarkdownOutlineAdapter
from vaner.intent.connectors.local_plan import DEFAULT_ALLOWLIST, DEFAULT_EXCLUDELIST, LocalPlanAdapter
from vaner.intent.ingest.pipeline import ingest_artefact
from vaner.store.artefacts import ArtefactStore

_LOG = logging.getLogger(__name__)


async def refresh_intent_artefacts_from_sources(config: Any, store: ArtefactStore) -> int:
    """Discover configured intent artefact sources and ingest candidates.

    This is deliberately best-effort: one unreadable file or connector
    failure should not break a precompute cycle or a cockpit refresh.
    Returns the number of accepted artefacts, including unchanged
    re-observations.
    """

    sources_cfg = getattr(config, "sources", None)
    intent_cfg = getattr(sources_cfg, "intent_artefacts", None) if sources_cfg is not None else None
    if intent_cfg is None or not bool(getattr(intent_cfg, "enabled", True)):
        return 0

    adapters: list[Any] = []
    tiers = getattr(intent_cfg, "tiers", None)
    if str(getattr(tiers, "T1", "auto")) != "off":
        local_cfg = getattr(intent_cfg, "local_plan", None)
        allowlist = tuple(getattr(local_cfg, "allowlist", []) or ())
        excludelist = tuple(getattr(local_cfg, "excludelist", []) or ())
        adapters.append(
            LocalPlanAdapter(
                workspace_root=config.repo_root,
                allowlist=allowlist or DEFAULT_ALLOWLIST,
                excludelist=excludelist or DEFAULT_EXCLUDELIST,
            )
        )

    markdown_cfg = getattr(intent_cfg, "markdown_outline", None)
    if markdown_cfg is not None and bool(getattr(markdown_cfg, "enabled", False)) and str(getattr(tiers, "T2", "opt_in")) != "off":
        adapters.append(
            MarkdownOutlineAdapter(
                workspace_root=config.repo_root,
                excludelist=tuple(getattr(markdown_cfg, "excludelist", []) or ()),
                max_candidates=int(getattr(markdown_cfg, "max_candidates", 500) or 500),
            )
        )

    github_cfg = getattr(intent_cfg, "github_issues", None)
    repos = tuple(getattr(github_cfg, "repos", []) or ()) if github_cfg is not None else ()
    if github_cfg is not None and bool(getattr(github_cfg, "enabled", False)) and repos and str(getattr(tiers, "T3", "opt_in")) != "off":
        adapters.append(
            GitHubIssuesAdapter(
                repos=repos,
                include_closed=bool(getattr(github_cfg, "include_closed", False)),
                max_issues=int(getattr(github_cfg, "max_issues", 200) or 200),
            )
        )

    global_cfg = getattr(intent_cfg, "global_client_plans", None)
    if (
        global_cfg is not None
        and bool(getattr(global_cfg, "enabled", False))
        and str(getattr(tiers, "T2", "opt_in")) != "off"
    ):
        adapters.append(
            GlobalClientPlansAdapter(
                workspace_root=config.repo_root,
                selected_clients=tuple(getattr(global_cfg, "selected_clients", []) or ()),
                allowed_roots=tuple(getattr(global_cfg, "allowed_roots", []) or ()),
                include_rollout_summaries=bool(getattr(global_cfg, "include_rollout_summaries", False)),
                max_file_bytes=int(getattr(global_cfg, "max_file_bytes", 2 * 1024 * 1024) or 2 * 1024 * 1024),
                max_files=int(getattr(global_cfg, "max_files", 500) or 500),
                excludelist=tuple(getattr(global_cfg, "excludelist", []) or ()),
            )
        )

    accepted = 0
    for adapter in adapters:
        try:
            candidates = list(await adapter.discover())
        except Exception as exc:
            _LOG.warning("intent source discovery failed connector=%s: %s", getattr(adapter, "connector", "unknown"), exc)
            continue
        for candidate in candidates:
            try:
                raw = await adapter.fetch(candidate)
                result = await ingest_artefact(raw, store=store)
            except Exception as exc:
                _LOG.warning("intent artefact ingest failed source=%s: %s", getattr(candidate, "source_uri", ""), exc)
                continue
            if result.accepted:
                accepted += 1
    return accepted
