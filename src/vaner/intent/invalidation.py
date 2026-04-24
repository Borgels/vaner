# SPDX-License-Identifier: Apache-2.0
"""WS6 — invalidation signals for the persistent prediction pool.

The registry no longer rebuilds per cycle (see
``PredictionRegistry.merge`` + ``VanerEngine._merge_prediction_specs``).
Instead, a prediction's artifacts survive across cycles and are demoted
/ staled only when a *signal* says the underlying evidence has moved.

This module owns the signal vocabulary and the builders that turn raw
cycle inputs (git state, recent categories, adoptions) into discrete
:class:`InvalidationSignal` records the registry can apply.

Design notes
------------

- **Signals are declarative**, not imperative. Builders compute *what
  changed*; the registry decides *what to do* based on the signal kind
  and each prediction's source/specificity.
- **No wall-clock decay.** Time passing alone is not a signal. If no
  underlying state has changed, a prediction stays as it is.
- **Additive.** New signal kinds can be layered in without touching the
  registry's merge or rebalance paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SignalKind = Literal[
    "file_change",
    "commit",
    "category_shift",
    "adoption",
]


@dataclass(frozen=True, slots=True)
class InvalidationSignal:
    """A discrete invalidation event the registry can apply.

    Kinds:

    - ``file_change``: one or more paths changed since the prediction's
      captured hashes. ``payload["changed_paths"]`` is the list of paths
      whose content hash differs; ``payload["new_hashes"]`` carries the
      fresh hash map for the registry to update captured state after
      applying the demotion.
    - ``commit``: the repo HEAD moved. ``payload["from_sha"]`` and
      ``payload["to_sha"]`` identify the transition. Phase-anchored
      (category-level) predictions are staled; file-anchored predictions
      are handled by the file_change signal.
    - ``category_shift``: the user has spent the last N turns in a
      category different from the prediction's anchor. ``payload["from"]``
      and ``payload["to"]`` describe the shift; ``payload["streak"]`` is
      the consecutive-turns count. Affects arc/history-sourced
      predictions whose anchor is a category string.
    - ``adoption``: the user adopted this prediction. ``payload[
      "prediction_id"]`` is the target. Marks ``spent=True`` via the
      registry's existing ``record_adoption`` path.
    """

    kind: SignalKind
    payload: dict[str, object] = field(default_factory=dict)


def build_file_change_signal(
    old_hashes: dict[str, str],
    new_hashes: dict[str, str],
) -> InvalidationSignal | None:
    """Compare two ``{path: hash}`` maps, emit a ``file_change`` signal if any
    path's hash moved.

    Semantics:
      - A path present in ``old_hashes`` with a different value in
        ``new_hashes`` is a change.
      - A path present in ``old_hashes`` but missing in ``new_hashes``
        (file deleted) is a change.
      - A path present only in ``new_hashes`` is *not* a change from
        the old snapshot's point of view — the new hash will be
        captured at the next briefing-synthesis time.

    Returns None when nothing has changed — callers should handle this
    to avoid a pointless registry pass.
    """
    if not old_hashes:
        return None
    changed: list[str] = []
    for path, sha in old_hashes.items():
        new_sha = new_hashes.get(path)
        if new_sha is None or new_sha != sha:
            changed.append(path)
    if not changed:
        return None
    return InvalidationSignal(
        kind="file_change",
        payload={"changed_paths": changed, "new_hashes": dict(new_hashes)},
    )


def build_commit_signal(old_sha: str, new_sha: str) -> InvalidationSignal | None:
    """Emit a ``commit`` signal when HEAD moved. Empty-string comparison is
    the natural no-op for uninitialised-repo cases.
    """
    old = (old_sha or "").strip()
    new = (new_sha or "").strip()
    if not new or old == new:
        return None
    return InvalidationSignal(
        kind="commit",
        payload={"from_sha": old, "to_sha": new},
    )


def build_category_shift_signal(
    recent_categories: list[str],
    *,
    streak_threshold: int = 3,
) -> InvalidationSignal | None:
    """Emit a ``category_shift`` signal when the user's last ``N`` turns land
    in the same category and that category differs from the one ``N+1``
    turns back.

    ``recent_categories`` is oldest → newest. Returns None when the list
    is too short, when the trailing streak is shorter than
    ``streak_threshold``, or when no earlier category exists for
    comparison.
    """
    if len(recent_categories) <= streak_threshold:
        return None
    tail = recent_categories[-streak_threshold:]
    if len(set(tail)) != 1:
        return None
    current = tail[0]
    # Find the most recent category before the streak that differs.
    prior = None
    for cat in reversed(recent_categories[:-streak_threshold]):
        if cat and cat != current:
            prior = cat
            break
    if prior is None:
        return None
    return InvalidationSignal(
        kind="category_shift",
        payload={"from": prior, "to": current, "streak": streak_threshold},
    )


def build_adoption_signal(prediction_id: str) -> InvalidationSignal:
    """Emit an ``adoption`` signal for ``prediction_id``.

    Kept here for symmetry — callers can apply adoption through
    ``registry.record_adoption`` directly, but packaging it as a signal
    lets a future batch-apply path stay uniform.
    """
    return InvalidationSignal(
        kind="adoption",
        payload={"prediction_id": prediction_id},
    )
