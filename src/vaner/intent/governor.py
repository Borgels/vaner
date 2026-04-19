# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


@dataclass(slots=True)
class PredictionGovernor:
    class Mode(StrEnum):
        BACKGROUND = "background"
        DEDICATED = "dedicated"
        BUDGET = "budget"

    mode: Mode = Mode.BACKGROUND
    budget_units: int = 100
    inter_iteration_delay: float = 0.1
    _remaining_units: int = field(init=False, default=0)
    _user_request_active: bool = field(init=False, default=False)
    _stopped: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._remaining_units = max(0, int(self.budget_units))
        self._stopped = False

    def should_continue(self, units: int = 1) -> bool:
        if self._stopped:
            return False
        if self.mode == self.Mode.BACKGROUND:
            return not self._user_request_active
        if self.mode == self.Mode.DEDICATED:
            return True
        return self._remaining_units >= units

    def iteration_done(self, units: int = 1) -> bool:
        if self.mode != self.Mode.BUDGET:
            return True
        if self._remaining_units < units:
            return False
        self._remaining_units -= units
        return self._remaining_units >= 0

    def notify_user_request_start(self) -> None:
        self._user_request_active = True

    def notify_user_request_end(self) -> None:
        self._user_request_active = False

    def stop(self) -> None:
        self._stopped = True

    @property
    def remaining(self) -> int:
        if self.mode == self.Mode.BUDGET:
            return self._remaining_units
        return -1
