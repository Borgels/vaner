# SPDX-License-Identifier: Apache-2.0
"""WS1 — Setup primitives: SetupAnswers dataclass (0.8.6).

``SetupAnswers`` is the immutable record of one completed Simple-Mode
wizard run. It is the input to WS3's ``select_policy_bundle()`` and is
persisted (in normal-form, on the ``[setup]`` section of
``.vaner/config.toml``) so the engine can re-run selection on hardware
changes without re-prompting the user.

Validation in ``__post_init__`` enforces the *only* invariant Simple Mode
cannot recover from: empty ``work_styles``. The engine has no priors to
average if no work styles were selected; UI surfaces are responsible for
forcing at least one selection (defaulting to ``"mixed"`` when the user
has no preference). All other fields are scalar enums and validated by
their ``Literal`` types when loaded through Pydantic.
"""

from __future__ import annotations

from dataclasses import dataclass

from vaner.setup.enums import (
    BackgroundPosture,
    CloudPosture,
    ComputePosture,
    Priority,
    WorkStyle,
)


@dataclass(frozen=True, slots=True)
class SetupAnswers:
    """Immutable record of one completed Simple-Mode wizard run.

    ``work_styles`` is a tuple (not a list) so the dataclass remains
    hashable and properly frozen. The wizard surface accepts a list and
    converts at the boundary.
    """

    work_styles: tuple[WorkStyle, ...]
    priority: Priority
    compute_posture: ComputePosture
    cloud_posture: CloudPosture
    background_posture: BackgroundPosture

    def __post_init__(self) -> None:
        if not self.work_styles:
            raise ValueError(
                "SetupAnswers.work_styles must contain at least one WorkStyle; "
                "the engine has no priors to average if no work styles are selected. "
                'Pass ("mixed",) when the user has no preference.'
            )


__all__ = ["SetupAnswers"]
