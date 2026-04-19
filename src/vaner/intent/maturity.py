# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from enum import StrEnum


class MaturityPhase(StrEnum):
    COLD_START = "cold_start"
    WARMING = "warming"
    LEARNING = "learning"
    MATURE = "mature"


class MaturityTracker:
    def phase_for_query_count(self, query_count: int) -> MaturityPhase:
        if query_count < 20:
            return MaturityPhase.COLD_START
        if query_count < 200:
            return MaturityPhase.WARMING
        if query_count < 2000:
            return MaturityPhase.LEARNING
        return MaturityPhase.MATURE
