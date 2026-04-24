# SPDX-License-Identifier: Apache-2.0
"""0.8.2 WS1 — intent-bearing artefact connectors.

Each connector implements :class:`vaner.intent.adapter.IntentArtefactSource`
and is responsible for discovering and fetching candidate artefacts from
its source system. The pipeline
(:func:`vaner.intent.ingest.pipeline.ingest_artefact`) classifies,
extracts, and persists — connectors never touch the classifier or store.

Shipped in WS1:

- :class:`LocalPlanAdapter` (T1, auto-enabled) — walks a configured
  allowlist of plan folders (``.claude/plans/``, ``.cursor/plans/``,
  ``docs/plans/``, ``docs/roadmap*``) within a workspace.
- :class:`MarkdownOutlineAdapter` (T2, opt-in) — scans the workspace
  allowlist for markdown files anywhere (broader than LocalPlanAdapter,
  subject to the same privacy excludelist).
- :class:`GitHubIssuesAdapter` (T3, opt-in) — pulls open issues and
  milestones from a configured per-repo allowlist, via the ``gh`` CLI.

Deferred to later work streams: notes-tool connectors (Obsidian,
Logseq), cloud-doc connectors, board connectors — all require auth /
network surface beyond WS1's reviewable scope.
"""

from vaner.intent.connectors.github_issues import GitHubIssuesAdapter
from vaner.intent.connectors.local_plan import LocalPlanAdapter
from vaner.intent.connectors.markdown_outline import MarkdownOutlineAdapter

__all__ = [
    "GitHubIssuesAdapter",
    "LocalPlanAdapter",
    "MarkdownOutlineAdapter",
]
