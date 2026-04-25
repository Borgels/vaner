# SPDX-License-Identifier: Apache-2.0
"""Vaner setup primitives — Simple-Mode wizard data model (0.8.6).

This package owns the outcome-level configuration surface introduced in
0.8.6:

- :mod:`vaner.setup.enums` — ``WorkStyle`` / ``Priority`` /
  ``ComputePosture`` / ``CloudPosture`` / ``BackgroundPosture`` /
  ``HardwareTier`` literal aliases.
- :mod:`vaner.setup.answers` — :class:`SetupAnswers` dataclass, the
  immutable record of one wizard run.
- :mod:`vaner.setup.policy` — :class:`VanerPolicyBundle` dataclass.
- :mod:`vaner.setup.catalog` — :data:`PROFILE_CATALOG` (the seven
  shipped bundles) and the ``bundle_by_id()`` lookup helper.

Hardware detection (:mod:`vaner.setup.hardware`), the selection
algorithm (:mod:`vaner.setup.select`), and the policy applicator
(:mod:`vaner.setup.apply`) land in subsequent 0.8.6 work streams.

Note: this ``__init__`` deliberately re-exports *only* the leaf modules
that have no dependency on the engine stack (``enums``, ``answers``).
Importing :class:`VanerPolicyBundle` or the catalogue requires
explicit submodule imports — ``from vaner.setup.policy import …`` —
because :mod:`vaner.setup.policy` depends transitively on
:mod:`vaner.intent.deep_run` and would create an import cycle if
re-exported from here while :mod:`vaner.models.config` also imports
from this package.
"""

from __future__ import annotations

from vaner.setup.answers import SetupAnswers
from vaner.setup.enums import (
    BackgroundPosture,
    CloudPosture,
    ComputePosture,
    HardwareTier,
    Priority,
    WorkStyle,
)

__all__ = [
    "BackgroundPosture",
    "CloudPosture",
    "ComputePosture",
    "HardwareTier",
    "Priority",
    "SetupAnswers",
    "WorkStyle",
]
