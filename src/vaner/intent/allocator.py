# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vaner.store.artefacts import ArtefactStore

_BUCKET_MIN = 0.05
_BUCKET_MAX = 0.70
_EMA_ALPHA = 0.10


@dataclass(frozen=True, slots=True)
class BudgetAllocation:
    exploit_ms: float
    hedge_ms: float
    invest_ms: float
    no_regret_ms: float

    @property
    def total_ms(self) -> float:
        return self.exploit_ms + self.hedge_ms + self.invest_ms + self.no_regret_ms


def expected_value_score(
    *,
    probability: float,
    payoff: float,
    reuse_potential: float,
    confidence_gain_per_second: float,
) -> float:
    return (
        max(0.0, float(probability))
        * max(0.0, float(payoff))
        * max(0.0, float(reuse_potential))
        * max(0.0, float(confidence_gain_per_second))
    )


class NoRegretSlice:
    """Always-reserved context slice built from the current working set.

    These are files that are *known* to be relevant regardless of the posterior:
    recently changed files, their 1-hop call-graph neighbors, todo/fixme clusters,
    and recent error paths. Collected synchronously from the store — no LLM needed.
    """

    async def collect(self, store: ArtefactStore, *, limit: int = 20) -> list[str]:
        paths: list[str] = []
        seen: set[str] = set()

        def _add(p: str) -> None:
            if p and p not in seen and len(paths) < limit:
                seen.add(p)
                paths.append(p)

        # Changed files from the most recent working-set snapshot.
        working_set = await store.get_latest_working_set()
        changed: list[str] = []
        if working_set is not None:
            for key in working_set.artefact_keys:
                rel = key.split(":", 1)[-1] if ":" in key else key
                changed.append(rel)
                _add(rel)

        # 1-hop call-graph neighbors of changed files.
        if changed:
            edges: list[Any] = list(await store.list_relationship_edges(limit=500) or [])
            changed_set = set(changed)
            for src, dst, *_ in edges:
                src_rel = str(src).split(":", 1)[-1] if ":" in str(src) else str(src)
                dst_rel = str(dst).split(":", 1)[-1] if ":" in str(dst) else str(dst)
                if src_rel in changed_set:
                    _add(dst_rel)
                elif dst_rel in changed_set:
                    _add(src_rel)

        # High-signal TODO/FIXME clusters.
        try:
            quality_issues: list[Any] = list(await store.list_quality_issues(limit=50) or [])
            for issue in quality_issues:
                issue_type = str(issue.get("type", issue.get("issue_type", ""))).lower()
                if "todo" in issue_type or "fixme" in issue_type:
                    key = str(issue.get("key", ""))
                    rel = key.split(":", 1)[-1] if ":" in key else key
                    _add(rel)
        except Exception:
            pass

        # Recent error-source signal events.
        try:
            signal_events: list[Any] = list(await store.list_signal_events(limit=20) or [])
            for event in signal_events:
                source = str(event.source if hasattr(event, "source") else event.get("source", ""))
                if "error" in source.lower():
                    payload = event.payload if hasattr(event, "payload") else event.get("payload", {})
                    if isinstance(payload, dict):
                        path = str(payload.get("path", payload.get("file", "")))
                        _add(path)
        except Exception:
            pass

        return paths[:limit]


class PortfolioAllocator:
    def __init__(
        self,
        *,
        exploit_ratio: float = 0.50,
        hedge_ratio: float = 0.20,
        invest_ratio: float = 0.10,
        no_regret_ratio: float = 0.20,
    ) -> None:
        self.exploit_ratio = max(0.0, exploit_ratio)
        self.hedge_ratio = max(0.0, hedge_ratio)
        self.invest_ratio = max(0.0, invest_ratio)
        self.no_regret_ratio = max(0.0, no_regret_ratio)

    def allocate(self, total_ms: float) -> BudgetAllocation:
        total = max(0.0, float(total_ms))
        ratio_sum = self.exploit_ratio + self.hedge_ratio + self.invest_ratio + self.no_regret_ratio
        if ratio_sum <= 0.0:
            return BudgetAllocation(exploit_ms=total, hedge_ms=0.0, invest_ms=0.0, no_regret_ms=0.0)
        scale = total / ratio_sum
        return BudgetAllocation(
            exploit_ms=self.exploit_ratio * scale,
            hedge_ms=self.hedge_ratio * scale,
            invest_ms=self.invest_ratio * scale,
            no_regret_ms=self.no_regret_ratio * scale,
        )

    def update_from_returns(self, bucket: str, *, useful: bool) -> None:
        """EMA-nudge a bucket ratio up on a hit, down on a miss.

        The nudge is small (α=0.10 of ±0.01) and each ratio is clamped to
        [0.05, 0.70] so no bucket can disappear or monopolise the budget.
        Ratios are NOT renormalised here — the engine normalises at allocation
        time via ``allocate()``'s ratio_sum scale factor.
        """
        delta = _EMA_ALPHA * 0.01
        if bucket == "exploit":
            new = self.exploit_ratio + delta if useful else self.exploit_ratio - delta
            self.exploit_ratio = max(_BUCKET_MIN, min(_BUCKET_MAX, new))
        elif bucket == "hedge":
            new = self.hedge_ratio + delta if useful else self.hedge_ratio - delta
            self.hedge_ratio = max(_BUCKET_MIN, min(_BUCKET_MAX, new))
        elif bucket == "invest":
            new = self.invest_ratio + delta if useful else self.invest_ratio - delta
            self.invest_ratio = max(_BUCKET_MIN, min(_BUCKET_MAX, new))
        elif bucket == "no_regret":
            new = self.no_regret_ratio + delta if useful else self.no_regret_ratio - delta
            self.no_regret_ratio = max(_BUCKET_MIN, min(_BUCKET_MAX, new))
