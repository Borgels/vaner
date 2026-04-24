# SPDX-License-Identifier: Apache-2.0
"""`<VANER_PREPARED_WORK_DIGEST version="1">` formatter.

Emits a compact list of currently-prepared predictions so context-mediating
integrations (proxies, injection-capable hosts) can tell the model that
Vaner has ready/maturing work without dumping full briefings into every
prompt.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from vaner.integrations.injection.tokens import count_tokens, truncate_to_budget

TokenCounter = Callable[[str], int]

DIGEST_VERSION = "1"
OPEN_TAG = f'<VANER_PREPARED_WORK_DIGEST version="{DIGEST_VERSION}">'
CLOSE_TAG = "</VANER_PREPARED_WORK_DIGEST>"


@dataclass(frozen=True)
class DigestEntry:
    """One row in the digest.

    ``readiness_label`` is the user-facing string (e.g. "Ready", "~20s",
    "Gathering evidence"). ``evidence_score`` is the raw 0..1 float the
    registry stores; formatter prints it only when
    ``include_confidence_details=True``.
    """

    label: str
    readiness_label: str
    evidence_score: float | None = None
    source_label: str | None = None
    eta_bucket_label: str | None = None


def build_digest(
    entries: Sequence[DigestEntry],
    *,
    budget_tokens: int,
    tokenizer: TokenCounter | None = None,
    include_confidence_details: bool = False,
    max_entries: int = 5,
) -> str:
    """Render the digest. Returns empty string if no entries or zero budget.

    The function respects ``budget_tokens`` exactly — it drops lower-ranked
    entries before the budget check, and truncates individual lines with
    :func:`truncate_to_budget` when still over budget.
    """
    if budget_tokens <= 0 or not entries:
        return ""

    kept = list(entries[:max_entries])
    while kept:
        block = _render(
            kept,
            include_confidence_details=include_confidence_details,
        )
        if count_tokens(block, tokenizer=tokenizer) <= budget_tokens:
            return block
        kept.pop()  # drop lowest-ranked entry and retry
    # Even the single best entry doesn't fit — emit a header-only form and
    # let the truncator cut the label line itself.
    head = f"{OPEN_TAG}\nVaner has prepared work but the current prompt budget is tight.\n{CLOSE_TAG}"
    return truncate_to_budget(head, budget_tokens=budget_tokens, tokenizer=tokenizer)


def _render(entries: Sequence[DigestEntry], *, include_confidence_details: bool) -> str:
    lines: list[str] = [OPEN_TAG, "Top prepared predictions:"]
    for i, entry in enumerate(entries, start=1):
        header = f'{i}. [{entry.readiness_label}] "{entry.label}"'
        if entry.eta_bucket_label and entry.eta_bucket_label != entry.readiness_label:
            header = f"{header} — {entry.eta_bucket_label}"
        lines.append(header)
        extras: list[str] = []
        if entry.source_label:
            extras.append(f"Source: {entry.source_label}.")
        if include_confidence_details and entry.evidence_score is not None:
            extras.append(f"Evidence: {entry.evidence_score:.2f}.")
        if extras:
            lines.append("   " + " ".join(extras))
    lines.append("")
    lines.append("If relevant, use vaner.predictions.active or vaner.predictions.adopt to inspect or adopt one.")
    lines.append(CLOSE_TAG)
    return "\n".join(lines)
